"""Where each tile of a (well, timepoint) sits, and how much they overlap.

Qt-free; numpy only. Positions come from the dataset's acquisition record
(``util/acquisition_record``) when it has them and from an explicit scan
pattern when it does not. The tile plan changes between experiments, and
possibly between two acquisitions of one dataset, so the grid's shape, the
overlap and the tile size are read for each (well, timepoint) and never carried
from one to another.

A timepoint that does not hold every tile of its grid is **not** placed into a
partial montage: it is reported as ``incomplete_grid`` and names the missing
tiles, so an incremental rerun can process it once the scan is complete.

Units are micrometres throughout; image axes are x right, y down.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from pipeline.nanowell_lattice import OwnershipResult, TileRectangle, assign_owners

OK = "ok"
INCOMPLETE_GRID = "incomplete_grid"
BAD_GRID = "bad_grid"

#: Tiles whose positions differ by less than this fraction of a tile's extent
#: along an axis are in the same row (or column): stage jitter is a few
#: micrometres, a neighbouring tile is ~90 % of an extent away.
_SAME_LINE_FRACTION = 0.25
#: A measured overlap this many percentage points from the declared one is
#: reported: one of the two is wrong about the acquisition.
OVERLAP_TOLERANCE_POINTS = 2.0
#: A step between neighbouring lines this far (as a fraction of a tile's
#: extent) from the median step makes the grid irregular, which is reported
#: because the overlap is then not one number.
_IRREGULAR_FRACTION = 0.1

_CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")


@dataclass(frozen=True)
class RecordedTile:
    """One tile as the acquisition record states it; a position may be None."""
    tile_id: str
    mosaic_xy_px: tuple[float, float] | None = None
    stage_xy_um: tuple[float, float] | None = None


@dataclass(frozen=True)
class TilePlacement:
    tile_id: str
    left_um: float
    top_um: float
    width_um: float
    height_um: float
    source: str                 # "mosaic", "stage" or "pattern"
    row: int
    col: int

    def rectangle(self) -> TileRectangle:
        return TileRectangle(self.tile_id, self.left_um, self.top_um,
                             self.left_um + self.width_um, self.top_um + self.height_um)


@dataclass(frozen=True)
class GridPlan:
    status: str                                     # OK / INCOMPLETE_GRID / BAD_GRID
    reason: str
    placements: tuple[TilePlacement, ...] = ()
    rows: int | None = None
    cols: int | None = None
    missing: tuple[str, ...] = ()                   # "r2c3": row 2, column 3 (from 0)
    #: Overlap between neighbouring tiles along each axis, from the positions.
    #: ``None`` for an axis with a single line of tiles.
    overlap_um: tuple[float | None, float | None] = (None, None)
    overlap_fraction: tuple[float | None, float | None] = (None, None)
    declared_overlap_percent: tuple[float | None, float | None] = (None, None)
    overlap_disagreement: str = ""
    notes: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        """True only for a complete, placed grid: the one a montage is built from."""
        return self.status == OK


# ── scan patterns ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ScanPattern:
    """The order a stage visits a grid: 4 corners x 2 major axes x 2 line modes."""
    start: str = "top-left"
    major: str = "row"          # "row": finish a row, then the next; "column": likewise
    serpentine: bool = False    # alternate lines run backwards

    def __post_init__(self):
        if self.start not in _CORNERS:
            raise ValueError(f"start must be one of {_CORNERS}, not {self.start!r}")
        if self.major not in ("row", "column"):
            raise ValueError(f"major must be 'row' or 'column', not {self.major!r}")


#: All sixteen patterns, in a fixed order.
ALL_PATTERNS = tuple(ScanPattern(c, m, s) for c in _CORNERS
                     for m in ("row", "column") for s in (False, True))


def scan_cells(rows: int, cols: int, pattern: ScanPattern) -> list[tuple[int, int]]:
    """Grid cells ``(row, col)`` in the order *pattern* visits them.

    Row 0 is the top row and column 0 the left column whatever the starting
    corner: the corner only says where the stage starts.
    """
    if rows < 1 or cols < 1:
        raise ValueError("A grid needs at least one row and one column")
    row_order = list(range(rows))[::-1] if pattern.start.startswith("bottom") else list(range(rows))
    col_order = list(range(cols))[::-1] if pattern.start.endswith("right") else list(range(cols))
    outer, inner = (row_order, col_order) if pattern.major == "row" else (col_order, row_order)
    cells = []
    for line, a in enumerate(outer):
        run = inner[::-1] if pattern.serpentine and line % 2 else inner
        cells.extend((a, b) if pattern.major == "row" else (b, a) for b in run)
    return cells


def _cell_name(row: int, col: int) -> str:
    return f"r{row}c{col}"


def _extent_um(tile_size_px, pixel_size_um) -> tuple[float, float] | None:
    sizes = []
    for axis in ("x", "y"):
        size, scale = tile_size_px.get(axis), pixel_size_um.get(axis)
        if (not size or not scale or not math.isfinite(scale) or not math.isfinite(size)
                or scale <= 0 or size <= 0):
            return None
        sizes.append(float(size) * float(scale))
    return tuple(sizes)


def place_from_pattern(tile_ids, rows: int, cols: int, pattern: ScanPattern, *,
                       tile_size_px: dict, pixel_size_um: dict,
                       overlap_percent: float) -> GridPlan:
    """Place tiles listed in acquisition order on a grid the user described.

    This is the path for data with no acquisition record: the grid, the scan
    pattern and a nominal overlap are the user's, and nothing here checks them
    against the images. Fewer tiles than ``rows * cols`` is an incomplete grid
    whose missing cells are the ones the scan had not reached.
    """
    ids = list(tile_ids)
    if rows < 1 or cols < 1:
        return GridPlan(BAD_GRID, f"A {rows} x {cols} grid has no cells")
    extent = _extent_um(tile_size_px, pixel_size_um)
    if extent is None:
        return GridPlan(BAD_GRID, "Tile size or pixel size is missing, so tiles cannot be placed")
    if not 0 <= overlap_percent < 100:
        return GridPlan(BAD_GRID, f"Nominal overlap {overlap_percent!r}% is not in [0, 100)")
    if len(set(ids)) != len(ids):
        return GridPlan(BAD_GRID, "Tile ids are not unique")
    cells = scan_cells(rows, cols, pattern)
    if len(ids) > len(cells):
        return GridPlan(BAD_GRID, f"{len(ids)} tiles do not fit a {rows} x {cols} grid", rows=rows, cols=cols)
    step = tuple(e * (1 - overlap_percent / 100) for e in extent)
    placements = tuple(
        TilePlacement(tile_id, col * step[0], row * step[1], extent[0], extent[1], "pattern", row, col)
        for tile_id, (row, col) in zip(ids, cells))
    missing = tuple(_cell_name(r, c) for r, c in cells[len(ids):])
    measured = (extent[0] - step[0] if cols > 1 else None, extent[1] - step[1] if rows > 1 else None)
    plan = dict(placements=placements, rows=rows, cols=cols, missing=missing,
                overlap_um=measured,
                overlap_fraction=tuple(None if m is None else m / e for m, e in zip(measured, extent)),
                declared_overlap_percent=(overlap_percent, overlap_percent),
                notes=("Placed from the scan pattern and a nominal overlap, not from recorded positions",))
    if missing:
        return GridPlan(INCOMPLETE_GRID,
                        f"{len(missing)} of {len(cells)} tiles are missing: " + ", ".join(missing), **plan)
    return GridPlan(OK, "", **plan)


# ── placement from recorded positions ────────────────────────────────────────
def _lines(values, extent):
    """Group positions into rows/columns; returns (centres, member indices)."""
    members: list[list[int]] = []
    for i in np.argsort(values):
        if members and abs(values[i] - np.mean([values[j] for j in members[-1]])) < _SAME_LINE_FRACTION * extent:
            members[-1].append(int(i))
        else:
            members.append([int(i)])
    return [float(np.mean([values[j] for j in group])) for group in members], members


def _cell_indices(centres, extent):
    """Grid index of each line centre, allowing a whole line to be absent.

    The step is the median of the gaps no larger than 1.5x the smallest, so a
    missing line (a double gap) does not stretch it.
    """
    if len(centres) == 1:
        return [0], None
    gaps = np.diff(centres)
    smallest = gaps.min()
    step = float(np.median(gaps[gaps <= 1.5 * smallest]))
    indices = [int(round((c - centres[0]) / step)) for c in centres]
    return indices, step


def _record_positions(tiles, pixel_size_um, stage_to_image) -> tuple[dict, dict] | GridPlan:
    """Each tile's position in um and where it came from, or the plan that refuses them."""
    positions, sources, unplaced = {}, {}, []
    for tile in tiles:
        if tile.mosaic_xy_px is not None:
            positions[tile.tile_id] = (tile.mosaic_xy_px[0] * pixel_size_um["x"],
                                       tile.mosaic_xy_px[1] * pixel_size_um["y"])
            sources[tile.tile_id] = "mosaic"
        elif tile.stage_xy_um is not None and stage_to_image is not None:
            positions[tile.tile_id] = (tile.stage_xy_um[0] * stage_to_image[0],
                                       tile.stage_xy_um[1] * stage_to_image[1])
            sources[tile.tile_id] = "stage"
        else:
            unplaced.append(tile.tile_id)
    if unplaced:
        why = ("have only a stage position and the stage axes' direction is not stated"
               if any(t.stage_xy_um is not None for t in tiles if t.tile_id in unplaced)
               else "have no recorded position")
        return GridPlan(BAD_GRID, f"Tile(s) {', '.join(sorted(unplaced))} {why}; place them from a scan pattern instead")
    if not positions:
        return GridPlan(BAD_GRID, "The timepoint has no tiles")
    if len(set(sources.values())) > 1:
        return GridPlan(BAD_GRID, "Some tiles are placed from mosaic positions and others from stage "
                                  "positions; the two are in different frames, so they cannot share a grid")
    return positions, sources


