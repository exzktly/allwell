"""Characterization tests for the bar / line collection pipeline.

These pin the *current* output of ``collect_bar_items`` / ``collect_line_series``
run against a real ``WellViewerApp`` — not the ``MockApp`` of the pure-logic
suites. Expected values are derived from the deterministic mini-dataset formula,
so any structural refactor that perturbs the collection pipeline trips a test.
"""

from __future__ import annotations

import math

import pytest

from tests.fixtures.mini_dataset import (
    CELLS_PER_TP, TIMEPOINTS, WELLS, cell_gfp, cell_mcherry,
)
from well_viewer.features.barplot.controller import collect_bar_items
from well_viewer.core.channel_state_controller import set_active_channel
from well_viewer.features.lineplot.controller import collect_line_series

pytestmark = pytest.mark.qt


def _expected_mean(value_fn, well, tp_hours):
    cells = range(1, CELLS_PER_TP + 1)
    return sum(value_fn(well, tp_hours, c) for c in cells) / CELLS_PER_TP


@pytest.fixture
def all_wells_selected(loaded_app):
    loaded_app._set_selected_wells(set(WELLS))
    return loaded_app


def test_collect_bar_items_per_well_means(all_wells_selected):
    for _label, tp_hours in TIMEPOINTS:
        _, items, _ = collect_bar_items(all_wells_selected._ctx, tp_hours)
        by_key = {it.key: it.mean for it in items}
        assert set(by_key) == set(WELLS)
        for well in WELLS:
            assert math.isclose(
                by_key[well], _expected_mean(cell_gfp, well, tp_hours), rel_tol=1e-6,
            )


def test_collect_bar_items_order_is_sorted(all_wells_selected):
    _, items, _ = collect_bar_items(all_wells_selected._ctx, 0.0)
    assert [it.key for it in items] == sorted(WELLS)


def test_collect_bar_items_stable_across_calls(all_wells_selected):
    _, first, _ = collect_bar_items(all_wells_selected._ctx, 24.0)
    _, second, _ = collect_bar_items(all_wells_selected._ctx, 24.0)
    assert ([(it.key, it.mean) for it in first]
            == [(it.key, it.mean) for it in second])


def test_collect_line_series_per_well_points(all_wells_selected):
    series, _ = collect_line_series(
        all_wells_selected._ctx,
        threshold=all_wells_selected._app_state.channel.threshold,
        use_sem=all_wells_selected._app_state.plot.use_sem,
    )
    by_key = {s.key: s for s in series}
    assert set(by_key) == set(WELLS)
    for well in WELLS:
        pts = {p.t: p.mean for p in by_key[well].points}
        for _label, tp_hours in TIMEPOINTS:
            assert math.isclose(
                pts[tp_hours], _expected_mean(cell_gfp, well, tp_hours), rel_tol=1e-6,
            )


def test_collect_bar_items_follows_active_channel(all_wells_selected):
    set_active_channel(all_wells_selected._ctx, "mcherry")
    _, items, _ = collect_bar_items(all_wells_selected._ctx, 0.0)
    by_key = {it.key: it.mean for it in items}
    for well in WELLS:
        assert math.isclose(
            by_key[well], _expected_mean(cell_mcherry, well, 0.0), rel_tol=1e-6,
        )
