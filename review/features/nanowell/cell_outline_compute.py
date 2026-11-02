"""Each counted cell's outline on a nanowell crop, from the run's ``nanowells/<well>_outlines.npz``.

The run keeps, beside the cell table, the pixels each counted cell covers, placed in the well frame
(``pipeline.nanowell_outlines``). A crop knows where its top-left pixel sits in the well frame and its scale
(``image_compute._cells_in``), so a cell's mask is traced (``skimage.measure.find_contours`` at half height, on the
mask padded by one pixel so a cell touching its box's edge still closes) and each traced point is carried from the
cell's tile pixels to the crop's: well µm = the box's corner + (index + ½) × the tile's µm per pixel, then crop index =
(well µm − the crop's corner) ÷ the crop's µm per pixel − ½. The same rule places the cell's dot, so a dot sits
inside its outline, and an outline traced from a montage crop is the same shape at the montage's coarser scale.

A cell is found by the cell table's own identity, ``(timepoint, fov, nucleus_id)``. A well whose run kept no outlines
(one counted before they were kept, or a file that could not be read) answers ``None`` for every cell, and the page
draws those cells as dots and says why (``renderer._cells_clause``). Qt-free.
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from review.data.nanowell_focus_gate import NANOWELL_DIR

if TYPE_CHECKING:
    from pipeline.nanowell_outlines import Outlines

log = logging.getLogger("review")


def well_outlines(data, well: str) -> Outlines | None:
    """*well*'s kept outlines (``pipeline.nanowell_outlines.Outlines``), or ``None`` when the run kept none. Read once
    per file and kept while it is unchanged on disk."""
    out_dir = getattr(data, "out_dir", None)
    if out_dir is None:
        return None
    from pipeline.nanowell_outlines import outlines_path

    path = outlines_path(Path(out_dir) / NANOWELL_DIR, well)
    try:
        stat = path.stat()
    except OSError:
        return None
    return _read(str(path), stat.st_mtime_ns, stat.st_size)


@functools.lru_cache(maxsize=4)
def _read(path: str, _mtime_ns: int, _size: int) -> Outlines | None:
    from pipeline.nanowell_outlines import read_outlines

    return read_outlines(Path(path))


def trace(mask: np.ndarray) -> list[np.ndarray]:
    """The closed boundaries of *mask*, each an ``(N, 2)`` array of ``(column, row)`` in the mask's pixel indices."""
    from skimage.measure import find_contours

    padded = np.pad(np.asarray(mask, dtype=float), 1)
    return [c[:, ::-1] - 1.0 for c in find_contours(padded, 0.5)]


def outline_in_crop(entry, left_um: float, top_um: float, um_per_px: float) -> list[np.ndarray] | None:
    """The boundaries of the outline *entry* — ``(x0_um, y0_um, cell µm per pixel, mask)`` — in the pixel indices of
    a crop whose top-left pixel's corner is at ``(left_um, top_um)`` and whose pixels are *um_per_px* across;
    ``None`` for no entry."""
    if entry is None:
        return None
    x0, y0, cell_px, mask = entry
    out = []
    for line in trace(mask):
        x_um = x0 + (line[:, 0] + 0.5) * cell_px
        y_um = y0 + (line[:, 1] + 0.5) * cell_px
        out.append(np.column_stack([(x_um - left_um) / um_per_px - 0.5, (y_um - top_um) / um_per_px - 0.5]))
    return out


def crop_outlines(outlines, rows, left_um: float, top_um: float, um_per_px: float) -> list | None:
    """One entry per row of *rows* (cell-table rows with ``timepoint``, ``fov`` and ``nucleus_id``): that cell's
    boundaries in the crop, or ``None`` where the run kept no outline of it. ``None`` as a whole when the well has
    no outlines or the table names no cell (no ``fov`` / ``nucleus_id`` columns)."""
    if outlines is None or rows is None or not {"fov", "nucleus_id"} <= set(rows.columns):
        return None
    out = []
    for tp, fov, label in zip(rows["timepoint"], rows["fov"], rows["nucleus_id"]):
        entry = None if label != label else outlines.get(tp, fov, label)        # NaN: a row with no label
        out.append(outline_in_crop(entry, left_um, top_um, um_per_px))
    return out
