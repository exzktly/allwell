"""Atomic output writers for the pipeline (images, CSVs, overlays).

Extracted from ``process_microscopy.py`` so the "write outputs to disk" helpers
live in one cohesive, Qt-free module: atomic image/text writes (tmp → fsync →
rename, so an interrupted worker never leaves partial bytes a later zip-up step
would pack into a corrupt archive), the per-well CSV writers, the completeness
check, the boundary overlay, and the scratch-base + staging-directory lifecycle
(``scratch_base_for`` / ``remove_directory`` / the stale sweeps / the staging
commit) that decides *which volume* all of that lands on.
``process_microscopy`` re-exports every name so its many ``_safe_imwrite`` /
``write_csvs_per_well`` / ``remove_directory`` call sites are unchanged.

Depends only on numpy + tifffile/imageio/scikit-image + stdlib — no
``process_microscopy`` internals — so there is no import cycle.
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
import time
import zipfile
from pathlib import Path

import numpy as np
import imageio.v3 as iio
from tifffile import imwrite
from skimage.segmentation import find_boundaries

# Log under the pipeline logger name so worker-log output is unchanged.
log = logging.getLogger("process_microscopy")


# Default scratch location, under the user's home directory. Deliberately a
# dedicated subdirectory rather than ``~`` itself: ``_sweep_stale_scratch_dirs``
# globs ``_tmp_extract_*`` / ``_tmp_images_*`` under the scratch base and
# *deletes* what it finds, so pointing the base at a directory the user also
# keeps their own files in would be destructive. Everything below it is
# transient per-run working state (it is NOT a cache — deleting it mid-run
# discards the per-FOV checkpoints a restart resumes from).
DEFAULT_SCRATCH_SUBDIR = Path(".allwell") / "scratch"


def default_scratch_base() -> "Path | None":
    """``~/.allwell/scratch``, created on demand, or None if unusable."""
    try:
        p = Path.home() / DEFAULT_SCRATCH_SUBDIR
        p.mkdir(parents=True, exist_ok=True)
        return p
    except (OSError, RuntimeError):
        # RuntimeError: Path.home() can't resolve a home directory at all.
        return None


def scratch_base_for(output_dir: Path) -> Path:
    """Directory that holds per-well working files (zip extraction + temp images).

    Resolution order:

    1. ``$ALLWELL_SCRATCH_DIR`` — the redirectable-scratch env the GUI sets from
       its "Scratch disk" field and the remote runner already uses.
    2. ``~/.allwell/scratch`` — the default (see :data:`DEFAULT_SCRATCH_SUBDIR`).
    3. *output_dir* — last-resort fallback when neither of the above can be
       created, so an unwritable home never fails the run outright.

    **Why the home directory is the default.** This scratch carries the whole
    run's transient I/O: every extracted input TIF, every intermediate image
    (~6 per FOV-group, each written tmp → fsync → rename, so three filesystem
    operations apiece), and a JSON checkpoint per FOV-group. It used to default
    to *output_dir*, which put tens of thousands of file operations from every
    worker process onto whatever volume the user chose for results. When that is
    a network mount the load is enough to drop the session, and macOS's SMB
    client does not re-open server-side handles across a reconnect — every
    descriptor opened before the drop then fails with ``OSError: [Errno 9] Bad
    file descriptor``, killing whichever wells had files open. Keeping the churn
    on local storage leaves only the two per-well result writes (the CSV and the
    ``<well>_out.zip``) on the network volume.

    Both zip- and folder-mode temps tolerate a separate filesystem: nothing is
    renamed across the boundary (folder mode's staging commit falls back to a
    copy when the rename would be cross-device — see
    :func:`_commit_staging_dir`).
    """
    override = os.environ.get("ALLWELL_SCRATCH_DIR", "").strip()
    if override:
        try:
            p = Path(override).expanduser()
            p.mkdir(parents=True, exist_ok=True)
            return p
        except OSError as exc:
            log.warning("Scratch dir %s unusable (%s); falling back.", override, exc)
    home = default_scratch_base()
    if home is not None:
        return home
    log.warning(
        "Home scratch dir (~/%s) unusable; using the output dir %s for per-well "
        "working files. On a network-mounted output dir this is the "
        "configuration that causes 'Bad file descriptor' well failures — set "
        "ALLWELL_SCRATCH_DIR (Analyze → Scratch disk) to a local path.",
        DEFAULT_SCRATCH_SUBDIR, output_dir,
    )
    return output_dir


# ---------------------------------------------------------------------------
# Staging-directory lifecycle
#
# The per-well staging dirs live under the scratch base above, so their
# create / sweep / commit lifecycle belongs next to it rather than in the
# orchestrator. ``process_microscopy`` re-exports all four names, so its call
# sites (and the remote worker) are unchanged.
# ---------------------------------------------------------------------------

def remove_directory(path: Path, *, retries: int = 4, backoff: float = 0.5) -> None:
    """Remove *path* and all its contents, with retries for remote filesystems.

    Networked / remote filesystems (SMB / NFS / AppleShare) routinely fail
    ``shutil.rmtree`` on the first attempt because:

    * The OS hasn't yet released file handles that backed recently-written
      files (lazy close on the server side).
    * macOS Finder reinjects ``.DS_Store`` sidecars and ``._<file>``
      AppleDouble metadata into the directory while it's being deleted.
    * Server-side indexers and antivirus scanners briefly hold scanned
      files open.

    The previous implementation logged a single warning and gave up,
    leaving stale ``_tmp_extract_<well>`` / ``_tmp_images_<well>``
    directories behind on every pipeline run. The retry loop here:

    1. Strips the read-only bit on individual files that raise
       ``PermissionError`` and retries that file.
    2. Sleeps with a linear backoff between full ``rmtree`` attempts so
       the kernel / server has time to release lazy handles.
    3. Logs at WARNING only after every attempt fails — so a transient
       remote-FS hiccup no longer pollutes the GUI log.
    """
    import stat as _stat

    if not path.exists():
        return

    def _on_rm_error(func, target, exc_info):
        # ``onerror`` runs per-file. PermissionError typically means a
        # read-only attribute set (common on Windows / SMB shares); flip
        # it and retry. Anything else: propagate so the outer attempt
        # falls through to the retry/backoff path.
        exc = exc_info[1] if exc_info else None
        if isinstance(exc, PermissionError):
            try:
                os.chmod(target, _stat.S_IWRITE | _stat.S_IREAD)
                func(target)
                return
            except OSError:
                pass
        if exc is not None:
            raise exc
        raise OSError(f"unknown rmtree error on {target!r}")

    last_exc: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            shutil.rmtree(path, onerror=_on_rm_error)
            if attempt > 0:
                log.info(
                    "Removed %s after %d retry(ies) (remote-FS lazy release).",
                    path, attempt,
                )
            else:
                log.debug("Removed temporary directory: %s", path)
            return
        except Exception as exc:  # noqa: BLE001 — every Errno → keep trying
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(backoff * (attempt + 1))

    if last_exc is not None:
        log.warning(
            "Could not remove %s after %d attempts: %s — leftover files "
            "can be removed manually.",
            path, retries, last_exc,
        )


def _commit_staging_dir(tmp_images: Path, final_out: Path) -> None:
    """Move the folder-mode staging dir into place as *final_out*.

    A plain ``rename`` is the fast path and the only one that was possible when
    the staging dir was pinned under ``output_dir``. Now that folder-mode
    staging honours the scratch base (which defaults to ``~/.allwell/scratch``,
    off the output volume), the two can live on different filesystems, where
    ``rename`` raises ``EXDEV``. Fall back to a copy + remove in that case.

    The copy is deliberately not atomic: the caller has already removed any
    previous *final_out*, and a crash mid-copy leaves the well's per-FOV
    checkpoints intact for ``--incremental`` to resume from.
    """
    try:
        tmp_images.rename(final_out)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    log.info(
        "Well output staged on a different filesystem than %s; copying instead "
        "of renaming.", final_out.parent,
    )
    shutil.copytree(tmp_images, final_out, dirs_exist_ok=True)
    remove_directory(tmp_images)


def _sweep_stale_scratch_dirs(root: Path, *, extra_extract: bool) -> None:
    """Remove stale ``_tmp_*`` staging dirs from a previous crashed run under
    *root*, but keep any ``_tmp_images_*`` dir holding ``.fov_*.done`` checkpoint
    markers (v5.2.0) so the worker resumes from them. ``extra_extract`` also
    sweeps the zip-mode ``_tmp_extract_*`` scratch (audit A10)."""
    if extra_extract:
        for _stale in sorted(root.glob("_tmp_extract_*")):
            if _stale.is_dir():
                log.warning("Removing stale temporary directory from previous run: %s", _stale.name)
                remove_directory(_stale)
    for _stale in sorted(root.glob("_tmp_images_*")):
        if not _stale.is_dir():
            continue
        if any(_stale.glob(".fov_*.done")):
            n_done = sum(1 for _ in _stale.glob(".fov_*.done"))
            log.info(
                "Keeping checkpoint dir %s (%d FOV(s) already done)",
                _stale.name, n_done,
            )
            continue
        log.warning("Removing stale temporary directory from previous run: %s", _stale.name)
        remove_directory(_stale)


def _sweep_stale_scratch_dirs_all(output_dir: Path, *, extra_extract: bool) -> None:
    """Sweep stale staging dirs from both the scratch base and *output_dir*.

    The scratch base is where staging lives now. ``output_dir`` is swept too
    (when it differs) because runs from before the scratch default moved to
    ``~/.allwell/scratch`` left their ``_tmp_images_*`` / ``_tmp_extract_*``
    dirs there, and nothing else would ever clean them up.
    """
    base = scratch_base_for(output_dir)
    _sweep_stale_scratch_dirs(base, extra_extract=extra_extract)
    if base.resolve() != output_dir.resolve():
        _sweep_stale_scratch_dirs(output_dir, extra_extract=extra_extract)


def _fsync_path(path: Path) -> None:
    """Open *path* read-only and fsync; ignore filesystems that don't support it."""
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(fd)
        except OSError:
            pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


