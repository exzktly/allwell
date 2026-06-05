"""
process_microscopy.py
---------------------
Production-ready pipeline to segment nuclei with StarDist and quantify
GFP intensities per nucleus from paired fluorescence microscopy images.

Zip-file mode (default when --input_dir contains .zip files)
------------------------------------------------------------
Input zips must be named after 96-well plate positions, e.g.:
    A01.zip  A02.zip  ...  H12.zip

For each zip the script will:
  1. Validate the name against the 96-well plate pattern (A-H, 01-12).
  2. Extract contents to a temporary directory  <output_dir>/tmp_<well>/.
  3. Run the full segmentation + quantification pipeline on that directory,
     writing QC images to a per-zip staging area.
  4. Compress all output images (masks + overlays) into
         <output_dir>/<well>_out.zip
  5. Delete the temporary directories (extracted input + staged images).
  6. Per-well CSVs are written to <output_dir> as usual (not zipped).

If no .zip files are found in --input_dir the script falls back to the
original flat-directory behaviour.

Usage
-----
python process_microscopy.py \\
    --input_dir  /data/raw \\        # may contain A01.zip … H12.zip
    --output_dir /data/results \\
    --nuclear_token NIR \\
    --gfp_token GFP \\
    --tophat_radius_nir 100 \\
    --tophat_radius_gfp 100 \\
    --no_tophat_nir \\
    --no_tophat_gfp \\
    --workers 4 \\
    --csv_prefix measurements

Filename convention (underscore-separated):
    <ExperimentName>_<Well>_<FOV>_<Timepoint>_NIR.tif
    e.g.  Exp01_B03_F001_T0001_NIR.tif
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import re
import shutil
import tempfile
import time
import zipfile
from collections import defaultdict
from pathlib import Path
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, as_completed

import tifffile                                      # noqa: F401  (side-effects)
from tifffile import imread, imwrite
import numpy as np
from scipy import ndimage as ndi
from scipy.ndimage import grey_opening
from skimage.filters import threshold_otsu
from skimage.segmentation import find_boundaries, watershed
import imageio.v3 as iio

# StarDist and csbdeep (which import TensorFlow) are intentionally NOT imported
# here at module level.  They are imported inside _worker_init, after the
# TF_NUM_INTRAOP/INTEROP_THREADS env vars have been set, so TF picks up the
# thread limits before it initialises its internal thread pools.

# ---------------------------------------------------------------------------
# Per-worker StarDist model (loaded once at worker startup, not per image)
# ---------------------------------------------------------------------------

_STARDIST_MODEL: "StarDist2D | None" = None

# Identifies the FOV currently being segmented, for diagnostic logging — set by
# process_image_group at entry so the per-FOV ``seg-input`` line (and the last
# line before a ClipperLib abort) names the culprit image. Per-worker process.
_SEG_FOV_LABEL: str = ""


# ---------------------------------------------------------------------------
# Robust file I/O helpers
#
# Every image that the pipeline writes is later read back by the same worker
# (zip-up, atomic-rename of the staging dir, or downstream tools). Two
# observed failure modes when many tifs are written in quick succession on
# slower / network-backed filesystems:
#
#   1. ``tifffile.imwrite(path)`` returns before the OS has flushed the
#      page cache, so a follow-up ``zipfile.write(path)`` can read a stale
#      / zero-length file or a half-written file and produce a corrupt
#      archive — sometimes manifesting later as ``OSError: Bad file
#      descriptor`` deep in libtiff/libpng C code.
#
#   2. A crash mid-write leaves a partial file on disk; the next run picks
#      it up and fails with cryptic decode errors.
#
# ``_safe_imwrite`` writes to ``__aw_tmp_<stem>.pid<N><ext>``, fsyncs the
# bytes, and then renames atomically. Any caller that subsequently sees
# ``path`` reads either a complete file or no file at all. Likewise
# ``_safe_atomic_write`` wraps the per-well CSV write with the same
# guarantee.
# ---------------------------------------------------------------------------


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


def _safe_imwrite(out_path: "Path | str", data: "np.ndarray") -> None:
    """Atomic image write: tmp file → fsync → rename.

    Routes ``.tif/.tiff`` through tifffile and ``.png`` through imageio v3.
    Other extensions fall back to tifffile. Cleans up the temp file on
    failure so the well's output directory never carries partial bytes
    that a later zip-up step would pack into a corrupt archive.

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
    try:
        if suffix == ".png":
            iio.imwrite(str(tmp), data)
        else:
            imwrite(str(tmp), data)
        _fsync_path(tmp)
        os.replace(str(tmp), str(out_path))
    except BaseException:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
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
        import io as _io
        self._fh = open(self._tmp, "w", newline="")
        return self._fh

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self._fh is not None:
                self._fh.flush()
                try:
                    os.fsync(self._fh.fileno())
                except OSError:
                    pass
                self._fh.close()
        finally:
            self._fh = None
        if exc_type is not None:
            try:
                if self._tmp.exists():
                    self._tmp.unlink()
            except OSError:
                pass
            return False
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


def _report_worker_failure(well_label: str, output_dir: "Path | None", exc: BaseException) -> None:
    """Log a worker failure with classification + a pointer to the trace file.

    Generic ``OSError: [Errno 9] Bad file descriptor`` and ``BrokenProcessPool``
    re-raised at the parent's ``future.result()`` call site otherwise look
    identical, even though they mean very different things (an internal error
    vs. the worker process dying outside Python). Spell out the difference and
    point the user at the per-well trace file written by the worker.
    """
    err_dir = (output_dir / "errors") if output_dir is not None else None
    err_hint = f"  (see worker traces in {err_dir})" if err_dir is not None else ""
    cls = type(exc).__name__
    if cls == "BrokenProcessPool":
        log.error(
            "FAILED well %s: worker process died unexpectedly (%s: %s).%s "
            "This typically means the worker was killed by the OS — most "
            "commonly out-of-memory, signal, or a native crash inside a "
            "C extension (StarDist / TF / tifffile). Reduce --workers or "
            "the per-well memory footprint and try again.",
            well_label, cls, exc, err_hint,
        )
        return
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 9:
        log.error(
            "FAILED well %s: %s — Bad file descriptor (errno 9).%s "
            "If the trace file is empty the worker's stderr was likely "
            "broken before the error site; the per-well trace file is the "
            "authoritative source.",
            well_label, cls, err_hint, exc_info=True,
        )
        return
    log.error("FAILED well %s: %s.%s", well_label, exc, err_hint, exc_info=True)


def _dump_well_failure_trace(output_dir: Path, well_label: str, exc: BaseException) -> "Path | None":
    """Persist a worker-side exception trace for *well_label*.

    ProcessPoolExecutor pickles the exception back to the parent, but if the
    worker's stderr is muted (e.g. broken pipe to the GUI log queue) the
    underlying traceback never reaches the user — they only see a generic
    ``OSError: [Errno 9] Bad file descriptor`` re-raised at the parent's
    ``future.result()`` call site. Writing the full ``traceback.format_exc()``
    to ``<output_dir>/errors/<well>_pid<N>.txt`` guarantees a readable trace
    even when the live log path is broken.

    Returns the trace-file path on success, or None if the trace could not be
    written (e.g. read-only output dir).
    """
    import traceback as _tb
    try:
        err_dir = output_dir / "errors"
        err_dir.mkdir(parents=True, exist_ok=True)
        err_path = err_dir / f"{well_label}_pid{os.getpid()}.txt"
        with err_path.open("w") as fh:
            fh.write(f"Well: {well_label}\n")
            fh.write(f"PID:  {os.getpid()}\n")
            fh.write(f"Exception type: {type(exc).__name__}\n")
            fh.write("\n")
            fh.write(_tb.format_exc())
        return err_path
    except Exception:                # noqa: BLE001
        return None


def _ensure_stardist_runtime_deps() -> None:
    """
    Fail fast with a clear message when StarDist runtime deps are missing.

    Without this preflight check, missing packages can cause worker startup
    failures that surface only as a generic BrokenProcessPool-style error.
    """
    missing: list[str] = []
    if importlib.util.find_spec("csbdeep") is None:
        missing.append("csbdeep")
    if importlib.util.find_spec("stardist") is None:
        missing.append("stardist")

    if missing:
        deps = ", ".join(missing)
        raise RuntimeError(
            "Missing required StarDist dependency package(s): "
            f"{deps}. Install them in this environment before running Analyze."
        )


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _ensure_pipeline_io_ready() -> None:
    """Re-bind ``sys.stdout`` / ``sys.stderr`` when the frozen-launcher's
    windowed PyInstaller bootloader leaves one or both set to ``None``.

    The bundled launcher is invoked from the GUI as
    ``subprocess.Popen([sys.executable, "--run-pipeline"],
    stdout=PIPE, stderr=STDOUT, ...)`` (see
    ``services/pipeline_service.spawn_pipeline``). The parent attaches a
    pipe to the child's fd 1, and the child's fd 2 is duped to fd 1 by
    ``stderr=STDOUT``. In source mode both file descriptors carry a
    valid Python text wrapper, so ``logging.basicConfig`` below attaches
    its ``StreamHandler`` to a writable ``sys.stderr`` and every
    ``log.info(...)`` reaches the GUI's progress queue.

    In a frozen windowed (``console=False``) build the PyInstaller
    bootloader still detaches Python from the absent terminal — it
    leaves ``sys.stdout = None`` / ``sys.stderr = None`` even though
    the underlying file descriptors are perfectly fine. The
    ``StreamHandler`` then either binds to ``None`` (every emit raises
    ``AttributeError`` and the message is silently dropped) or stays
    bound to a broken stream. Symptom: the GUI progress bar freezes
    immediately after the pipeline launches and only "thaws" once the
    job finishes by other means.

    This shim restores a working text wrapper around fd 1 / fd 2 when
    needed, so the module-level ``logging.basicConfig`` below picks up
    a valid sink and every subsequent ``log.<level>(...)`` reaches the
    parent's pipe.
    """
    import sys
    import io

    def _fd_ok(fd: int) -> bool:
        try:
            os.fstat(fd)
            return True
        except OSError:
            return False

    def _wrap_fd(fd: int):
        # ``closefd=False`` is critical — the StreamHandler outlives the
        # main script, but we must not close the parent's pipe end when
        # GC eventually drops this wrapper.
        try:
            raw = os.fdopen(fd, "wb", buffering=0, closefd=False)
            return io.TextIOWrapper(
                raw, encoding="utf-8", errors="replace",
                line_buffering=True, write_through=True,
            )
        except OSError:
            return None

    def _probe(stream) -> bool:
        if stream is None:
            return False
        try:
            stream.write("")
            stream.flush()
            return True
        except Exception:  # noqa: BLE001 — broken streams raise anything
            return False

    if not _probe(sys.stdout):
        wrapper = _wrap_fd(1) if _fd_ok(1) else None
        if wrapper is not None:
            sys.stdout = wrapper
    if not _probe(sys.stderr):
        # Prefer fd 2 if it's live; otherwise alias to sys.stdout so the
        # logging StreamHandler still has somewhere to write. The parent
        # spawns us with ``stderr=STDOUT`` so the two streams merge
        # downstream anyway.
        wrapper = _wrap_fd(2) if _fd_ok(2) else None
        if wrapper is None:
            wrapper = sys.stdout
        sys.stderr = wrapper


_ensure_pipeline_io_ready()


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 96-well plate helpers
# ---------------------------------------------------------------------------

# Valid well names: A1/A01 – H12  (rows A-H, columns 1-12 or 01-12)
_WELL_RE = re.compile(r"^([A-Ha-h])(\d{1,2})$")


def is_valid_well_name(stem: str) -> bool:
    """Return True if *stem* (filename without extension) is a valid 96-well position."""
    m = _WELL_RE.match(stem)
    if not m:
        return False
    col = int(m.group(2))
    return 1 <= col <= 12


def find_well_zips(input_dir: Path) -> list[tuple[str, Path]]:
    """
    Scan *input_dir* (non-recursively) for .zip files whose stem matches a
    96-well plate position.

    Returns a list of (well_label, zip_path) sorted by well_label.
    Zips with non-plate names are logged as warnings and skipped.
    """
    found: list[tuple[str, Path]] = []
    skipped: list[Path] = []

    for p in sorted(input_dir.glob("*.zip")):
        if p.name.startswith("."):
            log.debug("Skipping hidden file: %s", p.name)
            continue
        if is_valid_well_name(p.stem):
            # Normalise to upper-case, zero-padded column: a1 → A01
            m = _WELL_RE.match(p.stem)
            well = f"{m.group(1).upper()}{int(m.group(2)):02d}"
            found.append((well, p))
        else:
            skipped.append(p)

    if skipped:
        log.warning(
            "%d zip file(s) skipped (name not a valid 96-well position):",
            len(skipped),
        )
        for p in skipped:
            log.warning("  %s", p.name)

    log.info("Found %d well zip file(s) in %s", len(found), input_dir)
    return found


def find_well_folders(input_dir: Path) -> list[tuple[str, Path]]:
    """
    Scan *input_dir* (non-recursively) for subdirectories whose name matches
    a 96-well plate position (e.g. A01/, B12/).

    Returns a list of (well_label, folder_path) sorted by well_label.
    """
    found: list[tuple[str, Path]] = []
    try:
        entries = sorted(input_dir.iterdir())
    except OSError:
        return found
    for p in entries:
        if not p.is_dir() or p.name.startswith(".") or p.name.startswith("_"):
            continue
        if is_valid_well_name(p.name):
            m = _WELL_RE.match(p.name)
            well = f"{m.group(1).upper()}{int(m.group(2)):02d}"
            found.append((well, p))
    log.info("Found %d well folder(s) in %s", len(found), input_dir)
    return sorted(found, key=lambda x: x[0])


def _canonical_well_label(token: str) -> str | None:
    """Return canonical 96-well label (e.g. B03) for *token*, or None if invalid.

    Thin wrapper over :func:`well_token.canonical_well_label` so the
    pipeline and the GUI tools (WellPlateZipper, analyze_tab,
    services/input_resolution_service) all share one parser.
    """
    from well_token import canonical_well_label as _cwl
    return _cwl(token)


def organize_loose_tifs_into_well_folders(
    input_dir: Path,
    *,
    schema: list[str],
    sep: str,
) -> None:
    """
    Move loose top-level TIF/TIFF images in *input_dir* into per-well folders.

    If destination files already exist, files are skipped (no overwrite).
    """
    loose_images = [
        p for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".tif", ".tiff"}
    ]
    if not loose_images:
        return

    files_by_well: dict[str, list[Path]] = defaultdict(list)
    unparsable = 0
    for img in loose_images:
        try:
            parsed = parse_filename(img, schema=schema, sep=sep)
        except Exception:
            unparsable += 1
            continue
        well = _canonical_well_label(parsed.get("well", ""))
        if well is None:
            unparsable += 1
            continue
        files_by_well[well].append(img)

    if not files_by_well:
        if unparsable:
            log.info(
                "Found %d loose TIF/TIFF file(s) in %s, but none had a valid 96-well token.",
                len(loose_images),
                input_dir,
            )
        return

    moved = 0
    skipped_existing = 0
    for well, files in sorted(files_by_well.items()):
        well_dir = input_dir / well
        well_dir.mkdir(parents=True, exist_ok=True)
        for src in files:
            dst = well_dir / src.name
            if dst.exists():
                skipped_existing += 1
                log.warning(
                    "Loose image not moved (destination exists, no overwrite): %s",
                    dst,
                )
                continue
            shutil.move(str(src), str(dst))
            moved += 1

    log.info(
        "Organized loose images in %s: moved %d file(s) into %d well folder(s), "
        "skipped %d existing destination(s), ignored %d unparsable file(s).",
        input_dir,
        moved,
        len(files_by_well),
        skipped_existing,
        unparsable,
    )

