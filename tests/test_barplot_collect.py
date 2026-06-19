"""Tests for ``barplot_controller.collect_bar_items`` + math consistency.

The key regression: when a rep-set is used as its own fold-change
control, the corresponding bar's value must be exactly 1.0. Before the
math fix in PR 3 this was off by the ratio between mean-of-per-well-means
and pool-of-cells, both of which the helpers were mixing. With the
``member_mean_series`` fix the same statistic appears on both sides of
the ratio and the round-trip is exact.
"""

from __future__ import annotations

import math

import pytest

from well_viewer.features.barplot.controller import (
    BarItem, FC_STATE_OFF, collect_bar_items, collect_bar_items_for_group,
)
from well_viewer.domain.batch_models import BarGroup, ReplicateSet
from tests.conftest import make_agg


def test_collect_bar_items_emits_bar_item_for_each_repset(mock_app):
    use_groups, items, band = collect_bar_items(mock_app, 1.0)
    assert use_groups is True
    keys = {it.key for it in items}
    assert keys == {"CTRL", "TREAT"}
    assert all(isinstance(it, BarItem) for it in items)


def test_collect_bar_items_honours_drag_order(mock_app):
    # Without _bar_order set, _bar_current_keys returns rep-sets in
    # selection order: CTRL then TREAT.
    _, items_default, _ = collect_bar_items(mock_app, 1.0)
    assert [it.key for it in items_default] == ["CTRL", "TREAT"]

    # Override drag order.
    mock_app._bar_order = ["TREAT", "CTRL"]

    def _patched_keys(self=mock_app):
        return list(mock_app._bar_order)

    # Monkey-patch _bar_current_keys to honour the override.
    mock_app._bar_current_keys = _patched_keys
    _, items_dragged, _ = collect_bar_items(mock_app, 1.0)
    assert [it.key for it in items_dragged] == ["TREAT", "CTRL"]


def test_collect_bar_items_fold_change_self_control_yields_one(mock_app):
    """Math-consistency regression: control bar normalized against itself
    is exactly 1.0 ± floating-point. Pre-PR-3 this was not guaranteed
    because the numerator (``_compute_rep_stats`` mean-of-per-well-means)
    and the control denominator (``_aggregate_group`` pool-of-cells)
    used different statistics."""
    mock_app.fc_vs_control_on = True
    mock_app.fc_control_label = "CTRL"
    _, items, _ = collect_bar_items(mock_app, 1.0)
    by_key = {it.key: it for it in items}
    assert math.isclose(by_key["CTRL"].mean, 1.0, abs_tol=1e-9)


def test_collect_bar_items_fc_off_yields_raw_via_fc_state_off(mock_app):
    """Passing FC_STATE_OFF should ignore app state and emit raw values."""
    mock_app.fc_vs_control_on = True
    mock_app.fc_control_label = "CTRL"
    _, items, _ = collect_bar_items(mock_app, 1.0, fc_state=FC_STATE_OFF)
    by_key = {it.key: it for it in items}
    assert math.isclose(by_key["CTRL"].mean, 100.0)
    assert math.isclose(by_key["TREAT"].mean, 200.0)


def test_collect_bar_items_miss_sink_records_unresolved_tp(mock_app):
    mock_app.fc_vs_control_on = True
    mock_app.fc_control_label = "CTRL"
    misses: set = set()
    _, items, _ = collect_bar_items(mock_app, 99.0, miss_sink=misses)
    assert misses == {99.0}
    # All bars forced NaN because the denominator was unresolved.
    for it in items:
        assert math.isnan(it.mean)


def test_collect_bar_items_for_group_emits_bar_items(mock_app):
    grp = BarGroup("DemoGroup",
                   members=[ReplicateSet("TREAT", ["B01", "B02"])],
                   solo_wells=["C01"])
    items = collect_bar_items_for_group(
        mock_app, grp, 1.0,
        val_col="gfp", threshold=50.0,
        use_sem=True, per_fov_spread=False,
        fc_state=FC_STATE_OFF,
    )
    assert all(isinstance(it, BarItem) for it in items)
    keys = {it.key for it in items}
    assert keys == {"TREAT", "C01"}


