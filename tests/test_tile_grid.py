"""Tile placement and overlap, checked against grids built here from first principles."""

import numpy as np
import pytest

from pipeline.tile_grid import (
    ALL_PATTERNS, BAD_GRID, INCOMPLETE_GRID, OK, RecordedTile, ScanPattern, ownership_report,
    place_from_pattern, place_from_record, scan_cells, tiles_from_record,
)
from tests._nanowell_synth import make_array


def _grid(rows, cols, tile_px, overlap, *, jitter=0.0, seed=0, drop=()):
    """Recorded mosaic positions (px) for a rows x cols grid with *overlap* fraction."""
    rng = np.random.default_rng(seed)
    step = (tile_px[0] * (1 - overlap), tile_px[1] * (1 - overlap))
    tiles = []
    for r in range(rows):
        for c in range(cols):
            if (r, c) in drop:
                continue
            dx, dy = rng.uniform(-jitter, jitter, 2) if jitter else (0.0, 0.0)
            tiles.append(RecordedTile(f"F{r * cols + c}", (c * step[0] + dx, r * step[1] + dy)))
    return tiles


def _place(tiles, tile_px, scale=0.65, **options):
    return place_from_record(tiles, tile_size_px={"x": tile_px[0], "y": tile_px[1]},
                             pixel_size_um={"x": scale, "y": scale}, **options)


@pytest.mark.parametrize("rows, cols, tile_px, overlap", [
    (5, 5, (2048, 2048), 0.10),
    (4, 6, (1024, 768), 0.15),       # a different plan: nothing may assume 5 x 5 / 10 %
])
def test_grid_shape_and_overlap_are_read_from_the_positions(rows, cols, tile_px, overlap):
    plan = _place(_grid(rows, cols, tile_px, overlap, jitter=3.0), tile_px)
    assert plan.status == OK and plan.usable
    assert (plan.rows, plan.cols) == (rows, cols)
    for axis in (0, 1):
        assert plan.overlap_fraction[axis] == pytest.approx(overlap, abs=0.005)
        assert plan.overlap_um[axis] == pytest.approx(overlap * tile_px[axis] * 0.65, rel=0.05)
    assert {p.source for p in plan.placements} == {"mosaic"}


def test_a_tile_short_of_the_grid_is_incomplete_and_named():
    plan = _place(_grid(5, 5, (2048, 2048), 0.10, drop={(2, 3)}), (2048, 2048))
    assert plan.status == INCOMPLETE_GRID and not plan.usable
    assert plan.missing == ("r2c3",)
    assert "r2c3" in plan.reason and "1 of 25" in plan.reason


def test_a_whole_absent_line_is_seen_only_with_the_expected_shape():
    middle = {(r, 2) for r in range(5)}
    inferred = _place(_grid(5, 5, (2048, 2048), 0.10, drop=middle), (2048, 2048))
    assert inferred.missing == tuple(f"r{r}c2" for r in range(5))
    outer = {(r, 4) for r in range(5)}
    tiles = _grid(5, 5, (2048, 2048), 0.10, drop=outer)
    # Nothing in the positions says a fifth column was planned ...
    assert _place(tiles, (2048, 2048)).status == OK
    # ... so the plan the user states is what finds it. The record does not say which end of
    # the axis the absent line was at, so it is named without a position rather than labelled
    # with a wrong one.
    stated = _place(tiles, (2048, 2048), expected_shape=(5, 5))
    assert stated.status == INCOMPLETE_GRID and not stated.usable
    assert stated.missing == ("1 column(s) at an unknown outer end",)
    assert "unknown outer end" in stated.reason


def test_an_absent_first_row_is_not_reported_as_an_absent_last_row():
    # Row 0 of 5 is missing. Rows are indexed from the first one seen, so the old reading named
    # r4c0..r4c4 as missing and placed the second row at row 0.
    tiles = _grid(5, 5, (2048, 2048), 0.10, drop={(0, c) for c in range(5)})
    plan = _place(tiles, (2048, 2048), expected_shape=(5, 5))
    assert plan.status == INCOMPLETE_GRID
    assert plan.missing == ("1 row(s) at an unknown outer end",)
    assert not any(name.startswith("r4") for name in plan.missing)


def test_tiles_that_do_not_touch_are_refused_not_noted():
    # Two columns with the one between them absent: a 532 um gap, not an overlap.
    plan = _place(_grid(1, 3, (2048, 2048), 0.10, drop={(0, 1)}), (2048, 2048))
    assert plan.status == BAD_GRID and not plan.usable
    assert "do not touch" in plan.reason and "a gap of" in plan.reason