# Retry budget for the image write. ``tifffile`` writes the pixel buffer with
# ``ndarray.tofile``, which raises the cryptic ``OSError: <N> requested and 0
# written`` whenever the underlying ``fwrite`` comes back short. The error is at
# the *write* stage (the file already opened), so with free disk space available
# it is not "disk full" — the usual culprits on a local volume are a transient
# filesystem/I-O hiccup, a backup / anti-virus / indexing process momentarily
# holding the freshly-created file, or (macOS APFS) space that ``df`` reports
# free but that isn't yet writable. All of those clear on their own, so the right
# response is to back off and retry; only when the volume is genuinely out of
# room (free space below the image size) is retrying futile and we say so
# outright. If the retries are exhausted the caller still sees a failure — with
# an honest, actionable diagnostic rather than the raw numpy message — so the FOV
# is marked failed instead of emitting a truncated image. ``_RETRY_BASE_DELAY_S``
# is patched to 0 in tests. Mirrors the GUI-side shield in
# ``well_viewer/persistence/_io.py``.
_WRITE_MAX_ATTEMPTS = 5
_RETRY_BASE_DELAY_S = 0.5


def _free_bytes(path: Path) -> "int | None":
    """Free bytes on *path*'s volume, or ``None`` if it can't be determined."""
    try:
        return shutil.disk_usage(path.parent).free
    except OSError:
        return None


