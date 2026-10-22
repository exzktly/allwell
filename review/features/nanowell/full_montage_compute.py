"""A well's registered montage at full resolution, drawn from the raw tiles: the Full montage window's pixels.

The run keeps one montage per (well, timepoint), downsampled to 4,096 px on its long side (``image_compute``'s
``read_montage``). That is enough to see where the tiles went and not enough to see a cell, or to point at the
central square of an array whose square the run could not find. This draws the same montage from the input images
themselves, at whatever resolution the view asks for, down to one image pixel per screen pixel and beyond.

**Where each tile goes.** Every tile is placed where the run's registration put it (the grid record's ``tiles``:
``left_um``, ``top_um``, in the well frame), from the phase image of that tile in ``in/<well>.zip``
(``image_compute._member_for``, the member the run read), at the x pixel size, as the pipeline places them
(``nanowell_run.locate_timepoint``). Overlaps are not blended: tiles are drawn in the record's order and a later
tile covers an earlier one where they overlap, so every pixel shown is a pixel of one real image.

**Memory.** A well can be a hundred tiles of 2,048 px; held whole, that is about 800 MB. So each tile is kept as a
pyramid of 2× means: the levels at a quarter of the resolution and coarser are kept for every tile once it is read
(about 1/12 of the tile), and the two finest only within a byte budget (:data:`FINE_BYTES_KEPT`), each level of
each tile separately, never evicting a tile the current view draws.
A view that needs finer levels than are held is drawn from the coarser ones and says which tiles it wants, and the
window reads them on a worker and draws again (:class:`Rendered.wants`).

**Contrast** is one stretch for the whole well (the 1st to 99.5th percentile of every tile's coarse level, as
``tile_register.build_montage`` stretches its montage), so one tile does not look brighter than its neighbour only
because it was stretched alone.

Qt-free; reading tiles is file work, so the window calls :meth:`TileStore.load` on a worker.
"""

from __future__ import annotations

import io
import math
import threading
import zipfile
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from review.features.nanowell.image_compute import _member_for

#: Levels this coarse and coarser (1/4 resolution, 1/8, …) are kept for every tile once it is read.
KEEP_FROM_LEVEL = 2
#: The bytes the two finest levels (full and half resolution) may hold across tiles, beyond those the last view
#: drew. About 30 full-resolution 2,048-px 16-bit tiles.
FINE_BYTES_KEPT = 256 * 1024 * 1024
#: A pyramid stops when its level's longer side would be under this many pixels.
MIN_LEVEL_SIDE = 32
#: The stretch, as ``tile_register.build_montage`` applies it.
STRETCH_PERCENTILES = (1.0, 99.5)


@dataclass(frozen=True)
class PlacedTile:
    tile: str
    member: str              # the archive member holding its phase image
    left_um: float           # well frame
    top_um: float


@dataclass(frozen=True)
class MontageSource:
    """What the window needs to draw one (well, timepoint): the archive, the tiles and where they go."""
    well: str
    timepoint: str
    archive: Path
    pixel_size_um: float
    tiles: tuple[PlacedTile, ...]
    tile_size_um: tuple[float, float]          # width, height
    status: str                                # the grid record's status for this timepoint
    reason: str                                # and its reason, when it has one
    origin_method: str | None

    def extent_um(self) -> tuple[float, float, float, float]:
        """``(left, top, right, bottom)`` of every tile together, in the well frame."""
        w, h = self.tile_size_um
        return (min(t.left_um for t in self.tiles), min(t.top_um for t in self.tiles),
                max(t.left_um for t in self.tiles) + w, max(t.top_um for t in self.tiles) + h)


