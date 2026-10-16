"""The threshold range is the plate's, scanned one well at a time (tracker M5).

``recalculate_threshold`` used to concatenate every included value of the active
column across the plate — 850 MB for 96 wells of 1.1M cells — to take a min and a
max. It now takes each well's own extent through ``plate_scan`` and keeps it, so
the plate is read once per change to its rows rather than on every channel switch,
and an evicted well still counts.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from review.core import channel_state_controller
from review.core.channel_state import ChannelState
from review.core.dataset_state import DatasetState
from review.core.stats_cache_state import StatsCacheState
from review.core.widget_registry import WidgetRegistry
from review.data import data_loading
from review.data.row_cache import frame_bytes
from review.data.well_repository import WellRepository
from review.features.registry import build_feature_registry

# The plate's extremes sit in different wells, neither of them the one held last.
_VALUES = {"A01": [5.0, 7.0, 9.0], "B02": [3.0, 8.0, 900.0], "C03": [-2.0, 6.0, 8.0], "D04": [4.0, 5.0, 6.0]}


@pytest.fixture
def ctx(tmp_path):
    channel = ChannelState()
    dataset = DatasetState()
    for label, values in _VALUES.items():
        path = tmp_path / f"{label}.csv"
        pd.DataFrame({"well": [label] * 3, "fov": ["1"] * 3, "timepoint": ["0h"] * 3,
                      "nucleus_id": ["1", "2", "3"], channel.active_val_col: values}).to_csv(path, index=False)
        dataset.well_paths[label] = path
    obj = SimpleNamespace(
        state=SimpleNamespace(dataset=dataset, channel=channel, stats_cache=StatsCacheState(),
                              review_image=SimpleNamespace(override_version=0), qc=SimpleNamespace(params=None),
                              feature_slice=lambda name: None),
        widgets=WidgetRegistry(SimpleNamespace()), features=None,
        runtime=SimpleNamespace(cell_gating=SimpleNamespace(fluor_gate_edits=None),
                                review=SimpleNamespace(included_overrides={})))
    obj.state.repo = WellRepository(obj)
    build_feature_registry()
    dataset.cache.set_budget(frame_bytes(data_loading.read_well_frame(dataset.well_paths["D04"])) + 1)
    return obj


def test_the_range_spans_every_well_held_or_not(ctx):
    channel_state_controller.recalculate_threshold(ctx)
    channel = ctx.state.channel
    assert (channel.threshold_min, channel.threshold_max) == (-2.0, 900.0)
    assert len(ctx.state.dataset.cache) == 1, "every well was held; eviction was not exercised"


def test_a_channel_switch_back_does_not_reread_the_plate(ctx, monkeypatch):
    channel_state_controller.recalculate_threshold(ctx)
    reads: list = []
    real = data_loading.read_well_frame
    monkeypatch.setattr(data_loading, "read_well_frame", lambda path: reads.append(path) or real(path))
    ctx.state.channel.threshold_range_cache.clear()     # what a dataset reload clears
    channel_state_controller.recalculate_threshold(ctx)
    assert reads == [], f"the per-well extents were not kept: {len(reads)} well(s) re-read"
    assert (ctx.state.channel.threshold_min, ctx.state.channel.threshold_max) == (-2.0, 900.0)


def test_the_extent_of_a_well_counts_only_its_included_finite_values():
    df = pd.DataFrame({"v": [1.0, np.nan, 50.0, np.inf, -5.0], "Included": [1, 1, 1, 1, 0]})
    assert channel_state_controller._value_extent(df, "v") == (1.0, 50.0)
    assert channel_state_controller._value_extent(df.iloc[[1, 3]], "v") is None