# ---------------------------------------------------------------------------
# Zip extraction / compression helpers
# ---------------------------------------------------------------------------

def extract_zip(
    zip_path: Path,
    dest_dir: Path,
    members_to_extract: "set[str] | None" = None,
) -> int:
    """
    Extract image members of *zip_path* flat into *dest_dir*.

    When *members_to_extract* is provided, only those base filenames are
    extracted. This allows zip-mode processing to pull only the channel files
    required by discovered image groups instead of unpacking every image.

    CSV files and other non-image files inside the zip are skipped so
    that outputs from a previous run packaged back into the input zip
    cannot interfere with the current run or end up re-zipped in output.

    Returns the number of image files extracted.
    """
    image_exts = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}
    wanted = set(members_to_extract or [])
    dest_dir.mkdir(parents=True, exist_ok=True)
    skipped: list[str] = []
    extracted = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            # Strip any directory structure inside the zip; extract flat.
            member_name = Path(member.filename).name
            if not member_name:          # skip directory entries
                continue
            if member_name.startswith("."):
                skipped.append(member_name)
                continue
            if Path(member_name).suffix.lower() not in image_exts:
                skipped.append(member_name)
                continue
            if wanted and member_name not in wanted:
                continue
            target = dest_dir / member_name
            with zf.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted += 1
    if skipped:
        log.debug(
            "Skipped %d non-image file(s) in %s: %s",
            len(skipped), zip_path.name, ", ".join(skipped),
        )
    if wanted:
        log.info("Extracted %d selected image(s) from %s -> %s", extracted, zip_path.name, dest_dir)
    else:
        log.info("Extracted %s  ->  %s", zip_path.name, dest_dir)
    return extracted


def compress_images_to_zip(image_dir: Path, out_zip: Path) -> int:
    """
    Compress all image files (TIF/TIFF/PNG) found in *image_dir* into
    *out_zip*.  Returns the number of files added.
    CSV files and any other non-image files are explicitly excluded.

    Stray ``.tmp`` files left by a crashed atomic-write are skipped, and any
    zero-byte file is logged + dropped so a partial / never-flushed write
    can't be packaged into a corrupt archive.
    """
    include_exts = {".tif", ".tiff", ".png"}
    exclude_exts = {".csv"}
    candidate_files = sorted(
        p for p in image_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() in include_exts
        and p.suffix.lower() not in exclude_exts
        and not p.name.endswith(".tmp")
        # Stale _safe_imwrite / _AtomicTextHandle scratch files. The
        # `__aw_tmp_` prefix is unique enough that user filenames
        # don't false-positive (the old `.pid` substring would drop
        # e.g. `exp.pidgin.A01.tif`).
        and not p.name.startswith("__aw_tmp_")
    )
    image_files = _verify_files_complete(candidate_files)

    if not image_files:
        log.warning("No image files found in %s to compress.", image_dir)
        return 0

    out_zip.parent.mkdir(parents=True, exist_ok=True)
    # Build the zip via the same atomic-rename pattern the imwrite helper
    # uses: stage at <out_zip>.tmp, fsync, replace. Any caller that sees
    # *out_zip* gets either the complete archive or the previous one.
    tmp_zip = out_zip.with_name(f"__aw_tmp_{out_zip.name}.pid{os.getpid()}")
    try:
        with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for img_path in image_files:
                zf.write(img_path, arcname=img_path.name)
        _fsync_path(tmp_zip)
        os.replace(str(tmp_zip), str(out_zip))
    except BaseException:
        try:
            if tmp_zip.exists():
                tmp_zip.unlink()
        except OSError:
            pass
        raise

    log.info(
        "Compressed %d image(s)  ->  %s", len(image_files), out_zip.name
    )
    return len(image_files)


def compress_folder_images_to_zip_and_remove(folder: Path, out_zip: Path) -> int:
    """
    Compress image files in *folder* to *out_zip* and remove *folder*.

    Returns the number of image files added to the archive. If no image files
    are found, the source folder is left untouched.
    """
    n = compress_images_to_zip(folder, out_zip)
    if n <= 0:
        return 0
    remove_directory(folder)
    return n


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

# ---------------------------------------------------------------------------
# Filename schema helpers
# ---------------------------------------------------------------------------

#: Ordered list of recognised field names (the vocabulary for schema slots).
SCHEMA_FIELDS = ("experiment", "channel", "well", "fov", "timepoint", "ignore")

#: Default schema — matches the original hardcoded convention.
DEFAULT_SCHEMA = "experiment:channel:well:fov:timepoint"
DEFAULT_SEP    = "_"
DEFAULT_SEGMENTATION_METHOD = "stardist_nuclei"


def parse_schema(schema_str: str) -> list[str]:
    """
    Parse a colon-delimited schema string into a list of field names.

    Each token must be one of SCHEMA_FIELDS.  Unknown tokens are silently
    replaced with "ignore" so that old schema strings remain forward-compatible
    when new field names are added later.

    Example:
        "experiment:channel:well:fov:timepoint"
        -> ["experiment", "channel", "well", "fov", "timepoint"]
    """
    known = set(SCHEMA_FIELDS)
    return [
        tok if tok in known else "ignore"
        for tok in schema_str.strip().split(":")
        if tok  # skip empty tokens from trailing colons etc.
    ]


def validate_schema(schema: list[str]) -> list[str]:
    """
    Return a list of human-readable error messages for *schema*.
    An empty list means the schema is valid.

    Rules:
      • "channel" must appear exactly once.
      • "well"    must appear exactly once.
    """
    errors: list[str] = []
    if schema.count("channel") != 1:
        errors.append(
            f'"channel" must appear exactly once in the schema '
            f'(found {schema.count("channel")}).'
        )
    if schema.count("well") != 1:
        errors.append(
            f'"well" must appear exactly once in the schema '
            f'(found {schema.count("well")}).'
        )
    return errors


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------

def _parse_timepoint_hours(tp: str) -> float:
    """
    Convert a timepoint string to fractional hours.
    Returns float("nan") when the string cannot be parsed at all.

    Formats tried in order:
      1. DDdHHhMMm  e.g. "02d04h30m" -> 52.5  (full or partial components)
      2. Standalone unit suffix  e.g. "48h" -> 48.0, "2d" -> 48.0, "30m" -> 0.5
      3. Pure number  e.g. "48" or "1.5" -> treated as hours
      4. Prefixed ordinal  e.g. "T01", "t3", "day2", "D02" -> ordinal value
         (strip leading non-digit chars, use the numeric part as a plain
          index so relative ordering is preserved)
    """
    s = tp.strip()
    if not s:
        return float("nan")

    # 1. Full DDdHHhMMm (all components optional, but at least one required)
    m = re.fullmatch(
        r"(?:(\d{1,4})d)?(?:(\d{1,2})h)?(?:(\d{1,2})m)?",
        s, re.IGNORECASE,
    )
    if m and any(m.groups()):
        days  = int(m.group(1) or 0)
        hours = int(m.group(2) or 0)
        mins  = int(m.group(3) or 0)
        return days * 24.0 + hours + mins / 60.0

    # 2. Standalone unit: "48h", "2d", "30m", "90min"
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(h(?:ours?)?|d(?:ays?)?|m(?:in(?:utes?)?)?)",
                     s, re.IGNORECASE)
    if m:
        val  = float(m.group(1))
        unit = m.group(2)[0].lower()
        if unit == "h":
            return val
        if unit == "d":
            return val * 24.0
        if unit == "m":
            return val / 60.0

    # 3. Pure number (treat as hours)
    try:
        return float(s)
    except ValueError:
        pass

    # 4. Prefixed ordinal: strip leading non-digit characters, keep the number.
    #    e.g. "T01" -> 1.0, "day02" -> 2.0, "tp_3" -> 3.0
    m = re.search(r"(\d+(?:\.\d+)?)$", s)
    if m:
        return float(m.group(1))

    return float("nan")


def parse_filename(
    path: "Path | str",
    schema: "list[str] | None" = None,
    sep: str = DEFAULT_SEP,
) -> dict:
    """
    Decompose a filename into metadata fields using a user-supplied schema.

    Parameters
    ----------
    path   : Path or str — file path or bare stem.
    schema : ordered list of field names from SCHEMA_FIELDS.
             Defaults to the legacy 5-slot convention
             ["experiment", "channel", "well", "fov", "timepoint"].
    sep    : separator character used to split the filename stem.
             Defaults to "_".

    The stem is split on *sep* and each token is assigned to the
    corresponding schema field.  Slots labelled "ignore" are consumed but
    not stored.  If the stem has fewer tokens than schema slots the missing
    fields default to "" (or NaN for timepoint_hours).

    Returns a dict with keys:
        filename, experiment, channel, well, fov,
        timepoint, timepoint_hours
    (always present; empty / NaN when absent from the schema or filename).
    """
    if schema is None:
        schema = parse_schema(DEFAULT_SCHEMA)

    p    = Path(path) if not isinstance(path, Path) else path
    stem = p.stem if p.suffix else str(path)
    parts = stem.split(sep)

    # Build result with all known non-ignore fields defaulted to "".
    result: dict = {
        "filename":        p.name if p.suffix else str(path),
        "experiment":      "",
        "channel":         "",
        "well":            "",
        "fov":             "",
        "timepoint":       "",
        "timepoint_hours": float("nan"),
    }

    for i, field in enumerate(schema):
        if field == "ignore":
            continue
        token = parts[i] if i < len(parts) else ""
        if field == "timepoint":
            result["timepoint"]       = token
            # Empty timepoint slot — common when the schema includes
            # "timepoint" but the dataset is single-timepoint and the
            # filenames don't carry a timepoint token. NaN would
            # propagate into the CSV's timepoint_hours column and
            # break every downstream comparison. Treat as t=0.
            result["timepoint_hours"] = (
                0.0 if not token.strip()
                else _parse_timepoint_hours(token)
            )
        else:
            result[field] = token

    return result

# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def load_2d_gray(path: Path) -> np.ndarray:
    """Load a TIFF (or other format) as a 2-D float32 array."""
    img = imread(str(path))
    while img.ndim > 2:
        img = img[0]
    return img.astype(np.float32)


def apply_tophat(image: np.ndarray, radius: int) -> np.ndarray:
    """Return white top-hat corrected image (background subtraction).

    Uses scipy.ndimage.grey_closing with a square (2*radius+1) structuring
    element.  A square footprint enables separable 1D decomposition inside
    scipy, giving O(radius) complexity per pixel instead of the O(radius²)
    of skimage's disk-based morphology.  For radius=50 on a 900×900 image
    this is ~100× faster than the skimage disk path with negligible
    difference in background-subtraction quality for typical fluorescence
    images where the background varies slowly.
    """
    img = image.astype(np.float32)
    size = 2 * radius + 1                      # side length of the square
    background = grey_opening(img, size=(size, size))   # opening = erosion→dilation
    return img - background                    # always ≥ 0 (opening is anti-extensive)


SMFISH_LOG_KERNEL = np.array([
    [-4, -1, 0, -1, -4],
    [-1, 2, 3, 2, -1],
    [0, 3, 4, 3, 0],
    [-1, 2, 3, 2, -1],
    [-4, -1, 0, -1, -4],
], dtype=np.float32)


def apply_smfish_log(image: np.ndarray) -> np.ndarray:
    from scipy.ndimage import convolve
    return convolve(image.astype(np.float32), SMFISH_LOG_KERNEL)


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


def _segment_stardist_nuclei(nir_corr: np.ndarray) -> np.ndarray:
    from csbdeep.utils import normalize
    if _STARDIST_MODEL is None:
        raise RuntimeError(
            "StarDist model is not initialised. "
            "Ensure _worker_init ran before process_image_group."
        )
    # StarDist's polygon non-maximum-suppression uses ClipperLib, which casts
    # polygon coordinates to integers and aborts the whole *process* with an
    # uncatchable C++ exception —
    #   terminate called after throwing an instance of
    #   'ClipperLib::clipperException'  ("Coordinate outside allowed range")
    # — if the network is fed non-finite input. A single NaN/Inf pixel is enough:
    # ``normalize`` uses ``np.percentile`` (not the nan-aware variant), so one
    # NaN turns the percentiles NaN and the *entire* normalized image non-finite,
    # the regressed polygon distances become NaN/Inf, and the int cast overflows
    # ClipperLib's range. TIFFs that encode masked/saturated pixels as NaN/Inf,
    # or aggressive flat-field / background correction, can introduce these — so
    # scrub non-finite values before normalize and again after (belt and braces).
    arr = np.asarray(nir_corr, dtype=np.float32)
    if not np.all(np.isfinite(arr)):
        arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    nir_norm = normalize(arr, 1, 99.8)
    if not np.all(np.isfinite(nir_norm)):
        nir_norm = np.nan_to_num(nir_norm, nan=0.0, posinf=0.0, neginf=0.0)

    # ── ClipperLib diagnostics ───────────────────────────────────────────────
    # The abort ("coordinate outside allowed range") happens *inside*
    # predict_instances, in StarDist's polygon NMS, when the regressed vertices
    # land at out-of-range integer coordinates — the classic cause being a
    # pathological / near-constant input (e.g. an empty-well over-subtracted
    # nuclear field). It's an uncatchable C++ abort, so we can't try/except it;
    # instead log the input characteristics for *every* FOV. The last
    # ``seg-input`` line before the process dies names the culprit and its stats.
    fov = _SEG_FOV_LABEL or "?"
    lo, hi = float(nir_norm.min()), float(nir_norm.max())
    log.info("  seg-input %s: shape=%s min=%.4g max=%.4g mean=%.4g dyn_range=%.4g "
             "finite=%.0f%% nonzero=%.0f%%",
             fov, tuple(nir_norm.shape), lo, hi, float(nir_norm.mean()), hi - lo,
             float(np.isfinite(nir_norm).mean()) * 100.0,
             float(np.count_nonzero(nir_norm)) / nir_norm.size * 100.0)

    # A near-constant field has no nuclei and is the classic ClipperLib trigger —
    # skip prediction (0 nuclei) rather than risk the uncatchable abort.
    if hi - lo < 1e-6:
        log.warning("  seg-input %s: near-constant field (dyn_range≈0) — skipping "
                    "StarDist (0 nuclei) to avoid a ClipperLib abort", fov)
        return np.zeros(nir_norm.shape, dtype=np.int32)

    # Opt-in deep trace (ALLWELL_SEG_DEBUG=1): run the raw network once to log the
    # predicted probability / radial-distance magnitudes. Huge or non-finite
    # distances are the *direct* source of the out-of-range polygon coordinates;
    # this pins the failure to prediction vs input. Fully guarded — never alters
    # the production path, and falls back silently if the split API differs.
    if os.environ.get("ALLWELL_SEG_DEBUG"):
        try:
            pred = _STARDIST_MODEL.predict(nir_norm)
            prob, dist = pred[0], pred[1]
            log.info("  seg-debug %s: prob[min=%.3g max=%.3g] dist[min=%.3g "
                     "max=%.3g] dist_nonfinite=%d prob_shape=%s dist_shape=%s",
                     fov, float(np.nanmin(prob)), float(np.nanmax(prob)),
                     float(np.nanmin(dist)), float(np.nanmax(dist)),
                     int((~np.isfinite(dist)).sum()),
                     tuple(np.shape(prob)), tuple(np.shape(dist)))
        except Exception as exc:                                # noqa: BLE001
            log.warning("  seg-debug %s: predict() trace failed: %s", fov, exc)

    labels, _ = _STARDIST_MODEL.predict_instances(nir_norm)
    return labels.astype(np.int32)