@dataclass(frozen=True)
class _Cells:
    """Which grid cell each tile fell in, and the line structure it was read from."""
    row_of: dict
    col_of: dict
    cells: dict
    rows: int
    cols: int
    centres: tuple              # (x line centres, y line centres)
    steps: tuple                # (x step, y step); None for a single line


def _grid_cells(positions, extent) -> _Cells | GridPlan:
    ids = list(positions)
    lines = []
    for axis in (0, 1):
        values = np.array([positions[i][axis] for i in ids])
        centres, members = _lines(values, extent[axis])
        index, step = _cell_indices(centres, extent[axis])
        of = {ids[m]: i for i, group in zip(index, members) for m in group}
        lines.append((centres, step, index, of))
    (x_centres, x_step, col_index, col_of), (y_centres, y_step, row_index, row_of) = lines
    cells = {}
    for tile_id in ids:
        cell = (row_of[tile_id], col_of[tile_id])
        if cell in cells:
            return GridPlan(BAD_GRID, f"Tiles {cells[cell]} and {tile_id} fall in one grid cell "
                                      f"{_cell_name(*cell)}; the recorded positions do not form a grid")
        cells[cell] = tile_id
    return _Cells(row_of, col_of, cells, max(row_index) + 1, max(col_index) + 1,
                  (x_centres, y_centres), (x_step, y_step))


