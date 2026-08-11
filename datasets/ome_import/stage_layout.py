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

# How far a well's mean coordinate may sit from a whole number of pitches before
# the coordinates are read as the microscope's own frame rather than the plate's.
# A third of a pitch is 3 mm on a 96-well plate: far wider than any real
# centring error, and far short of the half-pitch at which two wells would be
# equally good answers.
_PLATE_FRAME_TOL = 0.33

# The plate this module reads layouts for. Named because three separate checks
# ask "does this fit?" and a bare 8 and 12 in each is three chances to disagree.
_PLATE_ROWS, _PLATE_COLS = 8, 12


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
    # ``(row, col)`` of the block's top-left well on the plate, when the
    # coordinates are stated in the plate's own frame — A01 at the origin, so
    # that every position lands on a whole number of pitches inside an 8x12
    # grid. Then the wells are arithmetic rather than a shape to be placed.
    # ``None`` when the frame is the microscope's instead (an offset zero,
    # negative coordinates, a block that would run off the plate), which is the
    # only case where the starting well has to come from somewhere else.
    absolute_origin: tuple[int, int] | None = None
    # The well-to-well step in the file's own coordinate units. Carried because
    # it is the conversion factor between stage coordinates and plate cells, and
    # re-deriving it somewhere else would be a second definition of the same
    # number (see :func:`path_in_cells`).
    pitch: float = 0.0

    @property
    def n_wells(self) -> int:
        """How many wells were imaged — not ``rows × cols``.

        The two are the same only for a filled rectangle. A scan shaped like an
        L, or one that skips wells, occupies fewer cells than its extent, and
        reporting the extent overstates the run by every well it never visited.
        """
        return len(set(self.relative_order)) or self.rows * self.cols

    @property
    def filled(self) -> bool:
        """True when every cell of the extent was imaged.

        What decides whether the grid fields can express this block at all: a
        rows × cols sweep visits all of them, so anything sparser has to be
        handed over as an explicit order.
        """
        return self.n_wells == self.rows * self.cols


def _jitter_tolerance(xs: list[float], ys: list[float]) -> float:
    """How far apart two coordinates may be and still be the same place.

    Read from the coarsest structure visible: the widest gap on either axis is
    at least the well pitch whenever more than one well was imaged, and a
    fraction of it sits far below the spacing of two fields of view while
    staying far above a stage's repeatability.

    One definition for the module, because two would disagree. The layout
    inference and the "has the stage come back here?" test are the same question
    asked twice, and a run whose fields group into wells under one tolerance and
    not the other produces a map drawn to a layout it contradicts.
    """
    if len(set(xs)) <= 1 and len(set(ys)) <= 1:
        return 0.0
    coarse = max(
        (b - a)
        for axis in (xs, ys)
        for a, b in zip(sorted(set(axis))[:-1], sorted(set(axis))[1:])
    )
    return coarse * _JITTER_FRACTION


def mirrored(positions, *, flip_x: bool = False,
             flip_y: bool = False) -> list[tuple[float, float]]:
    """*positions* reflected about the Y axis, the X axis, or both.

    A microscope's stage axes are not the plate's. Which way X grows depends on
    how the plate sits in the holder and on where the objective is, so a scan
    that ran left to right across the plate can be written with X *decreasing*
    — and read straight, the block comes out mirrored: the inferred visit order
    walks the wells in the wrong direction, the map draws the run back to front,
    and the conversion then writes each image into the horizontally opposite
    well. Every count is right and nothing on screen looks broken, which is why
    this is a control rather than a guess.

    Not inferred, because nothing in an OME-TIFF says which way the plate faced.
    The coordinates alone cannot tell a scan that ran right-to-left from one
    that ran left-to-right on a plate loaded the other way round; only the user
    knows, and the map is what shows them which they are looking at.

    Reflection, not rotation: negating a coordinate mirrors the run about the
    axis and leaves it there, so the shape, the spacing and the pitch are
    untouched and only the direction of travel changes. Everything downstream
    re-derives itself from these numbers, so nothing else has to know.
    """
    points = [(float(x), float(y)) for x, y in positions or []]
    if not (flip_x or flip_y):
        return points
    return [(-x if flip_x else x, -y if flip_y else y) for x, y in points]