def _segment_stardist_seeded_watershed_cell(
    nir_corr: np.ndarray,
    cytoplasm_raw: np.ndarray,
    tophat_radius_fluor: int,
    min_nucleus_area_px: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    stardist_labels = _segment_stardist_nuclei(nir_corr)
    nucleus_ids = np.unique(stardist_labels)
    nucleus_ids = nucleus_ids[nucleus_ids != 0]
    filtered_ids: list[int] = []
    for nid in nucleus_ids:
        area = int(np.count_nonzero(stardist_labels == nid))
        if area >= min_nucleus_area_px:
            filtered_ids.append(int(nid))

    nuclear_points = np.zeros_like(stardist_labels, dtype=np.int32)
    marker_id = 1
    for nid in filtered_ids:
        cy, cx = ndi.center_of_mass(stardist_labels == nid)
        if np.isnan(cy) or np.isnan(cx):
            continue
        y = int(np.clip(round(float(cy)), 0, stardist_labels.shape[0] - 1))
        x = int(np.clip(round(float(cx)), 0, stardist_labels.shape[1] - 1))
        nuclear_points[y, x] = marker_id
        marker_id += 1

    cytoplasm_tophat = apply_tophat(cytoplasm_raw, tophat_radius_fluor)
    try:
        thresh = threshold_otsu(cytoplasm_tophat)
    except ValueError:
        thresh = float(np.mean(cytoplasm_tophat))
    cytoplasm_binary_mask = cytoplasm_tophat > thresh
    distance = ndi.distance_transform_edt(cytoplasm_binary_mask)
    if marker_id <= 1:
        labels = np.zeros_like(stardist_labels, dtype=np.int32)
    else:
        labels = watershed(
            -distance,
            markers=nuclear_points,
            mask=cytoplasm_binary_mask,
        ).astype(np.int32)
    return labels, nuclear_points, cytoplasm_tophat, cytoplasm_binary_mask


# ---------------------------------------------------------------------------
# Auto-threshold estimation (self-contained — runs without well_viewer)
# ---------------------------------------------------------------------------
#
# Walks ``<output_dir>/*_out.zip``, picks the first/middle/last timepoint of
# every well, samples per-cell means (from the ``*_labels`` mask) plus a
# matched random background pixel (from the ``*_tophat`` fluor image) per
# channel, and runs Otsu on the pooled distribution. The thresholds are
# merged into ``pipeline_info.json``'s ``cell_gating.thresh_frac_on`` block
# (preserving any user-set values).
#
# The per-image sampler and timepoint helpers live in
# ``auto_threshold_core`` so the GUI's Cell Gating tab and this pipeline
# run produce identical defaults on the same dataset. ``auto_threshold_core``
# depends only on ``numpy`` + ``random`` so the pipeline-only deployment
# story is preserved.

from auto_threshold_core import (
    DEFAULT_CELLS_PER_IMAGE_CAP as _AUTO_THRESHOLD_CELLS_PER_IMAGE_CAP,
    pick_endpoint_timepoints as _pick_endpoint_timepoints,
    sample_cell_and_bg as _sample_cell_and_bg,
)


def _estimate_thresholds_standalone(
    output_dir: Path,
    *,
    fluor_channels: "list[str]",
    filename_schema: str,
    filename_sep: str,
    log: logging.Logger,
    cells_per_image_cap: int = _AUTO_THRESHOLD_CELLS_PER_IMAGE_CAP,
    rng_seed: "int | None" = None,
) -> "dict[str, float]":
    """Otsu-based per-channel threshold estimator that depends only on
    modules already imported by this file. Returns ``{channel: threshold}``.
    """
    import io as _io
    import random as _random

    channels = [str(c).strip().lower() for c in fluor_channels if str(c).strip()]
    if not channels:
        return {}
    output_dir = Path(output_dir)
    if not output_dir.exists():
        log.warning("Auto-threshold: output dir not found (%r)", str(output_dir))
        return {}

    schema_fields = [f.strip().lower() for f in str(filename_schema or "").split(":")
                     if f.strip()]
    sep = filename_sep or "_"

    def _parse_fields(stem: str) -> "dict[str, str]":
        parts = stem.split(sep)
        return {f: (parts[i] if i < len(parts) else "")
                for i, f in enumerate(schema_fields)}

    rng = _random.Random(rng_seed)
    per_channel: "dict[str, list[float]]" = {c: [] for c in channels}

    well_zips = sorted(p for p in output_dir.glob("*_out.zip") if not p.name.startswith("."))
    if not well_zips:
        log.info("Auto-threshold: no processed *_out.zip wells in %s", output_dir)
        return {}

    log.info("Auto-threshold: scanning %d well(s) for channels: %s",
             len(well_zips), ", ".join(channels))

    for w_idx, zpath in enumerate(well_zips, start=1):
        try:
            zf = zipfile.ZipFile(zpath, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            log.warning("Auto-threshold: cannot open %s: %s", zpath.name, exc)
            continue
        with zf:
            label_members: "dict[tuple[str, str], str]" = {}     # (fov, tp) -> name
            tophat_members: "dict[tuple[str, str, str], str]" = {}  # (ch, fov, tp) -> name
            for name in zf.namelist():
                if "/" in name or name.startswith("."):
                    continue
                base, _, ext = name.rpartition(".")
                if not base or ext.lower() not in ("tif", "tiff"):
                    continue
                if base.endswith("_labels"):
                    stem = base[: -len("_labels")]
                    fields = _parse_fields(stem)
                    fov = (fields.get("fov") or "").strip()
                    # Accept either canonical "timepoint" or the legacy
                    # "tp" alias. ``args.filename_schema`` is passed in
                    # *raw* (not via parse_schema), so a user who typed
                    # "experiment:channel:well:fov:tp" lands here with
                    # schema_fields == [..., "tp"]; dropping the
                    # fallback (PR #247 C2 fix) silently produced zero
                    # samples for every channel on those datasets.
                    tp = (fields.get("timepoint") or fields.get("tp") or "").strip()
                    # Single-timepoint datasets: when the filename has
                    # fewer tokens than the schema has fields, the
                    # timepoint slot parses as "" and `if fov and tp`
                    # below would drop every label silently. Use a
                    # synthetic "0" sentinel so the auto-threshold
                    # still aggregates per-fov samples.
                    if fov and not tp:
                        tp = "0"
                    if fov and tp:
                        label_members[(fov, tp)] = name
                elif base.endswith("_tophat"):
                    stem = base[: -len("_tophat")]
                    fields = _parse_fields(stem)
                    fov = (fields.get("fov") or "").strip()
                    tp = (fields.get("timepoint") or fields.get("tp") or "").strip()
                    if fov and not tp:
                        tp = "0"
                    ch = (fields.get("channel") or "").strip().lower()
                    if fov and tp and ch in per_channel:
                        tophat_members[(ch, fov, tp)] = name

            if not label_members:
                continue
            tps_seen = {tp for (_fov, tp) in label_members.keys()}
            picked_tps = set(_pick_endpoint_timepoints(tps_seen))
            if not picked_tps:
                continue
            log.info("Auto-threshold: %s → timepoints %s",
                     zpath.name, ", ".join(sorted(picked_tps)))

            for (fov, tp), label_name in label_members.items():
                if tp not in picked_tps:
                    continue
                for ch in channels:
                    fluor_name = tophat_members.get((ch, fov, tp))
                    if fluor_name is None:
                        continue
                    try:
                        labels_arr = imread(_io.BytesIO(zf.read(label_name)))
                        fluor_arr = imread(_io.BytesIO(zf.read(fluor_name)))
                    except Exception as exc:
                        log.debug("Auto-threshold: skipping %s / %s: %s",
                                  label_name, fluor_name, exc)
                        continue
                    if labels_arr.ndim == 3:
                        labels_arr = labels_arr[..., 0]
                    if fluor_arr.ndim == 3:
                        fluor_arr = fluor_arr[..., 0]
                    means, bg_values = _sample_cell_and_bg(
                        labels_arr, fluor_arr,
                        cap=cells_per_image_cap, rng=rng,
                    )
                    if not means:
                        continue
                    per_channel[ch].extend(means)
                    per_channel[ch].extend(bg_values)
        log.info("Auto-threshold: completed well %d/%d (%s)",
                 w_idx, len(well_zips), zpath.name)

    thresholds: "dict[str, float]" = {}
    for ch in channels:
        values = per_channel.get(ch) or []
        if len(values) < 2:
            log.info("Auto-threshold: skipping %s — only %d sample(s)",
                     ch.upper(), len(values))
            continue
        arr = np.asarray(values, dtype=np.float64)
        if arr.max() <= arr.min():
            log.info("Auto-threshold: skipping %s — constant distribution (min == max == %.3g)",
                     ch.upper(), float(arr.min()))
            continue
        try:
            thr = float(threshold_otsu(arr))
        except Exception as exc:
            log.warning("Auto-threshold: Otsu failed for %s: %s", ch.upper(), exc)
            continue
        thresholds[ch] = thr
        log.info("Auto-threshold: %s → %.4g (n=%d)", ch.upper(), thr, int(arr.size))
    return thresholds


def _apply_thresholds_to_pipeline_info(
    output_dir: Path,
    thresholds: "dict[str, float]",
    log: logging.Logger,
) -> "dict[str, float]":
    """Merge ``thresholds`` into ``output_dir/pipeline_info.json`` under
    ``cell_gating.thresh_frac_on``. User-set values are preserved.
    """
    if not thresholds:
        return {}
    info_path = output_dir / "pipeline_info.json"
    if not info_path.exists():
        info_path = output_dir.parent / "pipeline_info.json"
    if not info_path.exists():
        log.info("Auto-threshold: pipeline_info.json not found — defaults not persisted.")
        return {}
    try:
        existing = json.loads(info_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Auto-threshold: cannot read %s: %s", info_path, exc)
        return {}
    if not isinstance(existing, dict):
        return {}

    cell_gating = dict(existing.get("cell_gating") or {})
    tfo = dict(cell_gating.get("thresh_frac_on") or {})
    written: "dict[str, float]" = {}
    for ch, thr in thresholds.items():
        if ch in tfo:  # never overwrite a user-set value
            continue
        tfo[ch] = float(thr)
        written[ch] = float(thr)
    if not written:
        return {}
    cell_gating["thresh_frac_on"] = tfo
    existing["cell_gating"] = cell_gating
    try:
        tmp = info_path.with_suffix(info_path.suffix + ".tmp")
        tmp.write_text(json.dumps(existing, indent=2))
        tmp.replace(info_path)
    except OSError as exc:
        log.warning("Auto-threshold: cannot write %s: %s", info_path, exc)
        return {}
    return written


def build_processed_output_path(
    src_path: Path,
    *,
    output_dir: Path,
    suffix: str = "",
    ext_override: str | None = None,
    role: "object | None" = None,
    channel: str = "",
) -> Path:
    """Build output path as input stem + suffix + extension.

    Two equivalent calling conventions:

    * Legacy: pass ``suffix="_tophat"`` / ``"_labels"`` etc. directly.
    * Preferred: pass ``role=Role.FLUOR_TOPHAT`` (etc.) so the suffix
      comes from the canonical :data:`image_identity.ROLE_CATALOG`
      table — that catalog is the single source of truth shared with
      the reader-side classifier, so suffix drift between the writer
      and reader can't happen.

    The default extension is copied from *src_path* so processed outputs
    preserve source file type (``.tif`` / ``.tiff`` etc.) unless
    explicitly overridden.
    """
    ext = str(ext_override) if ext_override is not None else str(src_path.suffix)
    if not ext:
        ext = ".tif"
    if role is not None:
        # Catalog-driven path: consult image_identity for the suffix.
        # Lazy import so this module's CLI invocation doesn't always
        # pay the package init cost.
        from well_viewer.data.image_identity import role_output_filename
        return output_dir / role_output_filename(
            src_path.stem, role, channel=channel, ext=ext,
        )
    return output_dir / f"{src_path.stem}{suffix}{ext}"

# ---------------------------------------------------------------------------
# Per-image worker (runs in a subprocess)
# ---------------------------------------------------------------------------

def process_image_group(
    nuclear_path: Path,
    fluor_paths: "list[Path]",
    fluor_tokens: "list[str]",
    output_dir: Path,
    nuclear_token: str,
    tophat_nir: bool,
    tophat_radius_nir: int,
    tophat_fluor: "list[bool]",
    tophat_radius_fluor: "list[int]",
    save_tophat_images: bool,
    schema: "list[str] | None" = None,
    sep: str = DEFAULT_SEP,
    smfish_tokens: "list[str] | None" = None,
    segmentation_method: str = DEFAULT_SEGMENTATION_METHOD,
    cytoplasm_token: str = "",
    min_nucleus_area_px: int = 50,
) -> list[dict]:
    """
    Process one nuclear image together with an arbitrary number of fluorescent
    channel images.  Returns a list of per-nucleus measurement dicts.

    *fluor_paths*        : one Path per fluorescent channel (same order as
                           *fluor_tokens*).
    *fluor_tokens*       : channel token strings (e.g. ["GFP", "mCherry"]).
    *tophat_fluor*       : per-channel top-hat enable flags.
    *tophat_radius_fluor*: per-channel top-hat radii.

    Output CSV columns for each fluor channel are prefixed with the lower-cased
    token, e.g. "gfp_total_intensity", "mcherry_mean_intensity".
    """
    log.info("Processing %s  (%d fluor channel(s): %s)",
             nuclear_path.name, len(fluor_paths), ", ".join(fluor_tokens))
    global _SEG_FOV_LABEL
    _SEG_FOV_LABEL = nuclear_path.name

    _t = time.perf_counter()
    nir_raw = load_2d_gray(nuclear_path)
    log.info("  load nuclear: %.1f s  (shape %s)", time.perf_counter() - _t, nir_raw.shape)

    # Load every fluorescent channel and verify shape consistency.
    fluor_raws: list[np.ndarray] = []
    for fp in fluor_paths:
        _t = time.perf_counter()
        img = load_2d_gray(fp)
        if img.shape != nir_raw.shape:
            raise ValueError(
                f"Shape mismatch: nuclear {nir_raw.shape} vs "
                f"{fp.name} {img.shape}"
            )
        fluor_raws.append(img)
        log.info("  load fluor:  %.1f s  %s", time.perf_counter() - _t, fp.name)

    # Top-hat on nuclear channel.
    _t = time.perf_counter()
    nir_corr = apply_tophat(nir_raw, tophat_radius_nir) if tophat_nir else nir_raw
    log.info("  tophat_nir:  %.1f s  (radius %d, enabled=%s)",
             time.perf_counter() - _t, tophat_radius_nir, tophat_nir)

    # Top-hat on each fluorescent channel.
    fluor_tophat: list[np.ndarray] = []
    for i, (fraw, do_th, radius) in enumerate(
            zip(fluor_raws, tophat_fluor, tophat_radius_fluor)):
        _t = time.perf_counter()
        fc = apply_tophat(fraw, radius) if do_th else fraw
        fluor_tophat.append(fc)
        log.info("  tophat_%s:  %.1f s  (radius %d, enabled=%s)",
                 fluor_tokens[i].lower(), time.perf_counter() - _t, radius, do_th)

    smfish_set = set(smfish_tokens or [])
    fluor_corr = list(fluor_tophat)
    for i, tok in enumerate(fluor_tokens):
        if tok in smfish_set:
            _t = time.perf_counter()
            fluor_corr[i] = apply_smfish_log(fluor_tophat[i])
            log.info("  smfish_log_%s: %.1f s", tok.lower(), time.perf_counter() - _t)

    _t = time.perf_counter()
    labels: np.ndarray
    nuclear_points: np.ndarray | None = None
    cytoplasm_tophat: np.ndarray | None = None
    cytoplasm_binary_mask: np.ndarray | None = None
    if segmentation_method == "stardist_seeded_watershed_cell":
        if not cytoplasm_token:
            raise ValueError(
                "segmentation_method=stardist_seeded_watershed_cell requires --cytoplasm_token."
            )
        if cytoplasm_token not in fluor_tokens:
            raise ValueError(
                f"Cytoplasm token '{cytoplasm_token}' is missing from fluor token list: {fluor_tokens}"
            )
        cyto_idx = fluor_tokens.index(cytoplasm_token)
        labels, nuclear_points, cytoplasm_tophat, cytoplasm_binary_mask = (
            _segment_stardist_seeded_watershed_cell(
                nir_corr=nir_corr,
                cytoplasm_raw=fluor_raws[cyto_idx],
                tophat_radius_fluor=tophat_radius_fluor[cyto_idx],
                min_nucleus_area_px=min_nucleus_area_px,
            )
        )
        log.info(
            "  watershed:   %.1f s  (%d cells detected)",
            time.perf_counter() - _t,
            len(np.unique(labels)) - 1,
        )
    else:
        labels = _segment_stardist_nuclei(nir_corr)
        log.info("  stardist:    %.1f s  (%d nuclei detected)",
                 time.perf_counter() - _t, len(np.unique(labels)) - 1)

    # Save QC images.
    # Keep schema tokens in output stems so output names stay harmonized with
    # input naming conventions and schema-driven resolvers.
    _t = time.perf_counter()
    if save_tophat_images:
        th_nir_path = build_processed_output_path(
            nuclear_path,
            output_dir=output_dir,
            suffix="_tophat",
        )
        _safe_imwrite(th_nir_path, np.clip(nir_corr, 0, None).astype(np.float32))
        for fc, tok, fp in zip(fluor_tophat, fluor_tokens, fluor_paths):
            th_path = build_processed_output_path(
                fp,
                output_dir=output_dir,
                suffix="_tophat",
            )
            _safe_imwrite(th_path, np.clip(fc, 0, None).astype(np.float32))
        for i, (tok, fp) in enumerate(zip(fluor_tokens, fluor_paths)):
            if tok in smfish_set:
                smfish_path = build_processed_output_path(
                    fp,
                    output_dir=output_dir,
                    suffix="_smfish",
                )
                _safe_imwrite(smfish_path, np.clip(fluor_corr[i], 0, None).astype(np.float32))
        if segmentation_method == "stardist_seeded_watershed_cell":
            assert nuclear_points is not None
            assert cytoplasm_tophat is not None
            assert cytoplasm_binary_mask is not None
            _safe_imwrite(
                build_processed_output_path(
                    nuclear_path,
                    output_dir=output_dir,
                    suffix="_nuclear_points",
                ),
                nuclear_points.astype(np.int32),
            )
            _safe_imwrite(
                build_processed_output_path(
                    nuclear_path,
                    output_dir=output_dir,
                    suffix="_cytoplasm_tophat",
                ),
                np.clip(cytoplasm_tophat, 0, None).astype(np.float32),
            )
            _safe_imwrite(
                build_processed_output_path(
                    nuclear_path,
                    output_dir=output_dir,
                    suffix="_cytoplasm_otsu_mask",
                ),
                cytoplasm_binary_mask.astype(np.uint8),
            )
        log.info("  tophat tifs written")
    _safe_imwrite(
        build_processed_output_path(
            nuclear_path,
            output_dir=output_dir,
            suffix="_labels",
        ),
        labels,
    )
    save_overlay(
        nir_raw,
        labels,
        build_processed_output_path(
            nuclear_path,
            output_dir=output_dir,
            suffix="_overlay",
            ext_override=".png",
        ),
    )
    log.info("  save:        %.1f s", time.perf_counter() - _t)

    # Quantify each nucleus across all fluorescent channels.
    meta    = parse_filename(nuclear_path, schema=schema, sep=sep)
    nuc_ids = np.unique(labels)
    nuc_ids = nuc_ids[nuc_ids != 0]
    if len(nuc_ids) == 0:
        log.info("  quantify:    0.0 s  (0 nuclei)")
        return []

    _t = time.perf_counter()
    records: list[dict] = []
    idx = np.asarray(nuc_ids, dtype=np.int32)
    area = ndi.sum(np.ones_like(labels, dtype=np.float32), labels=labels, index=idx)
    fluor_stats: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, str]] = []
    # Quantification is always computed from top-hat corrected fluorescence
    # images. smFISH LoG outputs are only produced as optional intermediates.
    for fc, tok in zip(fluor_tophat, fluor_tokens):
        fluor_stats.append((
            ndi.sum(fc, labels=labels, index=idx),
            ndi.mean(fc, labels=labels, index=idx),
            ndi.maximum(fc, labels=labels, index=idx),
            ndi.minimum(fc, labels=labels, index=idx),
            ndi.standard_deviation(fc, labels=labels, index=idx),
            tok.lower(),
        ))

    for i, nid in enumerate(nuc_ids):
        rec: dict = {
            **meta,
            "nucleus_id": int(nid),
            "area_px":    int(area[i]),
        }
        for total, mean, maxv, minv, stdv, pfx in fluor_stats:
            rec[f"{pfx}_total_intensity"] = float(total[i])
            rec[f"{pfx}_mean_intensity"]  = float(mean[i])
            rec[f"{pfx}_max_intensity"]   = float(maxv[i])
            rec[f"{pfx}_min_intensity"]   = float(minv[i])
            rec[f"{pfx}_std_intensity"]   = float(stdv[i])
        records.append(rec)
    log.info("  quantify:    %.1f s  (%d nuclei)", time.perf_counter() - _t, len(records))

    return records

# ---------------------------------------------------------------------------
# Directory scan
# ---------------------------------------------------------------------------

def find_image_groups_in_zip(
    zip_path: Path,
    nuclear_token: str,
    fluor_tokens: "list[str]",
    dest_dir: Path,
    schema: "list[str] | None" = None,
    sep: str = DEFAULT_SEP,
) -> "tuple[list[tuple[Path, list[Path]]], set[str]]":
    """
    Discover complete image groups from filenames inside *zip_path*.

    Returns:
      - groups as paths rooted at *dest_dir* (where selected members are later extracted)
      - set of base member names that must be extracted to satisfy those groups
    """
    if schema is None:
        schema = parse_schema(DEFAULT_SCHEMA)

    channel_idx: "int | None" = schema.index("channel") if "channel" in schema else None
    image_exts = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}

    by_name: set[str] = set()
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            base = Path(member.filename).name
            if not base or base.startswith("."):
                continue
            if Path(base).suffix.lower() not in image_exts:
                continue
            if base in by_name:
                log.warning(
                    "Zip %s contains duplicate basename '%s'; using first occurrence.",
                    zip_path.name,
                    base,
                )
                continue
            by_name.add(base)

    def _derive_fluor_name(nuclear_name: str, fluor_token: str) -> str:
        p = Path(nuclear_name)
        if channel_idx is not None:
            parts = p.stem.split(sep)
            if channel_idx >= len(parts):
                return ""
            parts[channel_idx] = fluor_token
            return sep.join(parts) + p.suffix
        return p.name.replace(nuclear_token, fluor_token)

    groups: list[tuple[Path, list[Path]]] = []
    needed_members: set[str] = set()
    incomplete: list[str] = []

    for base in sorted(by_name):
        p = Path(base)
        if channel_idx is not None:
            stem_parts = p.stem.split(sep)
            if channel_idx >= len(stem_parts) or stem_parts[channel_idx] != nuclear_token:
                continue
        elif nuclear_token not in p.name:
            continue

        fluor_names = [_derive_fluor_name(base, tok) for tok in fluor_tokens]
        if any(not fn for fn in fluor_names):
            incomplete.append(base)
            continue
        missing = [fn for fn in fluor_names if fn not in by_name]
        if missing:
            incomplete.append(base)
            for fn in missing:
                log.warning("  missing fluor file in zip %s: %s", zip_path.name, fn)
            continue

        needed_members.add(base)
        needed_members.update(fluor_names)
        groups.append((dest_dir / base, [dest_dir / fn for fn in fluor_names]))

    if incomplete:
        log.warning(
            "%d nuclear file(s) skipped in %s due to missing fluorescent channel(s).",
            len(incomplete),
            zip_path.name,
        )

    log.info(
        "Found %d image group(s) in %s  (%d fluor channel(s): %s)",
        len(groups), zip_path.name, len(fluor_tokens), ", ".join(fluor_tokens),
    )
    return groups, needed_members