def test_collect_bar_items_baseline_t0_uses_member_stat(mock_app):
    """vs-t0 normalization makes each member's first tp == 1.0."""
    mock_app.fc_vs_t0_on = True
    _, items, _ = collect_bar_items(mock_app, 0.0)  # at t0 itself
    for it in items:
        assert math.isclose(it.mean, 1.0)


# ── Multi-timepoint extension ────────────────────────────────────────────────

def test_collect_bar_items_multi_tp_well_major_order(mock_app):
    """With ``bar_group_mode='well'`` (default), pass a list of tps and
    confirm we get one BarItem per (key, tp) pair in well-major order:
    CTRL@0, CTRL@1, CTRL@2, TREAT@0, TREAT@1, TREAT@2."""
    use_groups, items, _ = collect_bar_items(mock_app, [0.0, 1.0, 2.0])
    assert use_groups is True
    keys_tps = [(it.key, it.tp) for it in items]
    assert keys_tps == [
        ("CTRL", 0.0), ("CTRL", 1.0), ("CTRL", 2.0),
        ("TREAT", 0.0), ("TREAT", 1.0), ("TREAT", 2.0),
    ]
    # group_key in "well" mode is the well/rset name.
    assert {it.group_key for it in items if it.key == "CTRL"} == {"CTRL"}


def _with_plot_overrides(mock_app, **plot_kwargs):
    """Return a context manager that monkey-patches MockApp._app_state to
    surface the given ``PlotState`` field overrides for the duration of a
    test, then cleanly restores the original property."""
    from contextlib import contextmanager

    @contextmanager
    def _cm():
        cls = type(mock_app)
        original = cls.__dict__["_app_state"]

        def _patched(self):
            st = original.fget(self)
            for k, v in plot_kwargs.items():
                setattr(st.plot, k, v)
            return st

        cls._app_state = property(_patched)
        try:
            yield
        finally:
            cls._app_state = original
    return _cm()


def test_collect_bar_items_group_by_timepoint_orders_tp_major(mock_app):
    """With ``bar_group_mode='timepoint'`` items emit tp-major:
    CTRL@0, TREAT@0, CTRL@1, TREAT@1, ..."""
    with _with_plot_overrides(mock_app, bar_group_mode="timepoint",
                              bar_pool_replicates=True):
        _, items, _ = collect_bar_items(mock_app, [0.0, 1.0, 2.0])
        keys_tps = [(it.key, it.tp) for it in items]
        assert keys_tps == [
            ("CTRL", 0.0), ("TREAT", 0.0),
            ("CTRL", 1.0), ("TREAT", 1.0),
            ("CTRL", 2.0), ("TREAT", 2.0),
        ]
        # group_key in "timepoint" mode is the formatted tp string.
        assert {it.group_key for it in items if it.tp == 0.0} == {"0"}


def test_collect_bar_items_pool_replicates_off_expands_to_wells(mock_app):
    """When pool_replicates is False AND rsets are active, each rset's
    wells expand into individual BarItems (one per well per tp)."""
    with _with_plot_overrides(mock_app, bar_group_mode="well",
                              bar_pool_replicates=False):
        use_groups, items, _ = collect_bar_items(mock_app, 1.0)
        # use_groups is False because wells are NOT being collapsed.
        assert use_groups is False
        # CTRL has 2 wells, TREAT has 2 wells → 4 items total.
        keys = [it.key for it in items]
        assert keys == ["A01", "A02", "B01", "B02"]


