"""Give every nanowell the tiles do not hold with margin an image to be counted on.

A nanowell is *fused* when no single tile holds its disk with the measurement margin to spare
(``pipeline.tile_grid.ownership_report``). There are two kinds, and each is given the image it needs so that its
cells are counted like any other nanowell's rather than left without a count:

* an **edge** site lies wholly inside one tile, but within the margin of the union's outer edge. It is measured on
  the tile that holds it (the one whose centre is nearest, ties to the lexically first id), with the thinner margin
  that edge leaves.
* a **blend** site straddles a seam, so no tile holds the whole disk. The blend sites are binned into squares of
  :data:`BIN_PITCHES` lattice pitches, and each bin becomes one *seam tile*: a crop blended from every registered
  tile that covers it (``pipeline.tile_register.fused_crop``), wide enough to carry the surround the rim template
  and the levelling read. Binning rather than one crop per site keeps the seam's neighbours together, because a
  site's contrast is the median over its same-tile neighbours (``nanowell_run.CONTRAST_NEIGHBOURHOOD_PITCHES``).

A seam tile is measured by exactly the code an ordinary tile is: it is added to the timepoint's tile positions and
images under a :data:`SEAM_PREFIX` id, and its fluorescence channels are blended the same way on demand. It never
contributes to the pooled template, which is built from the ordinary tiles' sites alone. The rows keep ``fused``
set either way, so a nanowell measured from a blended crop can still be told apart from one measured on one tile,
and its ``owner`` names a real tile: the one covering most of its disk.

Qt-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

#: The id prefix of a seam tile. A colon never appears in a tile id a plate's file names give.
SEAM_PREFIX = "seam:"
#: The side of the square a seam tile covers, in lattice pitches (before its surround is added).
BIN_PITCHES = 6.0


def is_seam_tile(tile_id) -> bool:
    return isinstance(tile_id, str) and tile_id.startswith(SEAM_PREFIX)


@dataclass
class SeamTiles:
    """What a timepoint's measurement needs to count its fused sites: an owner for every site that can have one,
    the seam tiles' positions and images, and the real tile each fused site is reported under."""
    owners: tuple
    positions: dict = field(default_factory=dict)      # seam tile id -> (left, top) µm
    images: dict = field(default_factory=dict)         # seam tile id -> blended image
    #: seam tile id -> (centre µm, side µm): how to blend another channel onto the same pixels.
    frames: dict = field(default_factory=dict)
    reported: dict = field(default_factory=dict)       # site index -> the real tile a fused site's row names

    def channels(self, channels: dict | None, rectangles: dict, pixel_size_um: float) -> dict | None:
        """*channels* (``name -> f(tile id) -> image | None``) answering a seam tile id with that channel blended on
        the seam tile's pixels; a seam tile one of whose covering tiles has no image of the channel has none."""
        if not channels or not self.frames:
            return channels
        return {name: _blended_loader(load, self.frames, rectangles, pixel_size_um) for name, load in channels.items()}


def _blended_loader(load, frames: dict, rectangles: dict, pixel_size_um: float):
    cache: dict = {}

    def loader(tile_id):
        if not is_seam_tile(tile_id):
            return load(tile_id)
        if tile_id not in cache:
            centre, side = frames[tile_id]
            tiles = _covering(centre, side, rectangles)
            images = {t: load(t) for t in tiles}
            cache[tile_id] = None if any(v is None for v in images.values()) else _blend(
                centre, side, {t: (images[t], *rectangles[t][:2]) for t in tiles}, pixel_size_um)
        return cache[tile_id]
    return loader


def _covering(centre, side: float, rectangles: dict) -> list:
    half = side / 2
    x0, y0, x1, y1 = centre[0] - half, centre[1] - half, centre[0] + half, centre[1] + half
    return sorted(t for t, (left, top, right, bottom) in rectangles.items()
                  if left < x1 and right > x0 and top < y1 and bottom > y0)


