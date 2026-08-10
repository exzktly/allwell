"""Infer a plate layout from the stage coordinates in an OME-TIFF's metadata.

An OME-TIFF's ``<Plane>`` elements carry ``PositionX`` / ``PositionY`` — where on
the microscope stage each image was taken. That is enough to recover most of what
the converter page otherwise asks the user to type: how many wells were imaged,
how they are arranged, how many fields of view each holds, and the order the
stage visited them.

The separation the inference rests on is physical. Wells on a 96-well plate sit
on a 9 mm pitch, while the fields of view inside one well are hundreds of
micrometres apart — an order of magnitude smaller. So the coordinates along each
axis fall into tight clumps with wide gaps between them, and splitting each axis
at its largest gap recovers the well grid without needing to know the plate's
dimensions, the objective, or the units the file uses (every comparison here is
between distances within one file, so a common scale cancels).

What is deliberately *not* inferred is which physical well the block starts at.
Stage coordinates are relative to the microscope's own origin, which no file
states in plate terms, so the top-left of the imaged block could be any well. The
result therefore describes the block's **shape** and the **relative** visit order;
the page's Origin field places it, exactly as it does for a hand-entered grid.

Qt-free and dependency-free (stdlib only), so it is unit-testable without an
event loop and the page stays a builder.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass, field
from math import isfinite

# A gap has to be this many times the typical spacing along an axis before it is
# read as the step between wells rather than between fields of view. Well pitch
# is ~15x the FOV spacing on a normal plate scan, so this sits far below any real
# separation while staying above the jitter of a stage returning to a position.
_WELL_GAP_RATIO = 3.0

# Coordinates are rounded to this many decimals before grouping, so a stage that
# reports 4499.9999 and 4500.0001 for the same nominal position does not read as
# two positions.
_COORD_DECIMALS = 3

# How much of the coarsest visible spacing counts as stage jitter rather than a
# real step. Two per cent of a 9 mm well pitch is 180 um: an order of magnitude
# below the spacing of two fields of view, and three above a stage's
# repeatability.
_JITTER_FRACTION = 0.02


@dataclass(frozen=True)
class StageLayout:
    """A plate layout read out of stage coordinates.

    ``relative_order`` is the visit order as ``(row, col)`` offsets from the
    block's top-left corner — not well labels, because the coordinates say
    nothing about which physical well that corner is. A caller turns it into
    labels by placing it at an origin.
    """

    n_positions: int
    rows: int
    cols: int
    fovs_per_well: int
    relative_order: list[tuple[int, int]] = field(default_factory=list)
    # "row" when the stage swept along a row before stepping to the next, "col"
    # when it swept down a column. Matches the page's Serpentine control.
    direction: str = "row"
    # True when alternate rows (or columns) run in opposite directions, which is
    # what the page's serpentine preview draws. A raster scan — every row left
    # to right — is not serpentine, and its order has to be given explicitly.
    serpentine: bool = True
    # The well-to-well step in the file's own coordinate units. Carried because
    # it is the conversion factor between stage coordinates and plate cells, and
    # re-deriving it somewhere else would be a second definition of the same
    # number (see :func:`path_in_cells`).
    pitch: float = 0.0

    @property
    def n_wells(self) -> int:
        return self.rows * self.cols


def _distinct(values: list[float], tol: float) -> list[float]:
    """The distinct coordinates along an axis, merging anything within *tol*.

    A stage does not return to a nominal position exactly; it reports a few
    micrometres either side. Without this, that jitter reads as a separate
    coordinate and the gap analysis below sees a scale that is not there.
    """
    unique = sorted({round(float(v), _COORD_DECIMALS) for v in values})
    if not unique or tol <= 0:
        return unique
    out = [unique[0]]
    cluster = [unique[0]]
    for value in unique[1:]:
        if value - cluster[-1] <= tol:
            cluster.append(value)
            out[-1] = sum(cluster) / len(cluster)
        else:
            cluster = [value]
            out.append(value)
    return out


def _axis_scale(values: list[float], tol: float) -> float | None:
    """This axis's candidate for the well-to-well step, or ``None`` if it has none.

    A plate scan's coordinates along an axis carry up to two spacings — between
    the fields of view inside a well, and between the wells themselves. Where
    both are present the second stands out against the first
    (:data:`_WELL_GAP_RATIO`) and its smallest instance is the pitch. Where every
    gap is alike the axis has one spacing, which is a candidate too: the caller
    takes the largest candidate across both axes, since the pitch is the larger
    of the two scales by definition and an axis holding only fields of view must
    not be allowed to set it.
    """
    unique = _distinct(values, tol)
    if len(unique) < 2:
        return None
    gaps = [b - a for a, b in zip(unique[:-1], unique[1:])]
    typical = sorted(gaps)[len(gaps) // 2]
    if typical <= 0:
        return None
    large = [g for g in gaps if g > typical * _WELL_GAP_RATIO]
    return min(large) if large else typical


def _group_axis(values: list[float], pitch: float | None,
                tol: float = 0.0) -> dict[float, int]:
    """Map each distinct coordinate on one axis to its well index along it.

    *pitch* is the well step learned from whichever axis showed one (they share a
    plate, so they share a pitch). Splitting the ambiguous axis at half of it
    keeps a single column of wells from being read as several: the fields inside
    a well are far closer together than that.

    With no pitch anywhere — every gap on both axes alike — the two readings are
    "one well holding a grid of fields" and "a grid of wells holding one field
    each", and the coordinates genuinely cannot separate them. The second is
    taken, because one field per well is the ordinary plate scan and a montage
    spanning a whole plate's pitch is not; the page's own fields override it
    either way.
    """
    unique = _distinct(values, tol)
    if len(unique) < 2:
        return {v: 0 for v in unique}
    if pitch is None:
        return {v: i for i, v in enumerate(unique)}
    groups: dict[float, int] = {unique[0]: 0}
    index = 0
    for prev, cur in zip(unique[:-1], unique[1:]):
        if cur - prev > pitch / 2.0:
            index += 1
        groups[cur] = index
    return groups


def _nearest(unique: list[float], value: float) -> float:
    """The merged coordinate *value* belongs to (see :func:`_distinct`).

    Binary search rather than a scan of every candidate: this runs once per
    imaged position, and a timelapse plate scan holds tens of thousands.
    """
    i = bisect_left(unique, value)
    if i == 0:
        return unique[0]
    if i == len(unique):
        return unique[-1]
    before, after = unique[i - 1], unique[i]
    return before if value - before <= after - value else after


def _well_step(values: list[float], groups: dict[float, int],
               unique: list[float]) -> float | None:
    """Centre-to-centre distance between adjacent well groups on one axis.

    ``None`` when the axis holds a single group, which has no step to measure.
    Taken as the median of the consecutive differences so one irregular column
    — a scan that skipped a well — does not set the scale for the rest.
    """
    centres: dict[int, list[float]] = {}
    for value in values:
        centres.setdefault(groups[_nearest(unique, value)], []).append(value)
    if len(centres) < 2:
        return None
    means = [sum(vs) / len(vs) for _, vs in sorted(centres.items())]
    diffs = sorted(b - a for a, b in zip(means[:-1], means[1:]))
    return diffs[len(diffs) // 2]


def _first_visits(cells: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Each cell once, in the order it was first visited.

    A ``seen`` set rather than the obvious ``c not in cells[:i]``, which is
    quadratic: at 36,000 positions — a 96-well plate at 16 fields over 24
    timepoints — that alone took five seconds, on the UI thread, per keystroke.
    """
    seen: set[tuple[int, int]] = set()
    out: list[tuple[int, int]] = []
    for cell in cells:
        if cell not in seen:
            seen.add(cell)
            out.append(cell)
    return out


