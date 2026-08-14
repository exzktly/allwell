"""Unit tests for ``well_viewer.fold_change``.

Pure-logic tests — no Qt, no matplotlib. Run with::

    python -m pytest tests/test_fold_change.py -v
"""

from __future__ import annotations

import math


from well_viewer.domain.fold_change import (
    _match_control_mean, _TP_REL_TOL, _rel_err_sq, _propagate_spread,
    apply_cell_scale, build_cell_scaling,
    control_mean_at_for_bar, control_stats_at_for_bar,
    fold_change_state, fold_change_suffix,
    member_first_tp_stats, member_first_tp_value,
    member_mean_series, member_stats_series,
    normalize_pts, pts_to_mean_by_t, resolve_control_wells,
    scale_bar_value, first_tp_value,
)


# ── pts_to_mean_by_t / _match_control_mean ──────────────────────────────────

def test_pts_to_mean_by_t_skips_nan():
    pts = [(0.0, 1.0, 0, 0, 0, 0), (1.0, float("nan"), 0, 0, 0, 0),
           (2.0, 3.0, 0, 0, 0, 0)]
    assert pts_to_mean_by_t(pts) == {0.0: 1.0, 2.0: 3.0}


def _agg(t, mean, frac, n_above, *, frac_spread=0.0, n_pf_mean=0.0, n_pf_spread=0.0):
    # (t, mean, spread, frac, n_above, n_total, frac_spread, n_pf_mean, n_pf_spread)
    return (t, mean, 0.0, frac, n_above, 100, frac_spread, n_pf_mean, n_pf_spread)


def test_normalize_frac_n_scales_fraction_and_count_panels():
    """With ``normalize_frac_n`` the fraction ([3]) and N-events ([4]) fields
    rescale alongside the mean — the Line Graphs panels no longer stay at raw
    values under fold change."""
    pts = [_agg(0.0, 10.0, 0.4, 20), _agg(1.0, 20.0, 0.8, 40)]
    ctrl_mean = {0.0: (10.0, 0.0), 1.0: (10.0, 0.0)}
    ctrl_frac = {0.0: (0.4, 0.0), 1.0: (0.4, 0.0)}
    ctrl_n = {0.0: (20.0, 0.0), 1.0: (20.0, 0.0)}

    out = normalize_pts(
        pts, control_stats=ctrl_mean,
        control_frac_stats=ctrl_frac, control_n_stats=ctrl_n,
        normalize_frac_n=True,
    )
    # mean: 10/10=1, 20/10=2 ; frac: 0.4/0.4=1, 0.8/0.4=2 ; n: 20/20=1, 40/20=2
    assert out[0][1] == 1.0 and out[1][1] == 2.0
    assert out[0][3] == 1.0 and out[1][3] == 2.0
    assert out[0][4] == 1.0 and out[1][4] == 2.0


def test_normalize_frac_n_flag_off_leaves_frac_and_count_raw():
    """Default callers (bar / batch / CSV) don't pass the flag, so only the mean
    is normalized and the count field keeps its integer value."""
    pts = [_agg(0.0, 10.0, 0.4, 20), _agg(1.0, 20.0, 0.8, 40)]
    ctrl_mean = {0.0: (10.0, 0.0), 1.0: (10.0, 0.0)}
    out = normalize_pts(pts, control_stats=ctrl_mean)
    assert out[0][1] == 1.0 and out[1][1] == 2.0
    # frac + n_above untouched (raw)
    assert out[0][3] == 0.4 and out[1][3] == 0.8
    assert out[0][4] == 20 and out[1][4] == 40


def test_normalize_frac_n_per_fov_scales_index_7_8():
    """In FOV-spread mode the N panel plots the per-FOV-mean ([7]/[8]); FC scales
    that field, not the total-count [4]."""
    pts = [_agg(0.0, 10.0, 0.4, 20, n_pf_mean=5.0), _agg(1.0, 20.0, 0.8, 40, n_pf_mean=10.0)]
    ctrl_mean = {0.0: (10.0, 0.0), 1.0: (10.0, 0.0)}
    ctrl_n = {0.0: (5.0, 0.0), 1.0: (5.0, 0.0)}
    out = normalize_pts(
        pts, control_stats=ctrl_mean, control_n_stats=ctrl_n,
        normalize_frac_n=True, n_per_fov=True,
    )
    assert out[0][7] == 1.0 and out[1][7] == 2.0