def montage_source(data, well: str, timepoint: str) -> tuple[MontageSource | None, str]:
    """The full-resolution montage of *well* at *timepoint*, or ``(None, why not)``.

    Needs the grid record's tile placements, the acquisition block's pixel and tile size, and the input archive
    the run read. Each is a reason of its own when it is missing, because they are fixed in different places: a
    timepoint the run could not place has no placements; an output folder copied without ``in/`` has no images.
    """
    block = (data.grids.get(well) or {}).get(timepoint) or {}
    placed = block.get("tiles") or []
    if not placed:
        return None, (f"The grid record has no tile placements for {well} at {timepoint} "
                      f"({block.get('status', 'no record')}), so there is nothing registered to draw.")
    acquisition = (data.acquisition or {}).get(timepoint) or {}
    px = float((acquisition.get("pixel_size_um") or {}).get("x") or 0)
    size = acquisition.get("tile_size_px") or {}
    if px <= 0 or not size.get("x") or not size.get("y"):
        return None, f"The acquisition record gives no pixel size or tile size for {timepoint}."
    if data.in_dir is None:
        return None, "The dataset's in/ folder is not beside its results, so the raw tiles cannot be read."
    archive = Path(data.in_dir) / f"{well}.zip"
    try:
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
    except FileNotFoundError:
        return None, f"{archive.name} is not in {archive.parent}."
    except (OSError, zipfile.BadZipFile) as exc:
        return None, f"{archive.name} could not be read ({exc})."
    tiles, unfound = [], []
    for row in placed:
        member = _member_for(names, data, well, timepoint, str(row["tile"]))
        if member is None:
            unfound.append(str(row["tile"]))
            continue
        tiles.append(PlacedTile(str(row["tile"]), member, float(row["left_um"]), float(row["top_um"])))
    if not tiles:
        return None, f"{archive.name} holds no {data.phase_token} image of any tile of {well} at {timepoint}."
    note = (f" {len(unfound)} tile(s) have no image in {archive.name} and are not drawn: {', '.join(unfound)}."
            if unfound else "")
    return MontageSource(well, timepoint, archive, px, tuple(tiles),
                         (float(size["x"]) * px, float(size["y"]) * px), str(block.get("status") or ""),
                         str(block.get("reason") or "") + note, block.get("origin_method")), ""


def pyramid(image: np.ndarray) -> list[np.ndarray]:
    """``[full, 1/2, 1/4, …]``: the image as it was read (its own dtype, the cheapest to hold), then float32 levels,
    each the 2×2 mean of the one above (an odd edge row is dropped)."""
    levels = [np.asarray(image)]
    while max(levels[-1].shape[:2]) // 2 >= MIN_LEVEL_SIDE:
        a = levels[-1].astype(np.float32, copy=False)
        h, w = a.shape[0] // 2 * 2, a.shape[1] // 2 * 2
        levels.append(a[:h, :w].reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3)))
    return levels


@dataclass
class Rendered:
    image: np.ndarray                 # uint8, rows × columns
    covered: np.ndarray               # bool: a tile was drawn at this pixel
    wants: set = field(default_factory=set)       # (tile, level): finer levels the view needs and does not hold
    unread: set = field(default_factory=set)      # tiles in view never read at all