def test_positions_that_are_not_a_grid_are_refused_not_placed():
    tiles = [RecordedTile("A", (0.0, 0.0)), RecordedTile("B", (10.0, 5.0))]
    plan = _place(tiles, (2048, 2048))
    assert plan.status == BAD_GRID and "one grid cell" in plan.reason


def test_a_measured_overlap_that_contradicts_the_declared_one_is_reported():
    tiles = _grid(3, 3, (2048, 2048), 0.10)
    agreeing = _place(tiles, (2048, 2048), declared_overlap_percent={"x": 10.0, "y": 10.0})
    assert agreeing.overlap_disagreement == ""
    plan = _place(tiles, (2048, 2048), declared_overlap_percent={"x": 20.0, "y": 10.0})
    assert "along x" in plan.overlap_disagreement and "10.0 %" in plan.overlap_disagreement
    assert "20 %" in plan.overlap_disagreement and "along y" not in plan.overlap_disagreement


def test_stage_only_tiles_need_the_axis_direction_stated():
    stage = [RecordedTile(f"F{i}", None, (i * 100.0, 0.0)) for i in range(3)]
    refused = place_from_record(stage, tile_size_px={"x": 200, "y": 200},
                                pixel_size_um={"x": 1.0, "y": 1.0})
    assert refused.status == BAD_GRID and "direction is not stated" in refused.reason
    placed = place_from_record(stage, tile_size_px={"x": 200, "y": 200},
                               pixel_size_um={"x": 1.0, "y": 1.0}, stage_to_image=(-1.0, 1.0))
    assert placed.status == OK and {p.source for p in placed.placements} == {"stage"}
    # A stage axis running against the image's axis mirrors the grid: F0 is rightmost.
    assert [p.col for p in placed.placements] == [2, 1, 0]


def test_a_tile_with_no_position_is_named():
    plan = _place([RecordedTile("A", (0.0, 0.0)), RecordedTile("B")], (100, 100))
    assert plan.status == BAD_GRID and "B" in plan.reason and "no recorded position" in plan.reason


def test_calibration_is_read_per_timepoint_from_the_record():
    record = {
        "timepoints": {
            "0h": {"tile_size_px": {"x": 100, "y": 100}, "pixel_size_um": {"x": 1.0, "y": 1.0},
                   "declared_overlap_percent": {"x": 10, "y": 10}},
            "24h": {"tile_size_px": {"x": 50, "y": 50}, "pixel_size_um": {"x": 2.0, "y": 2.0},
                    "declared_overlap_percent": {"x": None, "y": None}},
        },
        "tiles": [{"well": "A1", "field": f"F{i}", "timepoint": tp, "mosaic_xy_px": [i * 90.0, 0.0],
                   "stage_xy_um": None} for tp in ("0h", "24h") for i in range(2)],
    }
    first = tiles_from_record(record, "A1", "0h")
    second = tiles_from_record(record, "A1", "24h")
    assert first[1] == {"x": 100, "y": 100} and second[1] == {"x": 50, "y": 50}
    assert tiles_from_record(record, "B2", "0h") is None
    plans = [place_from_record(t, tile_size_px=s, pixel_size_um=p, declared_overlap_percent=d)
             for t, s, p, d in (first, second)]
    # The same pixel positions are 90 um apart at 1 um/px and 180 um apart at 2 um/px.
    assert plans[0].status == OK
    assert plans[0].placements[1].left_um == pytest.approx(90.0)
    assert plans[0].overlap_um[0] == pytest.approx(10.0)
    # At 2 um/px the 50 px tiles are 100 um wide and 180 um apart: they do not touch, so the
    # second timepoint is refused with the gap named. Read with the first one's calibration it
    # would have looked fine, which is why the calibration is per timepoint.
    assert plans[1].status == BAD_GRID and "a gap of 80.0 um" in plans[1].reason


def test_sixteen_distinct_patterns_each_visit_every_cell_once():
    assert len(ALL_PATTERNS) == len(set(ALL_PATTERNS)) == 16
    orders = set()
    for pattern in ALL_PATTERNS:
        cells = scan_cells(3, 4, pattern)
        assert sorted(cells) == [(r, c) for r in range(3) for c in range(4)]
        orders.add(tuple(cells))
        if pattern.serpentine:      # a snake never jumps: consecutive cells are neighbours
            assert all(abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1 for a, b in zip(cells, cells[1:]))
    assert len(orders) == 16


