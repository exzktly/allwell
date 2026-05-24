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
