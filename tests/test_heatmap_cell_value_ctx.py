"""A8 R2 — the heat-map value grid is computed from ``ctx`` (not the god-object).

``redraw_heatmap`` and its helpers were converted to take ``ctx``. This locks in
the *data* path: ``_cell_value(ctx, …)`` must resolve the aggregation delegate
via ``ctx.app``, the rep-set-average toggle via ``ctx.state.heatmap``, and the
loaded wells via ``ctx.state.dataset`` — producing the same per-cell scalar the
god-object path did. (The imshow/colorbar *paint* is covered by an interactive
smoke, not here.)
"""
from __future__ import annotations

from types import SimpleNamespace

from well_viewer.core.app_runtime import AppRuntime
from well_viewer.features.heatmap import controller as C
from well_viewer.features.heatmap.controller import (
    METRIC_COUNT, METRIC_FRACTION, METRIC_MEAN,
)


def _agg(t, mean, *, frac=0.5, n_above=5, n_total=10):
    # AggPoint tuple: (t, mean, spread, frac, n_above, n_total, …)
    return (t, mean, 0.0, frac, n_above, n_total, 0.0, 0.0, 0.0)


def _ctx(*, pts_by_group, well_paths, repset_avg=False):
    """A minimal ctx over a pure-logic app that serves ``_aggregate_group``
    from an in-memory {frozenset(wells): [AggPoint]} map."""
    app = SimpleNamespace()

    def _aggregate_group(wells, **_kw):
        return list(pts_by_group.get(frozenset(wells), []))

    app._aggregate_group = _aggregate_group

    from well_viewer.core.app_state import AppState
    from well_viewer.features.registry import attach_feature_state
    st = AppState()
    attach_feature_state(st)
    st.dataset.well_paths = dict(well_paths)
    st.channel.active_val_col = "gfp_mean_intensity"
    st.heatmap.repset_avg = repset_avg
    return SimpleNamespace(app=app, state=st, runtime=AppRuntime(app))


def test_cell_value_mean_reads_delegate_via_ctx():
    ctx = _ctx(
        pts_by_group={frozenset({"A01"}): [_agg(0.0, 42.0), _agg(6.0, 99.0)]},
        well_paths={"A01": "A01"},
    )
    v = C._cell_value(ctx, ["A01"], 6.0, METRIC_MEAN,
                      "gfp_mean_intensity", 50.0, {})
    assert v == 99.0


def test_cell_value_count_and_fraction_via_ctx():
    ctx = _ctx(
        pts_by_group={frozenset({"A01"}): [_agg(0.0, 42.0, frac=0.25,
                                                 n_above=3, n_total=12)]},
        well_paths={"A01": "A01"},
    )
    assert C._cell_value(ctx, ["A01"], 0.0, METRIC_COUNT,
                         "gfp_mean_intensity", 50.0, {}) == 12.0
    assert C._cell_value(ctx, ["A01"], 0.0, METRIC_FRACTION,
                         "gfp_mean_intensity", 50.0, {}) == 0.25


def test_cell_value_drops_unloaded_wells():
    """A well absent from ``ctx.state.dataset.well_paths`` is excluded; with no
    valid wells the cell is NaN."""
    ctx = _ctx(pts_by_group={}, well_paths={"A01": "A01"})
    v = C._cell_value(ctx, ["Z99"], 0.0, METRIC_MEAN,
                      "gfp_mean_intensity", 50.0, {})
    assert v != v  # NaN


def test_cell_value_missing_timepoint_is_nan_for_mean():
    ctx = _ctx(
        pts_by_group={frozenset({"A01"}): [_agg(0.0, 42.0)]},
        well_paths={"A01": "A01"},
    )
    v = C._cell_value(ctx, ["A01"], 99.0, METRIC_MEAN,
                      "gfp_mean_intensity", 50.0, {})
    assert v != v  # NaN — no AggPoint at t=99
