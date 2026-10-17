"""Cell Gating's CDFs and optimizer lists come from narrow, remembered reads, and a load reads them once.

Tracker R2 (``_Docs/Markdowns/REREAD_TRACKER.md``). ``cell_gating_load_cell_areas``
ran three to four times per open — from ``set_ratio_metrics`` with no ratios and
again with the loaded ones, from the channel switch the second can make, and from
the load's ``recalculate_threshold`` — and ``set_ratio_metrics`` clears the
statistics cache its per-well memo lives in, so each run read every source well's
whole frame again. The optimizer controls it refreshes read Well A and Well B
whole three more times per run (timepoints, the fold-change baseline list, the
Min/Max fit). On the mini dataset with a ratio and a gate that was 36 of an open's
66 full reads.

Now each well is read narrowly through ``plate_scan.per_well(columns=...)``, the
answer is remembered per well, and during a load the calls rebuild the controls
only and owe the reading, which the load pays once
(``cell_gating_read_owed_cell_areas``) after the channels, ratios and gates are
final. ``tests/test_a_load_reads_cell_gating_once.py`` drives the real load.

The fixture is the narrow-read one (``tests/test_narrow_rows.py``) with every
verdict layer on, two changes made so the layers this tab reads bite inside the
first frame (FOV 1, the earliest timepoint): cell 1 is below the QC area floor
— and the brightest cell of the well, so the optimizer's range moves with it —
and cell 2 is outside the crop. It is doubled across two timepoints whose values
differ threefold, so a first frame taken from the wrong timepoint, or an extent
filed under the wrong one, is wrong rather than equal.
"""

from __future__ import annotations

import pandas as pd
import pytest

from review.data import data_loading as dl
from tests.test_narrow_rows import LAYERS, RATIO, WELL, _frame, _switch_on

pytest.importorskip("pyarrow.parquet")

QC_IN_FIRST_FRAME, CROP_IN_FIRST_FRAME = 1, 2
CHANNELS = ("gfp", "mcherry")


def _plate() -> pd.DataFrame:
    first = _frame()
    first.loc[QC_IN_FIRST_FRAME, "area_px"] = 10.0
    # The QC victim is the brightest cell in every channel and ratio, so an
    # extent that kept it is wrong at its upper end.
    first.loc[QC_IN_FIRST_FRAME, ["gfp_mean_intensity", "mcherry_mean_intensity"]] = 5000.0
    first.loc[CROP_IN_FIRST_FRAME, ["shape_centroid_row", "shape_centroid_col"]] = 900.0
    later = first.copy()
    later["timepoint"] = "24"
    later["timepoint_hours"] = 24.0
    for col in ("gfp_mean_intensity", "mcherry_mean_intensity", "area_px"):
        later[col] = later[col] * 3.0
    # Later rows first, so "the first rows of the file" is not the first frame.
    return pd.concat([later, first], ignore_index=True)


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
    _plate().to_csv(path, index=False)
    state.dataset.data_dir = tmp_path
    state.dataset.well_paths = {WELL: path}
    state.dataset.pipeline_info = _field_quality_block()
    state.dataset.fov_integrity = {}
    state.channel.fluor_channels = list(CHANNELS)
    state.channel.set_ratio_metrics([RATIO])
    store: dict = {}
    c = ViewerContext(state=state, bus=EventBus(), widgets=WidgetRegistry(None, store=store),
                      runtime=AppRuntime(None, store=store))
    state.repo = WellRepository(c)
    dl.read_well_frame(path)
    assert read_cached(path) is not None, "no Parquet cache; the narrow path would never be taken"
    return c


def _layers_on(ctx) -> None:
    for layer in LAYERS:
        _switch_on(ctx, layer)


def _full(ctx) -> pd.DataFrame:
    ctx.state.dataset.cache.clear()
    frame = dl.get_rows(ctx, WELL)
    ctx.state.dataset.cache.clear()
    return frame


def _count_reads(monkeypatch) -> dict:
    """Every Parquet read's column list, and every whole-frame read."""
    from review.data import parquet_cache

    seen: dict = {"parquet": [], "full": []}
    real_cached, real_full = parquet_cache.read_cached, dl.read_well_frame
    monkeypatch.setattr(parquet_cache, "read_cached",
                        lambda path, columns=None: seen["parquet"].append(columns)
                        or real_cached(path, columns=columns))
    monkeypatch.setattr(dl, "read_well_frame",
                        lambda path, *a, **k: seen["full"].append(path) or real_full(path, *a, **k))
    return seen