def retry_write(fn, *, what: str, cleanup=None):
    """Run *fn*, retrying on ``OSError`` with the ``_safe_imwrite`` backoff.

    The image writer has been retry-shielded for a while; the per-well CSV and
    the ``<well>_out.zip`` were not, so a transient filesystem fault on those
    two writes failed the whole well with the bare errno. That is what surfaces
    as ``OSError: [Errno 9] Bad file descriptor`` on a network-mounted output
    dir: macOS's SMB client invalidates open handles when the session drops and
    reconnects, and both of those writes happen on the output volume even when
    the scratch churn has been moved off it.

    *fn* must be **idempotent** — every caller stages to a ``__aw_tmp_`` file
    and commits with ``os.replace``, so re-running it is safe. *cleanup* (if
    given) removes the partial staging file between attempts.

    Non-``OSError`` exceptions are never retried. Once the budget is exhausted
    the failure propagates as an enriched ``OSError`` naming the target and the
    attempt count, so the caller marks the well failed with an actionable
    message instead of a bare errno.
    """
    for attempt in range(_WRITE_MAX_ATTEMPTS):
        try:
            return fn()
        except OSError as exc:
            if cleanup is not None:
                cleanup()
            if attempt + 1 >= _WRITE_MAX_ATTEMPTS:
                raise OSError(
                    f"Failed to write {what} after {_WRITE_MAX_ATTEMPTS} "
                    f"attempts ({exc}). A repeated failure here usually means "
                    f"the destination volume is unreliable under parallel load "
                    f"— on a network mount (SMB/NFS) a dropped session "
                    f"invalidates open file handles and every subsequent write "
                    f"fails. Point the output at local storage, or reduce "
                    f"--workers, and re-run with --incremental to resume."
                ) from exc
            log.warning(
                "write %s failed (%s); retry %d/%d after backoff",
                what, exc, attempt + 1, _WRITE_MAX_ATTEMPTS - 1,
            )
            time.sleep(_RETRY_BASE_DELAY_S * (2 ** attempt))