def test_match_control_mean_exact():
    assert _match_control_mean(1.0, {0.0: 10.0, 1.0: 20.0}) == 20.0


def test_match_control_mean_relative_tolerance_regression():
    # The bar tp combo formats with :.4g so 2/3 parses back as 0.6667.
    # Old absolute 1e-6 tolerance missed this; relative tolerance catches it.
    t_combo = float("0.6667")
    t_data = 2.0 / 3.0
    assert _match_control_mean(t_combo, {t_data: 42.0}) == 42.0


def test_match_control_mean_empty():
    assert _match_control_mean(0.0, {}) is None


def test_match_control_mean_far_miss():
    # Truly mismatched values are still rejected.
    assert _match_control_mean(0.0, {1.0: 5.0}) is None


def test_tp_rel_tol_value():
    # Document the constant — if this changes, downstream behaviour shifts.
    assert _TP_REL_TOL == 1e-3


# ── normalize_pts ───────────────────────────────────────────────────────────

def test_normalize_pts_control_only():
    pts = [(0.0, 100.0, 10.0, 0.5, 5, 10), (1.0, 200.0, 20.0, 0.5, 5, 10)]
    out = normalize_pts(pts, control_means={0.0: 100.0, 1.0: 100.0})
    assert math.isclose(out[0][1], 1.0)
    assert math.isclose(out[1][1], 2.0)
    # Spread divides by the same factor.
    assert math.isclose(out[0][2], 0.1)
    assert math.isclose(out[1][2], 0.2)


def test_normalize_pts_t0_only():
    pts = [(0.0, 50.0, 5.0, 0.5, 5, 10), (1.0, 200.0, 20.0, 0.5, 5, 10)]
    out = normalize_pts(pts, use_t0=True)
    # First finite mean becomes 1.0.
    assert math.isclose(out[0][1], 1.0)
    assert math.isclose(out[1][1], 4.0)


def test_normalize_pts_ddct_stacking():
    """Stacking is ΔΔCt-style: (X/C@t) / (X/C@0), not (X/X@0) / (C/C@0)."""
    pts = [(0.0, 100.0, 0, 0, 0, 0), (1.0, 300.0, 0, 0, 0, 0)]
    ctrl = {0.0: 50.0, 1.0: 100.0}
    out = normalize_pts(pts, control_means=ctrl, use_t0=True)
    # Stage 1: control: 100/50=2.0, 300/100=3.0.
    # Stage 2: t0 baseline = 2.0 → 2/2=1, 3/2=1.5.
    assert math.isclose(out[0][1], 1.0)
    assert math.isclose(out[1][1], 1.5)


def test_normalize_pts_missing_control_drops_to_nan_and_records_miss():
    pts = [(0.0, 100.0, 0, 0, 0, 0), (1.0, 200.0, 0, 0, 0, 0)]
    ctrl = {0.0: 50.0}  # no sample at t=1.0
    misses: set = set()
    out = normalize_pts(pts, control_means=ctrl, miss_sink=misses)
    assert math.isclose(out[0][1], 2.0)
    assert math.isnan(out[1][1])
    assert misses == {1.0}


def test_normalize_pts_empty_input():
    assert normalize_pts([]) == []


# ── scale_bar_value ─────────────────────────────────────────────────────────

def test_scale_bar_value_no_normalization():
    m, s = scale_bar_value(10.0, 1.0)
    assert (m, s) == (10.0, 1.0)


def test_scale_bar_value_control_only():
    m, s = scale_bar_value(10.0, 1.0, control_mean=2.0)
    assert (m, s) == (5.0, 0.5)


def test_scale_bar_value_t0_only():
    m, s = scale_bar_value(10.0, 1.0, t0_mean=5.0)
    assert (m, s) == (2.0, 0.2)


def test_scale_bar_value_both_axes():
    m, s = scale_bar_value(10.0, 1.0, control_mean=2.0, t0_mean=5.0)
    # 10/2 = 5; 5/5 = 1.0
    assert math.isclose(m, 1.0)
    assert math.isclose(s, 0.1)


