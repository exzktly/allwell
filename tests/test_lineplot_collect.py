"""Tests for ``lineplot_controller.collect_line_series``.

Pure-logic tests using the MockApp fixture from conftest.py — verifies
the line plot's single-source-of-truth collector emits the right
LineSeries shape for rep-set and per-well modes, honours drag order,
and applies fold-change once (matching the math-consistency invariant
from PR 3).
"""

from __future__ import annotations

import math


from well_viewer.features.barplot.controller import FC_STATE_OFF
from well_viewer.features.lineplot.controller import (
    LinePoint, collect_line_series,
)


def _kw(use_sem=True):
    """Default kwargs the collector needs from a MockApp's state."""
    return dict(threshold=50.0, use_sem=use_sem)


def test_collect_line_series_per_well_emits_one_series_per_well(mock_app):
    # No rep-sets active → per-well mode iterates ``_selected_wells``
    mock_app.selections = []
    series, band = collect_line_series(mock_app._ctx, **_kw())
    assert band == "SEM"
    keys = sorted(s.key for s in series)
    assert keys == ["A01", "A02", "B01", "B02", "C01"]
    for s in series:
        assert s.kind == "well"
        assert isinstance(s.points, list)
        assert all(isinstance(p, LinePoint) for p in s.points)


def test_collect_line_series_repset_mode_uses_mean_of_means(mock_app):
    # MockApp.selections defines CTRL (A01+A02) and TREAT (B01+B02).
    series, _ = collect_line_series(mock_app._ctx, **_kw())
    keys = {s.key for s in series}
    assert keys == {"CTRL", "TREAT"}
    treat = next(s for s in series if s.key == "TREAT")
    # B01/B02 both have mean 200 at t=1 → mean-of-means = 200.
    pts_at_1 = [p for p in treat.points if math.isclose(p.t, 1.0)]
    assert len(pts_at_1) == 1
    assert math.isclose(pts_at_1[0].mean, 200.0)


def test_collect_line_series_fc_state_off_yields_raw(mock_app):
    """Passing FC_STATE_OFF skips fold-change scaling even when the app
    has ``_fc_vs_control_on=True``."""
    mock_app.fc_vs_control_on = True
    mock_app.fc_control_label = "CTRL"
    series, _ = collect_line_series(mock_app._ctx, fc_state=FC_STATE_OFF, **_kw())
    by_key = {s.key: s for s in series}
    ctrl_pt = next(p for p in by_key["CTRL"].points if math.isclose(p.t, 1.0))
    # Raw mean: 100 (mean-of-means of A01/A02 at t=1).
    assert math.isclose(ctrl_pt.mean, 100.0)


def test_collect_line_series_fc_self_control_yields_one(mock_app):
    """Math-consistency regression — when CTRL is its own control, its
    series' points all equal 1.0 (same fix as the bar plot's PR 3)."""
    mock_app.fc_vs_control_on = True
    mock_app.fc_control_label = "CTRL"
    series, _ = collect_line_series(mock_app._ctx, **_kw())
    by_key = {s.key: s for s in series}
    for p in by_key["CTRL"].points:
        if p.has_mean:
            assert math.isclose(p.mean, 1.0, abs_tol=1e-9)


def test_collect_line_series_fc_treat_against_ctrl(mock_app):
    """TREAT's values at each tp divided by CTRL's mean at the same tp."""
    mock_app.fc_vs_control_on = True
    mock_app.fc_control_label = "CTRL"
    series, _ = collect_line_series(mock_app._ctx, **_kw())
    by_key = {s.key: s for s in series}
    # CTRL = 100 at every t. TREAT = 100/200/400 at t=0/1/2.
    # Ratio = 1.0 / 2.0 / 4.0.
    treat = by_key["TREAT"]
    by_t = {p.t: p for p in treat.points}
    assert math.isclose(by_t[0.0].mean, 1.0)
    assert math.isclose(by_t[1.0].mean, 2.0)
    assert math.isclose(by_t[2.0].mean, 4.0)


def test_collect_line_series_t0_baseline(mock_app):
    """vs-t0 baselines each member to its own earliest finite mean."""
    mock_app.fc_vs_t0_on = True
    series, _ = collect_line_series(mock_app._ctx, **_kw())
    for s in series:
        if not s.points:
            continue
        # Earliest point becomes ~1.0.
        first = min((p for p in s.points if p.has_mean), key=lambda p: p.t)
        assert math.isclose(first.mean, 1.0, abs_tol=1e-9)