def _safe_imwrite(out_path: "Path | str", data: "np.ndarray") -> None:
    """Atomic image write: tmp file → fsync → rename, with retry on OSError.

    Routes ``.tif/.tiff`` through tifffile and ``.png`` through imageio v3.
    Other extensions fall back to tifffile. Cleans up the temp file on
    failure so the well's output directory never carries partial bytes
    that a later zip-up step would pack into a corrupt archive.

    An ``OSError`` from the codec (notably numpy's cryptic ``<N> requested and 0
    written`` short-write) is retried up to ``_WRITE_MAX_ATTEMPTS`` times with
    exponential backoff — such failures are usually transient (filesystem/I-O
    blip, backup/AV holding the file, macOS APFS purgeable space) and clear on
    a retry; the partial tmp is unlinked between attempts. Two exits skip the
    remaining retries: a genuinely full volume (free space below the image
    size), which cannot be fixed by retrying, is reported immediately; and once
    the budget is exhausted the final failure propagates as an enriched
    ``OSError`` (original message + free space + likely causes + how to resume)
    so the caller marks the FOV failed rather than emitting a truncated image.
    Non-``OSError`` exceptions (e.g. ``KeyboardInterrupt``) are never retried.

    The tmp file is named ``__aw_tmp_<stem>.pid<N><ext>`` — a unique
    prefix the zip-up step's listers explicitly exclude. The trailing
    original extension is preserved so the codec auto-detects the
    format from the path.
    """
    out_path = Path(out_path)
    # `__aw_tmp_` prefix is unique enough that the scratch-file
    # exclusion can match on it without false positives from user
    # filenames (a TIF named `exp.pidgin.A01.tif` previously matched
    # the `.pid` substring check below).
    tmp = out_path.with_name(
        f"__aw_tmp_{out_path.stem}.pid{os.getpid()}{out_path.suffix}"
    )
    suffix = out_path.suffix.lower()
    need = int(getattr(data, "nbytes", 0) or 0)

    def _cleanup_tmp() -> None:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass

    for attempt in range(_WRITE_MAX_ATTEMPTS):
        try:
            if suffix == ".png":
                iio.imwrite(str(tmp), data)
            else:
                imwrite(str(tmp), data)
            _fsync_path(tmp)
            os.replace(str(tmp), str(out_path))
            return
        except OSError as exc:
            _cleanup_tmp()
            free = _free_bytes(out_path)
            # Genuinely out of room → retrying cannot help; say so plainly.
            if free is not None and free < need:
                raise OSError(
                    f"No space left on device while writing {out_path.name}: "
                    f"{free:,} byte(s) free on {out_path.parent}, need "
                    f"{need:,} for this image. Free up space (or point "
                    f"ALLWELL_SCRATCH_DIR at a roomier volume) and re-run with "
                    f"--incremental to resume."
                ) from exc
            if attempt + 1 >= _WRITE_MAX_ATTEMPTS:
                free_str = (f"{free:,} byte(s) free" if free is not None
                            else "free space unknown")
                raise OSError(
                    f"Failed to write {out_path.name} after "
                    f"{_WRITE_MAX_ATTEMPTS} attempts ({exc}); {free_str} on "
                    f"{out_path.parent}. A zero-byte write with space available "
                    f"usually means a transient filesystem/I-O error, a backup / "
                    f"anti-virus / indexing process holding the file, or (macOS "
                    f"APFS) space df reports free but that is not yet writable. "
                    f"Re-run with --incremental to resume the unfinished "
                    f"FOV-groups."
                ) from exc
            log.warning(
                "imwrite %s failed (%s); retry %d/%d after backoff",
                out_path.name, exc, attempt + 1, _WRITE_MAX_ATTEMPTS - 1,
            )
            time.sleep(_RETRY_BASE_DELAY_S * (2 ** attempt))
        except BaseException:
            _cleanup_tmp()
            raise


