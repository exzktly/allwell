"""A picture of what the nanowell count's segmenter was given, written beside a well's nanowell tables.

Test detection shows the same thing, but it runs on the machine the app runs on: where Torch cannot load there (a
Windows install whose Torch DLLs fail), it cannot run at all, while the analysis runs on a remote GPU. So the run
itself keeps, for its first measured timepoint, a sample of nanowells as they reached the segmenter — the eight
nearest the array's centre and the eight farthest from it — and writes them as one PNG,
``nanowells/<well>_segmenter_input.png``, which a remote run fetches with the tables.

Each crop is drawn on the 0-1 scale the segmenter was given (its tile's 1st-99th percentiles,
``nanowell_measure.SEGMENTER_PERCENTILES``), so a nanowell the model saw at low contrast is drawn at low contrast,
with what it found outlined in red. The top row is the centre sample, framed blue; the bottom row the edge sample,
framed orange. Qt-free and dependency-free (the PNG is written with ``zlib``), because it runs on the remote worker.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np

#: Nanowells kept from each end of the array, centre and edge.
PER_GROUP = 8
#: Side of one crop in the sheet, in pixels.
CELL_PX = 160
GAP_PX = 6
FRAME_PX = 3
FRAME = {"centre": (40, 110, 230), "edge": (240, 140, 20)}
OUTLINE = (255, 30, 30)
BACKGROUND = (255, 255, 255)
SUFFIX = "_segmenter_input.png"


def inspection_sites(points, indices, n: int = PER_GROUP) -> dict:
    """``{site index: "centre" | "edge"}``: the *n* of *indices* nearest the centre of their *points* and the *n*
    farthest from it (fewer when the array is small, never a site twice)."""
    indices = [int(i) for i in indices]
    if not indices:
        return {}
    pts = np.asarray(points, dtype=float)[indices]
    order = [indices[k] for k in np.argsort(np.hypot(*(pts - pts.mean(axis=0)).T), kind="stable")]
    centre = order[:n]
    edge = [i for i in order[::-1][:n] if i not in centre]
    return {**{i: "centre" for i in centre}, **{i: "edge" for i in edge}}


def sheet(kept: dict) -> np.ndarray | None:
    """The kept crops as one RGB image (two rows: centre, then edge), or ``None`` when nothing was kept."""
    rows = [[v for v in kept.values() if v["kind"] == kind] for kind in ("centre", "edge")]
    rows = [r for r in rows if r]
    if not rows:
        return None
    cols = max(len(r) for r in rows)
    step = CELL_PX + GAP_PX
    out = np.empty((GAP_PX + step * len(rows), GAP_PX + step * cols, 3), np.uint8)
    out[:] = BACKGROUND
    for r, row in enumerate(rows):
        for c, crop in enumerate(row):
            y, x = GAP_PX + r * step, GAP_PX + c * step
            out[y:y + CELL_PX, x:x + CELL_PX] = FRAME[crop["kind"]]
            inner = CELL_PX - 2 * FRAME_PX
            out[y + FRAME_PX:y + FRAME_PX + inner, x + FRAME_PX:x + FRAME_PX + inner] = _render(crop, inner)
    return out


def _render(crop: dict, side: int) -> np.ndarray:
    """One crop, grey on its 0-1 scale with its labels' edges in red, fitted into *side* x *side* (nearest)."""
    image = np.clip(np.nan_to_num(np.asarray(crop["image"], dtype=float), nan=0.0), 0.0, 1.0)
    labels = np.asarray(crop["labels"])
    h, w = image.shape
    scale = side / max(h, w, 1)
    ys = np.minimum((np.arange(side) / scale).astype(int), h - 1)
    xs = np.minimum((np.arange(side) / scale).astype(int), w - 1)
    inside = (np.arange(side)[:, None] < h * scale) & (np.arange(side)[None, :] < w * scale)
    grey = (image[np.ix_(ys, xs)] * 255).astype(np.uint8)
    rgb = np.repeat(grey[..., None], 3, axis=-1)
    lab = labels[np.ix_(ys, xs)]
    edge = np.zeros_like(lab, dtype=bool)
    edge[1:, :] |= lab[1:, :] != lab[:-1, :]
    edge[:, 1:] |= lab[:, 1:] != lab[:, :-1]
    edge &= (lab > 0) | np.roll(lab > 0, 1, axis=0) | np.roll(lab > 0, 1, axis=1)
    rgb[edge] = OUTLINE
    rgb[~inside] = BACKGROUND
    return rgb


def write_png(path: Path, rgb: np.ndarray) -> None:
    """*rgb* (``uint8``, H x W x 3) as a PNG at *path*: written with ``zlib`` alone, so nothing else is needed."""
    rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    body = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(body)
    tmp.replace(path)


def write_sheet(path: Path, inspection) -> bool:
    """Write the sheet for a well's ``(timepoint, kept)`` (``WellOutcome.inspection``) to *path*; ``False`` when
    there is nothing to draw (no timepoint measured, or no count)."""
    if not inspection:
        return False
    image = sheet(inspection[1])
    if image is None:
        return False
    write_png(path, image)
    return True