def find_image_groups(
    input_dir: Path,
    nuclear_token: str,
    fluor_tokens: "list[str]",
    schema: "list[str] | None" = None,
    sep: str = DEFAULT_SEP,
) -> "list[tuple[Path, list[Path]]]":
    """
    Recursively scan *input_dir* for nuclear-channel files and locate all
    fluorescent-channel counterparts for each.

    Returns a list of (nuclear_path, [fluor_path, ...]) tuples where the
    nuclear file and every fluorescent file exist on disk.  Groups where any
    fluorescent channel is missing are skipped with a warning.

    Channel identification uses the schema's "channel" slot for position-aware
    matching.  Falls back to substring replacement when no "channel" slot exists.
    """
    if schema is None:
        schema = parse_schema(DEFAULT_SCHEMA)

    extensions = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}
    groups: "list[tuple[Path, list[Path]]]" = []
    incomplete: list[Path] = []
    all_images = [
        p for p in sorted(input_dir.rglob("*"))
        if p.is_file()
        and not p.name.startswith(".")
        and not any(part.startswith(".") for part in p.parts)
        and p.suffix.lower() in extensions
    ]
    all_set = set(all_images)

    channel_idx: "int | None" = (
        schema.index("channel") if "channel" in schema else None
    )

    def _derive_fluor_path(nuclear_path: Path, fluor_token: str) -> Path:
        """Return the expected path for one fluorescent channel file."""
        if channel_idx is not None:
            parts = nuclear_path.stem.split(sep)
            parts[channel_idx] = fluor_token
            return nuclear_path.parent / (sep.join(parts) + nuclear_path.suffix)
        # Legacy fallback: simple substring replacement.
        return nuclear_path.parent / nuclear_path.name.replace(
            nuclear_token, fluor_token
        )

    for path in all_images:

        # Filter to nuclear channel files only.
        if channel_idx is not None:
            stem_parts = path.stem.split(sep)
            if channel_idx >= len(stem_parts):
                continue
            if stem_parts[channel_idx] != nuclear_token:
                continue
        else:
            if nuclear_token not in path.name:
                continue

        # Build the expected path for each fluorescent channel.
        fluor_paths = [_derive_fluor_path(path, tok) for tok in fluor_tokens]
        missing = [fp for fp in fluor_paths if fp not in all_set]
        if missing:
            incomplete.append(path)
            for fp in missing:
                log.warning("  missing fluor file: %s", fp.name)
        else:
            groups.append((path, fluor_paths))

    if incomplete:
        log.warning(
            "%d nuclear file(s) skipped due to missing fluorescent channel(s):",
            len(incomplete),
        )
        for p in incomplete:
            log.warning("  %s", p.name)

    log.info(
        "Found %d image group(s) in %s  (%d fluor channel(s): %s)",
        len(groups), input_dir, len(fluor_tokens), ", ".join(fluor_tokens),
    )
    return groups


# Backward-compatible alias kept for any external callers.
def find_image_pairs(
    input_dir: Path,
    nuclear_token: str,
    gfp_token: str,
    schema: "list[str] | None" = None,
    sep: str = DEFAULT_SEP,
) -> "list[tuple[Path, Path]]":
    """Deprecated alias for find_image_groups with a single fluor channel."""
    groups = find_image_groups(
        input_dir, nuclear_token, [gfp_token], schema=schema, sep=sep
    )
    return [(nuc, fluors[0]) for nuc, fluors in groups]

# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------


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
    # follow-up rename / read.
    with _safe_atomic_text_write(out_path) as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


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
# Worker initialiser
# ---------------------------------------------------------------------------

def _ensure_pkg_resources_shim() -> None:
    """Install a minimal ``pkg_resources`` stand-in if setuptools' real
    one is missing.

    setuptools >= 80 and recent Python builds drop ``pkg_resources``,
    but stardist's ``bioimageio_utils`` still does
    ``from pkg_resources import get_distribution`` at import time. Back
    that single call with ``importlib.metadata`` so the worker keeps
    loading even when setuptools no longer ships the legacy module.
    """
    import sys as _sys
    try:
        import pkg_resources  # noqa: F401
        return
    except Exception:
        pass
    import types
    from importlib import metadata as _md

    class _DistInfo:
        def __init__(self, version: str) -> None:
            self.version = version

    def _get_distribution(name: str) -> _DistInfo:
        try:
            return _DistInfo(_md.version(name))
        except _md.PackageNotFoundError as exc:
            raise Exception(f"DistributionNotFound: {exc}") from exc

    shim = types.ModuleType("pkg_resources")
    shim.get_distribution = _get_distribution           # type: ignore[attr-defined]
    shim.DistributionNotFound = Exception               # type: ignore[attr-defined]
    shim.VersionConflict = Exception                    # type: ignore[attr-defined]
    shim.parse_version = lambda v: v                    # type: ignore[attr-defined]
    _sys.modules["pkg_resources"] = shim


# Substrings in an exception message that indicate a corrupt / truncated cached
# model file (HDF5 weights or its download archive) rather than a transient
# network / SSL problem — so we know it is safe to purge the cache and re-fetch.
_CORRUPT_MODEL_MARKERS = (
    "truncated file",
    "unable to synchronously open file",
    "file signature not found",
    "bad object header",
    "not a valid hdf5",
    "unable to open file",
    "bad magic number",         # truncated zip archive
    "file is not a zip file",
)


def _purge_stardist_cache() -> "list[str]":
    """Remove cached StarDist pretrained-model files so the next
    ``from_pretrained`` re-downloads them.

    ``StarDist2D.from_pretrained`` caches the model under the keras cache
    (``$KERAS_HOME`` or ``~/.keras/models/…``) as ``weights_best.h5`` +
    ``config.json``. A run interrupted mid-download (e.g. the worker was killed,
    or an earlier failure aborted the pool) can leave a **truncated**
    ``weights_best.h5``; the next run reuses it and h5py dies with
    ``OSError: Unable to synchronously open file (truncated file: eof = …)``.

    Deletes any model folder containing a StarDist weights file (and stray
    ``*.h5`` / partial download archives) under each keras cache root. Returns
    the paths removed, for logging.
    """
    roots: "list[Path]" = []
    kh = os.environ.get("KERAS_HOME")
    if kh:
        roots.append(Path(kh))
    roots.append(Path(os.path.expanduser("~")) / ".keras")
    removed: "list[str]" = []
    seen: "set[str]" = set()
    for models in (r / "models" for r in roots):
        if not models.is_dir():
            continue
        targets: "set[Path]" = set()
        for p in models.rglob("*"):
            if not p.is_file():
                continue
            low = p.name.lower()
            if low == "weights_best.h5" or low.endswith("weights_best.h5"):
                targets.add(p.parent)                       # whole model folder
            elif low.endswith((".h5", ".zip", ".part", ".download", ".tmp")):
                targets.add(p)                              # stray / partial file
        for t in targets:
            key = str(t)
            if key in seen:
                continue
            seen.add(key)
            try:
                if t.is_dir():
                    shutil.rmtree(t, ignore_errors=True)
                else:
                    t.unlink()
                removed.append(key)
            except OSError:
                pass
    return removed