def _safe_atomic_text_write(out_path: "Path | str") -> "_AtomicTextHandle":
    """Return a context manager that yields a file handle for atomic text writing.

    On clean exit the buffered bytes are fsynced and the temp file is renamed
    over ``out_path``. On exception the temp file is removed and the
    exception propagates so callers see the original error.
    """
    return _AtomicTextHandle(Path(out_path))


class _AtomicTextHandle:
    def __init__(self, out_path: Path) -> None:
        self._out_path = out_path
        self._tmp = out_path.with_name(f"__aw_tmp_{out_path.name}.pid{os.getpid()}")
        self._fh: "io.TextIOBase | None" = None

    def __enter__(self):
        # newline="" matches csv.writer's expectation; the sole text-write
        # call site (_write_single_csv) needs that, and it does no harm to
        # other text content.
        self._fh = open(self._tmp, "w", newline="")
        return self._fh

    def __exit__(self, exc_type, exc_val, exc_tb):
        # ``flush()`` and ``close()`` used to run unguarded here, so an OSError
        # from either (an EBADF on a reconnected SMB mount is the common one)
        # escaped straight out of the caller as a bare errno. Capture it
        # instead: it must NOT be swallowed — a failed flush means the tmp file
        # is short, and renaming that over the target would silently truncate
        # the CSV — but it must not mask an exception already in flight either.
        close_exc: "OSError | None" = None
        try:
            if self._fh is not None:
                try:
                    self._fh.flush()
                    try:
                        os.fsync(self._fh.fileno())
                    except OSError:
                        # fsync isn't supported on every filesystem; the replace
                        # below is still atomic without it.
                        pass
                finally:
                    try:
                        self._fh.close()
                    except OSError as exc:
                        close_exc = exc
        except OSError as exc:
            close_exc = exc
        finally:
            self._fh = None

        if exc_type is not None or close_exc is not None:
            try:
                if self._tmp.exists():
                    self._tmp.unlink()
            except OSError:
                pass
            if exc_type is not None:
                return False        # propagate the caller's original exception
            raise close_exc         # our own failure — surface it to the retry
        os.replace(str(self._tmp), str(self._out_path))
        return False


def _verify_files_complete(paths: "list[Path]", min_size: int = 1) -> "list[Path]":
    """Return only paths that exist and have ``stat.st_size >= min_size``.

    Skipped paths are warned to the worker log so a partial/zero-length file
    doesn't get silently packed into an output archive that downstream tools
    will then refuse to open. The minimum size is intentionally tiny — we
    only want to catch the empty / zero-byte case where a write was started
    but never delivered any bytes; legitimate single-pixel TIFs are still
    far above this threshold.
    """
    ok: list[Path] = []
    for p in paths:
        try:
            st = p.stat()
        except OSError as exc:
            log.warning("Skipping unreadable file: %s (%s)", p, exc)
            continue
        if not p.is_file():
            log.warning("Skipping non-file path: %s", p)
            continue
        if st.st_size < min_size:
            log.warning(
                "Skipping zero/short file (size=%d): %s",
                st.st_size, p,
            )
            continue
        ok.append(p)
    return ok