def place_keys(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """One key per point, equal exactly when two images share a stage position.

    The module's single answer to "is this the same place?", because it is asked
    in four separate places — how many fields a well holds, whether the sequence
    repeats and how often, which places the map should draw, and which cell a
    coordinate belongs to — and three of those used plain equality on the
    rounded coordinate. A stage sent back to a nominal position reports it a
    fraction of a micrometre off, so under equality a six-well map imaged at
    four fields over three timepoints reports **twelve** fields per well and no
    timepoints at all, both from the same file.

    Snapping to the merged axis coordinates rather than comparing pairs: the
    same `_distinct` / `_nearest` machinery the well grouping runs on, and
    O(n log n) where the pairwise test is quadratic — a plate timelapse holds
    tens of thousands of positions and this runs on the UI thread.
    """
    if not points:
        return []
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    tol = _jitter_tolerance(xs, ys)
    ux, uy = _distinct(xs, tol), _distinct(ys, tol)
    return [(_nearest(ux, x), _nearest(uy, y)) for x, y in points]


def single_cycle(positions) -> list[tuple[float, float]]:
    """*positions* with every return to a place already imaged dropped.

    A timelapse images the same map once per timepoint, so its coordinates walk
    the same route over and over. Drawn in full that is one map overdrawn N
    times: every dot lands on a dot, every line on a line, and the numbering
    runs to N times the count of real positions while describing the geometry of
    one. So each place is drawn once, at the stage's first arrival, and the
    picture is the map of a single pass.

    Repeats are *dropped* rather than the list cut at the first one, because the
    two ways a file can interleave positions and timepoints put them in
    different places: a run that finishes the map before starting the next
    timepoint repeats nothing until the end, while one that takes every
    timepoint of a position before moving on repeats the file's *second*
    coordinate. Truncating would leave that second kind with a single dot.

    "The same place" is :func:`_jitter_tolerance`, the module's one definition
    of it — not equality. A stage sent back to a nominal position reports
    coordinates a fraction of a micrometre off the ones it reported before, and
    against equality every one of those reads as somewhere new. Neither is it a
    typical *step*: in a file ordered timepoints-within-position, consecutive
    coordinates are that jitter, so the typical step **is** the jitter and a
    tolerance scaled from it finds nothing.
    """
    points = [(float(x), float(y)) for x, y in positions or []
              if isfinite(float(x)) and isfinite(float(y))]
    if len(points) < 2:
        return points
    seen: set[tuple[float, float]] = set()
    kept: list[tuple[float, float]] = []
    for point, place in zip(points, place_keys(points)):
        if place in seen:
            continue
        seen.add(place)
        kept.append(point)
    return kept


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
    smallest = min(gaps)
    if smallest <= 0:
        return None
    # Measured against the *smallest* gap, not the median. The median is the
    # typical gap only while the gaps are all of one scale: an axis holding one
    # within-well step and one between-well step has a median of the larger, so
    # nothing is ever three times it and the two scales read as one.
    large = [g for g in gaps if g > smallest * _WELL_GAP_RATIO]
    # And the smallest instance of whichever scale wins, because a gap spans a
    # whole number of steps and never a fraction of one: a scan that skips wells
    # leaves gaps of two and three pitches beside the ordinary ones, and an
    # average of those is a step no well sits on. Jitter cannot drag the minimum
    # down — `_distinct` merged it before these gaps were measured.
    return min(large) if large else smallest


def _axis_has_wells(values: list[float], tol: float) -> bool:
    """True when this axis shows a well-to-well gap, not just fields of view.

    The distinction the pitch needs: an axis whose gaps are all alike may be a
    row of wells or the fields inside one, and only the other axis can say.
    """
    unique = _distinct(values, tol)
    if len(unique) < 2:
        return False
    gaps = [b - a for a, b in zip(unique[:-1], unique[1:])]
    smallest = min(gaps)
    return smallest > 0 and any(g > smallest * _WELL_GAP_RATIO for g in gaps)


def _pitch(xs: list[float], ys: list[float], tol: float) -> float | None:
    """The well-to-well step, from whichever axis is entitled to say.

    Two failure modes pull in opposite directions, which is why this is not
    simply the larger or the smaller candidate.

    An axis holding *only* fields of view has no well step in it at all, and its
    spacing — an order of magnitude smaller — must never become the pitch. That
    is what taking the larger candidate protected against.

    But a scan that **skips** a well leaves a double gap, and on an axis where
    every step is a skip the smallest gap is two pitches. Taking the larger
    candidate then reads a plate imaged on rows A and C as one whose wells are
    18 mm apart, merging the two rows into one and doubling its field count.

    So an axis that shows a well gap outranks one that does not, and among
    axes that do, the *smallest* wins: a gap can be several pitches wide, never
    a fraction of one.
    """
    scales = {axis: _axis_scale(values, tol)
              for axis, values in (("x", xs), ("y", ys))}
    with_wells = [scale for axis, scale in scales.items()
                  if scale is not None and _axis_has_wells(
                      xs if axis == "x" else ys, tol)]
    if with_wells:
        return min(with_wells)
    candidates = [s for s in scales.values() if s is not None]
    if not candidates:
        return None
    # Neither axis shows a well gap: every step on both is alike. One may still
    # be a well pitch and the other the fields inside a well — an order of
    # magnitude apart — and then the larger is the pitch. Within an order of
    # magnitude they are both well steps, and the smaller is the pitch for the
    # same reason as above.
    low, high = min(candidates), max(candidates)
    return high if high > low * _WELL_GAP_RATIO else low


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

    Indices count **wells, not groups**, so a skipped well leaves a hole. A scan
    of rows A and C used to index them 0 and 1 — the empty row silently closed
    up, the block reported as two adjacent rows, and every image in the second
    one written a row too high. How many wells a gap spans is exactly what the
    pitch is for, so it is used rather than assumed to be one.
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
            # How many wells were stepped over, not merely "the next one".
            # Rounded, so an ordinary step of one pitch is one whether the
            # stage lands a little short or a little long of nominal.
            index += max(1, round((cur - prev) / pitch))
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

    **Per well, not per group.** Two occupied groups two wells apart are two
    pitches apart, so a scan that skips a column measures 18 mm and calls it the
    pitch. That number is the conversion from stage units to plate cells, so the
    drawn map came out compressed by exactly the factor the scan was sparse by,
    which is the same class of error as reading the inter-well gap for the well
    step and an order of magnitude worse. The group indices already count wells
    (:func:`_group_axis`), so the distance is divided by the wells between them.
    """
    centres: dict[int, list[float]] = {}
    for value in values:
        centres.setdefault(groups[_nearest(unique, value)], []).append(value)
    if len(centres) < 2:
        return None
    ordered = sorted((index, sum(vs) / len(vs)) for index, vs in centres.items())
    steps = sorted(
        (b_mean - a_mean) / (b_index - a_index)
        for (a_index, a_mean), (b_index, b_mean) in zip(ordered[:-1], ordered[1:])
        if b_index > a_index
    )
    return steps[len(steps) // 2] if steps else None


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
    tol = _jitter_tolerance(xs, ys)
    pitch = _pitch(xs, ys, tol)
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
    # The block's **extent**, not its populated row and column counts. A scan
    # that skips wells — an L, a plate with two rows left out, a diagonal — is a
    # perfectly ordinary experiment, and the page has expressed arbitrary orders
    # since it grew a clickable plate. Refusing to read one because it is not a
    # filled rectangle threw away the whole layout, including the fields per
    # well and the visit order, which are right whatever the outline is; the
    # user then got no offer at all and no reason for its absence.
    rows = max(r for r, _ in occupied) + 1
    cols = max(c for _, c in occupied) + 1

    # Distinct *places* per well, not visits: a timelapse returns to every
    # position once per timepoint, and counting visits would report a well's
    # fields multiplied by the number of timepoints — 384 "fields" for a plate
    # imaged at 16 fields over 24 timepoints. Distinctness is `place_keys`,
    # never the rounded coordinate: a stage comes back a fraction of a
    # micrometre off, which rounding at three decimals preserves, so every
    # revisit counted as another field and the multiplied number was reported
    # anyway — by the very code whose comment says it must not be.
    seen_here: set[tuple[tuple[int, int], tuple[float, float]]] = set()
    counts: Counter = Counter()
    for cell, place in zip(cells, place_keys(points)):
        key = (cell, place)
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
    origin = _absolute_origin(points, cells, step)
    return StageLayout(
        n_positions=len(points),
        rows=rows,
        cols=cols,
        fovs_per_well=fovs,
        relative_order=order,
        direction=direction,
        serpentine=serpentine,
        pitch=float(step or 0.0),
        absolute_origin=origin,
    )


def place(layout: StageLayout, origin_row: int, origin_col: int) -> list[str]:
    """The visit order as well labels, with the block's corner at that cell.

    Returns ``[]`` if any position would fall outside the 96-well plate, since a
    suggestion the page cannot accept is worse than none.
    """
    labels: list[str] = []
    for r, c in layout.relative_order:
        row, col = origin_row + r, origin_col + c
        if not (0 <= row < _PLATE_ROWS and 0 <= col < _PLATE_COLS):
            return []
        labels.append(f"{chr(ord('A') + row)}{col + 1:02d}")
    return labels


def _absolute_origin(points, cells, step: float,
                     rows: int = _PLATE_ROWS,
                     cols: int = _PLATE_COLS) -> tuple[int, int] | None:
    """Where this block sits on the plate, if the coordinates say so.

    A scanner that reports positions in the *plate's* frame puts A01 at the
    origin, so every well's centre is a whole number of pitches out and the
    well is arithmetic: ``round(y / pitch)``, ``round(x / pitch)``. A scope
    reporting in its own frame has an arbitrary zero, and then the same
    arithmetic is meaningless — so it is only believed when **every** well in
    the block lands on its grid position within a fraction of a pitch and
    inside the plate. Measured on each well's mean coordinate rather than on
    single images, so the fields of view inside a well cannot pull it off.
    """
    if step <= 0 or not isfinite(step):
        return None
    sums: dict[tuple[int, int], list[float]] = {}
    for (r, c), (x, y) in zip(cells, points):
        acc = sums.setdefault((r, c), [0.0, 0.0, 0.0])
        acc[0] += x
        acc[1] += y
        acc[2] += 1
    base: tuple[int, int] | None = None
    for (r, c), (sx, sy, n) in sums.items():
        mx, my = sx / n, sy / n
        ci, ri = round(mx / step), round(my / step)
        if abs(mx - ci * step) > step * _PLATE_FRAME_TOL:
            return None
        if abs(my - ri * step) > step * _PLATE_FRAME_TOL:
            return None
        if not (0 <= ri < rows and 0 <= ci < cols):
            return None
        corner = (ri - r, ci - c)
        if base is None:
            base = corner
        elif corner != base:
            return None      # the block is not rigid on the plate's own grid
    if base is None or base[0] < 0 or base[1] < 0:
        return None
    return base


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


def infer_frame_order(positions) -> tuple[str, int, int] | None:
    """``(frame_order, n_positions, n_timepoints)`` from the coordinate sequence.

    A single-image OME-TIFF packs every stage position into its ``T`` axis,
    because an OME ``<Image>`` has no position dimension — so the file's own
    ``DimensionOrder`` cannot say how positions and timepoints interleave in
    there, and the converter asks the user. The coordinates answer it outright:
    a position visited once is a scan with one timepoint, a sequence that runs
    through every position before repeating is *positions within timepoint*,
    and one that repeats each position consecutively is *timepoints within
    position*.

    ``None`` when the sequence is neither — a partial timelapse, or coordinates
    that do not describe a repeated visit pattern at all.

    Every comparison here is between `place_keys`, not coordinates. A stage
    returns to a position a fraction of a micrometre off the value it reported
    last time, so under equality no visit is ever a repeat: a real timelapse
    reads as ``single`` with as many positions as it has frames, and the page is
    told there is one timepoint in a file holding twelve.
    """
    points = [(float(x), float(y)) for x, y in positions or []]
    if not points:
        return None
    keys = place_keys(points)
    first: list[tuple[float, float]] = []
    seen: set[tuple[float, float]] = set()
    for key in keys:
        if key not in seen:
            seen.add(key)
            first.append(key)
    n = len(first)
    if n == len(points):
        return "single", n, 1
    timepoints, remainder = divmod(len(points), n)
    if remainder or timepoints < 2:
        return None
    if keys[:n] == first:
        return "pos_in_time", n, timepoints
    if all(k == first[i // timepoints] for i, k in enumerate(keys)):
        return "time_in_pos", n, timepoints
    return None
