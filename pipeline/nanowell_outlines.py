"""Each counted cell's outline, kept so Review can draw a cell's shape over a nanowell crop, not just its centre.

A nanowell run's cell table records each cell as a position (``well_x_um``, ``well_y_um``), so Review's crops could only
mark a cell with a dot. This module keeps, beside the table, the pixels each counted cell covers, in a form Review can
place on any crop of the well without the run's images: ``nanowells/<well>_outlines.npz``, one file per well.

**Why not the kept cell masks** (``pipeline.nanowell_masks``). Those are a cache: the segmenter's raw labels for one
crop, keyed on a hash of the crop's pixels, with nothing saying where the crop was cut or which counted cell a label
became. They are what a re-run reuses, and they keep that job; Review could only find them by cutting every crop
exactly as the run did and hashing it. The outlines here are the opposite: the *final* labels — after
``segment_sites`` dropped a neighbour's objects and renumbered, after ``nanowell_measure.count_cells`` decided what
counts — one entry per row of the cell table, placed in the well frame. Neither file can stand in for the other.

**What an entry is.** The cell's bounding box on the tile it was counted on, as a bit mask, with the well-frame
position of the box's top-left corner and the tile's pixel size: ``x0_um``, ``y0_um``, ``um_per_px``, the box's
height and width, and the bits (``numpy.packbits`` of the mask, row-major). Tile pixel ``(r, c)`` covers
``[left + c·px, left + (c+1)·px)`` across, the convention ``nanowell_measure.cell_table`` places a cell's centre by,
so an outline and the cell's ``well_x_um`` / ``well_y_um`` agree. A seam tile's cells are placed by the seam tile's
own position, and the seam renumbering (``nanowell_run._renumber_seam_cells``) only changes the label the entry is
filed under, since the entry travels with its row.

**Keyed by the cell table's identity**: ``(timepoint, fov, nucleus_id)``, exactly the columns the per-cell CSV
writes, so Review reaches a cell's outline from the cell's row. While a run is measuring, each entry rides in the
cell columns as a short text (:data:`OUTLINE_COLUMN`, :func:`encode_outlines`), so it survives the timepoint
checkpoint's JSON and stays aligned with its row through every merge; it never reaches the CSV.

**Which entries a file keeps.** The cells of the cell table the run wrote, no more: the timepoints it measured, from
this run, and the timepoints an incremental run kept, from the file it kept them from. A timepoint measured with no
segmenter has no cells and so no entries; a run that leaves no entry at all removes the file rather than leaving one
that describes cells the table no longer holds. A timepoint whose cells were counted before outlines were kept has
none, and Review says so rather than drawing nothing silently.

The file is a plain ``.npz`` of arrays (no pickles): read with ``allow_pickle=False``. Qt-free.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

#: The file's own layout. A file of another version is read as holding no outlines (named in the log).
OUTLINES_VERSION = 1
#: ``nanowells/<well>{OUTLINES_SUFFIX}``.
OUTLINES_SUFFIX = "_outlines.npz"
#: The cell column an outline rides in while a run is measuring (one text per cell row; ``None`` for none).
OUTLINE_COLUMN = "outline"


def outlines_path(nanowell_dir: Path, well: str) -> Path:
    """Where *well*'s outlines are kept in *nanowell_dir* (an output folder's ``nanowells/``)."""
    return Path(nanowell_dir) / f"{well}{OUTLINES_SUFFIX}"


# ── one cell ────────────────────────────────────────────────────────────────
def encode_outlines(labels, cell_labels, pixel_size_um: float, tile_origin_um) -> list[str | None]:
    """The outline text of each of *cell_labels* on *labels* (one tile's label image), in that order.

    A label with no pixel on *labels* gets ``None``. One pass over the tile (``scipy.ndimage.find_objects``) finds
    every box, so the cost is the tile once plus each cell's own box."""
    from scipy import ndimage

    labels = np.asarray(labels)
    wanted = [int(label) for label in cell_labels]
    if not wanted:
        return []
    boxes = ndimage.find_objects(labels, max_label=max(wanted))
    left, top = float(tile_origin_um[0]), float(tile_origin_um[1])
    px = float(pixel_size_um)
    out: list[str | None] = []
    for label in wanted:
        box = boxes[label - 1] if 0 < label <= len(boxes) else None
        if box is None:
            out.append(None)
            continue
        mask = labels[box] == label
        rows, cols = box
        bits = base64.b64encode(np.packbits(mask.ravel()).tobytes()).decode("ascii")
        out.append(f"{left + cols.start * px!r} {top + rows.start * px!r} {px!r} {mask.shape[0]} {mask.shape[1]} "
                   f"{bits}")
    return out


def decode_outline(text: str) -> tuple[float, float, float, np.ndarray]:
    """``(x0_um, y0_um, um_per_px, mask)`` of an outline text made by :func:`encode_outlines`."""
    x0, y0, px, h, w, bits = text.split(" ")
    h, w = int(h), int(w)
    mask = np.unpackbits(np.frombuffer(base64.b64decode(bits), dtype=np.uint8), count=h * w).reshape(h, w)
    return float(x0), float(y0), float(px), mask.astype(bool)


def extend_aligned(into: dict, cells: dict) -> None:
    """Extend *into*'s columns with *cells*' (both column dicts of the cell table), keeping :data:`OUTLINE_COLUMN`
    one entry per ``label``: rows that came without outlines (a checkpoint written before they were kept) get
    ``None`` rather than shifting every later outline onto another cell's row."""
    before = len(into.get("label", ()))
    added = len(cells.get("label", ()))
    for name, values in cells.items():
        into.setdefault(name, []).extend(values)
    if added or OUTLINE_COLUMN in into:
        column = into.setdefault(OUTLINE_COLUMN, [])
        if OUTLINE_COLUMN not in cells:
            column[before:] = [None] * added
        if len(column) < before + added:
            column[:0] = [None] * (before + added - len(column))


# ── the file ────────────────────────────────────────────────────────────────
class Outlines:
    """One well's outlines file, read whole: ``(timepoint, fov, nucleus_id) -> (x0_um, y0_um, um_per_px, mask)``.

    The masks are decoded on demand (:meth:`get`), so reading a file of a long well costs its arrays, not a mask per
    cell."""

    def __init__(self, arrays: dict | None = None):
        arrays = arrays or {}
        self._arrays = arrays
        n = len(arrays.get("label", ()))
        self._index = {(str(arrays["timepoint"][k]), str(arrays["fov"][k]), int(arrays["label"][k])): k
                       for k in range(n)}

    def __len__(self) -> int:
        return len(self._index)

    def timepoints(self) -> set[str]:
        return {key[0] for key in self._index}

    def get(self, timepoint, fov, label) -> tuple[float, float, float, np.ndarray] | None:
        """The outline of the cell the table names ``(timepoint, fov, nucleus_id)``; ``None`` when the file has none."""
        try:
            k = self._index.get((str(timepoint), str(fov), int(label)))
        except (TypeError, ValueError):
            return None
        if k is None:
            return None
        a = self._arrays
        h, w, start = int(a["h"][k]), int(a["w"][k]), int(a["offset"][k])
        stop = start + (h * w + 7) // 8
        mask = np.unpackbits(a["bits"][start:stop], count=h * w).reshape(h, w).astype(bool)
        return float(a["x0_um"][k]), float(a["y0_um"][k]), float(a["um_per_px"][k]), mask

    def entries(self, timepoints) -> list[tuple]:
        """``(timepoint, fov, label, x0_um, y0_um, um_per_px, mask)`` of every entry at one of *timepoints*."""
        wanted = {str(t) for t in timepoints}
        out = []
        for (tp, fov, label) in self._index:
            if tp in wanted:
                out.append((tp, fov, label, *self.get(tp, fov, label)))
        return out


def read_outlines(path: Path) -> Outlines | None:
    """*path*'s outlines; ``None`` when there is no file, and an empty :class:`Outlines` (named in the log) when it
    cannot be read or is of another layout."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        with np.load(path, allow_pickle=False) as data:
            arrays = {name: data[name] for name in data.files}
    except (OSError, ValueError, KeyError) as exc:
        log.warning("The cell outlines kept in %s could not be read (%s: %s); no outline is drawn from it.",
                    path, type(exc).__name__, exc)
        return Outlines()
    version = int(arrays["version"]) if "version" in arrays else None
    if version != OUTLINES_VERSION:
        log.warning("The cell outlines kept in %s are of another layout (version %r, this build reads %d); no "
                    "outline is drawn from it.", path, version, OUTLINES_VERSION)
        return Outlines()
    return Outlines(arrays)


def save_outlines(path: Path, rows) -> int:
    """Write *rows* — ``(timepoint, fov, label, x0_um, y0_um, um_per_px, mask)`` — to *path*, atomically, and
    return how many were written. No rows removes *path* instead, so no file outlives the cells it described."""
    path = Path(path)
    rows = list(rows)
    if not rows:
        if path.is_file():
            path.unlink()
        return 0
    chunks, offsets, at = [], [], 0
    for row in rows:
        packed = np.packbits(np.asarray(row[6], dtype=bool).ravel())
        offsets.append(at)
        chunks.append(packed)
        at += packed.size
    arrays = {
        "version": np.array(OUTLINES_VERSION),
        "timepoint": np.array([str(r[0]) for r in rows]),
        "fov": np.array([str(r[1]) for r in rows]),
        "label": np.array([int(r[2]) for r in rows], dtype=np.int64),
        "x0_um": np.array([float(r[3]) for r in rows]),
        "y0_um": np.array([float(r[4]) for r in rows]),
        "um_per_px": np.array([float(r[5]) for r in rows]),
        "h": np.array([np.shape(r[6])[0] for r in rows], dtype=np.int32),
        "w": np.array([np.shape(r[6])[1] for r in rows], dtype=np.int32),
        "offset": np.array(offsets, dtype=np.int64),
        "bits": np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.uint8),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        np.savez_compressed(fh, **arrays)
    os.replace(tmp, path)
    return len(rows)


def rows_from_cells(cells: dict) -> list[tuple]:
    """The outline rows of a run's cell columns (``timepoint``, ``fov``, ``label`` and :data:`OUTLINE_COLUMN`):
    one per cell that has an outline, in row order."""
    outlines = cells.get(OUTLINE_COLUMN) or []
    labels = cells.get("label") or []
    if len(outlines) != len(labels):
        log.warning("The run's cell outlines do not line up with its cells (%d against %d); none are kept.",
                    len(outlines), len(labels))
        return []
    out = []
    for tp, fov, label, text in zip(cells.get("timepoint", ()), cells.get("fov", ()), labels, outlines):
        if text and label is not None:
            out.append((tp, fov, int(label), *decode_outline(text)))
    return out


def write_well_outlines(path: Path, cells: dict, prior_source: Path | None, kept) -> int:
    """Write the well's outlines file at *path*: this run's cells (*cells*, its cell columns) and, for the timepoints
    an incremental run *kept*, the entries of the file it kept them from (*prior_source*). Returns how many entries
    the file holds."""
    rows = rows_from_cells(cells)
    if kept:
        prior = read_outlines(prior_source) if prior_source is not None else None
        if prior is not None:
            rows = prior.entries(kept) + rows
    return save_outlines(path, rows)
