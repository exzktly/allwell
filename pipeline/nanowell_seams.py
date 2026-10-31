"""Give every nanowell the tiles do not hold with margin an image to be counted on.

A nanowell is *fused* when no single tile holds its disk with the measurement margin to spare
(``pipeline.tile_grid.ownership_report``). There are two kinds, and each is given the image it needs so that its
cells are counted like any other nanowell's rather than left without a count:

* an **edge** site lies wholly inside one tile, but within the margin of the union's outer edge. It is measured on
  the tile that holds it (the one whose centre is nearest, ties to the lexically first id), with the thinner margin
  that edge leaves.
* a **blend** site straddles a seam, so no tile holds the whole disk. The blend sites are binned so that each bin's
  sites, with their radius and the surround the rim template and the levelling read, fit inside one tile's extent,
  and each bin becomes one *seam tile*: an image **the size of a real tile**, centred on the bin's sites and blended
  from every registered tile that covers it (``pipeline.tile_register.fused_crop``). The size is the point: the
  segmenter is given whole tiles only, because Cellpose scales each image by its own percentiles, so a small crop
  holding a few nanowells is not segmented as a tile holding a hundred is (tracker L3,
  ``tests/test_nanowell_preview.py``). A seam tile holds what a real tile holds (nanowells, lattice and floor),
  so its percentiles are a tile's. Binning keeps a seam's sites together too, because a site's contrast is the
  median over its same-tile neighbours (``nanowell_run.CONTRAST_NEIGHBOURHOOD_PITCHES``).

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


def is_seam_tile(tile_id) -> bool:
    return isinstance(tile_id, str) and tile_id.startswith(SEAM_PREFIX)


@dataclass
class SeamTiles:
    """What a timepoint's measurement needs to count its fused sites: an owner for every site that can have one,
    the seam tiles' positions and images, and the real tile each fused site is reported under."""
    owners: tuple
    positions: dict = field(default_factory=dict)      # seam tile id -> (left, top) µm
    images: dict = field(default_factory=dict)         # seam tile id -> blended image
    #: seam tile id -> (centre µm, (width, height) µm): how to blend another channel onto the same pixels.
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
            centre, size = frames[tile_id]
            tiles = _covering(centre, size, rectangles)
            images = {t: load(t) for t in tiles}
            cache[tile_id] = None if any(v is None for v in images.values()) else _blend(
                centre, size, {t: (images[t], *rectangles[t][:2]) for t in tiles}, pixel_size_um)[0]
        return cache[tile_id]
    return loader


def _covering(centre, size, rectangles: dict) -> list:
    hw, hh = size[0] / 2, size[1] / 2
    x0, y0, x1, y1 = centre[0] - hw, centre[1] - hh, centre[0] + hw, centre[1] + hh
    return sorted(t for t, (left, top, right, bottom) in rectangles.items()
                  if left < x1 and right > x0 and top < y1 and bottom > y0)


def _pixels(extent_um: float, pixel_size_um: float) -> int:
    return max(int(round(extent_um / pixel_size_um)), 1)


def _blend(centre, size, tiles: dict, pixel_size_um: float) -> tuple[np.ndarray, tuple[float, float]]:
    """``(image, (left, top) µm)``: the *size* ``(width, height)`` µm rectangle about *centre*, as many pixels as a
    tile of that size has, blended from *tiles*; a pixel no tile covers takes the image's median, so the measurement
    downstream sees a flat floor there rather than a NaN."""
    from pipeline.tile_register import fused_crop

    nw, nh = _pixels(size[0], pixel_size_um), _pixels(size[1], pixel_size_um)
    side = max(nw, nh) * pixel_size_um
    blended = fused_crop(centre, side, tiles, pixel_size_um)
    square = np.asarray(blended.crop, dtype=np.float32)
    oy, ox = (square.shape[0] - nh) // 2, (square.shape[1] - nw) // 2
    image = np.ascontiguousarray(square[oy:oy + nh, ox:ox + nw])
    finite = np.isfinite(image)
    if not finite.all():
        image[~finite] = float(np.median(image[finite])) if finite.any() else 0.0
    # fused_crop's origin is the centre of its pixel (0, 0); a tile's position is that pixel's corner.
    left = blended.origin_um[0] - pixel_size_um / 2 + ox * pixel_size_um
    top = blended.origin_um[1] - pixel_size_um / 2 + oy * pixel_size_um
    return image, (float(left), float(top))


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
    site's holding tile, a blend site's seam tile. *surround_um* is how much image a seam tile keeps past each of its
    sites' disks: a bin is narrow enough that its sites, their radius and this surround fit in one tile's extent.
    *pitch_um* bounds a bin from below when the tiles are too small for that (never narrower than a pitch)."""
    owners = list(report.owners)
    out = SeamTiles(())
    radius = diameter_um / 2
    centres = np.asarray(centres, dtype=float)
    for i in report.edge_indices:
        held = _holding_tile(*centres[i], radius, rectangles)
        if held is not None:
            owners[i] = held
            out.reported[i] = held
    if not report.blend_indices or not rectangles:
        out.owners = tuple(owners)
        return out
    # A seam tile is the size of a real tile (the median, so one odd tile does not set it).
    size = (float(np.median([r[2] - r[0] for r in rectangles.values()])),
            float(np.median([r[3] - r[1] for r in rectangles.values()])))
    reach = 2 * (radius + surround_um)
    step = (max(size[0] - reach, pitch_um), max(size[1] - reach, pitch_um))
    bins: dict = {}
    for i in report.blend_indices:
        x, y = centres[i]
        bins.setdefault((math.floor(x / step[0]), math.floor(y / step[1])), []).append(i)
    for n, key in enumerate(sorted(bins)):
        members = bins[key]
        xy = centres[members]
        centre = (float((xy[:, 0].min() + xy[:, 0].max()) / 2), float((xy[:, 1].min() + xy[:, 1].max()) / 2))
        tiles = _covering(centre, size, rectangles)
        if not tiles or any(images.get(t) is None for t in tiles):
            continue
        tile_id = f"{SEAM_PREFIX}{n}"
        image, corner = _blend(centre, size, {t: (images[t], *rectangles[t][:2]) for t in tiles}, pixel_size_um)
        out.images[tile_id] = image
        out.positions[tile_id] = corner
        out.frames[tile_id] = (centre, size)
        for i in members:
            owners[i] = tile_id
            out.reported[i] = _most_covering(*centres[i], radius, rectangles)
    out.owners = tuple(owners)
    return out