def _missing_cells(found: _Cells, expected_shape) -> tuple[tuple[str, ...], tuple[int, int]] | GridPlan:
    """Names of the cells with no tile, and the grid shape to report."""
    outer = []
    if expected_shape is not None:
        if found.rows > expected_shape[0] or found.cols > expected_shape[1]:
            return GridPlan(BAD_GRID, f"The positions form a {found.rows} x {found.cols} grid, larger than "
                                      f"the expected {expected_shape[0]} x {expected_shape[1]}")
        # Lines are indexed from the first one observed, so a line missing from an outer end
        # cannot be placed: the record does not say which end it was. The cells inside the
        # observed extent are still exact; the absent outer lines are named without a position.
        for count, name in ((expected_shape[0] - found.rows, "row"),
                            (expected_shape[1] - found.cols, "column")):
            if count:
                outer.append(f"{count} {name}(s) at an unknown outer end")
    missing = tuple(_cell_name(r, c) for r in range(found.rows) for c in range(found.cols)
                    if (r, c) not in found.cells) + tuple(outer)
    return missing, (expected_shape if expected_shape is not None else (found.rows, found.cols))


def _overlap_findings(found: _Cells, extent, declared_overlap_percent):
    """Per-axis overlap from the spacing, the notes about it, and any disagreement with the file.

    Returns ``(overlap_um, overlap_fraction, declared_pair, disagreement, notes)``, or a
    refusing :class:`GridPlan` when neighbouring tiles do not touch.
    """
    overlap_um = tuple(None if s is None else extent[a] - s for a, s in enumerate(found.steps))
    overlap_fraction = tuple(None if o is None else o / extent[a] for a, o in enumerate(overlap_um))
    notes = []
    for axis in (0, 1):
        centres, step = found.centres[axis], found.steps[axis]
        if (step is not None and np.any(np.abs(np.diff(centres) - step) > _IRREGULAR_FRACTION * extent[axis])
                and not _only_multiples(np.diff(centres), step, extent[axis])):
            notes.append(f"The tile spacing along {'xy'[axis]} is irregular; the overlap there is the median")
        if overlap_um[axis] is not None and overlap_um[axis] < 0:
            return GridPlan(BAD_GRID, f"Neighbouring tiles along {'xy'[axis]} do not touch (a gap of "
                                      f"{-overlap_um[axis]:.1f} um): a tile is missing from between "
                                      f"them or the plan has no overlap, and either way the tiles "
                                      f"cannot be registered or assembled")
    declared = declared_overlap_percent or {}
    declared_pair = (declared.get("x"), declared.get("y"))
    disagreement = [
        f"along {'xy'[axis]} the positions give {measured * 100:.1f} % overlap but the file declares "
        f"{declared_pair[axis]:g} %"
        for axis, measured in enumerate(overlap_fraction)
        if measured is not None and declared_pair[axis] is not None
        and abs(measured * 100 - declared_pair[axis]) > OVERLAP_TOLERANCE_POINTS]
    return (overlap_um, overlap_fraction, declared_pair,
            ("; ".join(disagreement) + ": one of them is wrong about the acquisition" if disagreement else ""),
            tuple(notes))


