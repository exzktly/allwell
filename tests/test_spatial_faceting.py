"""Spatial tab multi-well faceting.

Boots the real ``ReviewApp`` with a shape-feature dataset (which carries
the ``shape_centroid_*`` columns the Spatial tab needs), selects several wells,
and asserts the per-frame levels (Cell / Colony / Intra-colony) compute one
payload per selected well and draw one panel per well — rather than collapsing
the selection to just the first well.
"""

from __future__ import annotations

import pytest

from tests.fixtures.mini_dataset import WELLS
from review.features.spatial import state as sp_state


def _open_spatial(app):
    app._set_selected_wells(set(app._app_state.dataset.well_paths))
    assert app._notebook.setCurrentByName("Spatial")
    assert hasattr(app, "_sp_canvas"), "Spatial tab did not build"
    return app._app_state.feature_slice("spatial")


@pytest.mark.slow
def test_spatial_map_facets_every_selected_well(loaded_app_shape):
    app = loaded_app_shape
    from review.features.spatial import controller as spc

    st = _open_spatial(app)
    st.level = sp_state.LEVEL_CELL
    st.viz = sp_state.VIZ_SPATIAL_MAP
    spc.sp_recompute(app._ctx)

    # One frame per selected well (not just the first), keyed by well label.
    assert not st.last_error, st.last_error
    assert len(st.frames) == len(WELLS)
    assert {f["well"] for f in st.frames} == set(WELLS)
    assert all(f.get("n_cells", 0) > 0 for f in st.frames)

    # One drawn panel per well (Cell-level map with no metric = no colorbar).
    fig = app._ctx.widgets.get_or_none("_sp_fig")
    assert len(fig.axes) == len(WELLS)


@pytest.mark.slow
def test_distribution_viz_facets_per_well(loaded_app_shape):
    """NND histogram (a distribution viz) also draws one panel per selected
    well, matching the scatter/distribution tab faceting."""
    app = loaded_app_shape
    from review.features.spatial import controller as spc

    st = _open_spatial(app)
    st.level = sp_state.LEVEL_CELL
    st.viz = sp_state.VIZ_NND_HISTOGRAM
    spc.sp_recompute(app._ctx)

    assert len(st.frames) == len(WELLS)
    fig = app._ctx.widgets.get_or_none("_sp_fig")
    assert len(fig.axes) == len(WELLS)


@pytest.mark.slow
def test_single_well_still_single_panel(loaded_app_shape):
    app = loaded_app_shape
    from review.features.spatial import controller as spc

    _open_spatial(app)
    app._set_selected_wells({WELLS[0]})
    st = app._app_state.feature_slice("spatial")
    st.level = sp_state.LEVEL_CELL
    st.viz = sp_state.VIZ_SPATIAL_MAP
    spc.sp_recompute(app._ctx)

    assert len(st.frames) == 1
    assert st.frames[0]["well"] == WELLS[0]
    fig = app._ctx.widgets.get_or_none("_sp_fig")
    assert len(fig.axes) == 1


@pytest.mark.slow
def test_summary_level_unaffected(loaded_app_shape):
    """Summary level stays a single collapsed matrix (not faceted) and leaves
    ``frames`` empty."""
    app = loaded_app_shape
    from review.features.spatial import controller as spc

    st = _open_spatial(app)
    st.level = sp_state.LEVEL_SUMMARY
    st.viz = sp_state.VIZ_SUMMARY_PANEL
    spc.sp_recompute(app._ctx)

    assert not st.last_error, st.last_error
    assert st.frames == []
    assert st.last_result.get("kind") == "summary_matrix"


@pytest.mark.slow
def test_colony_vs_intra_map_recolours_even_with_metric(loaded_app_shape):
    """Toggling Colony ↔ Intra-colony must visibly change the spatial map.

    Regression: the map used to colour by a picked metric at *every* level, so
    the two levels rendered identically whenever a metric was selected. The
    Intra-colony map now always expresses radial position (r/R), so the
    colorbar label differs between the levels.
    """
    app = loaded_app_shape
    from review.features.spatial import controller as spc

    _open_spatial(app)
    app._set_selected_wells({WELLS[0]})
    st = app._app_state.feature_slice("spatial")
    st.viz = sp_state.VIZ_SPATIAL_MAP
    st.metric_col = "gfp_mean_intensity"  # a real per-cell column → metric set
    fig = app._ctx.widgets.get_or_none("_sp_fig")

    st.level = sp_state.LEVEL_COLONY
    spc.sp_recompute(app._ctx)
    assert st.frames and st.frames[0].get("metric") is not None
    colony_labels = {a.get_ylabel() for a in fig.axes}
    assert "gfp_mean_intensity" in colony_labels  # metric colours the map
    assert "r / R" not in colony_labels

    st.level = sp_state.LEVEL_INTRA
    spc.sp_recompute(app._ctx)
    intra_labels = {a.get_ylabel() for a in fig.axes}
    assert "r / R" in intra_labels  # radial position colours the map
    assert "gfp_mean_intensity" not in intra_labels
    assert colony_labels != intra_labels
