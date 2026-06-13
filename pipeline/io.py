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