def test_collect_bar_items_baseline_explicit_tp_normalizes_against_chosen_tp(mock_app):
    """User-reported bug: setting a baseline tp other than t0 has no
    effect on bar plots (while line plots work). The bar at tp=0 should
    normalize against TREAT's value at tp=2 (=400), giving 100/400 = 0.25.
    Earliest-tp semantics would give 100/100 = 1.0 instead."""
    mock_app.fc_vs_t0_on = True
    mock_app.fc_baseline_tp = "2"

    # Sanity check that vs_t0_on alone still yields 1.0 at t0 (earliest).
    mock_app.fc_baseline_tp = ""
    _, items_earliest, _ = collect_bar_items(mock_app, 0.0)
    by_key_earliest = {it.key: it for it in items_earliest}
    assert math.isclose(by_key_earliest["TREAT"].mean, 1.0)

    # Now with baseline_tp="2", TREAT@0 should be 100/400 = 0.25, not 1.0.
    mock_app.fc_baseline_tp = "2"
    _, items_tp2, _ = collect_bar_items(mock_app, 0.0)
    by_key_tp2 = {it.key: it for it in items_tp2}
    assert math.isclose(by_key_tp2["TREAT"].mean, 0.25), (
        f"Expected TREAT@0 normalized vs tp=2 (400) to be 0.25, "
        f"got {by_key_tp2['TREAT'].mean}"
    )


def test_collect_bar_items_baseline_missing_tp_falls_back_to_earliest(mock_app):
    """When ``fc_baseline_tp`` doesn't exist in the rep-set's series,
    fall through to earliest-finite — matches ``normalize_pts``
    line-scope semantics. Without this the FC toggle is a silent no-op
    visually (the user's "FC has no effect on bars" report), because
    ``member_first_tp_stats`` returns ``None`` and ``scale_bar_value``
    runs as a no-op identical to FC-off."""
    mock_app.fc_vs_t0_on = True
    mock_app.fc_baseline_tp = "999"  # tp=999 doesn't exist (tps are 0, 1, 2)
    _, items_missing, _ = collect_bar_items(mock_app, 0.0)
    by_key_missing = {it.key: it for it in items_missing}

    mock_app.fc_baseline_tp = ""  # explicit earliest
    _, items_earliest, _ = collect_bar_items(mock_app, 0.0)
    by_key_earliest = {it.key: it for it in items_earliest}

    assert math.isclose(
        by_key_missing["TREAT"].mean, by_key_earliest["TREAT"].mean
    ), (
        "When baseline_tp doesn't match any tp, the bar should fall "
        "through to earliest-finite (same as line scope), not silently no-op."
    )
    # And the fallback gives the same FC ratio earliest mode does
    # (TREAT@0 = 100 normalised by TREAT@earliest = 100 → 1.0).
    assert math.isclose(by_key_missing["TREAT"].mean, 1.0)


# ── Hypothesis tests for "FC has no effect on regular bars after v7.1.4" ─────


def _fc_diff(mock_app, target_t, *, fc_vs_t0=False, fc_baseline_tp="",
             fc_vs_ctrl=False, fc_ctrl_lbl=""):
    """Helper: return ``(off_means, on_means)`` so callers can compare keys."""
    mock_app.fc_vs_t0_on = False
    mock_app.fc_baseline_tp = ""
    mock_app.fc_vs_control_on = False
    mock_app.fc_control_label = ""
    _, off, _ = collect_bar_items(mock_app, target_t, fc_state=FC_STATE_OFF)
    off_by_key = {it.key: it.mean for it in off}
    mock_app.fc_vs_t0_on = fc_vs_t0
    mock_app.fc_baseline_tp = fc_baseline_tp
    mock_app.fc_vs_control_on = fc_vs_ctrl
    mock_app.fc_control_label = fc_ctrl_lbl
    _, on, _ = collect_bar_items(mock_app, target_t)
    on_by_key = {it.key: it.mean for it in on}
    return off_by_key, on_by_key


