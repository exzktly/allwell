"""The threshold-range and timepoint scans read each well narrowly, once (tracker R3).

``_Docs/Markdowns/REREAD_TRACKER.md``, R3. Opening a plate larger than the row
cache read every well whole three more times for three small answers: the
threshold sliders' range (``_recalculate_threshold``'s extent scan), the
Distribution tab's timepoint menu (``get_all_timepoints``) and the Statistics
tab's (``stats_update_tp_menu``, which did not even go through the memo). Each
now reads through ``plate_scan`` with ``columns=`` — a narrow read carrying the
verdict layers' declared inputs — and the two menus share one scan.

The hazard is the narrow read's (``tests/test_narrow_rows.py``): every verdict
fails open, so a read that dropped one layer's input would count the cells that
layer excludes, and nothing would say so. The fixture is built so that it would
change every answer here. It is the narrow-read fixture with every verdict layer
switched on, and

* every cell some layer excludes carries a value of the plotted column above
  every included cell's, so a leaked cell moves the range's maximum;
* each of five further timepoints holds only cells one layer excludes, so a
  leaked cell adds a timepoint to the included menu — and the all-timepoints
  menu, a different question, must list them.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from review.data import data_loading as dl
from tests.test_narrow_rows import (
    CROP_CELL, CURATED_CELL, FOCUS_CELL, GATED_CELL, LAYERS, QC_CELL, RATIO, WELL, _frame, _switch_on,
)

pytest.importorskip("pyarrow.parquet")

VAL_COL = "mcherry_mean_intensity"
#: Cells of the timepoint-0 frame some layer excludes (field quality flags FOV 1,
#: the field exclusion is FOV 8).
EXCLUDED_AT_ZERO = {QC_CELL, CROP_CELL, FOCUS_CELL, CURATED_CELL, GATED_CELL, 0, 1, 2, 3, 28, 29, 30, 31}
#: A timepoint holding one cell, which only the named layer excludes.
LONE = {"qc": 101.0, "crop": 102.0, "focus": 103.0, "curation": 105.0, "field_exclusions": 107.0}


def _lone_cell(base: pd.DataFrame, hours: float) -> pd.DataFrame:
    """One ordinary cell (FOV 3, kept by every layer at timepoint 0) moved to *hours*."""
    cell = base.iloc[[10]].copy()
    cell["timepoint"] = f"{hours:g}"
    cell["timepoint_hours"] = hours
    cell[VAL_COL] = 100.0
    return cell


def _plate() -> pd.DataFrame:
    base = _frame()
    for i, cell in enumerate(sorted(EXCLUDED_AT_ZERO)):
        base.loc[cell, VAL_COL] = 1000.0 + i          # included cells span 1..500
    lone = {layer: _lone_cell(base, hours) for layer, hours in LONE.items()}
    lone["qc"]["area_px"] = 10.0
    lone["crop"][["shape_centroid_row", "shape_centroid_col"]] = 900.0
    lone["focus"]["qc_edge_focus"] = 0.01
    lone["field_exclusions"]["fov"] = "8"
    return pd.concat([base, *lone.values()], ignore_index=True)


@pytest.fixture
def ctx(tmp_path):
    from domain.image_identity import cell_keys_for_dataframe
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
    plate = _plate()
    plate.to_csv(path, index=False)
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
    assert read_cached(path) is not None, "no Parquet cache; the narrow path would never be taken"
    for layer in LAYERS:
        _switch_on(c, layer)
    keys = cell_keys_for_dataframe(plate, default_well=WELL)
    lone_curated = keys.iloc[len(plate) - len(LONE) + list(LONE).index("curation")]
    c.runtime.review.included_overrides = {
        **c.runtime.review.included_overrides, (WELL, lone_curated.fov, lone_curated.tp, lone_curated.nid): "0"}
    c.state.dataset.field_exclusions[(WELL, "8", "107")] = {"reason": "test", "at": "now"}
    c.state.dataset.cache.clear()
    return c


def _whole(ctx) -> pd.DataFrame:
    """The well read whole and projected — the path the scans used to take."""
    ctx.state.dataset.cache.clear()
    frame = dl.get_rows(ctx, WELL)
    ctx.state.dataset.cache.clear()
    return frame


def _count_reads(monkeypatch) -> list:
    from review.data import parquet_cache

    calls: list = []
    real = parquet_cache.read_cached

    def counted(path, columns=None):
        calls.append((Path(path).stem, columns))
        return real(path, columns=columns)

    monkeypatch.setattr(parquet_cache, "read_cached", counted)
    return calls


def test_the_fixture_would_catch_a_leaked_cell(ctx):
    """Every layer bites on the whole frame: the excluded cells are the extreme
    ones, and every lone timepoint is excluded entirely."""
    whole = _whole(ctx)
    included = dl.df_included_mask(whole).to_numpy()
    assert set(whole.index[~included][:len(EXCLUDED_AT_ZERO)]) == EXCLUDED_AT_ZERO
    assert whole.loc[included, VAL_COL].max() < 1000.0 <= whole[VAL_COL].max()
    kept_hours = set(whole.loc[dl.df_included_mask(whole, include_gate=False), "timepoint_hours"])
    assert kept_hours == {0.0}, kept_hours


def test_the_included_timepoints_are_the_whole_frame_s(ctx):
    whole = _whole(ctx)
    tp = pd.to_numeric(whole["timepoint_hours"], errors="coerce")
    expected = sorted(set(tp.where(dl.df_included_mask(whole, include_gate=False)).dropna()))
    assert expected == [0.0]
    assert dl.get_all_timepoints(ctx) == expected


def test_the_statistics_menu_lists_every_acquired_timepoint(ctx):
    """A different question from the included menu, asked of the same scan: an
    implementation answering both from one set fails one of the two."""
    assert dl.get_timepoints_with_rows(ctx) == sorted({0.0, *LONE.values()})
    assert dl.get_all_timepoints(ctx) == [0.0]


def test_the_statistics_menu_is_populated_from_the_scan(ctx, monkeypatch):
    """Through the real caller: the combo is handed every acquired timepoint."""
    import review.core.ui_helpers as ui_helpers
    from review.features.stats.controller import stats_update_tp_menu

    handed: list = []
    monkeypatch.setattr(ui_helpers, "set_combo_values",
                        lambda combo, values: handed.append(list(values)) if combo is not None else None)

    class Combo:
        def currentText(self):
            return "0"

        def setCurrentText(self, text):
            pass

    ctx.widgets.register("_stats_tp_cb", Combo())
    calls = _count_reads(monkeypatch)
    stats_update_tp_menu(ctx)
    assert handed[0] == [f"{t:.4g}" for t in sorted({0.0, *LONE.values()})]
    assert calls and all(cols is not None and "m0" not in cols for _, cols in calls), (
        f"the Statistics menu read the well whole: {calls}")
    assert WELL not in ctx.state.dataset.cache


def test_both_menus_share_one_narrow_read_per_well(ctx, monkeypatch):
    calls = _count_reads(monkeypatch)
    for _ in range(2):
        dl.get_timepoints_with_rows(ctx)
        dl.get_all_timepoints(ctx)
    assert len(calls) == 1, f"expected one read for both menus, got {len(calls)}"
    assert calls[0][1] is not None and "m0" not in calls[0][1], "the timepoint scan read the whole frame"
    assert WELL not in ctx.state.dataset.cache, "the timepoint scan put the whole frame in the row cache"


def test_the_spatial_timepoints_are_the_whole_frame_s_and_read_narrowly(ctx, monkeypatch):
    from review.features.spatial.collect import _included_hours, available_timepoints

    expected = sorted(_included_hours(_whole(ctx)))
    assert expected == [0.0]
    calls = _count_reads(monkeypatch)
    assert available_timepoints(ctx) == expected
    assert len(calls) == 1 and calls[0][1] is not None and "m0" not in calls[0][1], calls