def test_named_patterns_visit_cells_in_the_order_the_stage_would():
    assert scan_cells(2, 3, ScanPattern("top-left", "row", False)) == [
        (0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2)]
    assert scan_cells(2, 3, ScanPattern("bottom-right", "column", True)) == [
        (1, 2), (0, 2), (0, 1), (1, 1), (1, 0), (0, 0)]
    assert scan_cells(2, 3, ScanPattern("top-right", "row", True)) == [
        (0, 2), (0, 1), (0, 0), (1, 0), (1, 1), (1, 2)]


def test_pattern_placement_puts_the_first_tile_in_the_start_corner():
    plan = place_from_pattern(list("abcdef"), 2, 3, ScanPattern("bottom-left", "row", False),
                              tile_size_px={"x": 100, "y": 100}, pixel_size_um={"x": 1.0, "y": 1.0},
                              overlap_percent=10)
    assert plan.status == OK
    first = plan.placements[0]
    assert (first.tile_id, first.row, first.col) == ("a", 1, 0)
    assert (first.left_um, first.top_um) == (0.0, 90.0)
    assert plan.overlap_um == (pytest.approx(10.0), pytest.approx(10.0))
    assert plan.notes and "scan pattern" in plan.notes[0]


def test_an_unfinished_pattern_scan_names_the_cells_not_reached():
    plan = place_from_pattern(list("abcd"), 2, 3, ScanPattern("top-left", "row", False),
                              tile_size_px={"x": 100, "y": 100}, pixel_size_um={"x": 1.0, "y": 1.0},
                              overlap_percent=10)
    assert plan.status == INCOMPLETE_GRID and plan.missing == ("r1c1", "r1c2")


def _sites(pitch=172.0, radius=2600.0):
    return make_array(radius=radius, pitch=pitch, diameter=100.0).points


@pytest.mark.parametrize("overlap_um, enough", [(133.0, True), (90.0, False)])
def test_the_overlap_decides_whether_any_site_needs_fusing(overlap_um, enough):
    # Two 1331 um tiles side by side, 100 um nanowells, 10 um margin.
    width, height, diameter, margin = 1331.2, 1331.2, 100.0, 10.0
    step = width - overlap_um
    plan = place_from_record(
        [RecordedTile("L", (0.0, 0.0)), RecordedTile("R", (step, 0.0))],
        tile_size_px={"x": width, "y": height}, pixel_size_um={"x": 1.0, "y": 1.0})
    # A column of sites (x = 0 in the array's frame) is put on the seam, where the answer lives.
    sites = _sites() + ((width + step) / 2, height / 2)
    report = ownership_report(plan, sites, diameter, margin)
    assert report.required_overlap_um == 120.0
    assert report.holds == (enough, None)
    # Independent count, from the two rectangles directly. A site is owned iff its disk plus
    # margin fits inside one tile. Otherwise it is fused iff its bare disk lies inside the union
    # (one rectangle, since the tiles overlap), and then it needs blending iff no single tile
    # holds the bare disk; it is unimaged iff the union does not hold it.
    reach, radius = diameter / 2 + margin, diameter / 2

    def inside(x, y, left, right, pad):
        return left + pad <= x <= right - pad and pad <= y <= height - pad

    tiles = ((0.0, width), (step, step + width))

    def owned(x, y):
        return any(inside(x, y, a, b, reach) for a, b in tiles)

    def covered(x, y):
        return inside(x, y, 0.0, step + width, radius)

    fused = [i for i, (x, y) in enumerate(sites) if not owned(x, y) and covered(x, y)]
    blend = [i for i in fused if not any(inside(*sites[i], a, b, radius) for a, b in tiles)]
    assert list(report.fused_indices) == fused
    assert list(report.blend_indices) == blend
    assert list(report.edge_indices) == [i for i in fused if i not in blend]
    assert bool(blend) != enough, "seam sites exist exactly when the overlap is too small"
    assert report.owned + report.fused + report.unimaged == len(sites)


def test_an_unusable_grid_has_no_ownership_report():
    plan = _place(_grid(2, 2, (100, 100), 0.1, drop={(0, 0)}), (100, 100))
    with pytest.raises(ValueError, match="incomplete_grid"):
        ownership_report(plan, np.zeros((1, 2)), 10.0, 1.0)


