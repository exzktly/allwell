"""The Bar tab's Histogram mode and bin count are saved, restored and reset with its view."""
from __future__ import annotations

import types

import pytest

from review.persistence import view_state as vs

from tests.conftest import MockApp, make_agg


def _ctx():
    app = MockApp(per_well_pts={"A01": [make_agg(0.0, 1.0)]}, selections=[])
    ctx = app._ctx
    ctx.widgets = types.SimpleNamespace(get_or_none=lambda _n: None)
    return ctx


def _modes(bar):
    return bar.swarm, bar.violin, bar.histogram


def test_histogram_and_its_bins_survive_a_save_and_restore():
    ctx = _ctx()
    bar = ctx.state.feature_slice("bar")
    bar.histogram, bar.hist_bins = True, 17
    saved = vs._snapshot_bar(ctx)
    assert (saved["swarm"], saved["violin"], saved["histogram"], saved["hist_bins"]) == (False, False, True, 17)
    bar.histogram, bar.hist_bins = False, 30
    vs._apply_bar(ctx, saved)
    assert _modes(bar) == (False, False, True) and bar.hist_bins == 17


def test_an_older_view_naming_violin_turns_a_live_histogram_off():
    ctx = _ctx()
    bar = ctx.state.feature_slice("bar")
    bar.histogram = True
    vs._apply_bar(ctx, {"swarm": False, "violin": True})          # written before histograms existed
    assert _modes(bar) == (False, True, False)


def test_a_damaged_view_with_two_modes_on_keeps_one():
    ctx = _ctx()
    bar = ctx.state.feature_slice("bar")
    vs._apply_bar(ctx, {"swarm": True, "violin": True, "histogram": True})
    assert _modes(bar) == (False, False, True)
    vs._apply_bar(ctx, {"swarm": True, "violin": True})
    assert _modes(bar) == (False, True, False)


def test_a_view_naming_no_mode_leaves_the_mode_alone():
    ctx = _ctx()
    bar = ctx.state.feature_slice("bar")
    bar.histogram = True
    vs._apply_bar(ctx, {"group_mode": "well"})
    assert _modes(bar) == (False, False, True)


@pytest.mark.parametrize("bad", [0, 1, 201, 1000, "12", 12.0, True, None, float("nan")])
def test_a_bin_count_outside_the_box_is_not_restored(bad):
    ctx = _ctx()
    bar = ctx.state.feature_slice("bar")
    bar.hist_bins = 22
    vs._apply_bar(ctx, {"hist_bins": bad})
    assert bar.hist_bins == 22


def test_reset_returns_to_bars_and_the_default_bin_count():
    ctx = _ctx()
    bar = ctx.state.feature_slice("bar")
    bar.histogram, bar.hist_bins = True, 50
    window = types.SimpleNamespace(_app_state=ctx.state)
    vs._reset_bar(window)
    assert _modes(bar) == (False, False, False) and bar.hist_bins == 30