def _assert_narrow(seen: dict) -> None:
    assert not seen["full"], "a whole frame was read"
    assert seen["parquet"], "nothing was read; this measures nothing"
    assert all(cols is not None and "m0" not in cols for cols in seen["parquet"]), "a read was not narrow"


def _expected_first_frame(ctx):
    from review.features.home.cell_gating.controller import _first_frame_values

    return _first_frame_values(_full(ctx), CHANNELS, [RATIO])


def _as_lists(found):
    areas, fluor, ratios = found
    return (list(areas), {k: list(v) for k, v in fluor.items()}, {k: list(v) for k, v in ratios.items()})


# ── the CDFs ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("layers", [True, False], ids=["every_layer_on", "no_layer_on"])
def test_the_cdf_values_are_the_whole_frame_s(ctx, monkeypatch, layers):
    """With no layer on, nothing but the tab's own column list brings the area,
    the channels and both halves of the ratio into the read; with every layer on,
    a read missing a verdict's input would keep the QC and crop cells."""
    from review.features.home.cell_gating.controller import _read_first_frames

    if layers:
        _layers_on(ctx)
    expected = _as_lists(_expected_first_frame(ctx))
    assert expected[0] and set(expected[1]) == set(CHANNELS) and expected[2], (
        "a part of the answer is empty, so dropping its column would pass")
    seen = _count_reads(monkeypatch)
    assert _read_first_frames(ctx) == [WELL]
    _assert_narrow(seen)
    assert WELL not in ctx.state.dataset.cache, "the frame was put in the row cache"
    rt = ctx.runtime.cell_gating
    assert (rt.cell_areas, rt.fluor_data, rt.ratio_data) == expected


def test_the_fixture_s_layers_change_the_first_frame(ctx):
    """What makes the equality above mean something: the QC and crop verdicts
    drop two of the first frame's four cells, and the first frame is not the
    later timepoint's."""
    from review.features.home.cell_gating.controller import _first_frame_values

    ungated = _as_lists(_expected_first_frame(ctx))
    _layers_on(ctx)
    gated = _as_lists(_expected_first_frame(ctx))
    assert len(ungated[1]["gfp"]) == 4 and len(gated[1]["gfp"]) == 2
    raw = _full(ctx)
    later = _as_lists(_first_frame_values(raw[raw["timepoint"].astype(str) == "24"], CHANNELS, [RATIO]))
    assert later[1]["gfp"] != gated[1]["gfp"]


def test_a_repeat_reads_nothing_and_a_new_ratio_reads_again(ctx, monkeypatch):
    from review.features.home.cell_gating.controller import _read_first_frames
    from review.domain.ratio_models import RatioMetric

    _layers_on(ctx)
    ctx.state.dataset.cache.clear()
    seen = _count_reads(monkeypatch)
    _read_first_frames(ctx)
    _read_first_frames(ctx)
    assert len(seen["parquet"]) == 1, "the second call read the well again"
    other = RatioMetric(name="m_over_c", numerator_channel="mcherry", numerator_metric="mean_intensity",
                        denominator_channel="cy5", denominator_metric="mean_intensity")
    ctx.state.channel.set_ratio_metrics([RATIO, other])
    _read_first_frames(ctx)
    assert len(seen["parquet"]) == 2, "a changed ratio set reused values computed without it"
    assert set(ctx.runtime.cell_gating.ratio_data) == {RATIO.display_label(), other.display_label()}


# ── the optimizer's lists and its Min/Max fit ───────────────────────────────


def test_the_optimizer_timepoints_are_the_whole_frame_s(ctx, monkeypatch):
    from review.features.home.cell_gating.controller import _well_timepoints
    from review.features.home.cell_gating.gating_compute import _distinct_timepoints

    _layers_on(ctx)
    expected = _distinct_timepoints(_full(ctx))
    assert len(expected) == 2
    seen = _count_reads(monkeypatch)
    assert _well_timepoints(ctx, WELL) == expected
    assert _well_timepoints(ctx, WELL) == expected
    _assert_narrow(seen)
    assert len(seen["parquet"]) == 1