def test_scale_bar_value_zero_control_yields_nan():
    m, s = scale_bar_value(10.0, 1.0, control_mean=0.0)
    assert math.isnan(m)
    assert s == 0.0


def test_scale_bar_value_zero_t0_yields_nan():
    m, s = scale_bar_value(10.0, 1.0, t0_mean=0.0)
    assert math.isnan(m)


def test_scale_bar_value_nan_mean_propagates():
    m, s = scale_bar_value(float("nan"), 1.0, control_mean=2.0)
    assert math.isnan(m)


# ── first_tp_value ──────────────────────────────────────────────────────────

def test_first_tp_value_returns_earliest_finite():
    pts = [(1.0, 2.0, 0, 0, 0, 0), (0.0, 1.0, 0, 0, 0, 0),
           (2.0, 3.0, 0, 0, 0, 0)]
    assert first_tp_value(pts) == 1.0  # mean at t=0.0


def test_first_tp_value_skips_nan():
    pts = [(0.0, float("nan"), 0, 0, 0, 0), (1.0, 5.0, 0, 0, 0, 0)]
    assert first_tp_value(pts) == 5.0


def test_first_tp_value_empty():
    assert first_tp_value([]) is None


# ── fold_change_state / suffix ──────────────────────────────────────────────

def test_fold_change_state_reads_app(mock_app):
    mock_app.fc_vs_control_on = True
    mock_app.fc_control_label = "CTRL"
    fc = fold_change_state(mock_app._ctx)
    assert fc.vs_control_on is True
    assert fc.control_label == "CTRL"
    assert fc.vs_t0_on is False


def test_fold_change_suffix_off():
    assert fold_change_suffix(False, False) == ""


def test_fold_change_suffix_control_only():
    assert "vs CTRL" in fold_change_suffix(True, False, "CTRL")


def test_fold_change_suffix_both():
    s = fold_change_suffix(True, True, "CTRL")
    assert "vs CTRL" in s
    assert "vs t0" in s


def test_fold_change_suffix_names_operator():
    s = fold_change_suffix(True, True, "CTRL", op_ctrl="ratio", op_t0="subtract")
    assert "vs CTRL (ratio)" in s
    assert "vs t0 (subtraction)" in s


def test_fold_change_suffix_names_baseline_timepoint():
    # The vs-t0 axis now states which timepoint is the baseline.
    s = fold_change_suffix(False, True, op_t0="subtract", baseline_label="t=0h")
    assert "vs t=0h (subtraction)" in s
    # Falls back to "t0" when no explicit baseline label is given.
    assert "vs t0" in fold_change_suffix(False, True)


def test_fold_change_suffix_for_uses_resolved_baseline():
    from types import SimpleNamespace
    from well_viewer.domain.fold_change import fold_change_suffix_for

    def _app(baseline_tp):
        plot = SimpleNamespace(
            fc_vs_control_on=False, fc_vs_t0_on=True, fc_control_label="",
            fc_op_ctrl="ratio", fc_op_t0="subtract", fc_baseline_tp=baseline_tp,
        )
        return SimpleNamespace(state=SimpleNamespace(plot=plot))

    assert "vs t=24h (subtraction)" in fold_change_suffix_for(_app("24"))
    # Empty baseline → each member's earliest point.
    assert "vs first timepoint (subtraction)" in fold_change_suffix_for(_app(""))


# ── resolve_control_wells ───────────────────────────────────────────────────

def test_resolve_control_wells_rep_set(mock_app):
    assert resolve_control_wells(mock_app._ctx, "CTRL") == ["A01", "A02"]


def test_resolve_control_wells_solo(mock_app):
    assert resolve_control_wells(mock_app._ctx, "C01") == ["C01"]


def test_resolve_control_wells_unknown(mock_app):
    assert resolve_control_wells(mock_app._ctx, "ZZ99") == []


def test_resolve_control_wells_empty(mock_app):
    assert resolve_control_wells(mock_app._ctx, "") == []
    assert resolve_control_wells(mock_app._ctx, None) == []


