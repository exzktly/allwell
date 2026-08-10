"""Reading a plate layout out of an OME-TIFF's stage coordinates.

The inference in ``datasets.ome_import.stage_layout`` rests on one physical
fact: wells sit on a 9 mm pitch while the fields of view inside a well are a few
hundred micrometres apart. These fixtures are built at those real scales, in the
shapes a plate scan actually comes in — serpentine and raster, one field per well
and many, single rows and single columns, with and without stage jitter.
"""

from __future__ import annotations

import random

from datasets.ome_import.stage_layout import (
    StageLayout,
    infer_stage_layout,
    place,
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
