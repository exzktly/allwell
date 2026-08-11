"""Reading a plate layout out of an OME-TIFF's stage coordinates.

The inference in ``datasets.ome_import.stage_layout`` rests on one physical
fact: wells sit on a 9 mm pitch while the fields of view inside a well are a few
hundred micrometres apart. These fixtures are built at those real scales, in the
shapes a plate scan actually comes in — serpentine and raster, one field per well
and many, single rows and single columns, with and without stage jitter.
"""

from __future__ import annotations

import random
from math import isfinite

from datasets.ome_import.stage_layout import (
    StageLayout,
    infer_frame_order,
    infer_stage_layout,
    mirrored,
    path_in_cells,
    place,
    single_cycle,
)

PITCH = 9000.0    # 9 mm well pitch, in micrometres
FOV = 600.0       # spacing of two fields of view inside one well


def _scan(rows, cols, fovs, *, serpentine=True, sweep="row", jitter=0.0, side=None):
    """Stage coordinates for one scan, plus the (row, col) visit order it encodes."""
    rng = random.Random(7)
    points: list[tuple[float, float]] = []
    order: list[tuple[int, int]] = []
    outer = range(rows) if sweep == "row" else range(cols)
    for i, a in enumerate(outer):
        inner = list(range(cols) if sweep == "row" else range(rows))
        if serpentine and i % 2:
            inner.reverse()
        for b in inner:
            r, c = (a, b) if sweep == "row" else (b, a)
            order.append((r, c))
            per_line = side or (int(fovs ** 0.5) or 1)
            for f in range(fovs):
                points.append((
                    c * PITCH + (f % per_line) * FOV + rng.uniform(-jitter, jitter),
                    r * PITCH + (f // per_line) * FOV + rng.uniform(-jitter, jitter),
                ))
    return points, order


def test_serpentine_block_with_several_fields_per_well():
    points, order = _scan(2, 3, 4)
    layout = infer_stage_layout(points)
    assert (layout.rows, layout.cols, layout.fovs_per_well) == (2, 3, 4)
    assert layout.direction == "row"
    assert layout.serpentine
    assert layout.relative_order == order
    assert layout.n_positions == 24


def test_one_field_per_well_is_not_read_as_one_well_of_many_fields():
    """The ambiguous case: with one field per well every gap is the well pitch,
    so there is no second scale to find. A grid of wells is the reading that
    matches an ordinary plate scan."""
    points, order = _scan(3, 4, 1, serpentine=False)
    layout = infer_stage_layout(points)
    assert (layout.rows, layout.cols, layout.fovs_per_well) == (3, 4, 1)
    assert layout.relative_order == order
    assert not layout.serpentine          # a raster, and it says so


def test_a_full_plate_one_field_per_well():
    points, _ = _scan(8, 12, 1)
    layout = infer_stage_layout(points)
    assert (layout.rows, layout.cols, layout.fovs_per_well) == (8, 12, 1)


def test_a_single_row_of_wells_keeps_its_fields_together():
    """Only one row, so the Y axis carries the within-well spacing and nothing
    else. Taking it as a well step would report two rows of one field each."""
    points, order = _scan(1, 6, 2, side=1)      # the two fields stacked in Y
    layout = infer_stage_layout(points)
    assert (layout.rows, layout.cols, layout.fovs_per_well) == (1, 6, 2)
    assert layout.relative_order == order


def test_a_single_column_of_wells_keeps_its_fields_together():
    points, _ = _scan(2, 1, 3)
    layout = infer_stage_layout(points)
    assert (layout.rows, layout.cols, layout.fovs_per_well) == (2, 1, 3)


def test_a_column_sweep_is_reported_as_one():
    points, order = _scan(4, 2, 9, sweep="col")
    layout = infer_stage_layout(points)
    assert (layout.rows, layout.cols, layout.fovs_per_well) == (4, 2, 9)
    assert layout.direction == "col"
    assert layout.relative_order == order


def test_stage_jitter_does_not_multiply_the_positions():
    """A stage reports a few micrometres either side of a nominal position.
    Read literally, each visit becomes its own coordinate and the grid explodes."""
    for jitter in (5.0, 20.0):
        points, order = _scan(6, 4, 4, jitter=jitter)
        layout = infer_stage_layout(points)
        assert (layout.rows, layout.cols, layout.fovs_per_well) == (6, 4, 4), jitter
        assert layout.relative_order == order, jitter


def test_nothing_is_inferred_from_coordinates_that_are_not_a_grid():
    """A wrong layout quietly entered into the form is worse than no layout."""
    assert infer_stage_layout([]) is None
    assert infer_stage_layout([(0.0, 0.0)]) is None
    points, _ = _scan(2, 3, 4)
    assert infer_stage_layout(points[:-1]) is None          # one well short a field
    ragged = [(0.0, 0.0), (600.0, 0.0), (9000.0, 0.0), (0.0, 9000.0)]
    assert infer_stage_layout(ragged) is None               # not a filled rectangle


def test_place_puts_the_block_at_an_origin_and_refuses_to_overflow():
    points, _ = _scan(2, 3, 4)
    layout = infer_stage_layout(points)
    assert place(layout, 0, 0) == ["A01", "A02", "A03", "B03", "B02", "B01"]
    assert place(layout, 1, 1) == ["B02", "B03", "B04", "C04", "C03", "C02"]
    # Off the plate: no labels rather than invented ones.
    assert place(layout, 7, 10) == []


def test_n_wells_is_the_block_area():
    assert StageLayout(n_positions=8, rows=2, cols=4, fovs_per_well=1).n_wells == 8


# ── the path, in plate cells ─────────────────────────────────────────────────


def test_path_in_cells_anchors_the_first_position_at_the_chosen_well():
    points, _ = _scan(1, 2, 4)
    layout = infer_stage_layout(points)
    cells = path_in_cells(points, layout.pitch, 1, 1)      # anchor B02

    assert cells[0] == (1.0, 1.0)                          # centre of B02
    # The four fields inside the first well sit a fraction of a cell apart —
    # 600 um against a 9 mm pitch — which is the whole point of the drawing: a
    # plate map puts all four in the same cell and can say nothing about them.
    assert round(cells[1][0] - cells[0][0], 4) == round(FOV / PITCH, 4)
    assert all(abs(x - 1.0) < 0.5 and abs(y - 1.0) < 0.5 for x, y in cells[:4])
    # The second well is exactly one cell along.
    assert cells[4] == (2.0, 1.0)


def test_path_in_cells_is_empty_without_a_usable_scale():
    points, _ = _scan(2, 2, 4)
    assert path_in_cells(points, 0.0, 0, 0) == []
    assert path_in_cells([], PITCH, 0, 0) == []


def test_the_inferred_pitch_is_the_well_step_not_the_gap_between_wells():
    """Centre to centre, which is what converts stage units into plate cells.

    The gap the grouping threshold uses runs from the *last* field of one well
    to the *first* of the next, so it is short by the width of a well's own
    block of fields — 8400 against a 9000 pitch here. Drawn at that scale the
    path shrinks 7% per column and lands a whole cell out of place across a
    twelve-column plate.
    """
    for fovs in (1, 4, 9):
        layout = infer_stage_layout(_scan(2, 3, fovs)[0])
        assert round(layout.pitch, 3) == PITCH, fovs


def _revisited(points, cycles, *, wobble=0.31, interleave="pos_in_time"):
    """*points* imaged *cycles* times, as a stage that does not repeat exactly.

    The real mess this whole family of tests is about: a stage sent back to a
    nominal position reports it a fraction of a micrometre off what it reported
    before, so a fixture built as ``once * 12`` — every repeat identical to the
    original — passes any test written against plain equality while a real file
    fails it. Both interleavings, because a file may finish the map before
    starting the next timepoint or take every timepoint of a position first.
    """
    def moved(point, cycle):
        x, y = point
        return (x + wobble * ((cycle % 3) - 1), y - (wobble * 0.7) * (cycle % 2))

    if interleave == "pos_in_time":
        return [moved(p, t) for t in range(cycles) for p in points]
    return [moved(p, t) for p in points for t in range(cycles)]


def test_a_timelapse_counts_fields_not_visits():
    """A timelapse returns to every position once per timepoint. Counting
    visits reports a well's fields multiplied by the timepoint count — 48
    "fields per well" for a plate imaged at 4 fields over 12 timepoints."""
    once, _ = _scan(2, 3, 4)
    repeated = once * 12
    layout = infer_stage_layout(repeated)
    assert layout.fovs_per_well == 4
    assert (layout.rows, layout.cols) == (2, 3)
    assert layout.n_positions == len(repeated)     # every image still counted


def test_a_jittered_timelapse_counts_fields_not_visits():
    """The same count, on repeats that are not bit-identical to the original.

    The test above builds its timelapse as ``once * 12``, so every revisit is
    the same float as the first visit and an equality test passes. A stage does
    not do that, and against the real thing the field count came out multiplied
    by the timepoints — the multiplied number the test above exists to forbid,
    reported anyway."""
    once, _ = _scan(2, 3, 4)
    for interleave in ("pos_in_time", "time_in_pos"):
        layout = infer_stage_layout(
            _revisited(once, 12, interleave=interleave))
        assert layout.fovs_per_well == 4, interleave
        assert (layout.rows, layout.cols) == (2, 3), interleave


def test_single_cycle_keeps_the_first_visit_to_each_place():
    """The map is drawn once. Every dot of a twelve-timepoint run lands on the
    dot of the first timepoint, so drawing all of them is one map overdrawn
    twelve times, numbered to twelve times its real length."""
    once, _ = _scan(2, 3, 4)
    assert single_cycle(once) == once              # nothing repeats: untouched

    for interleave in ("pos_in_time", "time_in_pos"):
        drawn = single_cycle(_revisited(once, 12, interleave=interleave))
        assert len(drawn) == len(once), interleave
        # The order is the stage's, and the coordinates are the ones the file
        # gave for that first visit — not a rounded or averaged stand-in.
        for (dx, dy), (ox, oy) in zip(drawn, once):
            assert abs(dx - ox) < 1.0 and abs(dy - oy) < 1.0, interleave


def test_single_cycle_survives_what_is_not_a_timelapse():
    assert single_cycle([]) == []
    assert single_cycle([(1.0, 2.0)]) == [(1.0, 2.0)]
    # Every plane at one coordinate: one place, drawn once.
    assert single_cycle([(5.0, 5.0)] * 9) == [(5.0, 5.0)]
    # A coordinate that is not a number is not a position (it reaches a painter).
    assert single_cycle([(1.0, 2.0), (float("nan"), 3.0)]) == [(1.0, 2.0)]


def test_the_frame_order_reads_a_jittered_timelapse():
    """Which axis is positions and which is timepoints is read off the repeat
    pattern, and under equality a jittered file has no repeats at all: it reads
    as a single timepoint holding as many positions as the file has frames."""
    once, _ = _scan(2, 3, 4)
    assert infer_frame_order(once) == ("single", 24, 1)
    assert infer_frame_order(_revisited(once, 5)) == ("pos_in_time", 24, 5)
    assert infer_frame_order(
        _revisited(once, 5, interleave="time_in_pos")) == ("time_in_pos", 24, 5)


def test_single_cycle_is_not_quadratic():
    """96 wells x 16 fields x 24 timepoints, on the UI thread, per keystroke."""
    import time

    points = [
        (c * PITCH + (f % 4) * FOV, r * PITCH + (f // 4) * FOV)
        for _ in range(24) for r in range(8) for c in range(12) for f in range(16)
    ]
    assert len(points) == 36864
    start = time.perf_counter()
    drawn = single_cycle(points)
    elapsed = time.perf_counter() - start
    assert len(drawn) == 1536
    assert elapsed < 1.5, f"{elapsed:.2f}s — the quadratic scan is back"


def test_the_inference_stays_usable_on_a_full_timelapse_plate():
    """96 wells x 16 fields x 24 timepoints. The obvious "have I seen this
    cell before" scan over the list is quadratic and took five seconds here,
    on the UI thread, for a page that recomputes on every keystroke."""
    import time

    points = [
        (c * PITCH + (f % 4) * FOV, r * PITCH + (f // 4) * FOV)
        for _ in range(24) for r in range(8) for c in range(12) for f in range(16)
    ]
    assert len(points) == 36864
    start = time.perf_counter()
    layout = infer_stage_layout(points)
    elapsed = time.perf_counter() - start
    assert (layout.rows, layout.cols, layout.fovs_per_well) == (8, 12, 16)
    assert elapsed < 1.5, f"{elapsed:.2f}s — the quadratic scan is back"


def test_a_descending_axis_is_not_read_as_an_ascending_one():
    """An inverted scope reports Y decreasing down the plate. Read ascending,
    the first well visited lands at the block's *bottom* row, so the grid's
    top-left sweep mirrors every row — and the counts still look right."""
    points, order = _scan(2, 3, 1)
    flipped = [(x, -y) for x, y in points]
    layout = infer_stage_layout(flipped)
    assert (layout.rows, layout.cols) == (2, 3)
    # The first position visited must still be the first cell of the order.
    assert layout.relative_order[0] == (1, 0)     # bottom-left, ascending-Y
    assert layout.relative_order != order         # …and not the unflipped one


def test_coordinates_that_describe_nothing_are_refused():
    """A confident layout out of degenerate input is the worst outcome here: it
    is offered to the user as the file's own answer."""
    assert infer_stage_layout([(0.0, 0.0)] * 96) is None      # a stage writing 0,0
    assert infer_stage_layout([(5.0, 7.0), (5.0, 7.0)]) is None
    nan = float("nan")
    assert infer_stage_layout([(nan, nan)] * 4) is None
    # A stray non-finite point is dropped rather than poisoning the scale: NaN
    # reaches `pitch`, and `nan <= 0` is False, so it passes every guard below
    # and arrives at the painter as a coordinate.
    good, _ = _scan(2, 3, 4)
    with_nan = infer_stage_layout(good + [(nan, 0.0)])
    assert with_nan is not None
    assert (with_nan.rows, with_nan.cols) == (2, 3)
    assert isfinite(with_nan.pitch)
    assert infer_stage_layout([(float("inf"), 0.0)] * 4) is None
    # A layout that does survive never carries a scale nothing can be drawn at.
    good = infer_stage_layout(_scan(2, 3, 4)[0])
    assert good.pitch > 0


def test_plate_referenced_coordinates_name_the_wells_outright():
    """A scanner reporting in the *plate's* frame puts A01 at the origin, so
    every well centre is a whole number of pitches out and the well is
    arithmetic — no origin to place, no shape to position."""
    for (r0, c0) in ((0, 0), (2, 3), (6, 10)):
        points = [
            (c * PITCH + (f % 2) * FOV, r * PITCH + (f // 2) * FOV)
            for r in range(r0, r0 + 2) for c in range(c0, c0 + 2)
            for f in range(4)
        ]
        layout = infer_stage_layout(points)
        assert layout.absolute_origin == (r0, c0), (r0, c0)


def test_a_microscope_framed_file_says_nothing_about_the_plate():
    """An arbitrary zero, or coordinates that would run off the plate, means the
    frame is the scope's own — and then the wells genuinely are not in there."""
    base, _ = _scan(2, 3, 4)
    assert infer_stage_layout(base).absolute_origin == (0, 0)
    for offset in ((123456.0, 98765.0), (-50000.0, -50000.0), (PITCH / 2, 0.0)):
        shifted = [(x + offset[0], y + offset[1]) for x, y in base]
        assert infer_stage_layout(shifted).absolute_origin is None, offset


# ── mirroring the stage's axes ───────────────────────────────────────────────


def test_mirroring_reflects_the_run_and_leaves_its_shape_alone():
    points, _ = _scan(2, 3, 4)
    assert mirrored(points) == points                      # neither axis: a no-op
    assert mirrored([]) == []

    flipped = mirrored(points, flip_x=True)
    assert [x for x, _ in flipped] == [-x for x, _ in points]
    assert [y for _, y in flipped] == [y for _, y in points]
    # A reflection, so the spacing — and therefore the inferred pitch, grid and
    # field count — is exactly what it was.
    was, now = infer_stage_layout(points), infer_stage_layout(flipped)
    assert (now.rows, now.cols, now.fovs_per_well) == (was.rows, was.cols,
                                                       was.fovs_per_well)
    assert round(now.pitch, 6) == round(was.pitch, 6)

    both = mirrored(points, flip_x=True, flip_y=True)
    assert both == [(-x, -y) for x, y in points]


def test_a_mirrored_scan_walks_its_wells_the_other_way():
    """The reason the control exists. A stage whose X decreases across the
    plate describes the same block visited from the other end, and read
    straight every image lands in the horizontally opposite well."""
    points, order = _scan(2, 3, 1, serpentine=False)
    assert infer_stage_layout(points).relative_order == order

    flipped = infer_stage_layout(mirrored(points, flip_x=True))
    assert flipped.relative_order == [(r, 2 - c) for r, c in order]
    assert place(flipped, 0, 0) == ["A03", "A02", "A01", "B03", "B02", "B01"]


# ── shapes that are not filled rectangles ────────────────────────────────────


def _shape(cells, fovs=4):
    """Coordinates visiting exactly *cells*, in order, at real plate scales."""
    return [
        (c * PITCH + (f % 2) * FOV, r * PITCH + (f // 2) * FOV)
        for r, c in cells for f in range(fovs)
    ]


def test_a_shape_that_is_not_a_rectangle_is_still_read():
    """An L, a diagonal, a plate with a row left out: ordinary experiments. The
    fields per well and the visit order are right whatever the outline is, and
    refusing the whole layout because the outline is not a rectangle threw those
    away too — the user got no offer and no reason for its absence."""
    shapes = {
        "L": [(0, 0), (0, 1), (0, 2), (1, 0), (2, 0)],
        "diagonal": [(0, 0), (1, 1), (2, 2)],
        "gapped row": [(0, 0), (0, 1), (2, 0), (2, 1)],
        "gapped column": [(0, 0), (0, 2), (1, 0), (1, 2)],
    }
    for name, cells in shapes.items():
        layout = infer_stage_layout(_shape(cells))
        assert layout is not None, name
        assert layout.relative_order == cells, name
        assert layout.n_wells == len(cells), name
        assert layout.fovs_per_well == 4, name
        assert not layout.filled, name
        # The extent is the box around the shape, which is not its well count.
        assert (layout.rows, layout.cols) == (max(r for r, _ in cells) + 1,
                                              max(c for _, c in cells) + 1), name


def test_a_skipped_well_keeps_its_place_on_the_plate():
    """Rows A and C imaged, B not. Indexing the occupied groups consecutively
    closes the gap up: the block reads as two adjacent rows and every image in
    the second is written a row too high."""
    layout = infer_stage_layout(_shape([(0, 0), (0, 1), (2, 0), (2, 1)]))
    assert layout.relative_order == [(0, 0), (0, 1), (2, 0), (2, 1)]
    assert place(layout, 0, 0) == ["A01", "A02", "C01", "C02"]


def test_a_skipped_well_does_not_double_the_pitch():
    """The gap over a skipped well is two pitches, and both the axis scale and
    the well step will read it as one unless told otherwise. The pitch converts
    stage units into plate cells, so a doubled one draws the map at half scale
    — the same class of error as reading the inter-well gap for the well step,
    and far larger."""
    for cells in ([(0, 0), (0, 2), (1, 0), (1, 2)],       # a column skipped
                  [(0, 0), (0, 1), (2, 0), (2, 1)],       # a row skipped
                  [(0, 0), (0, 2), (0, 3)]):              # a gap and a step
        for fovs in (1, 4):
            layout = infer_stage_layout(_shape(cells, fovs))
            assert round(layout.pitch, 3) == PITCH, (cells, fovs)


def test_a_uniformly_skipped_scan_cannot_know_it_skipped():
    """The limit of the above, stated rather than papered over. Every
    comparison in this module is between distances *within* one file — no
    absolute units are trusted — so two wells alone, three pitches apart, are
    indistinguishable from two adjacent wells on a coarser plate. Something has
    to show a single step: another axis, or another pair of wells on this one.
    The block then reads as adjacent, which the drawn map shows and the user's
    grid overrides."""
    layout = infer_stage_layout(_shape([(0, 0), (0, 3)], fovs=1))
    assert layout.pitch == 3 * PITCH
    assert layout.relative_order == [(0, 0), (0, 1)]


def test_a_filled_rectangle_still_says_it_is_one():
    layout = infer_stage_layout(_shape([(0, 0), (0, 1), (1, 1), (1, 0)]))
    assert layout.filled
    assert layout.n_wells == 4 == layout.rows * layout.cols


def test_uneven_fields_per_well_is_still_refused():
    """The refusal that stays. A conversion writes one FOVs-per-well count for
    the whole run, so a scan whose wells hold different numbers of fields has no
    layout to offer — unlike a skipped well, which has one."""
    uneven = [(0.0, 0.0), (FOV, 0.0), (PITCH, 0.0)]     # two fields, then one
    assert infer_stage_layout(uneven) is None


def test_skipping_on_one_axis_only_is_recovered_from_the_other():
    """Rows skipped, columns not — the ordinary way a plate gets sampled. The
    two axes then measure *different* steps (18 mm down, 9 mm across), and the
    plate has one pitch: whichever axis shows a single well step supplies it,
    and the other's gaps are read as the multiples they are."""
    rows_skipped = [(r, c) for r in (0, 2, 4) for c in range(4)]
    cols_skipped = [(r, c) for r in range(3) for c in (0, 2, 4)]
    for name, cells, labels in (
            ("rows", rows_skipped, ["A01", "A02", "A03", "A04", "C01"]),
            ("cols", cols_skipped, ["A01", "A03", "A05", "B01", "B03"])):
        for fovs in (1, 4):
            layout = infer_stage_layout(_shape(cells, fovs))
            assert round(layout.pitch, 3) == PITCH, (name, fovs)
            assert layout.relative_order == cells, (name, fovs)
            assert place(layout, 0, 0)[:5] == labels, (name, fovs)


def test_one_adjacent_pair_anywhere_is_enough():
    """Rows A, B and D: the skip is irregular, so the axis carries both a single
    step and a double one and needs no help from the other axis."""
    cells = [(r, c) for r in (0, 1, 3) for c in range(3)]
    layout = infer_stage_layout(_shape(cells))
    assert round(layout.pitch, 3) == PITCH
    assert place(layout, 0, 0)[-3:] == ["D01", "D02", "D03"]


def test_a_scan_that_skips_uniformly_everywhere_reads_as_adjacent():
    """The limit, stated. Rows A, C and E in a single column: every step on the
    only axis that carries wells is a double one, and no absolute unit is
    trusted anywhere in this module, so two wells two pitches apart cannot be
    told from two adjacent wells on a coarser plate. It reads as A, B, C — which
    the drawn map shows at plate scale and the user's own grid overrides."""
    layout = infer_stage_layout(_shape([(0, 0), (2, 0), (4, 0)], fovs=1))
    assert place(layout, 0, 0) == ["A01", "B01", "C01"]
    assert layout.pitch == 2 * PITCH


def test_the_two_axes_must_agree_on_one_pitch():
    """One pitch serves both axes, and where that is false there is no layout.

    A 96-well plate is on 9 mm centres both ways, and reading each axis at its
    own word instead would make rows sampled A/C/E adjacent — the cross-axis
    agreement is what recovers a one-sided skip. So it has to hold: a scan whose
    rows sit 1.5 pitches apart is not that plate, and grouping it at the other
    axis's pitch rounds 1.5 to 2 and produces exactly the layout a genuine
    skipped row produces, with the same confidence.
    """
    cells = [(r, c) for r in range(3) for c in range(3)]

    def scan(y_step):
        return [(c * PITCH, r * y_step) for r, c in cells]

    assert infer_stage_layout(scan(PITCH * 1.5)) is None
    # Two pitches apart is a skipped row, which is a plate and reads as one.
    skipped = infer_stage_layout(scan(PITCH * 2))
    assert place(skipped, 0, 0)[:6] == ["A01", "A02", "A03", "C01", "C02", "C03"]
    # And ordinary stage slop is not a disagreement.
    close = infer_stage_layout(scan(PITCH * 1.05))
    assert place(close, 0, 0)[:6] == ["A01", "A02", "A03", "B01", "B02", "B03"]


def test_rows_of_different_lengths_are_read_as_they_were_imaged():
    """Row A across seven columns, rows B and C across six. Nothing about a
    scan makes its rows the same length — a plate is often filled to whatever
    the experiment needed — and the mapping was never the limitation: an
    ordered list of wells describes this exactly. Only the inference refused."""
    ragged = ([(0, c) for c in range(7)]
              + [(r, c) for r in (1, 2) for c in range(6)])
    snaked = ([(0, c) for c in range(7)]
              + [(1, c) for c in range(5, -1, -1)]
              + [(2, c) for c in range(6)])
    for name, cells in (("raster", ragged), ("snake", snaked)):
        for fovs in (1, 4):
            layout = infer_stage_layout(_shape(cells, fovs))
            assert layout is not None, (name, fovs)
            assert layout.relative_order == cells, (name, fovs)
            assert (layout.rows, layout.cols) == (3, 7), (name, fovs)
            assert layout.n_wells == 19, (name, fovs)
            assert layout.fovs_per_well == fovs, (name, fovs)
            assert not layout.filled, (name, fovs)
            assert round(layout.pitch, 3) == PITCH, (name, fovs)
    # The long row reaches column 7; the short ones stop at 6.
    labels = place(infer_stage_layout(_shape(snaked)), 0, 0)
    assert labels[:7] == ["A01", "A02", "A03", "A04", "A05", "A06", "A07"]
    assert labels[7:13] == ["B06", "B05", "B04", "B03", "B02", "B01"]
    assert "B07" not in labels and "C07" not in labels