def test_a_site_inside_the_outer_margin_band_is_edge_not_blend():
    plan = _place([RecordedTile("only", (0.0, 0.0))], (1000, 1000), scale=1.0)
    # diameter 100, margin 10: owned needs 60 um from the edge, the bare disk needs 50.
    sites = np.array([[500.0, 500.0], [55.0, 500.0], [30.0, 500.0]])
    report = ownership_report(plan, sites, 100.0, 10.0)
    assert report.owners == ("only", None, None)
    assert report.edge_indices == (1,) and report.blend_indices == ()
    assert report.unimaged_indices == (2,)
    assert report.holds == (None, None)


def test_mosaic_and_stage_positions_are_never_mixed_in_one_grid():
    tiles = [RecordedTile("A", (0.0, 0.0), (0.0, 0.0)), RecordedTile("B", None, (90.0, 0.0))]
    plan = place_from_record(tiles, tile_size_px={"x": 100, "y": 100}, pixel_size_um={"x": 1.0, "y": 1.0},
                             stage_to_image=(1.0, 1.0))
    assert plan.status == BAD_GRID and "different frames" in plan.reason


def test_ownership_follows_the_registered_position_not_the_recorded_one():
    plan = _place([RecordedTile("L", (0.0, 0.0)), RecordedTile("R", (900.0, 0.0))], (1000, 1000), scale=1.0)
    site = np.array([[1010.0, 500.0]])                  # inside R only if R sits at 900, left of 950 if it moved
    recorded = ownership_report(plan, site, 100.0, 10.0)
    assert recorded.owners == ("R",)
    moved = ownership_report(plan, site, 100.0, 10.0, positions_um={"R": (990.0, 0.0), "L": (0.0, 0.0)})
    assert moved.owners == (None,) and moved.fused_indices == (0,)


@pytest.mark.parametrize("overlap, every_site_has_an_owner", [(0.30, True), (0.10, False)])
def test_rendered_plans_cover_both_ownership_regimes(overlap, every_site_has_an_owner):
    """100 um nanowells on a 200 px tile at 4.3 um/px: 30 % overlap is 258 um (>= 120), 10 % is 86 um."""
    from tests._nanowell_synth import TilePlan, render_tiles
    plan = TilePlan(3, 3, (200, 200), overlap, 4.3)
    seam_x = (plan.step_um[0] + plan.extent_um[0]) / 2      # between the first and second columns
    union_w = plan.step_um[0] * 2 + plan.extent_um[0]
    # The array's central column is put on that seam, where a site straddles it if anything can.
    rendered = render_tiles(plan, pitch_um=172.0, diameter_um=100.0, radius_um=1200.0, seed=0,
                            array_shift_um=(seam_x - union_w / 2, 0.0))
    tiles = [RecordedTile(n, (x / 4.3, y / 4.3)) for n, (x, y) in rendered.true_left_top_um.items()]
    grid = _place(tiles, (200, 200), scale=4.3)
    assert grid.usable and grid.overlap_um[0] == pytest.approx(plan.extent_um[0] * overlap, rel=0.01)
    report = ownership_report(grid, rendered.sites_um, 100.0, 10.0)
    assert report.holds == (every_site_has_an_owner, every_site_has_an_owner)
    assert bool(report.blend_indices) != every_site_has_an_owner


def test_ownership_guarantee_is_measured_at_the_registered_positions():
    plan = _place([RecordedTile("A", (0.0, 0.0)), RecordedTile("B", (900.0, 0.0))], (1000, 1000), scale=1.0)
    site = np.array([[500.0, 500.0]])
    # Recorded 100 um apart in overlap; 100 + 2 * 10 needs 120, so the guarantee does not hold ...
    assert ownership_report(plan, site, 100.0, 10.0).holds == (False, None)
    # ... but registered 250 um in, it does: the report follows where the tiles really are.
    closer = ownership_report(plan, site, 100.0, 10.0, positions_um={"A": (0.0, 0.0), "B": (750.0, 0.0)})
    assert closer.holds == (True, None)


def test_unusable_inputs_are_refused_not_crashed_on():
    nan = place_from_record([RecordedTile("A", (0.0, 0.0))], tile_size_px={"x": float("nan"), "y": 10},
                            pixel_size_um={"x": 1.0, "y": 1.0})
    assert nan.status == BAD_GRID
    empty = place_from_pattern(["a"], 0, 3, ScanPattern(), tile_size_px={"x": 10, "y": 10},
                               pixel_size_um={"x": 1.0, "y": 1.0}, overlap_percent=10)
    assert empty.status == BAD_GRID and "no cells" in empty.reason