def test_resolve_control_wells_well_suffix_bypasses_repset(mock_app):
    # Pretend a rep-set is named "A01" — same as a well token.
    mock_app.selections.append({"name": "A01", "wells": ["B01"]})
    # Bare "A01" → rep-set (which points at B01)
    assert resolve_control_wells(mock_app._ctx, "A01") == ["B01"]
    # Suffixed "A01 (well)" → the actual A01 well
    assert resolve_control_wells(mock_app._ctx, "A01 (well)") == ["A01"]


# ── member_mean_series / control_mean_at_for_bar ────────────────────────────

def _kw(threshold=50.0, val_col="gfp", use_sem=True, per_fov_spread=False,
        fluor_gates=None):
    return dict(threshold=threshold, val_col=val_col, use_sem=use_sem,
                per_fov_spread=per_fov_spread,
                fluor_gates=fluor_gates or {})


def test_member_mean_series_repset_uses_mean_of_means(mock_app):
    series = member_mean_series(mock_app._ctx, "TREAT", **_kw())
    # Both B01 and B02 have means 100, 200, 400 — mean-of-means is identical.
    assert math.isclose(series[0.0], 100.0)
    assert math.isclose(series[1.0], 200.0)
    assert math.isclose(series[2.0], 400.0)


def test_member_mean_series_solo_well(mock_app):
    series = member_mean_series(mock_app._ctx, "C01", **_kw())
    assert math.isclose(series[0.0], 50.0)
    assert math.isclose(series[2.0], 200.0)


def test_member_mean_series_unknown_label(mock_app):
    assert member_mean_series(mock_app._ctx, "ZZ99", **_kw()) == {}


def test_control_mean_at_for_bar_repset(mock_app):
    v = control_mean_at_for_bar(mock_app._ctx, "CTRL", 1.0, **_kw())
    assert math.isclose(v, 100.0)


def test_control_mean_at_for_bar_missing_tp(mock_app):
    # Target tp not in any well of the control rep-set.
    v = control_mean_at_for_bar(mock_app._ctx, "CTRL", 99.0, **_kw())
    assert v is None


def test_member_first_tp_value_repset(mock_app):
    v = member_first_tp_value(mock_app._ctx, "TREAT", **_kw())
    assert math.isclose(v, 100.0)  # mean of B01, B02 at t=0


def test_member_first_tp_value_well(mock_app):
    v = member_first_tp_value(mock_app._ctx, "C01", **_kw())
    assert math.isclose(v, 50.0)


def test_member_first_tp_value_unknown(mock_app):
    assert member_first_tp_value(mock_app._ctx, "ZZ99", **_kw()) is None


# ── build_cell_scaling ──────────────────────────────────────────────────────

def _apply_one(resolver, well, tp, value):
    """Resolve the spec and apply it to a single-element array; return the
    scalar (or nan when the spec is missing)."""
    import numpy as np
    spec = resolver(well, tp)
    if spec.missing:
        return float("nan")
    out = apply_cell_scale(np.array([float(value)], dtype=float), spec)
    return float(out[0])


def test_build_cell_scaling_off():
    # No fold-change axes → spec is a no-op (apply leaves the value untouched).
    resolver = build_cell_scaling(
        None, ["A01"], fc_state=(False, "", False), **_kw(),
    )
    spec = resolver("A01", 0.0)
    assert not spec.missing
    assert math.isclose(_apply_one(resolver, "A01", 0.0, 42.0), 42.0)


def test_build_cell_scaling_control_only_divides(mock_app):
    # vs CTRL, ratio: cell value 200 / control mean 100 = 2.0.
    resolver = build_cell_scaling(
        mock_app._ctx, ["B01", "B02"], fc_state=(True, "CTRL", False), **_kw(),
    )
    assert math.isclose(_apply_one(resolver, "B01", 1.0, 200.0), 2.0)
    assert math.isclose(_apply_one(resolver, "B02", 1.0, 200.0), 2.0)


def test_build_cell_scaling_control_subtract(mock_app):
    # S1: with op_ctrl="subtract" the overlay SUBTRACTS the control mean
    # (200 - 100 = 100) instead of always dividing (which gave 2.0).
    resolver = build_cell_scaling(
        mock_app._ctx, ["B01"], fc_state=(True, "CTRL", False),
        op_ctrl="subtract", **_kw(),
    )
    assert math.isclose(_apply_one(resolver, "B01", 1.0, 200.0), 100.0)