def _safe_well(label: str) -> str:
    """Sanitise a well label for use as a filename component."""
    return label.replace("/", "_").replace("\\", "_")


def _write_single_csv(records: list[dict], out_path: Path) -> None:
    import csv
    fieldnames = list(records[0].keys())

    # Atomic write: tmp → fsync → rename. Without this a downstream consumer
    # (the GUI's CSV-loader, the Bar / Line plot tabs, or the next pipeline
    # run with --force) can pick up a half-written file when the worker is
    # interrupted or when the OS hasn't flushed the page cache before the
    # follow-up rename / read. Retried because this is one of only two writes
    # that must land on the (possibly network-mounted) output volume.
    def _write() -> None:
        with _safe_atomic_text_write(out_path) as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(records)

    retry_write(_write, what=Path(out_path).name)


def write_csvs_per_well(
    all_records: list[dict],
    output_dir: Path,
    csv_prefix: str,
) -> None:
    """Write one CSV per well, partitioned by the 'well' metadata field."""
    if not all_records:
        log.warning("No records to write; no per-well CSVs produced.")
        return

    from collections import defaultdict
    by_well: dict[str, list[dict]] = defaultdict(list)
    for rec in all_records:
        well = rec.get("well") or "unknown_well"
        by_well[well].append(rec)

    for well, records in sorted(by_well.items()):
        safe_well = _safe_well(well)
        out_path  = output_dir / f"{csv_prefix}_{safe_well}.csv"
        _write_single_csv(records, out_path)
        log.info("Well %-12s -> %s  (%d rows)", well, out_path.name, len(records))


# ---------------------------------------------------------------------------
# Incremental (FOV-level) merge helpers
#
# These let an ``--incremental`` run absorb only newly-added FOV-groups into an
# already-processed well without re-segmenting the FOVs already on disk: the
# done-set is read from the prior per-well CSV, the new rows are merged into that
# CSV, and the new output images are merged into the existing ``<well>_out.zip``.
# All three are atomic (tmp → fsync → rename) and de-dup so a re-run is a no-op.
# ---------------------------------------------------------------------------

# Image extensions packed into an output well-zip (mirrors compress_images_to_zip
# in process_microscopy). Kept here so merge_images_into_zip is self-contained
# and pipeline.io carries no process_microscopy import (no cycle).
_OUTPUT_IMAGE_EXTS = {".tif", ".tiff", ".png"}


# The stdlib-only ``(fov, timepoint)`` done-key readers were extracted to
# ``pipeline.incremental_keys`` (audit A2) so the imaging-stack-free remote
# client can share one implementation instead of hand-copying twins — importing
# this module drags in numpy/tifffile/skimage, which the client must avoid.
# Re-exported here so existing ``pipeline.io`` importers keep working.
from pipeline.incremental_keys import (  # noqa: E402,F401  (re-export, audit A2)
    fov_tp_from_name as _fov_tp_from_name,
    incremental_done_fov_keys,
    read_done_fov_keys,
    read_done_fov_keys_from_folder,
    read_done_fov_keys_from_zip,
)


def merge_records_into_csv(
    out_path: "Path | str",
    new_records: "list[dict]",
    *,
    key_fields: "tuple[str, ...]" = ("well", "fov", "timepoint", "nucleus_id"),
) -> int:
    """Append *new_records* to an existing per-well CSV, skipping duplicate keys.

    Existing rows are preserved verbatim (and their column order kept); records
    whose ``key_fields`` tuple already appears — in the file or earlier in
    *new_records* — are dropped, so a re-run that re-processes the same FOV is
    idempotent. New columns absent from the old header are appended to the
    field order. The write is atomic (tmp → fsync → rename). When the target
    doesn't exist this is a plain fresh write. Returns the number of rows added.
    """
    import csv

    path = Path(out_path)
    if not new_records:
        return 0

    def _key(row: "dict") -> tuple:
        return tuple(str(row.get(f, "")) for f in key_fields)

    existing_rows: "list[dict]" = []
    fieldnames: "list[str]" = []
    if path.exists():
        try:
            with open(path, newline="") as fh:
                reader = csv.DictReader(fh)
                fieldnames = list(reader.fieldnames or [])
                existing_rows = list(reader)
        except OSError:
            existing_rows = []
            fieldnames = []

    seen = {_key(r) for r in existing_rows}
    to_add: "list[dict]" = []
    for rec in new_records:
        k = _key(rec)
        if k in seen:
            continue
        seen.add(k)
        to_add.append(rec)

    if not to_add and path.exists():
        return 0

    # Union the field order: existing header first, then any new columns.
    if not fieldnames:
        fieldnames = list(new_records[0].keys())
    for rec in to_add:
        for col in rec.keys():
            if col not in fieldnames:
                fieldnames.append(col)

    def _write() -> None:
        with _safe_atomic_text_write(path) as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, restval="",
                                    extrasaction="ignore")
            writer.writeheader()
            writer.writerows(existing_rows)
            writer.writerows(to_add)

    retry_write(_write, what=path.name)
    return len(to_add)


