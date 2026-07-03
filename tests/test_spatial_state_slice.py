"""SpatialState slice registration test — matches the pilot A8 pattern.

Ensures ``AppState.spatial`` is attached by ``attach_feature_state`` on
construction, and that reading it before any slice mutation returns the
declared defaults from ``SpatialState``.
"""

from __future__ import annotations

from well_viewer.core.app_state import AppState
from well_viewer.features.registry import attach_feature_state
from well_viewer.features.spatial import state as sp_state


def test_spatial_slice_attached_with_defaults():
    st = AppState()
    attach_feature_state(st)
    spatial = st.feature_slice("spatial")
    assert spatial is not None
    assert spatial.level == sp_state.LEVEL_CELL
    assert spatial.viz == sp_state.VIZ_SPATIAL_MAP
    assert spatial.colony_method == sp_state.METHOD_DBSCAN
    assert spatial.colony_eps_mult == 3.0
    assert spatial.colony_min_samples == 4
    assert spatial.boundary_pct == 20.0
    assert spatial.show_hulls is True
    assert spatial.n_cells == 0
    assert spatial.last_result == {}


def test_spatial_config_round_trip():
    from well_viewer.features.spatial.state import SpatialState
    a = SpatialState()
    a.level = sp_state.LEVEL_INTRA
    a.viz = sp_state.VIZ_RADIAL_PROFILE
    a.metric_col = "gfp_mean_intensity"
    a.colony_eps_mult = 2.5
    a.boundary_pct = 15.0
    payload = a.to_config()
    b = SpatialState()
    b.apply_config(payload)
    assert b.level == sp_state.LEVEL_INTRA
    assert b.viz == sp_state.VIZ_RADIAL_PROFILE
    assert b.metric_col == "gfp_mean_intensity"
    assert b.colony_eps_mult == 2.5
    assert b.boundary_pct == 15.0