def _direction_and_serpentine(cells: list[tuple[int, int]]) -> tuple[str, bool]:
    """Read the sweep axis and whether alternate lines reverse, from the order."""
    first = _first_visits(cells)
    if len(first) < 2:
        return "row", True
    # Whichever coordinate changes on the first well-to-well step is the one the
    # stage swept along.
    direction = "row" if first[0][0] == first[1][0] else "col"
    lines: dict[int, list[int]] = {}
    for r, c in first:
        key, value = (r, c) if direction == "row" else (c, r)
        lines.setdefault(key, []).append(value)
    ascending = [line == sorted(line) for line in lines.values() if len(line) > 1]
    if len(ascending) < 2:
        return direction, True
    # Serpentine iff consecutive lines disagree about their direction.
    serpentine = all(a != b for a, b in zip(ascending[:-1], ascending[1:]))
    return direction, serpentine


def infer_stage_layout(positions) -> StageLayout | None:
    """Read a :class:`StageLayout` out of ``(x, y)`` stage coordinates.

    ``None`` when the coordinates cannot describe a plate scan: fewer than two
    positions, or wells holding different numbers of fields of view (which means
    the gap split found something that is not a well grid, and a guess built on
    it would be worse than no guess at all).
    """
    points = [(float(x), float(y)) for x, y in positions or []]
    # Anything that is not a finite number is not a stage position. A single
    # NaN reaches `pitch`, and `nan <= 0` is False, so it passes every downstream
    # guard and arrives at a painter as a coordinate.
    points = [(x, y) for x, y in points if isfinite(x) and isfinite(y)]
    if len(points) < 2 or len({(x, y) for x, y in points}) < 2:
        # Every plane at the same coordinate — a stage that writes 0,0 on every
        # plane, and they exist — describes no layout at all. Reported as one
        # well of one field it becomes a confident offer to convert a plate into
        # a single well.
        return None

    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    # Jitter tolerance first, from the coarsest structure visible: the widest gap
    # on either axis is at least the well pitch whenever more than one well was
    # imaged, and a fraction of it sits far below the spacing of two fields of
    # view while staying far above a stage's repeatability.
    coarse = max(
        (b - a)
        for axis in (xs, ys)
        for a, b in zip(sorted(set(axis))[:-1], sorted(set(axis))[1:])
    ) if len(set(xs)) > 1 or len(set(ys)) > 1 else 0.0
    tol = coarse * _JITTER_FRACTION
    # One pitch for the plate: the larger candidate across the two axes, since an
    # axis showing only the within-well spacing must not set it.
    scales = [s for s in (_axis_scale(xs, tol), _axis_scale(ys, tol))
              if s is not None]
    pitch = max(scales) if scales else None
    by_x = _group_axis(xs, pitch, tol)
    by_y = _group_axis(ys, pitch, tol)
    ux, uy = sorted(by_x), sorted(by_y)
    cells = [(by_y[_nearest(uy, y)], by_x[_nearest(ux, x)]) for x, y in points]
    # The *step* between wells, which is not the gap `_axis_scale` found: that
    # gap runs from the last field of one well to the first of the next, so it
    # is short by the width of a well's own block of fields. Only a threshold
    # needs the gap; converting stage units to plate cells needs the real
    # centre-to-centre distance, and a 7% error there walks the drawing a whole
    # cell out of place across twelve columns.
    step = _well_step(xs, by_x, ux) or _well_step(ys, by_y, uy) or pitch or 0.0
    if not isfinite(step) or step <= 0:
        return None      # no scale, so nothing downstream can be placed or drawn
    occupied = sorted(set(cells))
    rows = len({r for r, _ in occupied})
    cols = len({c for _, c in occupied})
    if rows * cols != len(occupied):
        return None            # not a filled rectangle — no grid to report

    # Distinct *coordinates* per well, not visits: a timelapse returns to every
    # position once per timepoint, and counting visits would report a well's
    # fields multiplied by the number of timepoints — 384 "fields" for a plate
    # imaged at 16 fields over 24 timepoints.
    seen_here: set[tuple[tuple[int, int], float, float]] = set()
    counts: Counter = Counter()
    for cell, (x, y) in zip(cells, points):
        key = (cell, round(x, _COORD_DECIMALS), round(y, _COORD_DECIMALS))
        if key in seen_here:
            continue
        seen_here.add(key)
        counts[cell] += 1
    per_well = set(counts.values())
    if len(per_well) != 1:
        return None            # uneven fields per well: not a plate scan pattern

    fovs = per_well.pop()
    direction, serpentine = _direction_and_serpentine(cells)
    order = _first_visits(cells)
    return StageLayout(
        n_positions=len(points),
        rows=rows,
        cols=cols,
        fovs_per_well=fovs,
        relative_order=order,
        direction=direction,
        serpentine=serpentine,
        pitch=float(step or 0.0),
    )