class TileStore:
    """The tiles of one :class:`MontageSource`, read on demand and kept as pyramids (see the module docstring).

    :meth:`load` is called on a worker; :meth:`render` on the GUI thread. A lock guards the maps, and :meth:`load`
    builds a pyramid before taking it, so the GUI never waits on a decode.
    """

    def __init__(self, source: MontageSource):
        self.source = source
        self._coarse: dict[str, dict[int, np.ndarray]] = {}
        self._fine: OrderedDict[tuple[str, int], np.ndarray] = OrderedDict()     # (tile, level) -> pixels
        self._fine_bytes = 0
        self._in_view: set[str] = set()
        self._stretch: tuple[float, float] | None = None
        self._lock = threading.Lock()

    def _read(self, tile: PlacedTile) -> np.ndarray:
        import tifffile

        with zipfile.ZipFile(self.source.archive) as zf:
            return np.asarray(tifffile.imread(io.BytesIO(zf.read(tile.member))))

    def load(self, names, *, level: int | None = None) -> None:
        """Read *names* (tile ids): their coarse levels and, with *level* finer than those, that level too.
        Raises what reading raises."""
        by_name = {t.tile: t for t in self.source.tiles}
        fine = level is not None and level < KEEP_FROM_LEVEL
        for name in names:
            with self._lock:
                held = name in self._coarse and (not fine or (name, level) in self._fine)
            if held:
                continue
            levels = pyramid(self._read(by_name[name]))
            with self._lock:
                self._coarse[name] = {k: a for k, a in enumerate(levels) if k >= KEEP_FROM_LEVEL or k == len(levels) - 1}
                if fine and level < len(levels) and (name, level) not in self._fine:
                    self._fine[(name, level)] = levels[level]
                    self._fine_bytes += levels[level].nbytes
                self._evict()
                if len(self._coarse) == len(by_name):
                    self._stretch = self._well_stretch()

    def _evict(self) -> None:
        """Let the least recently drawn fine levels go while over :data:`FINE_BYTES_KEPT`, never one of a tile the
        last view drew: evicting what the view needs would have it ask for the level again, and the two would
        trade tiles for ever. One view's needs are bounded by the screen, so this stays bounded too."""
        for key in [k for k in self._fine if k[0] not in self._in_view]:
            if self._fine_bytes <= FINE_BYTES_KEPT:
                break
            self._fine_bytes -= self._fine.pop(key).nbytes

    def loaded(self) -> int:
        with self._lock:
            return len(self._coarse)

    def _well_stretch(self) -> tuple[float, float]:
        values = np.concatenate([levels[max(levels)].ravel() for levels in self._coarse.values()])
        lo, hi = (float(v) for v in np.percentile(values, STRETCH_PERCENTILES))
        return lo, hi if hi > lo else lo + 1.0

    def _best(self, name: str, wanted: int) -> tuple[int, np.ndarray] | None:
        """The finest held level not finer than *wanted*, else the finest held at all."""
        levels = dict(self._coarse.get(name) or {})
        for level in range(KEEP_FROM_LEVEL):
            if (name, level) in self._fine:
                levels[level] = self._fine[(name, level)]
                self._fine.move_to_end((name, level))
        if not levels:
            return None
        usable = [k for k in levels if k >= wanted]
        k = min(usable) if usable else max(levels)
        return k, levels[k]

    def render(self, region_um: tuple[float, float, float, float], width: int, height: int) -> Rendered:
        """The well frame's *region_um* ``(left, top, right, bottom)`` drawn into *width* × *height* pixels.

        Each output pixel takes the value of the source pixel under its centre, from the level whose pixels are
        no larger than one output pixel (so a zoomed-out view averages, and a zoomed-in one shows image pixels
        as blocks). The region's aspect is the caller's to match to the output's.
        """
        left, top, right, bottom = region_um
        um_x, um_y = (right - left) / width, (bottom - top) / height
        px = self.source.pixel_size_um
        factor = min(um_x, um_y) / px                     # image pixels under one output pixel
        wanted = max(0, int(math.floor(math.log2(factor)))) if factor >= 1 else 0
        canvas = np.zeros((height, width), dtype=np.float32)
        covered = np.zeros((height, width), dtype=bool)
        out = Rendered(np.zeros((height, width), dtype=np.uint8), covered)
        xs = left + (np.arange(width) + 0.5) * um_x
        ys = top + (np.arange(height) + 0.5) * um_y
        tw, th = self.source.tile_size_um
        in_view = set()
        with self._lock:
            stretch = self._stretch
            for tile in self.source.tiles:
                cols = np.nonzero((xs >= tile.left_um) & (xs < tile.left_um + tw))[0]
                rows = np.nonzero((ys >= tile.top_um) & (ys < tile.top_um + th))[0]
                if not len(cols) or not len(rows):
                    continue
                in_view.add(tile.tile)
                best = self._best(tile.tile, wanted)
                if best is None:
                    out.unread.add(tile.tile)
                    continue
                level, image = best
                if level > wanted and wanted < KEEP_FROM_LEVEL:
                    out.wants.add((tile.tile, wanted))
                scale = px * 2 ** level
                sx = np.clip(((xs[cols] - tile.left_um) / scale).astype(int), 0, image.shape[1] - 1)
                sy = np.clip(((ys[rows] - tile.top_um) / scale).astype(int), 0, image.shape[0] - 1)
                canvas[np.ix_(rows, cols)] = image[np.ix_(sy, sx)]
                covered[np.ix_(rows, cols)] = True
            self._in_view = in_view
        if stretch is None and covered.any():
            stretch = (float(np.percentile(canvas[covered], STRETCH_PERCENTILES[0])),
                       float(np.percentile(canvas[covered], STRETCH_PERCENTILES[1])))
        if stretch is not None:
            lo, hi = stretch
            scaled = np.clip((canvas - lo) / max(hi - lo, 1e-9), 0.0, 1.0) * 255.0
            out.image = np.where(covered, scaled, 0).astype(np.uint8)
        return out


def fit_region(extent, width: int, height: int, *, margin: float = 0.02) -> tuple[float, float, float, float]:
    """The region that shows all of *extent* in a *width* × *height* view, centred, keeping its aspect."""
    left, top, right, bottom = extent
    span = max((right - left) / width, (bottom - top) / height) * (1 + 2 * margin)
    cx, cy = (left + right) / 2, (top + bottom) / 2
    return cx - span * width / 2, cy - span * height / 2, cx + span * width / 2, cy + span * height / 2