def _load_stardist_pretrained(StarDist2D, name: str = "2D_versatile_fluo"):
    """Load a StarDist pretrained model, recovering once from a corrupt cache.

    If the first load fails because the cached weights / archive is truncated
    (see :data:`_CORRUPT_MODEL_MARKERS`) — the classic symptom of a previous
    run that died mid-download — purge the cache and re-download exactly once.
    Any other error (and a second failure) propagates unchanged.
    """
    try:
        return StarDist2D.from_pretrained(name)
    except Exception as exc:                                 # noqa: BLE001
        if not any(m in str(exc).lower() for m in _CORRUPT_MODEL_MARKERS):
            raise
        removed = _purge_stardist_cache()
        log.warning(
            "StarDist model cache was corrupt (%s: %s) — purged %s and "
            "re-downloading once.",
            exc.__class__.__name__, exc, removed or "(nothing found to remove)")
        return StarDist2D.from_pretrained(name)


def _worker_init(force_cpu: bool = False, threads_per_worker: int = 0) -> None:
    """
    Called once in each worker process before any image pairs are processed.

    *force_cpu*          When True, forces StarDist onto CPU regardless of
                         platform.  When False, the effective device is chosen
                         automatically:
                           macOS  -> Metal GPU via tensorflow-metal if installed,
                                     otherwise CPU.
                           Other  -> CPU only (CUDA available if a CUDA-enabled
                                     TF build is installed and force_cpu=False).
    *threads_per_worker* CPU threads per worker for TF/BLAS inference.

    Platform logic
    --------------
    On macOS, tensorflow-metal registers Metal as a GPU device automatically.
    Calling tf.config.set_visible_devices([], "GPU") disables Metal too (not
    just CUDA) so we must NOT call it on macOS unless --cpu_only is set.
    On all other platforms we default to CPU to avoid unexpected CUDA usage.
    """
    import sys as _sys
    global _STARDIST_MODEL

    os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

    # Determine effective device policy before any TF import.
    _on_mac        = _sys.platform == "darwin"
    _effective_cpu = force_cpu or (not _on_mac)

    if threads_per_worker > 0:
        # Pin CPU thread pools before any library imports.
        t = str(threads_per_worker)
        os.environ["OMP_NUM_THREADS"]        = t
        os.environ["OPENBLAS_NUM_THREADS"]   = t
        os.environ["MKL_NUM_THREADS"]        = t
        os.environ["NUMEXPR_NUM_THREADS"]    = t
        os.environ["TF_NUM_INTRAOP_THREADS"] = t
        os.environ["TF_NUM_INTEROP_THREADS"] = t

    # Raise the per-worker open-file ceiling. macOS's default soft limit
    # (256) is easily exceeded by TF + StarDist + tifffile in a well with
    # many FOVs; the resulting EMFILE typically surfaces as a confusing
    # OSError downstream. We push the soft limit up to whatever the kernel
    # already permits (hard limit, capped at 4096) without requiring root.
    try:
        import resource as _resource
        _soft, _hard = _resource.getrlimit(_resource.RLIMIT_NOFILE)
        _target = max(_soft, min(_hard, 4096))
        if _target > _soft:
            _resource.setrlimit(_resource.RLIMIT_NOFILE, (_target, _hard))
    except (ImportError, ValueError, OSError):
        # resource is POSIX-only; ignore on platforms / environments where
        # the bump isn't permitted.
        pass

    # Restore stdout/stderr before configuring logging — the worker may
    # have inherited a windowed-PyInstaller-bootloader-detached
    # sys.stdout / sys.stderr (see ``_ensure_pipeline_io_ready`` for the
    # full rationale). Otherwise ``logging.basicConfig`` binds its
    # StreamHandler to ``None`` and every log line vanishes.
    _ensure_pipeline_io_ready()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  [worker %(process)d]  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Belt-and-braces: even after the shim above, an existing handler
    # bound to ``None`` (from ``logging.basicConfig`` having run at
    # module import before we got a chance to repair sys.stderr) needs
    # to be repointed at the live stream so the worker's logs actually
    # reach the parent's pipe.
    try:
        import sys as _sys2
        if _sys2.stderr is not None and not getattr(_sys2.stderr, "closed", False):
            _sys2.stderr.write("")
            _sys2.stderr.flush()
        else:
            raise OSError("sys.stderr is None or closed")
    except (OSError, AttributeError):
        try:
            import os as _os2
            _os2.dup2(1, 2)
            _sys2.stderr = _sys2.stdout                     # noqa: F841
            for _h in list(logging.getLogger().handlers):
                if isinstance(_h, logging.StreamHandler):
                    _h.stream = _sys2.stdout
        except Exception:
            pass

    # ── Device note for logging ────────────────────────────────────────────────
    if force_cpu:
        device_note = "CPU-only (--cpu_only)"
    elif _on_mac:
        device_note = "Metal GPU (macOS) if tensorflow-metal installed, else CPU"
    else:
        device_note = "CPU-only (non-macOS default)"
    thr_note = (f"{threads_per_worker} TF thread(s)" if threads_per_worker > 0
                else "TF default threads")
    log.info("Worker %d loading StarDist (%s, %s).", os.getpid(), device_note, thr_note)

    # Import StarDist and its TF dependency HERE, after env vars are set.
    # This is the first point in the worker where TF is allowed to initialise.
    # stardist's bioimageio_utils imports ``pkg_resources``, which was
    # removed from setuptools >= 80 / Python 3.12+. Install a tiny shim
    # backed by importlib.metadata before stardist loads so the worker
    # doesn't crash with ``ModuleNotFoundError: pkg_resources``.
    _ensure_pkg_resources_shim()
    from csbdeep.utils import normalize as _normalize   # noqa: F401 — triggers TF init
    from stardist.models import StarDist2D as _StarDist2D

    # Disable GPU on non-Mac platforms (or when --cpu_only is set).
    # On macOS we leave GPU visible so tensorflow-metal can use Metal.
    if _effective_cpu:
        import tensorflow as tf
        tf.config.set_visible_devices([], "GPU")

    _t0 = time.perf_counter()
    # from_pretrained downloads the model weights via urllib if they are not
    # already cached locally (~/.keras/models/).  On machines where the SSL
    # certificate store cannot be verified (e.g. corporate proxies, some HPC
    # clusters) this raises an SSL error.  The override below disables
    # certificate verification for this one call only; the original context
    # factory is restored immediately in the finally block so no other network
    # code in the worker is affected. _load_stardist_pretrained additionally
    # recovers from a truncated cache left by an interrupted earlier run (it
    # purges the corrupt weights and re-downloads once).
    import ssl as _ssl
    _orig_ssl_ctx = _ssl._create_default_https_context
    _ssl._create_default_https_context = _ssl._create_unverified_context
    try:
        _STARDIST_MODEL = _load_stardist_pretrained(_StarDist2D, "2D_versatile_fluo")
    finally:
        _ssl._create_default_https_context = _orig_ssl_ctx
    log.info("Worker %d model loaded in %.1f s.", os.getpid(), time.perf_counter() - _t0)

# ---------------------------------------------------------------------------
# Well-level task functions (run inside worker processes)
# ---------------------------------------------------------------------------

# ── Per-FOV checkpoint helpers (v5.2.0) ─────────────────────────────────────
#
# When a worker is killed mid-well, the next invocation should pick up where
# the previous one left off rather than redoing every FOV in the well. The
# scheme:
#
#   * After each FOV finishes (records computed, QC TIFs written), the worker
#     writes <tmp_images>/.fov_<stem>.records.json (atomic) then touches
#     <tmp_images>/.fov_<stem>.done. The records JSON is the cached output of
#     :func:`process_image_group` for that FOV.
#   * On startup, the worker scans <tmp_images> for valid checkpoint pairs and
#     skips those FOVs in the per-group loop, loading their records from disk.
#     Partially-written records (no .done marker) are reprocessed; orphan
#     __aw_tmp_* scratch files are deleted.
#   * On successful end-of-well (zip + CSV both written), the whole tmp_images
#     dir is removed in the usual finally-block — checkpoints are inherently
#     transient and never survive a successful well.
#
# Recovery cost for a killed-mid-well run drops from "redo every FOV in the
# well" to "redo only the in-flight FOV at kill time."

# Schema version stored in each records.json. Bumped if the records dict
# layout changes — older checkpoints with a different version are discarded
# on load so a freshly-built pipeline never reads stale records that would
# poison the per-well CSV.
_FOV_CHECKPOINT_SCHEMA = 1


def _json_safe(records: "list[dict]") -> "list[dict]":
    """Cast numpy scalars in record dicts to plain Python types so the
    records survive a JSON round-trip. Mutates a copy, not the input."""
    out: list[dict] = []
    for r in records:
        clean: dict = {}
        for k, v in r.items():
            # numpy scalar → Python builtin via .item(); everything else
            # passes through. Avoids the ``import numpy`` cost in the
            # common case where the records are already clean.
            item = getattr(v, "item", None)
            clean[k] = item() if callable(item) and v is not None else v
        out.append(clean)
    return out


def _fov_stem(nuclear_path: Path) -> str:
    """Stable per-FOV identifier within a well: the source file's stem.
    process_microscopy already keys QC filenames on this stem (see
    ``_qc_output_path`` around line 1305), so any QC file for this FOV
    starts with ``stem``."""
    return nuclear_path.stem


def _checkpoint_paths(tmp_images: Path, fov_id: str) -> "tuple[Path, Path]":
    """Return ``(records_json, done_marker)`` for a given FOV id."""
    return (
        tmp_images / f".fov_{fov_id}.records.json",
        tmp_images / f".fov_{fov_id}.done",
    )