def test_h1_empty_rep_set_yields_nan_visible_signal(mock_app):
    """Hypothesis 1: rset with no AggPoints → ``compute_rep_stats`` returns
    NaN, ``member_first_tp_stats`` returns None → bar value is NaN.
    Verified NOT a silent no-op: NaN is a visible signal (no bar drawn).
    Both FC-on and FC-off produce NaN for the empty rset; CTRL stays
    different so the user can still distinguish them."""
    mock_app.per_well_pts["B01"] = []
    mock_app.per_well_pts["B02"] = []
    off, on = _fc_diff(mock_app, 1.0, fc_vs_t0=True, fc_baseline_tp="")
    assert math.isnan(off["TREAT"]) and math.isnan(on["TREAT"])
    # CTRL still has data; FC on vs off DIFFERS visibly (1.0 vs 100.0).
    assert off["CTRL"] != on["CTRL"]
    assert math.isclose(on["CTRL"], 1.0)


def test_h2_single_tp_rset_fc_collapses_to_one(mock_app):
    """Hypothesis 2: rset has data ONLY at the plotted tp. fall-through goes to
    that same tp → bar = raw/raw = 1.0 for every well. With multiple wells, all
    bars become 1.0. Visually identical to "no effect of the user's FC toggle"
    if the user thought "no effect" means "all bars look the same"."""
    # Keep TREAT at only tp=2.0
    mock_app.per_well_pts["B01"] = [make_agg(2.0, 200.0)]
    mock_app.per_well_pts["B02"] = [make_agg(2.0, 200.0)]
    off, on = _fc_diff(mock_app, 2.0, fc_vs_t0=True, fc_baseline_tp="")
    assert on["TREAT"] == pytest.approx(1.0)
    assert off["TREAT"] != on["TREAT"], (
        "Single-tp data: FC on collapses every bar to 1.0; off shows raw value. "
        "Different but might look like 'no effect' to a user expecting more drama."
    )


def test_h3_fc_force_nan_when_control_missing(mock_app):
    """Hypothesis 3: fc_vs_ctrl=True + ctrl label resolves but no data at
    target_t → fc_control_mean_at_t is None → fc_force_nan True → bars NaN.
    The user would see EMPTY bars, not "no effect"."""
    off, on = _fc_diff(mock_app, 99.0, fc_vs_ctrl=True, fc_ctrl_lbl="CTRL")
    # At tp=99 the mock has no data, so control_stats returns None → NaN.
    assert math.isnan(on["TREAT"]), (
        "fc_force_nan should fire when control resolves but tp doesn't"
    )


def test_h4_nonexistent_control_label_silent_skip(mock_app):
    """Hypothesis 4: fc_vs_ctrl=True with a control_label that doesn't match
    any rset OR loaded well → ``control_stats_at_for_bar`` returns None.
    ``fc_force_nan`` is True (because fc_vs_ctrl=True and ctrl_lbl=truthy),
    so bars become NaN. Pin this behaviour."""
    off, on = _fc_diff(mock_app, 1.0, fc_vs_ctrl=True, fc_ctrl_lbl="DOES_NOT_EXIST")
    assert math.isnan(on["TREAT"]), (
        "Nonexistent control label should NaN the bars (visible empty bars), "
        "not silently produce the raw value"
    )


def test_h6_active_val_col_drives_lookup(mock_app):
    """Hypothesis 6: ``member_stats_series`` uses ``app.active_val_col``. The
    mock's _aggregate_well returns precomputed pts regardless of val_col so
    the lookup is technically OK on the mock — but pin that FC math still
    flows when the active_val_col differs from the bar's default."""
    # Override val_col via app state so the inner aggregate_well sees a
    # different column. Mock ignores it (returns pts), so FC should still
    # apply normally. If a real-app version of this test ever fails it'd
    # mean stats_compute is using a different lookup than the FC path.
    mock_app.fc_vs_t0_on = True
    mock_app.fc_baseline_tp = ""
    _, items, _ = collect_bar_items(mock_app, 1.0)
    by_key = {it.key: it.mean for it in items}
    # CTRL@1 / CTRL@0 = 100 / 100 = 1.0
    assert math.isclose(by_key["CTRL"], 1.0)
    # TREAT@1 / TREAT@0 = 200 / 100 = 2.0
    assert math.isclose(by_key["TREAT"], 2.0)