def test_collect_line_series_miss_sink_records_unresolved(mock_app):
    """When the control has no sample at a member's timepoint, the
    miss is recorded in the sink and the point goes NaN."""
    # CTRL data only has t=0,1,2. Drop one tp from CTRL to force a miss.
    mock_app.per_well_pts["A01"] = mock_app.per_well_pts["A01"][:2]
    mock_app.per_well_pts["A02"] = mock_app.per_well_pts["A02"][:2]
    # Now CTRL has no sample at t=2.
    mock_app.fc_vs_control_on = True
    mock_app.fc_control_label = "CTRL"
    misses: set = set()
    _, _ = collect_line_series(mock_app._ctx, miss_sink=misses, **_kw())
    assert 2.0 in misses


def test_collect_line_series_well_branch_honours_drag_order(mock_app):
    """``_line_order_wells`` reorders per-well traces."""
    mock_app.selections = []  # disable rep-sets to enter per-well branch
    mock_app._line_order_wells = ["C01", "B01"]
    series, _ = collect_line_series(mock_app._ctx, **_kw())
    keys = [s.key for s in series]
    # Pre-ordered (C01 then B01) come first; the rest in their natural order.
    assert keys[:2] == ["C01", "B01"]


def test_collect_line_series_includes_n_wells_in_display(mock_app):
    """Rep-set series with >1 wells get '(n=N)' in their display label."""
    series, _ = collect_line_series(mock_app._ctx, **_kw())
    treat = next(s for s in series if s.key == "TREAT")
    assert "n=2" in treat.display


def test_linepoint_as_aggpoint_round_trip():
    """LinePoint → AggPoint tuple → preserves all fields needed by line_metric_row."""
    pt = (1.5, 100.0, 10.0, 0.5, 5, 10, 0.05, 1.0, 0.2)
    from well_viewer.features.lineplot.controller import _aggpoint_to_linepoint
    lp = _aggpoint_to_linepoint(pt)
    out = lp.as_aggpoint()
    assert out[0] == pt[0]
    assert out[1] == pt[1]
    assert out[2] == pt[2]
    assert out[3] == pt[3]
    assert out[4] == pt[4]
    assert out[5] == pt[5]
    assert out[6] == pt[6]


def test_collect_line_series_fov_spread_populates_frac_spread(mock_app, monkeypatch):
    """Regression: in FOV-error (per-FOV spread) mode the rep-set collector must
    carry the per-FOV fraction error (``compute_rep_per_fov_stats``'s 4th return)
    into ``LinePoint.frac_spread``. It used to drop it (hardcoded 0.0), so the
    "fraction above threshold" subplot had no error band and fell back to shading
    the area under the line.

    Per the unified replicate rule, per-FOV spread applies only to a SINGLE-well
    set (FOVs are its replicates); a multi-well set is per-well. So this uses a
    single-well active set to exercise the per-FOV path.
    """
    import well_viewer.features.lineplot.controller as lp

    # Single-well active set → replicate_unit == "fov" when FOV mode is on. The
    # collector now reads rep-sets / FOV flag from ``ctx.state`` (A8), so drive
    # them through the MockApp's typed-state fields rather than method stubs.
    mock_app.selections = [{"name": "SOLO", "wells": ["A01"], "hidden": False}]
    # Force FOV-spread mode and stub the per-FOV stats with a known non-zero
    # fraction error (idx 3) + per-FOV n-above mean/spread (idx 4/5).
    monkeypatch.setattr(mock_app, "_use_fov_replicates", True, raising=False)
    monkeypatch.setattr(
        lp, "compute_rep_per_fov_stats",
        lambda ctx, rset, t, thr, sem: (100.0, 5.0, 0.4, 0.12, 3.0, 1.0),
    )

    series, _ = collect_line_series(mock_app._ctx, **_kw())
    frac_pts = [p for s in series for p in s.points if p.has_frac]
    assert frac_pts, "expected fraction points in rep-set FOV-spread mode"
    assert all(math.isclose(p.frac, 0.4) for p in frac_pts)
    assert all(math.isclose(p.frac_spread, 0.12) for p in frac_pts), \
        [p.frac_spread for p in frac_pts]
