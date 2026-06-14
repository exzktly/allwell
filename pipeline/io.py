"""Atomic output writers for the pipeline (images, CSVs, overlays).

Extracted from ``process_microscopy.py`` so the "write outputs to disk" helpers
live in one cohesive, Qt-free module: atomic image/text writes (tmp → fsync →
rename, so an interrupted worker never leaves partial bytes a later zip-up step
would pack into a corrupt archive), the per-well CSV writers, the completeness
check, and the boundary overlay. ``process_microscopy`` re-exports every name so
its many ``_safe_imwrite`` / ``write_csvs_per_well`` call sites are unchanged.

Depends only on numpy + tifffile/imageio/scikit-image + stdlib — no
``process_microscopy`` internals — so there is no import cycle.
"""

from __future__ import annotations

import logging
import os
import zipfile
from pathlib import Path

import numpy as np
import imageio.v3 as iio
from tifffile import imwrite
from skimage.segmentation import find_boundaries

# Log under the pipeline logger name so worker-log output is unchanged.
log = logging.getLogger("process_microscopy")


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


def read_done_fov_keys(csv_path: "Path | str") -> "set[tuple[str, str]]":
    """Return the ``(fov, timepoint)`` pairs already present in a per-well CSV.

    These are the FOV-groups an incremental run must NOT reprocess. The values
    are read straight from the CSV's ``fov`` / ``timepoint`` columns — the exact
    strings ``parse_filename`` produced when the rows were first written — so a
    freshly-parsed group's ``(fov, timepoint)`` compares equal without any
    re-normalisation. A missing / unreadable / column-less CSV yields an empty
    set (treated as "nothing done yet", i.e. process everything).
    """
    import csv

    path = Path(csv_path)
    if not path.exists():
        return set()
    keys: "set[tuple[str, str]]" = set()
    try:
        with open(path, newline="") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames or "fov" not in reader.fieldnames \
                    or "timepoint" not in reader.fieldnames:
                return set()
            for row in reader:
                keys.add((str(row.get("fov", "")), str(row.get("timepoint", ""))))
    except OSError:
        return set()
    return keys


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

    with _safe_atomic_text_write(path) as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, restval="",
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing_rows)
        writer.writerows(to_add)
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
        try:
            if tmp_zip.exists():
                tmp_zip.unlink()
        except OSError:
            pass
        raise
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