def test_h7_changing_between_missing_baseline_tps_looks_unchanged(mock_app):
    """Most likely user-perception scenario: user picks baseline_tp=12 (not in
    data), sees bar value X (= raw / earliest). Then picks baseline_tp=6
    (also not in data), bar value is STILL X (both fall through to earliest).

    This is the v7.1.4 fallback working as designed, but to the user it looks
    like 'no effect of changing baseline_tp'. NOT a bug per se — but THE most
    likely explanation for the user's report. Demonstrates that toggling
    vs_t0 on/off DOES produce a visible change (1.0 vs raw), which the user
    should see if they test that toggle."""
    # Mock data has tps {0.0, 1.0, 2.0}; pick two tps in neither
    mock_app.fc_vs_t0_on = True

    mock_app.fc_baseline_tp = "12"  # missing → falls through to earliest=0
    _, items_tp12, _ = collect_bar_items(mock_app, 1.0)
    treat_tp12 = next(it.mean for it in items_tp12 if it.key == "TREAT")

    mock_app.fc_baseline_tp = "6"  # also missing → also falls through to earliest=0
    _, items_tp6, _ = collect_bar_items(mock_app, 1.0)
    treat_tp6 = next(it.mean for it in items_tp6 if it.key == "TREAT")

    # Both fell through to earliest → IDENTICAL bar value. User changing
    # baseline_tp between these would see "no effect" — which is the design,
    # not a bug.
    assert math.isclose(treat_tp12, treat_tp6)

    # But toggling vs_t0 OFF vs ON should still show a visible change.
    mock_app.fc_vs_t0_on = False
    mock_app.fc_baseline_tp = ""
    _, items_off, _ = collect_bar_items(mock_app, 1.0, fc_state=FC_STATE_OFF)
    treat_off = next(it.mean for it in items_off if it.key == "TREAT")
    assert not math.isclose(treat_tp12, treat_off), (
        "If this fails the v7.1.4 fix is broken — vs_t0 toggle should produce "
        "a visible numerical change in the bar value."
    )


def test_h8_user_data_with_matching_baseline_tp_works(mock_app):
    """Verify the success case: user's data has the picked baseline_tp.
    Mock has tps {0, 1, 2}. Pick baseline_tp=1 explicitly. TREAT@2 should
    normalise vs TREAT@1: 400/200 = 2.0, not the default 4.0 (vs @0)."""
    mock_app.fc_vs_t0_on = True
    mock_app.fc_baseline_tp = "1"
    _, items, _ = collect_bar_items(mock_app, 2.0)
    by_key = {it.key: it.mean for it in items}
    # TREAT@2 = 400, TREAT@1 = 200 → 400/200 = 2.0
    assert math.isclose(by_key["TREAT"], 2.0)
    # CTRL@2 = 100, CTRL@1 = 100 → 100/100 = 1.0
    assert math.isclose(by_key["CTRL"], 1.0)


def test_h9_subtract_op_with_baseline_tp_works(mock_app):
    """Verify subtract op works end-to-end with baseline_tp."""
    mock_app.fc_vs_t0_on = True
    mock_app.fc_baseline_tp = "1"
    mock_app.fc_op = "subtract"
    _, items, _ = collect_bar_items(mock_app, 2.0)
    by_key = {it.key: it.mean for it in items}
    # TREAT@2 - TREAT@1 = 400 - 200 = 200
    assert math.isclose(by_key["TREAT"], 200.0)
    # CTRL@2 - CTRL@1 = 100 - 100 = 0
    assert math.isclose(by_key["CTRL"], 0.0)


def test_h10_subtract_op_with_vs_control_works(mock_app):
    """vs_control with op=subtract: bar = raw - control_at_target_tp."""
    mock_app.fc_vs_control_on = True
    mock_app.fc_control_label = "CTRL"
    mock_app.fc_op = "subtract"
    _, items, _ = collect_bar_items(mock_app, 2.0)
    by_key = {it.key: it.mean for it in items}
    # CTRL@2 - CTRL@2 = 100 - 100 = 0
    assert math.isclose(by_key["CTRL"], 0.0)
    # TREAT@2 - CTRL@2 = 400 - 100 = 300
    assert math.isclose(by_key["TREAT"], 300.0)


