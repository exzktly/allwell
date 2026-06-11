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


from well_viewer.features.barplot.controller import (
    BarItem, FC_STATE_OFF, collect_bar_items, collect_bar_items_for_group,
)
from well_viewer.domain.batch_models import BarGroup, ReplicateSet


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