def place_from_record(tiles, *, tile_size_px: dict, pixel_size_um: dict,
                      declared_overlap_percent: dict | None = None,
                      expected_shape: tuple[int, int] | None = None,
                      stage_to_image: tuple[float, float] | None = None) -> GridPlan:
    """Place one (well, timepoint)'s tiles from the positions the record holds.

    *tiles* are :class:`RecordedTile`. The mosaic position (pixels, in the
    image's own axes) is preferred. A tile with only a stage position is placed
    from it **only if** *stage_to_image* (the sign of each stage axis relative
    to the image's) is given: a stage axis may run against the image's, and
    guessing the sign would mirror the grid. A tile with neither is named in
    the reason and the grid is not usable.

    *expected_shape* ``(rows, cols)`` is the plan the user or the record's
    other timepoints expect. Without it the grid is inferred from the
    positions, which cannot see a whole outer row or column that is absent.
    """
    tiles = list(tiles)
    extent = _extent_um(tile_size_px, pixel_size_um)
    if extent is None:
        return GridPlan(BAD_GRID, "The record has no tile size or pixel size for this timepoint, "
                                  "so tiles cannot be placed")
    if len({t.tile_id for t in tiles}) != len(tiles):
        return GridPlan(BAD_GRID, "Tile ids are not unique")
    placed = _record_positions(tiles, pixel_size_um, stage_to_image)
    if isinstance(placed, GridPlan):
        return placed
    positions, sources = placed
    found = _grid_cells(positions, extent)
    if isinstance(found, GridPlan):
        return found
    shaped = _missing_cells(found, expected_shape)
    if isinstance(shaped, GridPlan):
        return shaped
    missing, (rows, cols) = shaped
    overlap = _overlap_findings(found, extent, declared_overlap_percent)
    if isinstance(overlap, GridPlan):
        return overlap
    overlap_um, overlap_fraction, declared_pair, disagreement, notes = overlap
    placements = tuple(TilePlacement(i, positions[i][0], positions[i][1], extent[0], extent[1],
                                     sources[i], found.row_of[i], found.col_of[i]) for i in positions)
    plan = dict(placements=placements, rows=rows, cols=cols, missing=missing, overlap_um=overlap_um,
                overlap_fraction=overlap_fraction, declared_overlap_percent=declared_pair,
                overlap_disagreement=disagreement, notes=notes)
    if missing:
        return GridPlan(INCOMPLETE_GRID,
                        f"{len(missing)} of {rows * cols} tiles are missing: " + ", ".join(missing), **plan)
    return GridPlan(OK, "", **plan)


def _only_multiples(gaps, step, extent):
    """True when every gap is a whole multiple of the step (a line is absent)."""
    ratios = gaps / step
    return bool(np.all(np.abs(ratios - np.round(ratios)) * step <= _IRREGULAR_FRACTION * extent))