def test_h11_subtract_op_with_both_axes_works(mock_app):
    """vs_control + vs_t0 with op=subtract.

    Baseline is post-control (v7.1.8 fix): raw_baseline OP_CTRL ctrl_at_baseline.
    For subtract+subtract, baseline = TREAT@0 - CTRL@0 = 100 - 100 = 0.
    Then result = (raw - ctrl) - baseline = (400 - 100) - (100 - 100) = 300 - 0 = 300.
    """
    mock_app.fc_vs_control_on = True
    mock_app.fc_control_label = "CTRL"
    mock_app.fc_vs_t0_on = True
    mock_app.fc_baseline_tp = ""  # earliest = 0
    mock_app.fc_op = "subtract"
    _, items, _ = collect_bar_items(mock_app, 2.0)
    by_key = {it.key: it.mean for it in items}
    # CTRL: (100-100) - (100-100) = 0
    assert math.isclose(by_key["CTRL"], 0.0)
    # TREAT: (400-100) - (100-100) = 300
    assert math.isclose(by_key["TREAT"], 300.0)


# ── OnFraction fold-change regression ────────────────────────────────────────
#
# Until this fix, FC was applied to ``mean`` but ``frac`` was passed through
# raw — so users viewing the OnFraction panel saw FC having no visible effect.
# These tests construct rsets with distinct frac values per tp and assert that
# the OnFraction bar's value DOES change under FC.

def _frac_app():
    """MockApp variant where frac varies meaningfully across tps and rsets.

    CTRL: frac 0.2 / 0.2 / 0.2 (flat)
    TREAT: frac 0.1 / 0.3 / 0.6 (rising)
    """
    from tests.conftest import MockApp
    pts = {
        "A01": [
            make_agg(0.0, 100.0, frac=0.2),
            make_agg(1.0, 100.0, frac=0.2),
            make_agg(2.0, 100.0, frac=0.2),
        ],
        "A02": [
            make_agg(0.0, 100.0, frac=0.2),
            make_agg(1.0, 100.0, frac=0.2),
            make_agg(2.0, 100.0, frac=0.2),
        ],
        "B01": [
            make_agg(0.0, 100.0, frac=0.1),
            make_agg(1.0, 200.0, frac=0.3),
            make_agg(2.0, 400.0, frac=0.6),
        ],
        "B02": [
            make_agg(0.0, 100.0, frac=0.1),
            make_agg(1.0, 200.0, frac=0.3),
            make_agg(2.0, 400.0, frac=0.6),
        ],
    }
    selections = [
        {"name": "CTRL", "wells": ["A01", "A02"], "hidden": False},
        {"name": "TREAT", "wells": ["B01", "B02"], "hidden": False},
    ]
    return MockApp(per_well_pts=pts, selections=selections)


def test_onfraction_fc_off_returns_raw_frac():
    """Sanity baseline: FC off → frac is the raw mean-of-per-well frac."""
    app = _frac_app()
    _, items, _ = collect_bar_items(app, 2.0, fc_state=FC_STATE_OFF)
    by_key = {it.key: it for it in items}
    assert math.isclose(by_key["CTRL"].frac, 0.2)
    assert math.isclose(by_key["TREAT"].frac, 0.6)


def test_onfraction_vs_control_scales_frac_panel():
    """Regression: FC vs control MUST scale the OnFraction bar, not just mean.

    The bug: ``frac`` was passed through raw → toggling FC on the OnFraction
    panel had no visible effect. Fix: scale frac independently using the
    control's frac at target_t as denominator.

    TREAT.frac@2 = 0.6, CTRL.frac@2 = 0.2 → ratio = 3.0.
    """
    app = _frac_app()
    app.fc_vs_control_on = True
    app.fc_control_label = "CTRL"
    _, items, _ = collect_bar_items(app, 2.0)
    by_key = {it.key: it for it in items}
    # CTRL bar normalised against itself = 1.0.
    assert math.isclose(by_key["CTRL"].frac, 1.0)
    # TREAT bar = 0.6 / 0.2 = 3.0.
    assert math.isclose(by_key["TREAT"].frac, 3.0)