def merge_images_into_zip(out_zip: "Path | str", image_dir: "Path | str") -> int:
    """Add the new images in *image_dir* to *out_zip*, keeping existing members.

    Members already present in the zip win on an arcname collision (matches the
    "keep existing, skip incoming" rule used elsewhere), so re-running is a
    no-op. Existing members are copied across into a fresh archive and the write
    is atomic (tmp → fsync → rename) — the same crash-safety contract as
    ``compress_images_to_zip``, since zip append-mode is not crash-safe. When the
    target doesn't exist this is a plain fresh compress. Returns images added.
    """
    out_zip = Path(out_zip)
    image_dir = Path(image_dir)

    candidates = sorted(
        p for p in image_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() in _OUTPUT_IMAGE_EXTS
        and not p.name.endswith(".tmp")
        and not p.name.startswith("__aw_tmp_")
    ) if image_dir.is_dir() else []
    images = _verify_files_complete(candidates)

    existing_names: "set[str]" = set()
    if out_zip.exists():
        try:
            with zipfile.ZipFile(out_zip, "r") as zf:
                existing_names = set(zf.namelist())
        except (OSError, zipfile.BadZipFile):
            existing_names = set()

    to_add = [p for p in images if p.name not in existing_names]
    if not to_add and out_zip.exists():
        return 0

    out_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp_zip = out_zip.with_name(f"__aw_tmp_{out_zip.name}.pid{os.getpid()}")

    def _cleanup_tmp() -> None:
        try:
            if tmp_zip.exists():
                tmp_zip.unlink()
        except OSError:
            pass

    def _build() -> None:
        try:
            with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                if out_zip.exists():
                    with zipfile.ZipFile(out_zip, "r") as src:
                        for name in src.namelist():
                            zf.writestr(src.getinfo(name), src.read(name))
                for p in to_add:
                    zf.write(p, arcname=p.name)
            _fsync_path(tmp_zip)
            os.replace(str(tmp_zip), str(out_zip))
        except BaseException:
            # Non-OSError (e.g. KeyboardInterrupt) isn't retried, so clean up
            # here too rather than relying on retry_write's cleanup hook.
            _cleanup_tmp()
            raise

    # Retried for the same reason as the per-well CSV: this lands on the output
    # volume, and staging to __aw_tmp_ + os.replace makes it safe to re-run.
    retry_write(_build, what=out_zip.name, cleanup=_cleanup_tmp)
    return len(to_add)


def save_overlay(nir_raw: np.ndarray, labels: np.ndarray, out_path: Path) -> None:
    """Save an 8-bit RGB PNG with nucleus boundaries drawn in red."""
    mn, mx = nir_raw.min(), nir_raw.max()
    grey = ((nir_raw - mn) / (mx - mn) * 255).astype(np.uint8) if mx > mn \
           else np.zeros_like(nir_raw, dtype=np.uint8)
    rgb = np.stack([grey, grey, grey], axis=-1)
    boundaries = find_boundaries(labels, mode="outer")
    rgb[boundaries, 0] = 255
    rgb[boundaries, 1] = 0
    rgb[boundaries, 2] = 0
    _safe_imwrite(out_path, rgb)