def zoom_region(region, factor: float, at_um: tuple[float, float]):
    """*region* scaled by *factor* (under 1 zooms in) about the well-frame point *at_um*, which stays put."""
    left, top, right, bottom = region
    x, y = at_um
    return (x - (x - left) * factor, y - (y - top) * factor, x + (right - x) * factor, y + (bottom - y) * factor)


def to_well(region, width: int, height: int, px: float, py: float) -> tuple[float, float]:
    """The well-frame point under view pixel ``(px, py)`` (continuous; pixel *i* covers ``[i, i + 1)``)."""
    left, top, right, bottom = region
    return left + px * (right - left) / width, top + py * (bottom - top) / height


def to_view(region, width: int, height: int, x_um: float, y_um: float) -> tuple[float, float]:
    """The view pixel position of the well-frame point ``(x_um, y_um)``; the inverse of :func:`to_well`."""
    left, top, right, bottom = region
    return (x_um - left) * width / (right - left), (y_um - top) * height / (bottom - top)


# ── what is drawn over the pixels ────────────────────────────────────────────
@dataclass(frozen=True)
class SiteOverlay:
    x_um: float
    y_um: float
    radius_um: float
    label: str                 # ``q,r`` as Test detection writes it; "" for a site with no address
    state: str


def site_overlays(metrics, well: str, timepoint: str) -> list[SiteOverlay]:
    """The nanowells of *well* at *timepoint*, at their measured well-frame positions, from the page's metrics."""
    if metrics is None or not len(metrics):
        return []
    rows = metrics[(metrics["well"] == well) & (metrics["timepoint"] == timepoint)]
    rows = rows[rows["well_x_um"].notna() & rows["well_y_um"].notna()]
    out = []
    for row in rows.itertuples(index=False):
        q, r = getattr(row, "nw_q", None), getattr(row, "nw_r", None)
        addressed = q is not None and r is not None and not (math.isnan(q) or math.isnan(r))
        out.append(SiteOverlay(float(row.well_x_um), float(row.well_y_um), float(getattr(row, "radius_um", 0) or 0),
                               f"{int(q)},{int(r)}" if addressed else "", str(getattr(row, "state", "") or "")))
    return out


def anchors_file(data) -> Path | None:
    """The dataset's anchors file (``util.nanowell_anchors``), or ``None`` without an ``in/`` folder to keep it in."""
    from util.nanowell_anchors import anchors_path

    return None if data.in_dir is None else anchors_path(data.in_dir)


def read_well_anchor(data, well: str, timepoint: str) -> tuple[tuple[float, float] | None, str]:
    """``(position, "")`` of the anchor set for *well* at *timepoint*, ``(None, "")`` without one, or
    ``(None, why)`` when the file cannot be used."""
    from util.nanowell_anchors import AnchorsFileError, read_anchors, well_anchors

    path = anchors_file(data)
    if path is None:
        return None, ""
    try:
        return well_anchors(read_anchors(path), well).get(timepoint), ""
    except AnchorsFileError as exc:
        return None, str(exc)


def anchored_wells(data) -> list[str]:
    """The wells with an anchor set at any timepoint: what a re-run to use them is limited to."""
    from util.nanowell_anchors import AnchorsFileError, read_anchors

    path = anchors_file(data)
    try:
        return sorted({well for well, _tp in read_anchors(path)}) if path is not None else []
    except AnchorsFileError:
        return []


def status_text(source: MontageSource, anchor, anchor_problem: str = "") -> str:
    """What the window says about this timepoint: how it was addressed (or why not) and its anchor."""
    from util.nanowell_anchors import origin_phrase

    head = f"{source.well} at {source.timepoint}: {source.status or 'no status'}"
    if source.origin_method:
        head += f", origin from {origin_phrase(source.origin_method)}"
    lines = [head + (f". {source.reason}" if source.reason else ".")]
    if anchor_problem:
        lines.append(f"The anchors file cannot be used: {anchor_problem}")
    elif anchor is not None:
        used = source.origin_method == "manual"
        lines.append(f"Anchor set by hand at ({anchor[0]:.1f}, {anchor[1]:.1f}) µm"
                     + ("; this run used it." if used else
                        "; not used yet: re-run the well in Analyze to address it from the anchor."))
    elif source.status == "unaddressed":
        lines.append("Turn on Set anchor and click the central square to anchor this well; the other timepoints "
                     "are then addressed from it, through the rest of the plate, when the well is re-run.")
    return "\n".join(lines)