def test_build_cell_scaling_t0_only(mock_app):
    # vs t0, ratio: B01/C01 baselines are 100 / 50.
    resolver = build_cell_scaling(
        mock_app._ctx, ["B01", "C01"], fc_state=(False, "", True), **_kw(),
    )
    assert math.isclose(_apply_one(resolver, "B01", 2.0, 300.0), 3.0)
    assert math.isclose(_apply_one(resolver, "C01", 2.0, 300.0), 6.0)


def test_build_cell_scaling_both(mock_app):
    # control_mean (100) then t0_mean (100), both ratio: 10000 / 100 / 100 = 1.0.
    resolver = build_cell_scaling(
        mock_app._ctx, ["B01"], fc_state=(True, "CTRL", True), **_kw(),
    )
    assert math.isclose(_apply_one(resolver, "B01", 1.0, 10000.0), 1.0)


def test_build_cell_scaling_control_missing_is_blank(mock_app):
    # tp 99 doesn't exist in CTRL data → spec.missing → caller blanks the slot.
    resolver = build_cell_scaling(
        mock_app._ctx, ["B01"], fc_state=(True, "CTRL", False), **_kw(),
    )
    assert resolver("B01", 99.0).missing


def test_build_cell_scaling_per_tp_control(mock_app):
    # S3: the resolver honours each column's own tp — control C01's mean differs
    # between t=0 (50) and t=1 (100), so the same cell value scales differently
    # instead of collapsing to the first tp.
    resolver = build_cell_scaling(
        mock_app._ctx, ["B01"], fc_state=(True, "C01", False), **_kw(),
    )
    assert math.isclose(_apply_one(resolver, "B01", 0.0, 100.0), 2.0)   # /50
    assert math.isclose(_apply_one(resolver, "B01", 1.0, 100.0), 1.0)   # /100


# ── Error propagation ───────────────────────────────────────────────────────

def test_rel_err_sq_basic():
    # (10% relative error)² = 0.01
    assert math.isclose(_rel_err_sq(100.0, 10.0), 0.01)


def test_rel_err_sq_safe_for_zero_value():
    assert _rel_err_sq(0.0, 1.0) == 0.0


def test_rel_err_sq_safe_for_nan_spread():
    assert _rel_err_sq(10.0, float("nan")) == 0.0


def test_propagate_spread_no_denom_error_matches_simple_divide():
    # With denom_rel_err_sq=0, the propagated spread equals |spread/factor|
    # — the legacy behaviour. new_mean = 50, mean = 100, spread = 10
    # → simple divide: 10/2 = 5; quadrature: 50 * |10/100| = 5. Match.
    assert math.isclose(_propagate_spread(50.0, 100.0, 10.0, 0.0), 5.0)


def test_propagate_spread_with_denom_error():
    # X/C = 100/2 = 50. σ_X/X = 0.1, σ_C/C = 0.2.
    # σ_Y/Y = sqrt(0.01 + 0.04) = sqrt(0.05) ≈ 0.2236
    # σ_Y = 50 * 0.2236 ≈ 11.18
    out = _propagate_spread(50.0, 100.0, 10.0, 0.04)
    assert math.isclose(out, 50.0 * math.sqrt(0.05))


def test_scale_bar_value_backwards_compat_exact_denom():
    """No control_spread → matches the legacy behaviour exactly."""
    m, s = scale_bar_value(100.0, 10.0, control_mean=2.0)
    assert (m, s) == (50.0, 5.0)


def test_scale_bar_value_with_control_spread_propagates():
    """Control's 10% relative error joins the numerator's 10% in quadrature."""
    m, s = scale_bar_value(100.0, 10.0, control_mean=2.0, control_spread=0.2)
    assert math.isclose(m, 50.0)
    # σ_Y = 50 * sqrt((10/100)² + (0.2/2)²) = 50 * sqrt(0.02) ≈ 7.071
    assert math.isclose(s, 50.0 * math.sqrt(0.02))


