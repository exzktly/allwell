"""Pure-logic tests for the Cell Browser collection layer (no Qt)."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from review.features.image_browser.cell_browser.collect import (
    Criterion,
    collect_cells,
    enumerate_properties,
)


def _df():
    return pd.DataFrame({
        "Included": [1, 1, 1, 1],
        "area_px": [100.0, 200.0, 300.0, 400.0],
        "timepoint_hours": [0.0, 0.0, 24.0, 24.0],
        "timepoint": ["0", "0", "24", "24"],
        "fov": ["1", "2", "1", "2"],
        "filename": [f"img_{i}.tif" for i in range(4)],
        "nucleus_id": ["10", "11", "12", "13"],
        "gfp_mean_intensity": [100.0, 600.0, 700.0, 50.0],
        "cy5_smfish_count": [0, 5, 9, 1],
        "shape_circularity": [0.2, 0.9, 0.95, float("nan")],
        "shape_centroid_row": [10.0, 20.0, 30.0, 40.0],
        "shape_centroid_col": [11.0, 21.0, 31.0, 41.0],
    })


class _AnyDf(dict):
    """Row cache that returns the one test frame for any queried well —
    mirrors the old ``_get_rows = lambda label: df`` fake now that the
    controller fetches rows via the ctx-native ``get_rows(ctx, label)``."""
    def __init__(self, df):
        super().__init__()
        self._df = df

    def __contains__(self, k):
        return True

    def __getitem__(self, k):
        return self._df


def _app(df):
    app = SimpleNamespace()
    from review.core.cell_gating_state import CellGatingState
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths={"A01": None}, cache=_AnyDf(df)),
        selection=SimpleNamespace(selected_wells=["A01"], selections=[],
                                  active_solo_wells=set()),
        channel=SimpleNamespace(ratio_index={}),
        cell_gating=CellGatingState(),
    )
    from review.core.app_runtime import AppRuntime
    app._ctx = SimpleNamespace(app=app, state=app._app_state,
                               runtime=AppRuntime(app))
    return app


# ── enumerate_properties ─────────────────────────────────────────────────────

def test_enumerate_lists_all_property_families():
    app = _app(_df())
    cols = dict(enumerate_properties(app._ctx))
    assert "gfp_mean_intensity" in cols
    assert "cy5_smfish_count" in cols
    assert "shape_circularity" in cols
    # Labels are human-friendly, not raw column names.
    assert cols["shape_circularity"] == "Circularity"


# ── collect_cells ────────────────────────────────────────────────────────────

def test_single_criterion_min():
    app = _app(_df())
    cells = collect_cells(app._ctx, [Criterion("gfp_mean_intensity", lo=500.0)])
    assert sorted(c.nucleus_id for c in cells) == ["11", "12"]  # 600, 700


def test_min_and_max_band():
    app = _app(_df())
    cells = collect_cells(app._ctx, [Criterion("shape_circularity", lo=0.5, hi=0.92)])
    assert [c.nucleus_id for c in cells] == ["11"]  # 0.9 only; 0.95 too high, NaN fails


def test_multiple_criteria_anded():
    app = _app(_df())
    cells = collect_cells(app._ctx, [
        Criterion("gfp_mean_intensity", lo=500.0),
        Criterion("cy5_smfish_count", lo=8.0),
    ])
    assert [c.nucleus_id for c in cells] == ["12"]  # gfp700 & spots9


def test_timepoint_filter():
    app = _app(_df())
    cells = collect_cells(app._ctx, [], timepoints=[24.0])
    assert sorted(c.nucleus_id for c in cells) == ["12", "13"]


def test_fov_filter():
    app = _app(_df())
    cells = collect_cells(app._ctx, [], fovs=["1"])
    assert sorted(c.nucleus_id for c in cells) == ["10", "12"]


def test_descriptor_carries_image_locators():
    app = _app(_df())
    cells = collect_cells(app._ctx, [Criterion("gfp_mean_intensity", lo=650.0)])
    c = cells[0]
    assert c.well == "A01" and c.fov == "1" and c.tp_token == "24"
    assert c.timepoint_h == 24.0 and c.filename == "img_2.tif"
    assert c.centroid_row == 30.0 and c.centroid_col == 31.0


def test_gate_mode_excludes_qc_failed_cells_inside_the_rectangle():
    """Gate mode must mirror the scatter cloud exactly.

    The per-cell scatter pre-filters by ``df_included_mask`` (QC / crop /
    cell-gating "Included") before drawing the gate, so a cell that fails those
    layers is invisible on the plot even when it sits inside the rectangle. The
    gate gallery must apply the same base inclusion — otherwise a QC-excluded
    cell inside the gate shows as a phantom extra tile the user can't see on the
    scatter they gated from (regression: the gate mask ignored ``Included``, so a
    2-cell gate produced a 3-cell gallery)."""
    df = pd.DataFrame({
        "Included": [1, 1, 0],                       # 3rd cell fails inclusion
        "w1_mean_intensity": [30.0, 43.0, 33.0],     # all inside x ∈ [28, 44]
        "w2_mean_intensity": [10.0, 18.0, 14.0],     # all inside y ∈ [5, 26]
        "timepoint_hours": [0.0, 0.0, 0.0],
        "timepoint": ["0", "0", "0"],
        "fov": ["1", "1", "1"],
        "filename": ["a.tif", "b.tif", "c.tif"],
        "nucleus_id": ["1", "2", "3"],
    })
    app = _app(df)
    app._ctx.runtime.cell_browser.state = SimpleNamespace(
        scatter_gate_mode=True, label_spot_count=False)
    app._ctx.runtime.cell_browser.gate_choices = None
    app._app_state.cell_gating.scatter_gates = [{
        "name": "Gate 1",
        "x_col": "w1_mean_intensity", "y_col": "w2_mean_intensity",
        "x0": 28.0, "x1": 44.0, "y0": 5.0, "y1": 26.0,
    }]
    cells = collect_cells(app._ctx, [])
    # Only the two Included cells inside the gate — the QC-failed one is dropped,
    # matching what the scatter actually plots.
    assert {c.nucleus_id for c in cells} == {"1", "2"}


def test_nan_fails_active_bound():
    app = _app(_df())
    # row 3 (nid 13) has NaN circularity → excluded by any circularity bound.
    cells = collect_cells(app._ctx, [Criterion("shape_circularity", lo=0.0)])
    assert "13" not in [c.nucleus_id for c in cells]


def test_empty_criteria_returns_all():
    app = _app(_df())
    cells = collect_cells(app._ctx, [])
    assert len(cells) == 4


def test_no_selection_falls_back_to_all_loaded_wells():
    # With no explicit well selection the Cell Browser draws from every loaded
    # well (mirrors Cell Gating) so the gallery populates on first open.
    app = _app(_df())
    app._app_state.selection.selected_wells = []
    cells = collect_cells(app._ctx, [Criterion("gfp_mean_intensity", lo=0.0)])
    assert len(cells) == 4  # all rows of the one loaded well (A01)


def test_source_wells_mirror_groups_when_groups_exist():
    # When groups exist the gallery MIRRORS the plotting layer (visible groups +
    # solos) — NOT the individual selection. So even with selected_wells=[A01],
    # a group covering B02 makes the source [B02] (what the Scatter plots).
    from review.features.image_browser.cell_browser.collect import (
        cell_browser_source_wells,
    )
    app = _app(_df())
    app._app_state.dataset.well_paths = {"A01": None, "B02": None}
    app._app_state.selection.selected_wells = ["A01"]      # stale individual sel
    app._app_state.selection.selections = [{"name": "grp", "wells": ["B02"]}]
    app._rep_sets_active = lambda: [SimpleNamespace(name="grp", wells=["B02"])]
    assert cell_browser_source_wells(app._ctx) == ["B02"]       # group mirror wins


def test_source_wells_include_active_solo_wells():
    # Solo wells (not in any group) are part of what the plotting layer draws, so
    # the gallery includes them alongside the visible group wells.
    from review.features.image_browser.cell_browser.collect import (
        cell_browser_source_wells,
    )
    app = _app(_df())
    app._app_state.dataset.well_paths = {"A01": None, "B02": None}
    app._app_state.selection.selections = [{"name": "grp", "wells": ["B02"]}]
    app._app_state.selection.active_solo_wells = {"A01"}
    app._rep_sets_active = lambda: [SimpleNamespace(name="grp", wells=["B02"])]
    assert cell_browser_source_wells(app._ctx) == ["A01", "B02"]


def test_source_wells_use_selection_when_no_groups():
    from review.features.image_browser.cell_browser.collect import (
        cell_browser_source_wells,
    )
    app = _app(_df())
    app._app_state.dataset.well_paths = {"A01": None, "B02": None}
    app._app_state.selection.selected_wells = ["A01"]
    app._rep_sets_active = lambda: []
    assert cell_browser_source_wells(app._ctx) == ["A01"]


def test_timepoint_filter_is_strict_include_list():
    # None → not filtered (all 4 rows); [] → options exist but none chosen → 0;
    # a value → only that timepoint's cells (tps are [0,0,24,24]).
    app = _app(_df())
    assert len(collect_cells(app._ctx, [], timepoints=None)) == 4
    assert collect_cells(app._ctx, [], timepoints=[]) == []
    assert len(collect_cells(app._ctx, [], timepoints=[0.0])) == 2


def test_fov_filter_is_strict_include_list():
    # None → not filtered; [] → 0; a value → only that FOV (fovs are [1,2,1,2]).
    app = _app(_df())
    assert len(collect_cells(app._ctx, [], fovs=None)) == 4
    assert collect_cells(app._ctx, [], fovs=[]) == []
    assert len(collect_cells(app._ctx, [], fovs=["1"])) == 2


def test_live_sync_relaxes_empty_timepoints_and_fovs():
    # On the live auto-sync path an empty include-list relaxes to "all" so a
    # Split View selection change shows cells instead of a blank gallery; the
    # default (Generate) path keeps the strict tri-state.
    app = _app(_df())
    assert collect_cells(app._ctx, [], timepoints=[]) == []                  # Generate: strict
    assert len(collect_cells(app._ctx, [], timepoints=[], live_sync=True)) == 4
    assert len(collect_cells(app._ctx, [], fovs=[], live_sync=True)) == 4


def test_channels_prefer_app_list():
    app = _app(_df())
    app._app_state.channel.fluor_channels = ["gfp", "mcherry"]
    from review.features.image_browser.cell_browser.collect import cell_browser_channels
    assert cell_browser_channels(app._ctx) == ["gfp", "mcherry"]


def test_channels_fall_back_to_detection_when_app_list_empty():
    # If the app-wide fluor_channels list is empty/unset, detect from the data
    # so the chips still appear (regression: blank channel row).
    app = _app(_df())
    app._app_state.channel.fluor_channels = []
    from review.features.image_browser.cell_browser.collect import cell_browser_channels
    assert "gfp" in cell_browser_channels(app._ctx)  # from gfp_mean_intensity column


def test_channels_coerced_to_str():
    # Non-string tokens would crash tok.upper() when building chips; coerce them.
    app = _app(_df())
    app._app_state.channel.fluor_channels = [1, "gfp"]
    from review.features.image_browser.cell_browser.collect import cell_browser_channels
    assert cell_browser_channels(app._ctx) == ["1", "gfp"]


# ── N9: spot-count label population ──────────────────────────────────────────

def test_label_spot_count_populates_active_channel_count():
    app = _app(_df())
    app._app_state.channel.active_channel = "cy5"
    app._cell_browser_state = SimpleNamespace(
        label_spot_count=True, scatter_gate_mode=False)
    cells = collect_cells(app._ctx, [])
    assert {c.nucleus_id: c.spot_count for c in cells} == {
        "10": 0, "11": 5, "12": 9, "13": 1}


def test_label_spot_count_off_leaves_none():
    app = _app(_df())
    app._app_state.channel.active_channel = "cy5"
    app._cell_browser_state = SimpleNamespace(
        label_spot_count=False, scatter_gate_mode=False)
    cells = collect_cells(app._ctx, [])
    assert all(c.spot_count is None for c in cells)


def test_label_spot_count_none_when_channel_has_no_smfish_column():
    app = _app(_df())
    app._app_state.channel.active_channel = "gfp"  # no gfp_smfish_count column
    app._cell_browser_state = SimpleNamespace(
        label_spot_count=True, scatter_gate_mode=False)
    cells = collect_cells(app._ctx, [])
    assert all(c.spot_count is None for c in cells)


# ── N1: scatter-gate source mode ─────────────────────────────────────────────

def _gate(x_col, y_col, x0, x1, y0, y1, name="G1"):
    return {"x_col": x_col, "y_col": y_col,
            "x0": x0, "x1": x1, "y0": y0, "y1": y1, "name": name}


def test_scatter_gate_mode_sources_from_gate_and_ignores_criteria():
    app = _app(_df())
    app._app_state.cell_gating.scatter_gates = [_gate("gfp_mean_intensity", "area_px", 500.0, 800.0, 0.0, 1000.0)]
    app._cell_browser_state = SimpleNamespace(
        label_spot_count=False, scatter_gate_mode=True)
    # A criterion that would exclude everything is ignored in gate mode.
    cells = collect_cells(app._ctx, [Criterion("gfp_mean_intensity", lo=0.0, hi=1.0)])
    # gate keeps gfp in [500,800] → nids 11 (600) and 12 (700).
    assert sorted(c.nucleus_id for c in cells) == ["11", "12"]


def test_scatter_gate_mode_empty_when_no_gates_drawn():
    app = _app(_df())
    app._app_state.cell_gating.scatter_gates = []
    app._cell_browser_state = SimpleNamespace(
        label_spot_count=False, scatter_gate_mode=True)
    assert collect_cells(app._ctx, []) == []


def test_scatter_gate_mode_empty_timepoints_relax_to_all():
    # In gate mode with NO scatter timepoint selection, an empty timepoint
    # include-list relaxes to "all" so the gated cells aren't hidden by the
    # default (the fallback when the scatter subset is unavailable).
    app = _app(_df())
    app._app_state.cell_gating.scatter_gates = [_gate("gfp_mean_intensity", "area_px", 0.0, 9999.0, 0.0, 9999.0)]
    app._cell_browser_state = SimpleNamespace(
        label_spot_count=False, scatter_gate_mode=True)
    cells = collect_cells(app._ctx, [], timepoints=[])
    assert len(cells) == 4


def test_scatter_gate_mode_honours_scatter_selected_timepoints():
    # In gate mode the gallery mirrors the scatter cloud, which is drawn over the
    # scatter's OWN selected timepoint subset. With the browser picker at its
    # deselected default, only the scatter-selected timepoints load — NOT every
    # timepoint (the Split-View cell-gating bug).
    app = _app(_df())
    app._app_state.cell_gating.scatter_gates = [
        _gate("gfp_mean_intensity", "area_px", 0.0, 9999.0, 0.0, 9999.0)]
    app._cell_browser_state = SimpleNamespace(
        label_spot_count=False, scatter_gate_mode=True)
    # Scatter shows only tp=0 (24 unchecked). tps in the frame are [0,0,24,24].
    app._ctx.runtime.scatter.tp_selections = {"0.0": True, "24.0": False}
    cells = collect_cells(app._ctx, [], timepoints=[])
    assert sorted(c.nucleus_id for c in cells) == ["10", "11"]  # tp=0 only


def test_scatter_gate_mode_explicit_browser_timepoints_still_win():
    # An explicit Cell-Browser timepoint selection overrides the scatter subset.
    app = _app(_df())
    app._app_state.cell_gating.scatter_gates = [
        _gate("gfp_mean_intensity", "area_px", 0.0, 9999.0, 0.0, 9999.0)]
    app._cell_browser_state = SimpleNamespace(
        label_spot_count=False, scatter_gate_mode=True)
    app._ctx.runtime.scatter.tp_selections = {"0.0": True, "24.0": False}
    cells = collect_cells(app._ctx, [], timepoints=[24.0])
    assert sorted(c.nucleus_id for c in cells) == ["12", "13"]  # browser pick wins


def test_scatter_gate_choices_filter_to_named_gates():
    # Two gates drawn; the sidebar picks only "G1" → only G1's cells appear.
    app = _app(_df())
    app._app_state.cell_gating.scatter_gates = [
        _gate("gfp_mean_intensity", "area_px", 500.0, 800.0, 0.0, 1000.0, name="G1"),
        _gate("gfp_mean_intensity", "area_px", 0.0, 60.0, 0.0, 1000.0, name="G2"),
    ]
    app._cell_browser_state = SimpleNamespace(
        label_spot_count=False, scatter_gate_mode=True)
    app._cell_browser_gate_choices = {"G1"}  # exclude G2 (which would add nid 13, gfp 50)
    cells = collect_cells(app._ctx, [])
    assert sorted(c.nucleus_id for c in cells) == ["11", "12"]  # G1 only, not 13
    # None ⇒ all gates → G1 ∪ G2 brings in nid 13 (gfp 50) and 10 (gfp 100? no, 100>60)
    app._cell_browser_gate_choices = None
    cells = collect_cells(app._ctx, [])
    assert "13" in {c.nucleus_id for c in cells}  # G2 contributes the low-gfp cell


def test_gate_mode_sources_from_live_selection():
    # Gate mode sources from the LIVE shared selection (so the Cell Browser and
    # Scatter stay synced in Split View) — NOT a frozen per-gate well stamp.
    app = _app(_df())
    app._app_state.dataset.well_paths = {"A01": None, "B02": None}
    app._app_state.selection.selected_wells = ["A01", "B02"]
    g = _gate("gfp_mean_intensity", "area_px", 0.0, 9999.0, 0.0, 9999.0)
    g["wells"] = ["A01"]  # stale stamp must be ignored
    app._app_state.cell_gating.scatter_gates = [g]
    app._cell_browser_state = SimpleNamespace(
        label_spot_count=False, scatter_gate_mode=True)
    app._cell_browser_gate_choices = None
    cells = collect_cells(app._ctx, [])
    assert {c.well for c in cells} == {"A01", "B02"}  # follows the live selection