def _blend(centre, side: float, tiles: dict, pixel_size_um: float) -> np.ndarray:
    """The square of *side* µm about *centre*, blended from *tiles*; a pixel no tile covers takes the crop's median,
    so the measurement downstream sees a flat floor there rather than a NaN."""
    from pipeline.tile_register import fused_crop

    crop = np.asarray(fused_crop(centre, side, tiles, pixel_size_um).crop, dtype=np.float32)
    finite = np.isfinite(crop)
    if not finite.all():
        crop[~finite] = float(np.median(crop[finite])) if finite.any() else 0.0
    return crop


def _holding_tile(x: float, y: float, radius: float, rectangles: dict):
    """The tile that holds the disk wholly (nearest centre, ties to the lexically first id), or ``None``."""
    holding = [(((left + right) / 2 - x) ** 2 + ((top + bottom) / 2 - y) ** 2, t)
               for t, (left, top, right, bottom) in rectangles.items()
               if left <= x - radius and x + radius <= right and top <= y - radius and y + radius <= bottom]
    return min(holding)[1] if holding else None


def _most_covering(x: float, y: float, radius: float, rectangles: dict):
    """The tile covering most of the disk's bounding square (ties to the lexically first id): the real tile a blend
    site's row names."""
    best = None
    for t in sorted(rectangles):
        left, top, right, bottom = rectangles[t]
        area = max(0.0, min(right, x + radius) - max(left, x - radius)) * max(0.0, min(bottom, y + radius)
                                                                              - max(top, y - radius))
        if area > 0 and (best is None or area > best[0]):
            best = (area, t)
    return None if best is None else best[1]


def rectangles_of(plan, positions: dict) -> dict:
    """``tile id -> (left, top, right, bottom)`` µm of every placed tile at its registered position."""
    out = {}
    for p in plan.placements:
        left, top = positions.get(p.tile_id, (p.left_um, p.top_um))
        out[p.tile_id] = (float(left), float(top), float(left) + p.width_um, float(top) + p.height_um)
    return out


def seam_tiles(report, centres, rectangles: dict, images: dict, diameter_um: float, pitch_um: float,
               surround_um: float, pixel_size_um: float) -> SeamTiles:
    """Owners for *report*'s fused sites (*centres*: every site's centre, µm, the frame *rectangles* are in): an edge
    site's holding tile, a blend site's seam tile. *surround_um* is how far past a bin's outermost site centre its
    seam tile reaches, beyond the site's own radius."""
    owners = list(report.owners)
    out = SeamTiles(())
    radius = diameter_um / 2
    centres = np.asarray(centres, dtype=float)
    for i in report.edge_indices:
        held = _holding_tile(*centres[i], radius, rectangles)
        if held is not None:
            owners[i] = held
            out.reported[i] = held
    bins: dict = {}
    step = max(BIN_PITCHES * pitch_um, diameter_um)
    for i in report.blend_indices:
        x, y = centres[i]
        bins.setdefault((math.floor(x / step), math.floor(y / step)), []).append(i)
    for n, key in enumerate(sorted(bins)):
        members = bins[key]
        xy = centres[members]
        lo, hi = xy.min(axis=0) - radius - surround_um, xy.max(axis=0) + radius + surround_um
        side = float(max(hi - lo))
        centre = (float((lo[0] + hi[0]) / 2), float((lo[1] + hi[1]) / 2))
        tiles = _covering(centre, side, rectangles)
        if not tiles or any(images.get(t) is None for t in tiles):
            continue
        tile_id = f"{SEAM_PREFIX}{n}"
        out.images[tile_id] = _blend(centre, side, {t: (images[t], *rectangles[t][:2]) for t in tiles}, pixel_size_um)
        out.positions[tile_id] = (centre[0] - side / 2, centre[1] - side / 2)
        out.frames[tile_id] = (centre, side)
        for i in members:
            owners[i] = tile_id
            out.reported[i] = _most_covering(*centres[i], radius, rectangles)
    out.owners = tuple(owners)
    return out
