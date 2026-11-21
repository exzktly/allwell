"""Spatial: a cell's neighbours are in its own field, and a colony with no hull has no hull area.

A well imaged in several fields has each cell's centroid in its own field's pixels, so the fields share coordinates
without sharing a frame. The tab used to hand them to the compute layer as one point cloud: every neighbour search
found cells of other fields, so the local density was several times a field's, the nearest neighbour was a cell of
another field, colonies were joined across fields and Ripley counted pairs that do not exist.

The fixtures make the two answers differ by construction. Field 2 is field 1 moved one pixel to the right, on a
ten-pixel grid: inside a field every nearest neighbour is 10 px away, and over the overlay it is 1 px. The rows of
the two fields are interleaved, so a result that is right only for fields stored one after the other fails too.

The hull half: under single linkage most colonies are one or two cells, and those have no convex hull. Their area was
stored as 0.0, so the frame's median hull area was 0.0.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from review.domain import spatial_compute as spc

GRID = np.array([[10.0 * i, 10.0 * j] for i in range(6) for j in range(6)])


def _two_fields():
    """Field "1" is the grid, field "2" the grid moved 1 px in x; rows interleaved field by field."""
    pts = np.empty((72, 2))
    pts[0::2] = GRID
    pts[1::2] = GRID + [1.0, 0.0]
    fields = np.array(["1", "2"] * 36)
    return pts, fields


def test_the_nearest_neighbour_is_looked_for_inside_the_field():
    pts, fields = _two_fields()
    assert spc.nearest_neighbour_distance(pts, fields).tolist() == [10.0] * 72
    # The overlay, for the record of what the fixture distinguishes.
    assert spc.nearest_neighbour_distance(pts).tolist() == [1.0] * 72


def test_local_density_is_the_field_s_and_not_the_overlay_s():
    pts, fields = _two_fields()
    one_field = spc.local_density(GRID)
    got = spc.local_density(pts, fields=fields)
    assert np.array_equal(got[0::2], one_field) and np.array_equal(got[1::2], one_field)
    # An interior cell: four neighbours at 10 px and the fifth on a diagonal, so k / (pi d_k^2) = 5 / (200 pi).
    interior = 7                                           # GRID row (1, 1)
    assert got[2 * interior] == pytest.approx(5.0 / (200.0 * math.pi))
    # The overlay, for the record: the same cells read at twice the density.
    assert float(np.median(spc.local_density(pts))) == pytest.approx(2 * float(np.median(got)))
    knn = spc.knn_mean_distance(pts, fields=fields)
    assert np.array_equal(knn[0::2], spc.knn_mean_distance(GRID))


def test_a_colony_never_spans_two_fields():
    row = np.array([[0.0, 0.0], [10.0, 0.0], [20.0, 0.0], [30.0, 0.0]])
    pts = np.vstack([row, row + [35.0, 0.0]])              # overlaid, the two rows are one chain 5 px apart
    fields = np.array(["1"] * 4 + ["2"] * 4)
    split = spc.detect_colonies(pts, method=spc.METHOD_SINGLE_LINKAGE, eps=12.0, fields=fields)
    assert split.n_colonies == 2
    assert split.labels.tolist() == [0, 0, 0, 0, 1, 1, 1, 1]
    assert spc.detect_colonies(pts, method=spc.METHOD_SINGLE_LINKAGE, eps=12.0).n_colonies == 1


def test_the_default_colony_radius_comes_from_within_field_distances():
    pts, fields = _two_fields()
    assert spc.detect_colonies(pts, eps_mult=3.0, fields=fields).eps == 30.0
    assert spc.detect_colonies(pts, eps_mult=3.0).eps == 3.0


def test_ripley_pools_fields_and_counts_no_pair_across_them():
    rng = np.random.default_rng(4)
    one = rng.uniform(0, 500, (120, 2))
    twice = np.vstack([one, one])                          # two fields holding the same pattern
    fields = np.array(["1"] * 120 + ["2"] * 120)
    alone = spc.ripley_l(one)
    pooled = spc.ripley_l(twice, fields=fields)
    assert np.allclose(pooled.r, alone.r)
    assert np.allclose(pooled.L_minus_r, alone.L_minus_r)
    assert pooled.n_points == 240
    # Laid over each other, every cell has a partner at distance zero and the curve is far above the field's.
    overlay = spc.ripley_l(twice)
    assert float(overlay.L_minus_r[0] - alone.L_minus_r[0]) > 5.0


def test_one_field_is_computed_exactly_as_before():
    rng = np.random.default_rng(9)
    pts = rng.uniform(0, 300, (80, 2))
    same = np.array(["3"] * 80)
    assert np.array_equal(spc.nearest_neighbour_distance(pts, same), spc.nearest_neighbour_distance(pts))
    a, b = spc.ripley_l(pts, fields=same), spc.ripley_l(pts)
    assert np.array_equal(a.L_minus_r, b.L_minus_r) and np.array_equal(a.csr_hi, b.csr_hi)


# ── the hull ────────────────────────────────────────────────────────────────────────────────────────────────────

#: A 3-4-5 triangle (area 6), a pair, and two single cells, each further than the cut from the others.
TRIANGLE_AND_SCRAPS = np.array([
    [0.0, 0.0], [3.0, 0.0], [0.0, 4.0],
    [100.0, 0.0], [102.0, 0.0],
    [200.0, 0.0],
    [300.0, 0.0],
])


def test_a_sparse_field_does_not_shrink_ripley_s_radii_for_the_whole_well():
    """A field of two cells 100 px apart and 5 px off a line has a window 5 px across.

    The default radius range was a quarter of the *smallest* field's shorter side, so that one field cut the radii
    read for all 302 cells to 1.25 px. It is the cell-count-weighted median field's, which here is the 300-cell
    field's own.
    """
    rng = np.random.default_rng(0)
    dense = rng.uniform(0.0, 1000.0, size=(300, 2))
    sparse = np.array([[10.0, 10.0], [110.0, 15.0]])
    fields = np.array(["1"] * 300 + ["2"] * 2)
    alone = spc.ripley_l(dense)
    pooled = spc.ripley_l(np.vstack([dense, sparse]), fields=fields)
    assert float(pooled.r.max()) == pytest.approx(float(alone.r.max()))
    assert float(alone.r.max()) == pytest.approx(0.25 * float(np.ptp(dense, axis=0).min()))


def test_a_colony_too_small_for_a_hull_has_no_hull_area():
    labels = spc.detect_colonies(TRIANGLE_AND_SCRAPS, method=spc.METHOD_SINGLE_LINKAGE, eps=6.0).labels
    stats = spc.colony_stats(TRIANGLE_AND_SCRAPS, labels)
    by_size = dict(zip(stats.size.tolist(), stats.hull_area.tolist()))
    assert by_size[3] == pytest.approx(6.0)
    assert math.isnan(by_size[2]) and math.isnan(by_size[1])


def test_the_median_hull_area_is_over_the_colonies_that_have_one_and_says_how_many(monkeypatch):
    # The frame's default radius is a multiple of its median nearest-neighbour distance; pin the cut instead.
    real = spc.detect_colonies
    monkeypatch.setattr(spc, "detect_colonies", lambda pts, **kw: real(
        pts, method=spc.METHOD_SINGLE_LINKAGE, eps=6.0, fields=kw.get("fields")))
    summary = spc.summarise_frame(TRIANGLE_AND_SCRAPS, colony_method=spc.METHOD_SINGLE_LINKAGE)
    assert summary.n_colonies == 4
    assert summary.n_colonies_with_hull == 1
    assert summary.hull_area_median == pytest.approx(6.0)          # 0.0 when the three scraps counted as zeros
    assert summary.packing_density_median == pytest.approx(0.5)


# ── through the tab's own collectors ────────────────────────────────────────────────────────────────────────────

pytest.importorskip("PySide6")


@pytest.fixture
def ctx(qtbot, tmp_path):
    import pandas as pd

    from tests._minimal_shell import load_dataset, minimal_shell

    pts, fields = _two_fields()
    rows = [{"well": "A01", "fov": fields[i], "timepoint": "0h", "timepoint_hours": 0.0, "channel": "dapi",
             "nucleus_id": i + 1, "area_px": 200.0, "gfp_mean_intensity": 100.0 + i, "dapi_mean_intensity": 300.0,
             "shape_centroid_col": pts[i, 0], "shape_centroid_row": pts[i, 1]} for i in range(len(pts))]
    pd.DataFrame(rows).to_csv(tmp_path / "syn_A01.csv", index=False)
    (tmp_path / "pipeline_info.json").write_text(json.dumps({
        "schema": "mini", "separator": "_", "fluor_tokens": ["gfp"], "nuclear_token": "dapi",
        "available_timepoints": ["0h"], "fov_index": -1, "tp_index": 2}))
    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, tmp_path)
    return ctx


def test_the_summary_panel_reads_each_field_on_its_own(ctx):
    from review.features.spatial.collect import collect_summary_matrix, summary_matrix_to_csv_rows

    payload = collect_summary_matrix(ctx, ["A01"], [0.0], metric_col="gfp_mean_intensity")
    summary = payload["summaries"][0, 0]
    assert summary.n_cells == 72
    assert summary.nnd_median == 10.0                               # 1.0 over the overlay
    assert summary.local_density_median == pytest.approx(float(np.median(spc.local_density(GRID))))
    row = summary_matrix_to_csv_rows(payload)[0]
    assert row["nnd_median"] == 10.0 and row["n_colonies_with_hull"] == summary.n_colonies_with_hull


def test_the_tab_s_frame_payload_reads_each_field_on_its_own(ctx):
    from review.features.spatial import state as sp_state
    from review.features.spatial.controller import _compute_frame_payload

    st = ctx.state.feature_slice("spatial")
    st.timepoint = 0.0
    st.level = sp_state.LEVEL_COLONY
    st.viz = sp_state.VIZ_COLONY_SIZE
    payload = _compute_frame_payload(ctx, st, "A01")
    assert "error" not in payload
    assert payload["nnd"].tolist() == [10.0] * 72
    assert payload["colony_eps"] == pytest.approx(st.colony_eps_mult * 10.0)
    labels, fields = payload["colony_labels"], payload["fov"]
    for colony in set(labels.tolist()) - {-1}:
        assert len(set(fields[labels == colony].tolist())) == 1


def test_positions_in_the_well_frame_are_one_frame():
    import pandas as pd

    from review.features.spatial.collect import field_ids

    df = pd.DataFrame({"fov": ["1", "2"], "well_x_um": [0.0, 1.0], "well_y_um": [0.0, 1.0]})
    assert field_ids(df, "µm") is None
    assert field_ids(df, "px").tolist() == ["1", "2"]