def _load_fov_checkpoint(tmp_images: Path, fov_id: str) -> "list[dict] | None":
    """Return cached records for ``fov_id``, or None if no valid
    checkpoint exists.

    Treats a missing marker, a missing records file, an unreadable
    records file, or a schema-version mismatch all as "no checkpoint" —
    in every case the caller will reprocess the FOV and overwrite the
    on-disk state.
    """
    records_file, marker = _checkpoint_paths(tmp_images, fov_id)
    if not (marker.exists() and records_file.exists()):
        return None
    try:
        payload = json.loads(records_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") != _FOV_CHECKPOINT_SCHEMA:
        return None
    records = payload.get("records")
    if not isinstance(records, list):
        return None
    return records


def _save_fov_checkpoint(
    tmp_images: Path, fov_id: str, records: "list[dict]",
) -> None:
    """Atomically persist ``records`` and create the ``.done`` marker.

    Writes records.json first, fsync-then-rename, then touches the marker
    — so a process killed between the two writes leaves NO marker and
    the next run reprocesses the FOV (rather than reading half-written
    records).
    """
    records_file, marker = _checkpoint_paths(tmp_images, fov_id)
    tmp = records_file.with_suffix(".records.json.tmp")
    payload = {"schema": _FOV_CHECKPOINT_SCHEMA, "records": _json_safe(records)}
    text = json.dumps(payload)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(records_file)
    # The marker is the atomicity boundary — present iff records_file
    # is on disk and complete.
    marker.touch()


def _prune_scratch_files(tmp_images: Path) -> None:
    """Remove ``__aw_tmp_*`` partials from a previous killed run.

    These come from ``_safe_imwrite`` writes that didn't complete. The
    zip step already filters them out, but leaving them on disk wastes
    space and can confuse a human inspecting the checkpoint state.
    """
    if not tmp_images.is_dir():
        return
    for p in tmp_images.iterdir():
        if p.name.startswith("__aw_tmp_"):
            try:
                p.unlink()
            except OSError:
                pass


def _process_groups_in_worker(
    groups: "list[tuple[Path, list[Path]]]",
    shared_kwargs: dict,
    well_label: str,
    fov_threads: int = 1,
) -> "tuple[list[dict], list[str]]":
    """
    Process all image groups for one well within a single worker process.

    Each group is (nuclear_path, [fluor_path, ...]).  StarDist is already
    loaded by _worker_init, so the model is reused across every group.

    *fov_threads* controls concurrent FOV processing via ThreadPoolExecutor.
    fov_threads=1 (default) is safe for any backend; >1 benefits multi-core CPU.

    Returns (records, failed_names).

    v5.2.0: each FOV that completes successfully is persisted as a small
    JSON checkpoint inside ``output_dir`` (the well's ``tmp_images``).
    A subsequent invocation against the same partly-finished well loads
    those checkpoints and skips the corresponding FOVs entirely.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed

    fov_threads = shared_kwargs.pop("_fov_threads", fov_threads)
    fov_threads = max(1, min(fov_threads, len(groups), os.cpu_count() or 1))
    kwargs = {k: v for k, v in shared_kwargs.items() if not k.startswith("_")}
    tmp_images: Path = kwargs["output_dir"]
    _prune_scratch_files(tmp_images)

    all_records: list[dict] = []
    failed:      list[str]  = []

    def _run_group(nuclear_path: Path, fluor_paths: "list[Path]") -> "list[dict]":
        _t0 = time.perf_counter()
        recs = process_image_group(nuclear_path, fluor_paths, **kwargs)
        log.info("FOV %s processed in %.1f s (%d nuclei)",
                 nuclear_path.name, time.perf_counter() - _t0, len(recs))
        for r in recs:
            r["well"] = well_label
        # Persist this FOV's records so a kill before end-of-well
        # doesn't lose the work. The marker write is the last step in
        # _save_fov_checkpoint so a half-written records.json never
        # appears "done" to the next invocation.
        try:
            _save_fov_checkpoint(tmp_images, _fov_stem(nuclear_path), recs)
        except OSError as ckpt_exc:
            log.warning(
                "FOV %s — could not write checkpoint (%s); FOV will be "
                "reprocessed on restart.", nuclear_path.name, ckpt_exc,
            )
        return recs

    def _process_or_resume(
        nuclear_path: Path, fluor_paths: "list[Path]",
    ) -> "list[dict]":
        """Load cached records if a checkpoint exists, else process the
        FOV from scratch."""
        fov_id = _fov_stem(nuclear_path)
        cached = _load_fov_checkpoint(tmp_images, fov_id)
        if cached is not None:
            log.info(
                "FOV %s — resumed from checkpoint (%d records)",
                nuclear_path.name, len(cached),
            )
            return cached
        return _run_group(nuclear_path, fluor_paths)

    if fov_threads == 1 or len(groups) == 1:
        for nuclear_path, fluor_paths in groups:
            try:
                all_records.extend(_process_or_resume(nuclear_path, fluor_paths))
            except Exception as exc:           # noqa: BLE001
                log.error("FAILED %s: %s", nuclear_path.name, exc, exc_info=True)
                failed.append(nuclear_path.name)
    else:
        with ThreadPoolExecutor(max_workers=fov_threads) as tex:
            future_map = {
                tex.submit(_process_or_resume, nuc, fluors): nuc
                for nuc, fluors in groups
            }
            for fut in _as_completed(future_map):
                nuclear_path = future_map[fut]
                try:
                    all_records.extend(fut.result())
                except Exception as exc:       # noqa: BLE001
                    log.error("FAILED %s: %s", nuclear_path.name, exc, exc_info=True)
                    failed.append(nuclear_path.name)

    return all_records, failed


def process_well_zip_task(
    well_label: str,
    zip_path: Path,
    output_dir: Path,
    shared_kwargs_template: dict,
    nuclear_token: str,
    fluor_tokens: "list[str]",
    csv_prefix: str,
    schema: "list[str] | None" = None,
    sep: str = DEFAULT_SEP,
) -> "tuple[list[dict], list[str]]":
    """
    Top-level picklable task: full lifecycle for one zipped well.

    Runs inside a worker process (StarDist already loaded by _worker_init).
    1. Discover complete image groups from zip member names, then extract only
       required channel files to a temporary directory.
    2. Discover image groups (nuclear + all fluor channels) and process them.
    3. Write the per-well CSV.
    4. Compress output images to <output_dir>/<well>_out.zip.
    5. Remove temporary directories.

    Returns (records, failed_names).
    """
    if schema is None:
        schema = parse_schema(DEFAULT_SCHEMA)
    log.info("Well %s — starting (pid %d)", well_label, os.getpid())
    tmp_extract = output_dir / f"_tmp_extract_{well_label}"
    tmp_images  = output_dir / f"_tmp_images_{well_label}"

    # tmp_extract is always per-run scratch: extraction can re-derive
    # itself from the source zip in seconds, no checkpoint value.
    if tmp_extract.exists():
        log.warning("Well %s: removing stale tmp dir %s", well_label, tmp_extract.name)
        remove_directory(tmp_extract)

    # tmp_images carries per-FOV checkpoints (v5.2.0). If it holds at
    # least one ``.fov_*.done`` marker, treat it as a partial-progress
    # dir for this well and keep its contents — _process_groups_in_worker
    # will pick up where the previous attempt stopped. Otherwise wipe
    # it (matches pre-v5.2.0 behavior on a clean restart).
    if tmp_images.exists():
        has_checkpoint = any(tmp_images.glob(".fov_*.done"))
        if has_checkpoint:
            log.info(
                "Well %s: resuming from %d FOV checkpoint(s) in %s",
                well_label,
                sum(1 for _ in tmp_images.glob(".fov_*.done")),
                tmp_images.name,
            )
        else:
            log.warning("Well %s: removing stale tmp dir %s", well_label, tmp_images.name)
            remove_directory(tmp_images)

    try:
        try:
            tmp_extract.mkdir(parents=True, exist_ok=True)
            tmp_images.mkdir(parents=True,  exist_ok=True)

            groups, needed_members = find_image_groups_in_zip(
                zip_path,
                nuclear_token,
                fluor_tokens,
                tmp_extract,
                schema=schema,
                sep=sep,
            )
            if not groups:
                log.warning("Well %s: no image groups found in %s.", well_label, zip_path.name)
                return [], []

            n_extracted = extract_zip(zip_path, tmp_extract, members_to_extract=needed_members)
            log.info(
                "Well %s: extracted %d/%d required image file(s).",
                well_label,
                n_extracted,
                len(needed_members),
            )

            kwargs = {**shared_kwargs_template, "output_dir": tmp_images}
            records, failed = _process_groups_in_worker(groups, kwargs, well_label)

            if records:
                safe_well = _safe_well(well_label)
                csv_path  = output_dir / f"{csv_prefix}_{safe_well}.csv"
                _write_single_csv(records, csv_path)
                log.info("Well %-12s -> %s  (%d rows)", well_label, csv_path.name, len(records))
            else:
                log.warning("Well %s: no records produced; CSV not written.", well_label)

            out_zip = output_dir / f"{well_label}_out.zip"
            n_compressed = compress_images_to_zip(tmp_images, out_zip)
            log.info("Well %s: %d image(s) -> %s", well_label, n_compressed, out_zip.name)

            # Mark the well as functionally complete *before* the
            # finally-block cleanup. On slow / unreliable storage (NAS,
            # network mounts) the subsequent ``remove_directory`` calls
            # can hang for minutes, which used to delay the well_done
            # event the GUI reads from "temporary directories removed".
            # All user-facing artefacts (CSV + out_zip) are already
            # written at this point, so the well *is* done — the
            # cleanup is internal housekeeping.
            log.info("Well %s — done.", well_label)
            return records, failed
        except BaseException as exc:
            trace_path = _dump_well_failure_trace(output_dir, well_label, exc)
            if trace_path is not None:
                log.error(
                    "Well %s failed (%s: %s); worker trace written to %s",
                    well_label, type(exc).__name__, exc, trace_path,
                    exc_info=True,
                )
            else:
                log.error(
                    "Well %s failed (%s: %s); could not write worker trace file",
                    well_label, type(exc).__name__, exc,
                    exc_info=True,
                )
            raise

    finally:
        remove_directory(tmp_extract)
        remove_directory(tmp_images)
        log.info("Well %s — temporary directories removed.", well_label)


def process_well_folder_task(
    well_label: str,
    well_folder: Path,
    output_dir: Path,
    shared_kwargs_template: dict,
    nuclear_token: str,
    fluor_tokens: "list[str]",
    csv_prefix: str,
    compress_input_well_folders: bool = False,
    compress_output_well_folders: bool = False,
    schema: "list[str] | None" = None,
    sep: str = DEFAULT_SEP,
) -> "tuple[list[dict], list[str]]":
    """
    Top-level picklable task: full lifecycle for one uncompressed well folder.

    Runs inside a worker process (StarDist already loaded by _worker_init).
    1. Discover image groups directly from *well_folder*.
    2. Process groups, writing QC images to a temporary staging directory.
    3. Write per-well CSV to <output_dir>/<csv_prefix>_<well>.csv.
    4. Rename staging directory to <output_dir>/<well_label>/.
    5. Optionally compress input/output well folders to per-well zip files.

    Returns (records, failed_names).
    """
    if schema is None:
        schema = parse_schema(DEFAULT_SCHEMA)
    log.info("Well %s — starting (folder mode, pid %d)", well_label, os.getpid())

    tmp_images = output_dir / f"_tmp_images_{well_label}"
    # v5.2.0: preserve per-FOV checkpoints if any .fov_*.done markers
    # exist; otherwise wipe stale state. Same semantics as zip-mode.
    if tmp_images.exists():
        has_checkpoint = any(tmp_images.glob(".fov_*.done"))
        if has_checkpoint:
            log.info(
                "Well %s: resuming from %d FOV checkpoint(s) in %s",
                well_label,
                sum(1 for _ in tmp_images.glob(".fov_*.done")),
                tmp_images.name,
            )
        else:
            log.warning("Well %s: removing stale tmp dir %s", well_label, tmp_images.name)
            remove_directory(tmp_images)

    _committed = False
    try:
        try:
            tmp_images.mkdir(parents=True, exist_ok=True)

            groups = find_image_groups(
                well_folder, nuclear_token, fluor_tokens, schema=schema, sep=sep
            )
            if not groups:
                log.warning("Well %s: no image groups found in %s.", well_label, well_folder)
                return [], []

            kwargs = {**shared_kwargs_template, "output_dir": tmp_images}
            records, failed = _process_groups_in_worker(groups, kwargs, well_label)

            if records:
                safe_well = _safe_well(well_label)
                csv_path  = output_dir / f"{csv_prefix}_{safe_well}.csv"
                _write_single_csv(records, csv_path)
                log.info("Well %-12s -> %s  (%d rows)", well_label, csv_path.name, len(records))
            else:
                log.warning("Well %s: no records produced; CSV not written.", well_label)

            # Atomically rename staging dir to final output folder.
            final_out = output_dir / well_label
            if final_out.exists():
                remove_directory(final_out)
            tmp_images.rename(final_out)
            log.info("Well %s: output folder -> %s", well_label, final_out.name)

            if compress_output_well_folders:
                out_zip = output_dir / f"{well_label}_out.zip"
                n_out = compress_folder_images_to_zip_and_remove(final_out, out_zip)
                if n_out > 0:
                    log.info(
                        "Well %s: compressed output folder -> %s (%d image(s))",
                        well_label,
                        out_zip.name,
                        n_out,
                    )

            if compress_input_well_folders:
                in_zip = well_folder.parent / f"{well_label}.zip"
                n_in = compress_folder_images_to_zip_and_remove(well_folder, in_zip)
                if n_in > 0:
                    log.info(
                        "Well %s: compressed input folder -> %s (%d image(s))",
                        well_label,
                        in_zip.name,
                        n_in,
                    )
            _committed = True

            # Mark the well as done before the (potential) staging-dir
            # cleanup in the finally block — same reasoning as the zip
            # task: cleanup can hang on NAS storage and we don't want
            # that to defer the GUI's well_done event.
            log.info("Well %s — done.", well_label)
            return records, failed
        except BaseException as exc:
            # Capture the worker-side traceback to disk before propagating.
            # ProcessPoolExecutor pickles the exception back to the parent,
            # but generic OSError("Bad file descriptor") re-raises at
            # future.result() with little context if the worker's stderr is
            # broken. The persisted trace gives the user the original site.
            trace_path = _dump_well_failure_trace(output_dir, well_label, exc)
            if trace_path is not None:
                log.error(
                    "Well %s failed (%s: %s); worker trace written to %s",
                    well_label, type(exc).__name__, exc, trace_path,
                    exc_info=True,
                )
            else:
                log.error(
                    "Well %s failed (%s: %s); could not write worker trace file",
                    well_label, type(exc).__name__, exc,
                    exc_info=True,
                )
            raise

    finally:
        if not _committed and tmp_images.exists():
            remove_directory(tmp_images)
            log.info("Well %s — staging directory removed.", well_label)


def process_well_flat_task(
    well_label: str,
    groups: "list[tuple[Path, list[Path]]]",
    shared_kwargs: dict,
) -> "tuple[list[dict], list[str]]":
    """
    Top-level picklable task: process all image groups for one flat-mode well.

    Runs inside a worker process (StarDist already loaded by _worker_init).
    Returns (records, failed_names).
    """
    log.info("Well %s — starting flat-mode (pid %d, %d group(s))",
             well_label, os.getpid(), len(groups))
    return _process_groups_in_worker(groups, shared_kwargs, well_label)

# ---------------------------------------------------------------------------
# Well-level pool dispatcher (replaces run_pipeline_on_directory)
# ---------------------------------------------------------------------------

def run_pipeline_on_wells(
    wells: "list[tuple[str, list[tuple[Path, Path]]]]",
    shared_kwargs: dict,
    workers: int,
    force_cpu: bool = False,
    threads_per_worker: int = 0,
    task_fn = None,
) -> "tuple[list[dict], list[str]]":
    """
    Submit one worker task per well and collect results.

    *wells*: list of (well_label, pairs) — pairs is a list of (nir, gfp) Paths.
    *threads_per_worker*: passed to _worker_init to pin TF/BLAS thread counts.
    *task_fn*: callable(well_label, pairs, shared_kwargs) -> (records, failed).
               Defaults to process_well_flat_task.
    """
    if task_fn is None:
        task_fn = process_well_flat_task

    _ensure_stardist_runtime_deps()

    all_records: list[dict] = []
    all_failed:  list[str]  = []

    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=ctx,
        initializer=_worker_init,
        initargs=(force_cpu, threads_per_worker),
    ) as executor:
        future_to_well = {
            executor.submit(task_fn, well_label, pairs, shared_kwargs): well_label
            for well_label, pairs in wells
        }
        for future in as_completed(future_to_well):
            well_label = future_to_well[future]
            try:
                records, failed = future.result()
                all_records.extend(records)
                all_failed.extend(failed)
            except Exception as exc:           # noqa: BLE001
                _report_worker_failure(well_label, shared_kwargs.get("output_dir"), exc)
                all_failed.append(well_label)

    return all_records, all_failed

# ---------------------------------------------------------------------------
# Legacy shim kept for backward compatibility
# ---------------------------------------------------------------------------

def run_pipeline_on_directory(
    input_dir: Path,
    output_dir: Path,
    shared_kwargs: dict,
    nuclear_token: str,
    fluor_tokens: "list[str]",
    workers: int,
    skip_wells: "set[str] | None" = None,
    force_cpu: bool = False,
    threads_per_worker: int = 0,
    groups: "list[tuple[Path, list[Path]]] | None" = None,
    schema: "list[str] | None" = None,
    sep: str = DEFAULT_SEP,
) -> "tuple[list[dict], list[str]]":
    """
    Flat-directory entry point: group image groups by well, then run one worker per well.

    *groups* may be passed in if already discovered (avoids a second directory scan).
    """
    if schema is None:
        schema = parse_schema(DEFAULT_SCHEMA)
    if groups is None:
        groups = find_image_groups(input_dir, nuclear_token, fluor_tokens,
                                   schema=schema, sep=sep)
    if not groups:
        log.warning("No valid image groups found in %s.", input_dir)
        return [], []

    from collections import defaultdict
    by_well: dict[str, list] = defaultdict(list)
    for nuc, fluors in groups:
        tok = _well_token_from_path(nuc, nuclear_token, schema=schema, sep=sep) or "unknown"
        by_well[tok].append((nuc, fluors))

    # Filter already-complete wells
    if skip_wells:
        before = len(by_well)
        by_well = {w: p for w, p in by_well.items() if w not in skip_wells}
        n_removed = before - len(by_well)
        if n_removed:
            log.info("Skipped %d already-complete well(s).", n_removed)
        if not by_well:
            log.info("All wells already complete; nothing to do.")
            return [], []

    wells = sorted(by_well.items())
    log.info("Flat mode: %d well(s), %d worker(s).", len(wells), workers)

    return run_pipeline_on_wells(
        wells=wells,
        shared_kwargs={**shared_kwargs, "output_dir": output_dir},
        workers=workers,
        force_cpu=force_cpu,
        threads_per_worker=threads_per_worker,
    )

# ---------------------------------------------------------------------------
# Skip-detection helpers
# ---------------------------------------------------------------------------

def _well_token_from_path(
    nir_path: Path,
    nuclear_token: str,
    schema: "list[str] | None" = None,
    sep: str = DEFAULT_SEP,
) -> str:
    """Extract the well token from an image filename, or return empty string."""
    return parse_filename(nir_path, schema=schema, sep=sep)["well"]


def output_exists_for_well(output_dir: Path, well_label: str, csv_prefix: str) -> bool:
    """
    Return True if processable output already exists for *well_label*.

    Both outputs must be present and non-empty to count as complete:
      • <output_dir>/<well>_out.zip       – compressed QC images
      • <output_dir>/<csv_prefix>_<well>.csv – per-well measurements

    This prevents re-processing wells that completed successfully in a prior
    run while still allowing partially-completed wells to be retried.
    """
    safe_well = _safe_well(well_label)
    out_zip  = output_dir / f"{well_label}_out.zip"
    csv_path = output_dir / f"{csv_prefix}_{safe_well}.csv"
    zip_ok = out_zip.exists()  and out_zip.stat().st_size  > 0
    csv_ok = csv_path.exists() and csv_path.stat().st_size > 0
    return zip_ok and csv_ok


def output_exists_for_well_folder(
    output_dir: Path,
    well_label: str,
    csv_prefix: str,
    compressed_output: bool = False,
) -> bool:
    """
    Return True if folder-mode output already exists for *well_label*.

    Both outputs must be present and non-empty to count as complete:
      • <output_dir>/<well>/                   – output image folder (must contain ≥1 file), OR
        <output_dir>/<well>_out.zip            – output image archive when *compressed_output* is True
      • <output_dir>/<csv_prefix>_<well>.csv   – per-well measurements
    """
    safe_well  = _safe_well(well_label)
    out_folder = output_dir / well_label
    out_zip    = output_dir / f"{well_label}_out.zip"
    csv_path   = output_dir / f"{csv_prefix}_{safe_well}.csv"
    folder_ok  = out_folder.is_dir() and any(out_folder.iterdir())
    zip_ok     = out_zip.exists() and out_zip.stat().st_size > 0
    csv_ok     = csv_path.exists() and csv_path.stat().st_size > 0
    out_ok     = zip_ok if compressed_output else folder_ok
    return out_ok and csv_ok


def output_exists_for_well_flat(output_dir: Path, well_label: str, csv_prefix: str) -> bool:
    """
    Return True if a per-well CSV already exists for *well_label* (flat mode).

    In flat mode there is no output zip; only the CSV is checked.
    """
    safe_well = _safe_well(well_label)
    csv_path  = output_dir / f"{csv_prefix}_{safe_well}.csv"
    return csv_path.exists() and csv_path.stat().st_size > 0


def _unique_tokens_preserve_order(tokens: "list[str]") -> "list[str]":
    """Stable de-dupe for channel tokens, ignoring case and surrounding whitespace."""
    seen: set[str] = set()
    out: list[str] = []
    for tok in tokens:
        cleaned = str(tok or "").strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key not in seen:
            out.append(cleaned)
            seen.add(key)
    return out


# ---------------------------------------------------------------------------
# Zip-aware orchestration
# ---------------------------------------------------------------------------

def process_well_zips(
    input_dir: Path,
    output_dir: Path,
    well_zips: "list[tuple[str, Path]]",
    shared_kwargs_template: dict,
    nuclear_token: str,
    fluor_tokens: "list[str]",
    workers: int,
    csv_prefix: str,
    force: bool = False,
    force_cpu: bool = False,
    threads_per_worker: int = 0,
    schema: "list[str] | None" = None,
    sep: str = DEFAULT_SEP,
) -> None:
    """
    Dispatch one worker process per well zip.

    Each worker: extracts its zip, processes all image pairs sequentially
    (StarDist loaded once), writes the CSV, compresses QC images, and cleans up.
    Up to *workers* wells are processed in parallel.
    """
    if schema is None:
        schema = parse_schema(DEFAULT_SCHEMA)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Sweep stale dirs from previous crashed runs. v5.2.0: a
    # _tmp_images_* dir that holds any .fov_*.done marker is treated
    # as per-FOV checkpoint state (not garbage) and survives the sweep —
    # the worker will pick up where the previous attempt left off.
    # _tmp_extract_* dirs are always per-run scratch.
    for _stale in sorted(output_dir.glob("_tmp_extract_*")):
        if _stale.is_dir():
            log.warning("Removing stale temporary directory from previous run: %s", _stale.name)
            remove_directory(_stale)
    for _stale in sorted(output_dir.glob("_tmp_images_*")):
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

    # Skip already-complete wells unless --force
    n_skipped = 0
    to_process: list[tuple[str, Path]] = []
    for well_label, zip_path in well_zips:
        if not force and output_exists_for_well(output_dir, well_label, csv_prefix):
            log.info(
                "SKIP well %s — output already exists. Use --force to reprocess.",
                well_label,
            )
            n_skipped += 1
        else:
            to_process.append((well_label, zip_path))

    if not to_process:
        log.info("All wells already complete.")
        log.info("Zip mode complete. %d skipped.", n_skipped)
        return

    _ensure_stardist_runtime_deps()

    log.info("Zip mode: %d well(s) to process, %d worker(s).", len(to_process), workers)

    # Submit one worker per well zip directly.
    all_records: list[dict] = []
    all_failed:  list[str]  = []

    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=ctx,
        initializer=_worker_init,
        initargs=(force_cpu, threads_per_worker),
    ) as executor:
        future_to_well = {
            executor.submit(
                process_well_zip_task,
                well_label,
                zip_path,
                output_dir,
                shared_kwargs_template,
                nuclear_token,
                fluor_tokens,
                csv_prefix,
                schema,
                sep,
            ): well_label
            for well_label, zip_path in to_process
        }
        for future in as_completed(future_to_well):
            well_label = future_to_well[future]
            try:
                records, failed = future.result()
                all_records.extend(records)
                all_failed.extend(failed)
            except Exception as exc:           # noqa: BLE001
                _report_worker_failure(well_label, output_dir, exc)
                all_failed.append(well_label)

    log.info("=" * 60)
    log.info("Zip mode complete.")
    log.info(
        "Total wells : %d  |  processed : %d  |  skipped : %d  |  "
        "nuclei : %d  |  failed wells : %d",
        len(well_zips), len(to_process), n_skipped,
        len(all_records), len(all_failed),
    )
    if all_failed:
        log.warning("Failed wells/pairs:")
        for name in all_failed:
            log.warning("  %s", name)


def process_well_folders(
    input_dir: Path,
    output_dir: Path,
    well_folders: "list[tuple[str, Path]]",
    shared_kwargs_template: dict,
    nuclear_token: str,
    fluor_tokens: "list[str]",
    workers: int,
    csv_prefix: str,
    compress_input_well_folders: bool = False,
    compress_output_well_folders: bool = False,
    force: bool = False,
    force_cpu: bool = False,
    threads_per_worker: int = 0,
    schema: "list[str] | None" = None,
    sep: str = DEFAULT_SEP,
) -> None:
    """
    Dispatch one worker process per well folder (uncompressed input).

    Each worker: discovers image groups in its well subfolder, processes them,
    writes the CSV, and saves output images to <output_dir>/<well>/.
    Up to *workers* wells are processed in parallel.
    """
    if schema is None:
        schema = parse_schema(DEFAULT_SCHEMA)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Sweep stale staging dirs left by a previous crashed run, but
    # keep any _tmp_images_* dir that holds per-FOV checkpoint markers
    # (v5.2.0) — the matching worker will resume from those.
    for _stale in sorted(output_dir.glob("_tmp_images_*")):
        if not _stale.is_dir():
            continue
        if any(_stale.glob(".fov_*.done")):
            n_done = sum(1 for _ in _stale.glob(".fov_*.done"))
            log.info(
                "Keeping checkpoint dir %s (%d FOV(s) already done)",
                _stale.name, n_done,
            )
            continue
        log.warning("Removing stale staging directory from previous run: %s", _stale.name)
        remove_directory(_stale)

    # Skip already-complete wells unless --force
    n_skipped = 0
    to_process: list[tuple[str, Path]] = []
    for well_label, folder_path in well_folders:
        if not force and output_exists_for_well_folder(
            output_dir,
            well_label,
            csv_prefix,
            compressed_output=compress_output_well_folders,
        ):
            log.info(
                "SKIP well %s — output already exists. Use --force to reprocess.",
                well_label,
            )
            n_skipped += 1
        else:
            to_process.append((well_label, folder_path))

    if not to_process:
        log.info("All wells already complete.")
        log.info("Folder mode complete. %d skipped.", n_skipped)
        return

    _ensure_stardist_runtime_deps()

    log.info("Folder mode: %d well(s) to process, %d worker(s).", len(to_process), workers)

    all_records: list[dict] = []
    all_failed:  list[str]  = []

    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=ctx,
        initializer=_worker_init,
        initargs=(force_cpu, threads_per_worker),
    ) as executor:
        future_to_well = {
            executor.submit(
                process_well_folder_task,
                well_label,
                folder_path,
                output_dir,
                shared_kwargs_template,
                nuclear_token,
                fluor_tokens,
                csv_prefix,
                compress_input_well_folders,
                compress_output_well_folders,
                schema,
                sep,
            ): well_label
            for well_label, folder_path in to_process
        }
        for future in as_completed(future_to_well):
            well_label = future_to_well[future]
            try:
                records, failed = future.result()
                all_records.extend(records)
                all_failed.extend(failed)
            except Exception as exc:           # noqa: BLE001
                _report_worker_failure(well_label, output_dir, exc)
                all_failed.append(well_label)

    log.info("=" * 60)
    log.info("Folder mode complete.")
    log.info(
        "Total wells : %d  |  processed : %d  |  skipped : %d  |  "
        "nuclei : %d  |  failed wells : %d",
        len(well_folders), len(to_process), n_skipped,
        len(all_records), len(all_failed),
    )
    if all_failed:
        log.warning("Failed wells/pairs:")
        for name in all_failed:
            log.warning("  %s", name)

# ---------------------------------------------------------------------------
# pipeline_info.json sidecar
#
# This file is the contract between the analysis pipeline and the
# well-viewer / image-resolver consumers. It is written exclusively by
# this script — see ``main()`` — so that a successful run always pairs
# its outputs with the metadata describing how they were produced.
# ---------------------------------------------------------------------------

PIPELINE_INFO_FILENAME = "pipeline_info.json"


def _pipeline_info_jsonable(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _pipeline_info_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_pipeline_info_jsonable(v) for v in value]
    return value


def _effective_fluor_tokens_for_sidecar(
    fluor_tokens: "list[str]",
    *,
    nuclear_token: str,
    segmentation_method: str,
    cytoplasm_token: str,
) -> "list[str]":
    """Channels that end up with quantified intensity columns in the CSVs."""
    ordered = [str(nuclear_token or "").strip(), *[str(tok or "").strip() for tok in fluor_tokens]]
    if segmentation_method == "stardist_seeded_watershed_cell":
        ordered.append(str(cytoplasm_token or "").strip())
    out: "list[str]" = []
    seen: "set[str]" = set()
    for tok in ordered:
        if not tok:
            continue
        key = tok.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tok)
    return out


def _parse_numeric_token(value: str) -> "float | None":
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _collect_image_stems(input_dir: Path) -> "set[str]":
    stems: "set[str]" = set()
    for pat in ("*.tif", "*.tiff", "*.TIF", "*.TIFF"):
        for p in input_dir.glob(pat):
            stems.add(p.stem)
    try:
        well_dirs = [p for p in input_dir.iterdir() if p.is_dir()]
    except OSError:
        well_dirs = []
    for well_dir in well_dirs:
        for pat in ("*.tif", "*.tiff", "*.TIF", "*.TIFF"):
            for p in well_dir.glob(pat):
                stems.add(p.stem)
    for z in input_dir.glob("*.zip"):
        try:
            with zipfile.ZipFile(z, "r") as zf:
                for member in zf.namelist():
                    name = Path(member).name
                    lower = name.lower()
                    if not lower.endswith((".tif", ".tiff")):
                        continue
                    stems.add(Path(name).stem)
        except Exception:
            continue
    return stems


def _collect_available_schema_values(
    input_dir: Path,
    *,
    filename_schema: str,
    filename_sep: str,
    field_name: str,
    sort_key,
) -> "list[str]":
    fields = [f.strip().lower() for f in filename_schema.split(":")]
    try:
        field_idx = fields.index(field_name)
    except ValueError:
        return []
    values: "set[str]" = set()
    for stem in _collect_image_stems(input_dir):
        parts = stem.split(filename_sep)
        if 0 <= field_idx < len(parts):
            tok = str(parts[field_idx]).strip()
            if tok:
                values.add(tok)
    return sorted(values, key=sort_key)


def _collect_available_timepoints(
    input_dir: Path,
    *,
    filename_schema: str,
    filename_sep: str,
) -> "list[str]":
    def _sort_key(tp: str):
        h = _parse_timepoint_hours(tp)
        return (
            (isinstance(h, float) and h != h),  # NaN sentinel sorts last
            (h if (isinstance(h, float) and h == h) else 0.0),
            tp,
        )
    return _collect_available_schema_values(
        input_dir,
        filename_schema=filename_schema,
        filename_sep=filename_sep,
        field_name="timepoint",
        sort_key=_sort_key,
    )


def _collect_available_fovs(
    input_dir: Path,
    *,
    filename_schema: str,
    filename_sep: str,
) -> "list[str]":
    def _sort_key(fov: str):
        n = _parse_numeric_token(fov)
        return (n is None, n or 0.0, fov)
    return _collect_available_schema_values(
        input_dir,
        filename_schema=filename_schema,
        filename_sep=filename_sep,
        field_name="fov",
        sort_key=_sort_key,
    )


def write_pipeline_info(
    output_dir: Path,
    *,
    input_dir: Path,
    filename_schema: str,
    filename_sep: str,
    nuclear_token: str,
    fluor_tokens: "list[str]",
    smfish_tokens: "list[str]",
    segmentation_method: str,
    cytoplasm_token: str,
    min_nucleus_area_px: int,
    execution_options: "dict | None" = None,
) -> Path:
    """Serialize the run metadata sidecar to ``<output_dir>/pipeline_info.json``."""
    if segmentation_method != "stardist_seeded_watershed_cell":
        cytoplasm_token = ""
    fields = [f.strip() for f in filename_schema.split(":")]
    info = {
        "schema": filename_schema,
        "separator": filename_sep,
        "schema_fields": fields,
        "nuclear_token": str(nuclear_token or ""),
        "well_index": fields.index("well") if "well" in fields else -1,
        "channel_index": fields.index("channel") if "channel" in fields else -1,
        "fov_index": fields.index("fov") if "fov" in fields else -1,
        "tp_index": fields.index("timepoint") if "timepoint" in fields else -1,
        "fluor_tokens": _effective_fluor_tokens_for_sidecar(
            fluor_tokens,
            nuclear_token=nuclear_token,
            segmentation_method=segmentation_method,
            cytoplasm_token=cytoplasm_token,
        ),
        "smfish_tokens": list(smfish_tokens or []),
        "segmentation_method": segmentation_method,
        "cytoplasm_token": cytoplasm_token,
        "min_nucleus_area_px": int(min_nucleus_area_px),
        "available_timepoints": _collect_available_timepoints(
            input_dir, filename_schema=filename_schema, filename_sep=filename_sep,
        ),
        "available_fovs": _collect_available_fovs(
            input_dir, filename_schema=filename_schema, filename_sep=filename_sep,
        ),
        "execution_options": _pipeline_info_jsonable(dict(execution_options or {})),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    p = output_dir / PIPELINE_INFO_FILENAME
    # Preserve user-side blocks that the GUI writes back into this file
    # (cell_gating thresholds, saved selections, ratios, notes). Re-running
    # the pipeline against the same output dir must not clobber them.
    if p.exists():
        try:
            existing = json.loads(p.read_text())
            if isinstance(existing, dict):
                for preserved_key in (
                    "cell_gating",
                    "sample_definitions",
                    "ratios",
                    "notes",
                ):
                    if preserved_key in existing and preserved_key not in info:
                        info[preserved_key] = existing[preserved_key]
        except (OSError, json.JSONDecodeError):
            pass
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(info, indent=2))
        os.replace(tmp, p)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    return p


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Parallel StarDist nuclei segmentation + GFP quantification. "
            "Accepts a flat directory of images, a directory of per-well "
            "zip files (A01.zip … H12.zip), or a directory of per-well "
            "subfolders (A01/ … H12/)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    p.add_argument("--input_dir",  type=Path, required=True,
                   help="Directory of images, per-well zip files, or per-well subfolders")
    p.add_argument("--output_dir", type=Path, default=Path("results"),
                   help="Destination for CSVs, masks, overlays, and output folders/zips")

    p.add_argument("--nuclear_token", default="NIR",
                   help="Channel token identifying nuclear-channel files "
                        "(used for segmentation and quantified like other channels)")
    p.add_argument("--fluor_tokens", nargs="+", default=["GFP"],
                   metavar="TOKEN",
                   help="One or more channel tokens identifying fluorescent "
                        "channels to quantify (e.g. GFP mCherry DAPI). "
                        "Each token produces its own intensity columns in the CSV.")
    p.add_argument(
        "--segmentation_method",
        default=DEFAULT_SEGMENTATION_METHOD,
        choices=("stardist_nuclei", "stardist_seeded_watershed_cell"),
        help=(
            "Segmentation method. 'stardist_nuclei' keeps existing behavior. "
            "'stardist_seeded_watershed_cell' uses StarDist nuclear seeds and "
            "2D watershed on a cytoplasm Otsu mask."
        ),
    )
    p.add_argument(
        "--cytoplasm_token",
        default="",
        help=(
            "Channel token used as cytoplasm image for watershed mode. "
            "Required when --segmentation_method=stardist_seeded_watershed_cell. "
            "In watershed mode this channel is also quantified."
        ),
    )
    p.add_argument(
        "--min_nucleus_area_px",
        type=int,
        default=50,
        help="Minimum StarDist nucleus area (pixels) kept as watershed seeds.",
    )

    p.add_argument("--filename_schema", default=DEFAULT_SCHEMA,
                   help=(
                       "Colon-separated ordered field names describing the "
                       "filename structure.  Each token must be one of: "
                       + ", ".join(SCHEMA_FIELDS) + ".  "
                       "'channel' and 'well' must each appear exactly once.  "
                       "Slots whose content is not needed should be labelled "
                       "'ignore'.  "
                       f"Default: '{DEFAULT_SCHEMA}' matches the convention "
                       "Exp01_NIR_B03_F001_02d04h30m.tif"
                   ))
    p.add_argument("--filename_sep", default=DEFAULT_SEP,
                   help=(
                       "Single character that separates fields in image "
                       f"filenames.  Default: '{DEFAULT_SEP}'"
                   ))

    p.add_argument("--tophat_radius_nir", type=int, default=100,
                   help="Top-hat structuring element radius for the nuclear "
                        "channel (pixels)")
    p.add_argument("--tophat_radius_fluor", type=int, default=100,
                   help="Top-hat structuring element radius applied to every "
                        "fluorescent channel (pixels)")
    p.add_argument("--no_tophat_nir", action="store_true",
                   help="Disable white top-hat on the nuclear channel")
    p.add_argument("--no_tophat_fluor", action="store_true",
                   help="Disable white top-hat on all fluorescent channels")
    p.add_argument("--smfish_tokens", nargs="*", default=[],
                   help="Fluorescent channel tokens containing smFISH data. "
                        "A LoG spot-enhancement kernel is applied after tophat.")

    p.add_argument("--no_save_tophat",   action="store_true",
                   help="Do not save top-hat/smFISH intermediate TIFFs")
    p.add_argument(
        "--compress_input_well_folders",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Folder mode only: after each well finishes, compress "
            "<input_dir>/<well>/ to <input_dir>/<well>.zip and remove "
            "the source folder."
        ),
    )
    p.add_argument(
        "--compress_output_well_folders",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Folder mode only: compress each output well folder to "
            "<output_dir>/<well>_out.zip and remove the folder."
        ),
    )

    p.add_argument("--tf_threads", type=int, default=0,
                   help=(
                       "CPU threads per worker for TensorFlow/BLAS inference. "
                       "0 (default) = auto-select based on cpu_count (always 4). "
                       "Common values: "
                       "4  = default, suits most x86 CPUs (≤32 cores); "
                       "8  = for CPUs with large L3 caches (e.g. EPYC, Xeon Scalable) "
                       "     where inference scales beyond 4 threads; "
                       "16 = for very wide-cache server CPUs where per-image "
                       "     throughput still improves at 16 threads "
                       "(workers = cpu_count // tf_threads in all cases). "
                       "Ignored when running on GPU (--cpu_only not set)."
                   ))
    p.add_argument(
        "--workers",
        type=int,
        default=0,
        help=(
            "Number of parallel worker processes. "
            "0 (default) = auto-select from CPU availability and tf_threads."
        ),
    )

    p.add_argument("--csv_prefix", default="measurements",
                   help="Filename prefix for per-well CSVs (<prefix>_<well>.csv)")

    p.add_argument("--force", action="store_true",
                   help=(
                       "Reprocess all wells even if output already exists. "
                       "By default, any well whose <well>_out.zip and "
                       "<csv_prefix>_<well>.csv are both present is skipped."
                   ))

    p.add_argument("--cpu_only", action="store_true",
                   help=(
                       "Force StarDist to run on CPU even if a GPU is available. "
                       "Sets CUDA_VISIBLE_DEVICES='' in every worker process "
                       "before TensorFlow is initialised, preventing GPU use. "
                       "Useful for debugging, reproducibility, or when sharing "
                       "a GPU with other jobs."
                   ))

    p.add_argument("--no_auto_threshold", action="store_true",
                   help=(
                       "Skip the post-pipeline auto-threshold estimation. "
                       "Used by AllWell's batched remote-execution mode (v5.4.7+) "
                       "to suppress the per-batch threshold pass, which would "
                       "otherwise overwrite the previous batch's defaults using "
                       "incomplete data. The runner re-runs the estimation once "
                       "locally after all batches finish, against the full "
                       "downloaded output dir."
                   ))

    return p

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser    = build_parser()
    args      = parser.parse_args()
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()

    if not input_dir.is_dir():
        parser.error(f"--input_dir does not exist or is not a directory: {input_dir}")

    # ── Schema validation ─────────────────────────────────────────────────────
    schema = parse_schema(args.filename_schema)
    sep    = args.filename_sep
    schema_errors = validate_schema(schema)
    if schema_errors:
        parser.error("Invalid --filename_schema:\n" + "\n".join(f"  {e}" for e in schema_errors))
    if args.min_nucleus_area_px <= 0:
        parser.error("--min_nucleus_area_px must be a positive integer.")
    if args.segmentation_method == "stardist_seeded_watershed_cell":
        if not args.cytoplasm_token:
            parser.error(
                "--cytoplasm_token is required when "
                "--segmentation_method=stardist_seeded_watershed_cell."
            )
        if args.cytoplasm_token == args.nuclear_token:
            parser.error("--cytoplasm_token must be different from --nuclear_token.")
    log.info("Schema   : %s  (sep=%r)", args.filename_schema, sep)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Write the pipeline_info.json sidecar before any image work so the
    # well-viewer / image-resolver have schema metadata available even if
    # processing crashes partway through.
    try:
        info_path = write_pipeline_info(
            output_dir,
            input_dir=input_dir,
            filename_schema=args.filename_schema,
            filename_sep=args.filename_sep,
            nuclear_token=args.nuclear_token,
            fluor_tokens=list(args.fluor_tokens or []),
            smfish_tokens=list(args.smfish_tokens or []),
            segmentation_method=args.segmentation_method,
            cytoplasm_token=args.cytoplasm_token or "",
            min_nucleus_area_px=args.min_nucleus_area_px,
            execution_options=vars(args),
        )
        log.info("Wrote sidecar : %s", info_path)
    except Exception as exc:  # log but don't abort the run
        log.warning("Could not write %s: %s", PIPELINE_INFO_FILENAME, exc)

    # ── Parallelism auto-configuration ────────────────────────────────────────
    # On GPU: TF manages its own scheduling — don't pin threads.
    # On CPU: StarDist inference sweet spot is ~4 threads per inference on
    #   modern x86 (L3 bandwidth saturates past that for typical microscopy
    #   image sizes).  Workers = floor(cpu_count / tf_threads) so every core
    #   is owned by exactly one worker with no contention.
    #   This scales automatically: 8 cores → 2 workers, 16 → 4, 30 → 7, 64 → 16.
    try:
        cpu_count = len(os.sched_getaffinity(0))
    except AttributeError:
        cpu_count = mp.cpu_count()
    # Reserve one core for the main process (orchestration, CSV writing, pool
    # management).  Without this, workers saturate all cores and the OS
    # time-slices the main process in, adding scheduling latency.
    available = max(1, cpu_count - 1)

    # Thread pinning applies whenever workers share CPU cores — regardless of
    # whether --cpu_only is set.  Without pinning, every TF worker tries to use
    # all available cores, causing contention even if no GPU is present.
    if args.tf_threads > 0:
        tf_threads = min(args.tf_threads, available)
    else:
        # Default sweet spot for StarDist is 4 threads, but clamp to the
        # available core budget so a 2-core host doesn't spawn 4 TF
        # threads on 1 reserved core (worse than going single-threaded).
        tf_threads = min(4, available)
    workers            = max(1, available // tf_threads)
    threads_per_worker = tf_threads

    import sys as _sys
    _on_mac = _sys.platform == "darwin"

    if args.cpu_only:
        # Explicit override: CPU regardless of platform
        pass
    elif _on_mac:
        # macOS: tensorflow-metal handles GPU scheduling internally.
        # Use fewer workers (Metal serialises GPU calls anyway) and don't
        # pin as many CPU threads — Metal does its own thread management.
        workers            = max(1, args.tf_threads if args.tf_threads > 0 else 2)
        threads_per_worker = tf_threads
    else:
        # Non-Mac CPU-only default: full worker pool
        workers            = max(1, available // tf_threads)
        threads_per_worker = tf_threads

    workers_override = False
    if args.workers > 0:
        workers_override = True
        workers = max(1, min(args.workers, available))
        if workers != args.workers:
            log.warning(
                "Requested --workers=%d exceeds available CPU budget (%d); using %d.",
                args.workers,
                available,
                workers,
            )

    # Effective device for logging
    if args.cpu_only:
        _device_str = "CPU only (--cpu_only)"
    elif _on_mac:
        _device_str = "Metal GPU (macOS) if tensorflow-metal installed, else CPU"
    else:
        _device_str = "CPU only (non-macOS)"

    log.info("Input    : %s", input_dir)
    log.info("Output   : %s", output_dir)
    log.info("CPUs     : %d total, %d available (1 reserved for main process)",
             cpu_count, available)
    log.info("Device   : %s", _device_str)
    log.info("TF threads/worker : %d  (workers: %d x %d = %d cores)",
             tf_threads, workers, tf_threads, workers * tf_threads)
    if workers_override:
        log.info("Workers override : --workers=%d", args.workers)

    fluor_tokens_for_quant = [args.nuclear_token, *list(args.fluor_tokens)]
    if (
        args.segmentation_method == "stardist_seeded_watershed_cell"
        and args.cytoplasm_token
    ):
        fluor_tokens_for_quant.append(args.cytoplasm_token)
    fluor_tokens_for_quant = _unique_tokens_preserve_order(fluor_tokens_for_quant)

    # Per-channel top-hat flags and radii: one entry per fluor token.
    n_fluor            = len(fluor_tokens_for_quant)
    tophat_fluor       = [not args.no_tophat_fluor] * n_fluor
    tophat_radius_fluor = [args.tophat_radius_fluor] * n_fluor

    log.info("Segmentation method : %s", args.segmentation_method)
    if args.segmentation_method == "stardist_seeded_watershed_cell":
        log.info("Cytoplasm token     : %s", args.cytoplasm_token)
        log.info("Min nucleus area px : %d", args.min_nucleus_area_px)
    log.info("Fluor channels : %s", ", ".join(fluor_tokens_for_quant))
    log.info("Tophat fluor   : radius=%d, enabled=%s",
             args.tophat_radius_fluor, not args.no_tophat_fluor)
    log.info("Save tophat    : %s", not args.no_save_tophat)

    # kwargs forwarded to every process_image_group call.
    shared_kwargs = dict(
        output_dir=output_dir,
        nuclear_token=args.nuclear_token,
        fluor_tokens=fluor_tokens_for_quant,
        tophat_nir=not args.no_tophat_nir,
        tophat_radius_nir=args.tophat_radius_nir,
        tophat_fluor=tophat_fluor,
        tophat_radius_fluor=tophat_radius_fluor,
        save_tophat_images=not args.no_save_tophat,
        schema=schema,
        sep=sep,
        smfish_tokens=args.smfish_tokens,
        segmentation_method=args.segmentation_method,
        cytoplasm_token=args.cytoplasm_token,
        min_nucleus_area_px=args.min_nucleus_area_px,
    )

    # If the selected input is an "in" directory, normalise any loose TIF/TIFF
    # files into per-well folders before selecting processing mode.
    if input_dir.name.lower() == "in":
        organize_loose_tifs_into_well_folders(input_dir, schema=schema, sep=sep)

    # ── Decide mode: zip, folder, or flat ───────────────────────────────────
    well_zips    = find_well_zips(input_dir)
    well_folders = find_well_folders(input_dir) if not well_zips else []

    if well_zips:
        log.info("Zip mode: %d well zip(s) detected.", len(well_zips))
        process_well_zips(
            input_dir=input_dir,
            output_dir=output_dir,
            well_zips=well_zips,
            shared_kwargs_template=shared_kwargs,
            nuclear_token=args.nuclear_token,
            fluor_tokens=fluor_tokens_for_quant,
            workers=workers,
            csv_prefix=args.csv_prefix,
            force=args.force,
            force_cpu=args.cpu_only,
            threads_per_worker=threads_per_worker,
            schema=schema,
            sep=sep,
        )
    elif well_folders:
        log.info("Folder mode: %d well folder(s) detected.", len(well_folders))
        process_well_folders(
            input_dir=input_dir,
            output_dir=output_dir,
            well_folders=well_folders,
            shared_kwargs_template=shared_kwargs,
            nuclear_token=args.nuclear_token,
            fluor_tokens=fluor_tokens_for_quant,
            workers=workers,
            csv_prefix=args.csv_prefix,
            compress_input_well_folders=args.compress_input_well_folders,
            compress_output_well_folders=args.compress_output_well_folders,
            force=args.force,
            force_cpu=args.cpu_only,
            threads_per_worker=threads_per_worker,
            schema=schema,
            sep=sep,
        )
    else:
        log.info("Flat mode: no well zips or folders found, scanning directory directly.")

        # Discover groups once; reuse for both skip detection and processing.
        groups_all = find_image_groups(input_dir, args.nuclear_token, fluor_tokens_for_quant,
                                       schema=schema, sep=sep)
        skip_wells: set[str] = set()
        if not args.force:
            for nuc, _ in groups_all:
                well = parse_filename(nuc, schema=schema, sep=sep)["well"]
                if well and output_exists_for_well_flat(output_dir, well, args.csv_prefix):
                    skip_wells.add(well)
            if skip_wells:
                log.info(
                    "Flat mode: skipping %d already-complete well(s): %s. "
                    "Use --force to reprocess.",
                    len(skip_wells), sorted(skip_wells),
                )

        all_records, failed = run_pipeline_on_directory(
            input_dir=input_dir,
            output_dir=output_dir,
            shared_kwargs=shared_kwargs,
            nuclear_token=args.nuclear_token,
            fluor_tokens=fluor_tokens_for_quant,
            workers=workers,
            skip_wells=skip_wells,
            force_cpu=args.cpu_only,
            threads_per_worker=threads_per_worker,
            groups=groups_all,
            schema=schema,
            sep=sep,
        )
        if not all_records and not failed:
            log.error("No valid image pairs found. Exiting.")
            return
        write_csvs_per_well(all_records, output_dir, args.csv_prefix)
        log.info("=" * 60)
        log.info("Total rows : %d", len(all_records))
        if failed:
            log.warning("%d pair(s) failed:", len(failed))
            for name in failed:
                log.warning("  %s", name)
        log.info("Per-well CSVs written to: %s", output_dir)
        log.info("=" * 60)

    # Auto-threshold pass: now that every well has been processed and the
    # output zips + pipeline_info sidecar are on disk, compute a default
    # ThreshFracOn cut per channel via Otsu on a balanced per-cell /
    # background-pixel distribution. The result lands in
    # ``pipeline_info.json``'s ``cell_gating.thresh_frac_on`` block and the
    # Cell Gating tab picks it up the next time the dataset is loaded.
    #
    # The implementation lives entirely inside this module (see
    # ``_estimate_thresholds_standalone`` above) so the pipeline can produce
    # auto-thresholds even when ``well_viewer`` is not installed — the only
    # third-party dependencies are skimage's threshold_otsu and tifffile's
    # imread, which the rest of the pipeline already uses. Any failure is
    # logged and the pipeline continues — auto-threshold is informational
    # only and the user can always set thresholds manually in the GUI.
    #
    # v5.4.7: skipped when ``--no_auto_threshold`` is set (AllWell's
    # batched remote runner passes this so the per-batch pass doesn't
    # overwrite the prior batch's defaults with incomplete-plate data;
    # the runner re-runs it once locally after all batches drain).
    if args.no_auto_threshold:
        log.info("Auto-threshold skipped (--no_auto_threshold).")
        return
    try:
        thresholds = _estimate_thresholds_standalone(
            output_dir,
            fluor_channels=fluor_tokens_for_quant,
            filename_schema=args.filename_schema,
            filename_sep=args.filename_sep,
            log=log,
        )
        written = _apply_thresholds_to_pipeline_info(output_dir, thresholds, log)
        if written:
            log.info(
                "Auto-threshold defaults written for %d channel(s): %s",
                len(written),
                ", ".join(f"{ch.upper()}={v:.4g}" for ch, v in written.items()),
            )
        else:
            log.info("Auto-threshold pass produced no new defaults.")
    except Exception as exc:
        log.warning("Auto-threshold pass failed: %s", exc)


if __name__ == "__main__":
    main()