@pytest.mark.parametrize("channel_key", ["mcherry", RATIO.key()])
def test_the_optimizer_range_is_the_whole_frame_s(ctx, monkeypatch, channel_key):
    from review.features.home.cell_gating.controller import _well_value_extent
    from review.features.home.cell_gating.gating_compute import (
        _distinct_timepoints,
        _valid_values_for_channel,
    )

    _layers_on(ctx)
    ratios = ctx.state.channel.ratio_index
    full = _full(ctx)
    timepoints = [None, *_distinct_timepoints(full)]
    expected = []
    for tp in timepoints:
        values = _valid_values_for_channel(full, channel_key, ratios, tp)
        expected.append((float(values.min()), float(values.max())))
    assert len(set(expected)) == len(expected), "the timepoints agree; a misfiled extent would pass"
    seen = _count_reads(monkeypatch)
    for tp, want in zip(timepoints, expected):
        assert _well_value_extent(ctx, WELL, channel_key, ratios, tp) == want, tp
    _assert_narrow(seen)
    unfiltered = _valid_values_for_channel(full.assign(QC_Pass=1, Crop_Pass=1), channel_key, ratios, None)
    assert (float(unfiltered.min()), float(unfiltered.max())) != expected[0], (
        "the verdicts do not move the extent; a read without them would pass")


# ── a load owes the reading and pays it once ────────────────────────────────


@pytest.fixture
def tab(ctx, monkeypatch):
    """The reading half of the tab: the controls and the CDF canvas are another
    test's subject (``test_a_load_reads_cell_gating_once.py`` builds them)."""
    from types import SimpleNamespace

    from review.features.home.cell_gating import controller as cg

    drawn: list = []
    monkeypatch.setattr(cg, "_cell_gating_build_channel_controls", lambda ctx: None)
    monkeypatch.setattr(cg, "_cell_gating_plot_cdf", lambda ctx: drawn.append(len(ctx.runtime.cell_gating.cell_areas)))
    rt = ctx.runtime.cell_gating
    rt.fluor_gate_edits = {}
    said: list = []
    rt.status_label = SimpleNamespace(setText=said.append)
    return SimpleNamespace(drawn=drawn, said=said)


def test_during_a_load_the_calls_owe_the_reading_and_the_load_pays_it_once(ctx, tab, monkeypatch):
    from review.core import plot_hold
    from review.features.home.cell_gating.controller import (
        cell_gating_load_cell_areas,
        cell_gating_read_owed_cell_areas,
    )

    _layers_on(ctx)
    expected = _as_lists(_expected_first_frame(ctx))
    seen = _count_reads(monkeypatch)
    plot_hold.hold(ctx, plot_hold.HOLD_LOADING)
    for _call in range(3):
        cell_gating_load_cell_areas(ctx)
    assert seen == {"parquet": [], "full": []}, "a call during the load read the plate"
    assert ctx.runtime.cell_gating.areas_owed and not tab.drawn
    cell_gating_read_owed_cell_areas(ctx)
    _assert_narrow(seen)
    assert len(seen["parquet"]) == 1
    rt = ctx.runtime.cell_gating
    assert (rt.cell_areas, rt.fluor_data, rt.ratio_data) == expected
    assert tab.drawn == [len(expected[0])] and tab.said[-1].startswith(f"Loaded {len(expected[0])} cells")
    cell_gating_read_owed_cell_areas(ctx)
    assert len(seen["parquet"]) == 1 and tab.drawn == [len(expected[0])], "the debt was paid twice"
    plot_hold.release(ctx)


def test_outside_a_load_a_call_reads_at_once(ctx, tab, monkeypatch):
    from review.features.home.cell_gating.controller import cell_gating_load_cell_areas

    expected = _as_lists(_expected_first_frame(ctx))
    seen = _count_reads(monkeypatch)
    cell_gating_load_cell_areas(ctx)
    _assert_narrow(seen)
    rt = ctx.runtime.cell_gating
    assert (rt.cell_areas, rt.fluor_data, rt.ratio_data) == expected
    assert not rt.areas_owed and tab.drawn

