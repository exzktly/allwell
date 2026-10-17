"""With FOV replicates on, the bar plot's prefetch builds the populations the draw uses.

``prefetch_populations`` reads the wells the row cache does not hold several at
a time, so the bar collector's loop finds every population remembered. It always
built them *without* field labels, and with FOV replicates on the collector asks
``aggregate_well_cached(per_fov_spread=True)``, which needs them — so every well
was rebuilt one after another, the parallel prefetch was wasted, and the first
draw read each well twice.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from review.data import data_loading as dl
from tests.test_narrow_rows import LAYERS, RATIO, _frame, _switch_on
from tests.test_replicate_extras_read_narrowly import _count_reads

pytest.importorskip("pyarrow.parquet")

WELLS = ("A01", "A02", "A03")
VAL = "mcherry_mean_intensity"
GATES = {"cy5": 6.0}


@pytest.fixture
def ctx(tmp_path):
    from review.core.app_runtime import AppRuntime
    from review.core.app_state import AppState
    from review.core.event_bus import EventBus
    from review.core.widget_registry import WidgetRegistry
    from review.data.well_repository import WellRepository
    from review.features.registry import attach_feature_state, build_feature_registry
    from review.viewer_context import ViewerContext
    from tests.test_narrow_rows import _field_quality_block

    build_feature_registry()
    state = AppState()
    attach_feature_state(state)
    paths = {}
    for well in WELLS:
        path = tmp_path / f"{well}.csv"
        _frame().assign(well=well).to_csv(path, index=False)
        dl.read_well_frame(path)
        paths[well] = path
    state.dataset.data_dir = tmp_path
    state.dataset.well_paths = paths
    state.dataset.pipeline_info = _field_quality_block()
    state.dataset.fov_integrity = {}
    state.channel.ratio_index = {RATIO.key(): RATIO}
    store: dict = {}
    c = ViewerContext(state=state, bus=EventBus(), widgets=WidgetRegistry(None, store=store),
                      runtime=AppRuntime(None, store=store))
    state.repo = WellRepository(c)
    for layer in LAYERS:
        _switch_on(c, layer)
    c.state.dataset.cache.clear()
    return c


def _draw(ctx, *, per_fov: bool):
    """What the bar collector does for one timepoint, through its own method."""
    from review.domain.stats_compute import aggregate_well_cached
    from review.features.barplot.collect import _BarCollector

    def well_item(label, *_a):
        return aggregate_well_cached(ctx, label, threshold=250.0, use_sem=False, val_col=VAL,
                                     fluor_gates=GATES, per_fov_spread=per_fov)

    collector = SimpleNamespace(
        ctx=ctx, hidden_members=set(),
        req=SimpleNamespace(use_groups=False, val_col=VAL, fluor_gates=GATES,
                            per_fov_spread=per_fov),
        format_tp=lambda t: f"{t:g}",
        _well_specs=lambda tp_str: [(w, "#000", w, w) for w in WELLS],
        _well_item=well_item,
    )
    return _BarCollector.items_for_tp(collector, 0.0)


@pytest.mark.parametrize("per_fov", [False, True])
def test_the_first_draw_reads_each_well_once(ctx, monkeypatch, per_fov):
    calls = _count_reads(monkeypatch)
    _draw(ctx, per_fov=per_fov)
    assert len(calls) == len(WELLS), (
        f"the first bar draw (FOV replicates {'on' if per_fov else 'off'}) read "
        f"{len(calls)} times for {len(WELLS)} wells")
    calls.clear()
    _draw(ctx, per_fov=per_fov)
    assert calls == []


def test_the_prefetched_per_field_aggregate_is_the_direct_one(ctx):
    got = _draw(ctx, per_fov=True)
    for well, have in zip(WELLS, got):
        ctx.state.dataset.cache.clear()
        full = dl.get_rows(ctx, well)
        want = dl.aggregate_with_threshold_df(
            full, threshold=250.0, use_sem=False, val_col=VAL, fluor_gates=GATES,
            per_fov_spread=True, ratios=ctx.state.channel.ratio_index)
        assert [tuple(p) for p in have] == [tuple(p) for p in want]
