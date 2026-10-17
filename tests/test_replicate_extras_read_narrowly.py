"""``group_replicate_stats`` takes a well's median and all-cell mean from one narrow, remembered read.

Tracker R1 (``_Docs/Markdowns/REREAD_TRACKER.md``). The per-well branch used to
call ``_raw_extra`` on the well's whole frame for every group, timepoint and
producer that asked — on a plate larger than the row cache, a full read from
disk each time, 80 of the 165 an open of an 8-well plate of 1.5M-cell wells
made. ``_well_extra`` reads the well once through the aggregate's narrow read
and computes every timepoint in that pass.

The fixture is the narrow-read one (``tests/test_narrow_rows.py``) with every
verdict layer switched on, so a read that dropped a verdict's input column
counts excluded cells, and doubled across two timepoints whose values differ
threefold, so an answer filed under the wrong timepoint is wrong by a factor of
three rather than equal.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from review.data import data_loading as dl
from tests.test_narrow_rows import LAYERS, RATIO, WELL, _frame, _ratio_scatter_gating, _switch_on

pytest.importorskip("pyarrow.parquet")

TIMES = (0.0, 24.0)


def _two_timepoints() -> pd.DataFrame:
    first = _frame()
    later = _frame()
    later["timepoint"] = "24"
    later["timepoint_hours"] = 24.0
    for col in ("gfp_mean_intensity", "cy5_mean_intensity", "mcherry_mean_intensity"):
        later[col] = later[col] * 3.0
    return pd.concat([first, later], ignore_index=True)


@pytest.fixture
def ctx(tmp_path):
    from review.core.app_runtime import AppRuntime
    from review.core.app_state import AppState
    from review.core.event_bus import EventBus
    from review.core.widget_registry import WidgetRegistry
    from review.data.parquet_cache import read_cached
    from review.data.well_repository import WellRepository
    from review.features.registry import attach_feature_state, build_feature_registry
    from review.viewer_context import ViewerContext
    from tests.test_narrow_rows import _field_quality_block

    build_feature_registry()
    state = AppState()
    attach_feature_state(state)
    path = tmp_path / f"{WELL}.csv"
    _two_timepoints().to_csv(path, index=False)
    state.dataset.data_dir = tmp_path
    state.dataset.well_paths = {WELL: path}
    state.dataset.pipeline_info = _field_quality_block()
    state.dataset.fov_integrity = {}
    state.channel.ratio_index = {RATIO.key(): RATIO}
    store: dict = {}
    c = ViewerContext(state=state, bus=EventBus(), widgets=WidgetRegistry(None, store=store),
                      runtime=AppRuntime(None, store=store))
    state.repo = WellRepository(c)
    dl.read_well_frame(path)
    assert read_cached(path) is not None
    for layer in LAYERS:
        _switch_on(c, layer)
    return c


def _expected(ctx, target_t, threshold, val_col, gates, on_col):
    """``_raw_extra`` on the whole frame — the path the memo replaces."""
    from review.domain.stats_compute import _raw_extra

    ctx.state.dataset.cache.clear()
    full = dl.get_rows(ctx, WELL)
    ctx.state.dataset.cache.clear()
    return _raw_extra(full, target_t, threshold, val_col, gates, ctx.state.channel.ratio_index, on_col)


def _count_reads(monkeypatch) -> list:
    from review.data import parquet_cache

    calls: list = []
    real = parquet_cache.read_cached

    def counted(path, columns=None):
        calls.append(columns)
        return real(path, columns=columns)

    monkeypatch.setattr(parquet_cache, "read_cached", counted)
    return calls


@pytest.mark.parametrize("val_col, threshold", [("mcherry_mean_intensity", 250.0), (RATIO.key(), 9.0)])
def test_the_replicate_extras_are_the_whole_frame_s(ctx, val_col, threshold):
    from review.domain.stats_compute import group_replicate_stats

    gates = {"cy5": 6.0}
    ctx.state.dataset.cache.clear()
    for t in TIMES:
        (rep,) = group_replicate_stats(ctx, [WELL], t, use_fov=False, val_col=val_col,
                                       threshold=threshold, fluor_gates=gates)
        median, mean_all = _expected(ctx, t, threshold, val_col, gates, None)
        assert np.isfinite(median) and np.isfinite(mean_all), "nothing was on; NaN equals NaN"
        assert (rep.median, rep.mean_all) == pytest.approx((median, mean_all), rel=1e-12), t
    other = _expected(ctx, TIMES[1], threshold, val_col, gates, None)
    assert other != pytest.approx(_expected(ctx, TIMES[0], threshold, val_col, gates, None)), (
        "the two timepoints agree, so an answer filed under the wrong one would pass")


def test_a_thresholding_on_column_decides_above(ctx):
    from review.domain.stats_compute import _well_extra

    ctx.state.cell_gating.applied_gating = _ratio_scatter_gating()
    ctx.state.dataset.cache.clear()
    val_col, ratios = "mcherry_mean_intensity", ctx.state.channel.ratio_index
    for t in TIMES:
        got = _well_extra(ctx, WELL, t, 250.0, val_col, {}, ratios, "Gate_On")
        want = _expected(ctx, t, 250.0, val_col, {}, "Gate_On")
        assert got == pytest.approx(want, rel=1e-12, nan_ok=True), t
        assert got != pytest.approx(_expected(ctx, t, 250.0, val_col, {}, None), nan_ok=True), (
            "Gate_On picks the same cells as the threshold; this measures nothing")


def test_a_well_is_read_once_narrowly_for_every_timepoint_and_producer(ctx, monkeypatch):
    from review.domain.stats_compute import group_replicate_stats

    ctx.state.dataset.cache.clear()
    calls = _count_reads(monkeypatch)
    kw = dict(use_fov=False, val_col="mcherry_mean_intensity", threshold=250.0, fluor_gates={"cy5": 6.0})
    for _producer in range(3):
        for t in TIMES:
            assert group_replicate_stats(ctx, [WELL], t, **kw)
    assert WELL not in ctx.state.dataset.cache, "a whole frame was read into the row cache"
    assert len(calls) == 2, f"expected the aggregate's read and the extras' read, got {len(calls)}"
    assert all(cols is not None and "m0" not in cols for cols in calls), "a read was not narrow"


def test_a_target_between_the_well_s_timepoints_is_computed_for_itself(ctx):
    """A target matched only by tolerance is not a key of the table; it must
    still be the whole-frame answer, not NaN and not a neighbour's entry."""
    from domain.tp_match import tp_close
    from review.domain.stats_compute import _well_extra

    near = 24.0 + 0.01
    assert tp_close(near, 24.0) and near not in TIMES
    ctx.state.dataset.cache.clear()
    got = _well_extra(ctx, WELL, near, 250.0, "mcherry_mean_intensity", {}, ctx.state.channel.ratio_index, None)
    assert got == pytest.approx(_expected(ctx, near, 250.0, "mcherry_mean_intensity", {}, None), rel=1e-12)