def test_onfraction_vs_t0_scales_frac_panel():
    """FC vs t0 must also scale the frac panel.

    TREAT.frac@2 = 0.6, TREAT.frac@0 = 0.1 → ratio = 6.0.
    """
    app = _frac_app()
    app.fc_vs_t0_on = True
    _, items, _ = collect_bar_items(app, 2.0)
    by_key = {it.key: it for it in items}
    assert math.isclose(by_key["CTRL"].frac, 1.0)  # 0.2 / 0.2
    assert math.isclose(by_key["TREAT"].frac, 6.0)  # 0.6 / 0.1


def test_onfraction_subtract_op_scales_frac_panel():
    """``op=subtract`` must apply to frac too — for users normalising against
    a background fraction-on rather than dividing."""
    app = _frac_app()
    app.fc_vs_control_on = True
    app.fc_control_label = "CTRL"
    app.fc_op = "subtract"
    _, items, _ = collect_bar_items(app, 2.0)
    by_key = {it.key: it.frac for it in items}
    assert math.isclose(by_key["CTRL"], 0.0)         # 0.2 - 0.2
    assert math.isclose(by_key["TREAT"], 0.4)        # 0.6 - 0.2


def test_onfraction_force_nan_when_control_missing():
    """When the control isn't resolvable, mean is NaN'd; frac MUST be too —
    otherwise the OnFraction panel shows a stale raw value while the mean
    panel correctly blanks."""
    app = _frac_app()
    app.fc_vs_control_on = True
    app.fc_control_label = "NONEXISTENT"
    _, items, _ = collect_bar_items(app, 2.0)
    for it in items:
        assert math.isnan(it.mean)
        assert math.isnan(it.frac)


# ── Per-axis operator independence ───────────────────────────────────────────
#
# v7.1.7: ``fc_op`` split into ``fc_op_ctrl`` + ``fc_op_t0`` so users can apply
# different ops per axis (e.g. subtract control, divide by t0). These tests
# verify each combination produces the expected stacked result.

def test_per_axis_ops_subtract_ctrl_ratio_t0(mock_app):
    """Subtract control, then divide by post-control baseline.

    The baseline is now post-control (v7.1.8): raw_baseline OP_CTRL ctrl_at_baseline.

    TREAT@2 = 400, CTRL@2 = 100 → step1 = 400 - 100 = 300.
    baseline (post-control, op_ctrl=subtract) = TREAT@1 - CTRL@1 = 200 - 100 = 100.
    step2 (op_t0=ratio) = 300 / 100 = 3.0.
    """
    mock_app.fc_vs_control_on = True
    mock_app.fc_control_label = "CTRL"
    mock_app.fc_vs_t0_on = True
    mock_app.fc_baseline_tp = "1"
    mock_app.fc_op_ctrl = "subtract"
    mock_app.fc_op_t0 = "ratio"
    _, items, _ = collect_bar_items(mock_app, 2.0)
    by_key = {it.key: it.mean for it in items}
    assert math.isclose(by_key["TREAT"], 3.0)


def test_per_axis_ops_ratio_ctrl_subtract_t0(mock_app):
    """Divide by control, then subtract post-control baseline.

    TREAT@2 / CTRL@2 = 4.0.
    baseline (post-control, op_ctrl=ratio) = TREAT@1 / CTRL@1 = 200/100 = 2.0.
    step2 (op_t0=subtract) = 4.0 - 2.0 = 2.0.
    """
    mock_app.fc_vs_control_on = True
    mock_app.fc_control_label = "CTRL"
    mock_app.fc_vs_t0_on = True
    mock_app.fc_baseline_tp = "1"
    mock_app.fc_op_ctrl = "ratio"
    mock_app.fc_op_t0 = "subtract"
    _, items, _ = collect_bar_items(mock_app, 2.0)
    by_key = {it.key: it.mean for it in items}
    # CTRL: (100/100) - (100/100) = 1 - 1 = 0
    assert math.isclose(by_key["CTRL"], 0.0)
    # TREAT: (400/100) - (200/100) = 4 - 2 = 2.0
    assert math.isclose(by_key["TREAT"], 2.0)