# ── the record -> tiles ──────────────────────────────────────────────────────
def tiles_from_record(record: dict, well: str, timepoint: str) -> tuple | None:
    """``(tiles, tile_size_px, pixel_size_um, declared_overlap_percent)`` for one
    (well, timepoint) of an acquisition record, or ``None`` if it has none.

    The calibration belongs to the timepoint, not to the dataset: acquisitions
    merged later may differ.
    """
    meta = (record or {}).get("timepoints", {}).get(timepoint)
    if meta is None:
        return None
    tiles = [RecordedTile(t["field"],
                          None if t["mosaic_xy_px"] is None else tuple(t["mosaic_xy_px"]),
                          None if t["stage_xy_um"] is None else tuple(t["stage_xy_um"]))
             for t in record["tiles"] if t["well"] == well and t["timepoint"] == timepoint]
    if not tiles:
        return None
    return (tiles, dict(meta["tile_size_px"]), dict(meta["pixel_size_um"]),
            dict(meta["declared_overlap_percent"]))


# ── ownership (what the measured overlap means for the nanowells) ────────────
@dataclass(frozen=True)
class OwnershipReport:
    owners: tuple[str | None, ...]
    owned: int
    fused: int
    unimaged: int
    #: The overlap a nanowell needs along an axis to lie wholly inside one tile
    #: with *margin* to spare on both sides: ``diameter + 2 * margin``.
    required_overlap_um: float
    holds: tuple[bool | None, bool | None]      # None: that axis has one line of tiles
    #: Sites no tile holds with margin but the tiles together cover. Two kinds,
    #: because they need different work: ``blend_indices`` straddle a seam, so
    #: no single tile holds the whole disk and a crop must be blended from
    #: several; ``edge_indices`` lie wholly inside one tile but within *margin*
    #: of the union's outer edge, so a single tile measures them with a thinner
    #: margin than the rest.
    fused_indices: tuple[int, ...]
    blend_indices: tuple[int, ...]
    edge_indices: tuple[int, ...]
    unimaged_indices: tuple[int, ...]


def _moved(placement: TilePlacement, positions_um: dict | None) -> TileRectangle:
    """The tile's rectangle at its registered position when one is given."""
    if positions_um is None or placement.tile_id not in positions_um:
        return placement.rectangle()
    left, top = positions_um[placement.tile_id]
    return TileRectangle(placement.tile_id, left, top, left + placement.width_um, top + placement.height_um)


def _overlap_of(rectangles) -> tuple[float | None, float | None]:
    """Per-axis overlap of rectangles at their current positions, as :func:`place_from_record` measures it."""
    out = []
    for axis in (0, 1):
        starts = np.array([r.left if axis == 0 else r.top for r in rectangles])
        extent = float(np.median([(r.right - r.left) if axis == 0 else (r.bottom - r.top) for r in rectangles]))
        centres, _ = _lines(starts, extent)
        _, step = _cell_indices(centres, extent)
        out.append(None if step is None else extent - step)
    return out[0], out[1]


def ownership_report(plan: GridPlan, site_xy_um, nanowell_diameter_um: float,
                     margin_um: float, *, positions_um: dict | None = None) -> OwnershipReport:
    """How many sites each ownership class holds, and whether the overlap guarantees it.

    Where the overlap along both axes is at least ``diameter + 2 * margin``,
    no site straddles a seam, so ``blend_indices`` is empty. It does not make
    ``fused`` zero: a site whose disk fits a tile but not with *margin* to spare
    because it is near the union's outer edge is fused too, and is reported as
    an ``edge_indices`` site instead. Where the overlap is too small,
    ``blend_indices`` counts the sites needing a crop blended from several
    tiles (``tile_register.fused_crop``).
    """
    if not plan.usable:
        raise ValueError(f"Ownership needs a usable grid, not {plan.status}: {plan.reason}")
    rectangles = [_moved(p, positions_um) for p in plan.placements]
    result: OwnershipResult = assign_owners(site_xy_um, nanowell_diameter_um, rectangles, margin_um)
    required = nanowell_diameter_um + 2 * margin_um
    overlap_um = plan.overlap_um if positions_um is None else _overlap_of(rectangles)
    holds = tuple(None if o is None else o >= required for o in overlap_um)
    sites = np.asarray(site_xy_um, dtype=float)
    radius = nanowell_diameter_um / 2
    blend, edge = [], []
    for index in result.fused_indices:
        x, y = sites[index]
        whole = any(t.left <= x - radius and x + radius <= t.right
                    and t.top <= y - radius and y + radius <= t.bottom for t in rectangles)
        (edge if whole else blend).append(index)
    return OwnershipReport(result.owners, sum(o is not None for o in result.owners),
                           len(result.fused_indices), len(result.unimaged_indices), required, holds,
                           result.fused_indices, tuple(blend), tuple(edge), result.unimaged_indices)