def test_scale_bar_value_both_axes_with_spreads():
    """All three relative errors stack in quadrature."""
    m, s = scale_bar_value(
        100.0, 10.0,
        control_mean=2.0, control_spread=0.2,
        t0_mean=5.0, t0_spread=0.5,
    )
    # mean: 100 / 2 / 5 = 10
    assert math.isclose(m, 10.0)
    # σ_Y/Y = sqrt(0.01 + 0.01 + 0.01) = sqrt(0.03)
    assert math.isclose(s, 10.0 * math.sqrt(0.03))


def test_scale_bar_value_spread_always_nonneg():
    """Negative inputs don't yield a negative spread."""
    m, s = scale_bar_value(100.0, 10.0, control_mean=-2.0, control_spread=0.2)
    assert math.isclose(m, -50.0)
    # Spread is always |new_mean| * sqrt(...) — non-negative.
    assert s >= 0


def test_normalize_pts_control_only_with_stats_propagates():
    """control_stats path threads the control's spread through."""
    pts = [(0.0, 100.0, 10.0, 0.5, 5, 10), (1.0, 200.0, 20.0, 0.5, 5, 10)]
    stats = {0.0: (100.0, 10.0), 1.0: (100.0, 10.0)}
    out = normalize_pts(pts, control_stats=stats)
    # Means: 1.0 and 2.0 (X/C).
    assert math.isclose(out[0][1], 1.0)
    assert math.isclose(out[1][1], 2.0)
    # Spreads include the control's 10% relative error.
    # σ_Y[0] = 1.0 * sqrt(0.01 + 0.01) = sqrt(0.02)
    assert math.isclose(out[0][2], math.sqrt(0.02))
    # σ_Y[1] = 2.0 * sqrt(0.01 + 0.01) = 2*sqrt(0.02)
    assert math.isclose(out[1][2], 2.0 * math.sqrt(0.02))


def test_normalize_pts_t0_with_baseline_spread_propagates():
    """vs-t0 picks up the baseline's spread from the AggPoint."""
    pts = [(0.0, 50.0, 5.0, 0.5, 5, 10), (1.0, 100.0, 10.0, 0.5, 5, 10)]
    out = normalize_pts(pts, use_t0=True)
    # Baseline: (50, 5) → rel err 0.1.
    # out[0]: mean = 50/50 = 1; spread = 1 * sqrt(0.01 + 0.01) = sqrt(0.02)
    assert math.isclose(out[0][1], 1.0)
    assert math.isclose(out[0][2], math.sqrt(0.02))
    # out[1]: mean = 100/50 = 2; spread = 2 * sqrt(0.01 + 0.01) = 2*sqrt(0.02)
    assert math.isclose(out[1][1], 2.0)
    assert math.isclose(out[1][2], 2.0 * math.sqrt(0.02))


def test_normalize_pts_control_means_still_works():
    """Legacy control_means parameter path treats denominator as exact."""
    pts = [(0.0, 100.0, 10.0, 0.5, 5, 10)]
    out = normalize_pts(pts, control_means={0.0: 100.0})
    # No control spread → simple divide.
    assert math.isclose(out[0][1], 1.0)
    assert math.isclose(out[0][2], 0.1)


def test_member_stats_series_returns_means_and_spreads(mock_app):
    series = member_stats_series(mock_app._ctx, "TREAT", **_kw())
    # MockApp._compute_rep_stats returns (mean, 0.0, frac, 0.0) — spread
    # is 0 here, but the shape we care about is (mean, spread).
    for t, (m, s) in series.items():
        assert isinstance(m, float)
        assert isinstance(s, float)
        assert s >= 0


def test_control_stats_at_for_bar_returns_tuple(mock_app):
    stats = control_stats_at_for_bar(mock_app._ctx, "CTRL", 1.0, **_kw())
    assert stats is not None
    mean, spread = stats
    assert math.isclose(mean, 100.0)
    assert spread >= 0


def test_member_first_tp_stats_returns_tuple(mock_app):
    stats = member_first_tp_stats(mock_app._ctx, "C01", **_kw())
    assert stats is not None
    mean, spread = stats
    assert math.isclose(mean, 50.0)