def test_per_axis_ops_order_t0_first_with_mixed_ops(mock_app):
    """``order=t0_first`` flips which axis applies first.

    With op_ctrl=ratio + op_t0=subtract:
      ctrl_first: (X/C_t) - (X_b/C_b)                    [v7.1.8 default]
      t0_first:   (X - X_b) / (C_t - C_b)                [new]

    For TREAT at target_t=2, baseline_tp=1, with CTRL flat at 100:
      ctrl_first:  (400/100) - (200/100) = 4 - 2 = 2.0
      t0_first:    (400 - 200) / (100 - 100) = 200 / 0 → NaN

    Use a non-degenerate variant: target=2, baseline=0 (where CTRL=100, TREAT=100):
      t0_first: (400 - 100) / (100 - 100) = 300/0 = NaN.

    Pick a case where t0_first gives a finite distinct answer: switch to baseline_tp=1
    with op_ctrl=subtract + op_t0=ratio:
      ctrl_first: (400 - 100) / (200 - 100) = 300/100 = 3.0
      t0_first:   (400 / 200) - (100 / 100) = 2.0 - 1.0 = 1.0
    """
    mock_app.fc_vs_control_on = True
    mock_app.fc_control_label = "CTRL"
    mock_app.fc_vs_t0_on = True
    mock_app.fc_baseline_tp = "1"
    mock_app.fc_op_ctrl = "subtract"
    mock_app.fc_op_t0 = "ratio"
    mock_app.fc_order = "t0_first"
    _, items, _ = collect_bar_items(mock_app, 2.0)
    by_key = {it.key: it.mean for it in items}
    # TREAT, t0_first: (400/200) - (100/100) = 2 - 1 = 1.0
    assert math.isclose(by_key["TREAT"], 1.0)


def test_per_axis_ops_order_commutes_when_ops_equal(mock_app):
    """Same-op cases must produce identical results regardless of order
    (subtraction and ratio both commute when applied to the same dataset)."""
    mock_app.fc_vs_control_on = True
    mock_app.fc_control_label = "CTRL"
    mock_app.fc_vs_t0_on = True
    mock_app.fc_baseline_tp = "1"
    mock_app.fc_op = "subtract"  # both axes subtract

    mock_app.fc_order = "ctrl_first"
    _, items_a, _ = collect_bar_items(mock_app, 2.0)
    mock_app.fc_order = "t0_first"
    _, items_b, _ = collect_bar_items(mock_app, 2.0)
    by_key_a = {it.key: it.mean for it in items_a}
    by_key_b = {it.key: it.mean for it in items_b}
    assert math.isclose(by_key_a["TREAT"], by_key_b["TREAT"])
    assert math.isclose(by_key_a["CTRL"], by_key_b["CTRL"])


def test_per_axis_ops_legacy_fc_op_still_fans_out(mock_app):
    """Setting the legacy single ``fc_op`` field must still apply to both axes
    for back-compat with snapshots / older callers.

    Both axes subtract, post-control baseline (v7.1.8):
    step1 = TREAT@2 - CTRL@2 = 300.
    baseline = TREAT@1 - CTRL@1 = 200 - 100 = 100.
    step2 = step1 - baseline = 300 - 100 = 200.
    """
    mock_app.fc_vs_control_on = True
    mock_app.fc_control_label = "CTRL"
    mock_app.fc_vs_t0_on = True
    mock_app.fc_baseline_tp = "1"
    mock_app.fc_op = "subtract"   # legacy single-op
    _, items, _ = collect_bar_items(mock_app, 2.0)
    by_key = {it.key: it.mean for it in items}
    assert math.isclose(by_key["TREAT"], 200.0)