def place(layout: StageLayout, origin_row: int, origin_col: int) -> list[str]:
    """The visit order as well labels, with the block's corner at that cell.

    Returns ``[]`` if any position would fall outside the 96-well plate, since a
    suggestion the page cannot accept is worse than none.
    """
    labels: list[str] = []
    for r, c in layout.relative_order:
        row, col = origin_row + r, origin_col + c
        if not (0 <= row < 8 and 0 <= col < 12):
            return []
        labels.append(f"{chr(ord('A') + row)}{col + 1:02d}")
    return labels


def path_in_cells(positions, pitch: float,
                  anchor_row: int, anchor_col: int) -> list[tuple[float, float]]:
    """The acquisition path in **plate cells**, ready to draw over a plate map.

    Returns ``(x, y)`` per imaged position where one unit is one well and
    ``(anchor_col, anchor_row)`` is the centre of the well the user says the scan
    started in. Fractional by design: the fields of view *inside* a well are a
    fraction of a cell apart, and showing where they sit is the point — a plate
    map can only draw the well a position belongs to, never the order the stage
    took within it.

    The anchor comes from the user rather than the file because stage
    coordinates are relative to the microscope's origin (see the module
    docstring): the file fixes the path's shape, the user fixes where it starts.
    """
    points = [(float(x), float(y)) for x, y in positions or []]
    if not points or pitch <= 0:
        return []
    x0, y0 = points[0]
    return [
        ((x - x0) / pitch + anchor_col, (y - y0) / pitch + anchor_row)
        for x, y in points
    ]
