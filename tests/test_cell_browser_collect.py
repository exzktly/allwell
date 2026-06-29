"""Pure-logic tests for the Cell Browser collection layer (no Qt)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from well_viewer.features.image_browser.cell_browser.collect import (
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


def _app(df):
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths={"A01": None}),
        selection=SimpleNamespace(selected_wells=["A01"]),
        channel=SimpleNamespace(ratio_index={}),
    )
    app._get_rows = lambda label: df
    app._parse_rc = lambda s: (s, 0, 0)
    # No replicate-set groups by default → Cell Browser falls back to the
    # individual well selection (then to all loaded wells).
    app._rep_sets_active = lambda: []
    return app


# ── enumerate_properties ─────────────────────────────────────────────────────

def test_enumerate_lists_all_property_families():
    app = _app(_df())
    cols = dict(enumerate_properties(app))
    assert "gfp_mean_intensity" in cols
    assert "cy5_smfish_count" in cols
    assert "shape_circularity" in cols
    # Labels are human-friendly, not raw column names.
    assert cols["shape_circularity"] == "Circularity"


# ── collect_cells ────────────────────────────────────────────────────────────

def test_single_criterion_min():
    app = _app(_df())
    cells = collect_cells(app, [Criterion("gfp_mean_intensity", lo=500.0)])
    assert sorted(c.nucleus_id for c in cells) == ["11", "12"]  # 600, 700


def test_min_and_max_band():
    app = _app(_df())
    cells = collect_cells(app, [Criterion("shape_circularity", lo=0.5, hi=0.92)])
    assert [c.nucleus_id for c in cells] == ["11"]  # 0.9 only; 0.95 too high, NaN fails


def test_multiple_criteria_anded():
    app = _app(_df())
    cells = collect_cells(app, [
        Criterion("gfp_mean_intensity", lo=500.0),
        Criterion("cy5_smfish_count", lo=8.0),
    ])
    assert [c.nucleus_id for c in cells] == ["12"]  # gfp700 & spots9


def test_timepoint_filter():
    app = _app(_df())
    cells = collect_cells(app, [], timepoints=[24.0])
    assert sorted(c.nucleus_id for c in cells) == ["12", "13"]


def test_fov_filter():
    app = _app(_df())
    cells = collect_cells(app, [], fovs=["1"])
    assert sorted(c.nucleus_id for c in cells) == ["10", "12"]


def test_descriptor_carries_image_locators():
    app = _app(_df())
    cells = collect_cells(app, [Criterion("gfp_mean_intensity", lo=650.0)])
    c = cells[0]
    assert c.well == "A01" and c.fov == "1" and c.tp_token == "24"
    assert c.timepoint_h == 24.0 and c.filename == "img_2.tif"
    assert c.centroid_row == 30.0 and c.centroid_col == 31.0


def test_nan_fails_active_bound():
    app = _app(_df())
    # row 3 (nid 13) has NaN circularity → excluded by any circularity bound.
    cells = collect_cells(app, [Criterion("shape_circularity", lo=0.0)])
    assert "13" not in [c.nucleus_id for c in cells]


def test_empty_criteria_returns_all():
    app = _app(_df())
    cells = collect_cells(app, [])
    assert len(cells) == 4


def test_no_selection_falls_back_to_all_loaded_wells():
    # With no explicit well selection the Cell Browser draws from every loaded
    # well (mirrors Cell Gating) so the gallery populates on first open.
    app = _app(_df())
    app._app_state.selection.selected_wells = []
    cells = collect_cells(app, [Criterion("gfp_mean_intensity", lo=0.0)])
    assert len(cells) == 4  # all rows of the one loaded well (A01)


def test_source_wells_follow_replicate_groups():
    # When sample-definition groups exist, the Cell Browser must draw from the
    # SAME wells the Scatter plots (the visible group wells), not from the
    # passive individual selection — otherwise the two panes diverge in Split
    # View. Here the group covers only B02, while selected_wells points at A01.
    from well_viewer.features.image_browser.cell_browser.collect import (
        cell_browser_source_wells,
    )
    app = _app(_df())
    app._app_state.dataset.well_paths = {"A01": None, "B02": None}
    app._app_state.selection.selected_wells = ["A01"]
    app._rep_sets_active = lambda: [SimpleNamespace(name="grp", wells=["B02"])]
    assert cell_browser_source_wells(app) == ["B02"]  # follows the group, not A01


def test_source_wells_use_selection_when_no_groups():
    from well_viewer.features.image_browser.cell_browser.collect import (
        cell_browser_source_wells,
    )
    app = _app(_df())
    app._app_state.dataset.well_paths = {"A01": None, "B02": None}
    app._app_state.selection.selected_wells = ["A01"]
    app._rep_sets_active = lambda: []
    assert cell_browser_source_wells(app) == ["A01"]


def test_timepoint_filter_is_strict_include_list():
    # None → not filtered (all 4 rows); [] → options exist but none chosen → 0;
    # a value → only that timepoint's cells (tps are [0,0,24,24]).
    app = _app(_df())
    assert len(collect_cells(app, [], timepoints=None)) == 4
    assert collect_cells(app, [], timepoints=[]) == []
    assert len(collect_cells(app, [], timepoints=[0.0])) == 2


def test_fov_filter_is_strict_include_list():
    # None → not filtered; [] → 0; a value → only that FOV (fovs are [1,2,1,2]).
    app = _app(_df())
    assert len(collect_cells(app, [], fovs=None)) == 4
    assert collect_cells(app, [], fovs=[]) == []
    assert len(collect_cells(app, [], fovs=["1"])) == 2


def test_channels_prefer_app_list():
    app = _app(_df())
    app._app_state.channel.fluor_channels = ["gfp", "mcherry"]
    from well_viewer.features.image_browser.cell_browser.collect import cell_browser_channels
    assert cell_browser_channels(app) == ["gfp", "mcherry"]


def test_channels_fall_back_to_detection_when_app_list_empty():
    # If the app-wide fluor_channels list is empty/unset, detect from the data
    # so the chips still appear (regression: blank channel row).
    app = _app(_df())
    app._app_state.channel.fluor_channels = []
    from well_viewer.features.image_browser.cell_browser.collect import cell_browser_channels
    assert "gfp" in cell_browser_channels(app)  # from gfp_mean_intensity column


def test_channels_coerced_to_str():
    # Non-string tokens would crash tok.upper() when building chips; coerce them.
    app = _app(_df())
    app._app_state.channel.fluor_channels = [1, "gfp"]
    from well_viewer.features.image_browser.cell_browser.collect import cell_browser_channels
    assert cell_browser_channels(app) == ["1", "gfp"]


# ── N9: spot-count label population ──────────────────────────────────────────

def test_label_spot_count_populates_active_channel_count():
    app = _app(_df())
    app._app_state.channel.active_channel = "cy5"
    app._cell_browser_state = SimpleNamespace(
        label_spot_count=True, scatter_gate_mode=False)
    cells = collect_cells(app, [])
    assert {c.nucleus_id: c.spot_count for c in cells} == {
        "10": 0, "11": 5, "12": 9, "13": 1}


def test_label_spot_count_off_leaves_none():
    app = _app(_df())
    app._app_state.channel.active_channel = "cy5"
    app._cell_browser_state = SimpleNamespace(
        label_spot_count=False, scatter_gate_mode=False)
    cells = collect_cells(app, [])
    assert all(c.spot_count is None for c in cells)


def test_label_spot_count_none_when_channel_has_no_smfish_column():
    app = _app(_df())
    app._app_state.channel.active_channel = "gfp"  # no gfp_smfish_count column
    app._cell_browser_state = SimpleNamespace(
        label_spot_count=True, scatter_gate_mode=False)
    cells = collect_cells(app, [])
    assert all(c.spot_count is None for c in cells)


# ── N1: scatter-gate source mode ─────────────────────────────────────────────

def _gate(x_col, y_col, x0, x1, y0, y1, name="G1"):
    return {"x_col": x_col, "y_col": y_col,
            "x0": x0, "x1": x1, "y0": y0, "y1": y1, "name": name}


def test_scatter_gate_mode_sources_from_gate_and_ignores_criteria():
    app = _app(_df())
    app._scatter_gates = [_gate("gfp_mean_intensity", "area_px", 500.0, 800.0, 0.0, 1000.0)]
    app._cell_browser_state = SimpleNamespace(
        label_spot_count=False, scatter_gate_mode=True)
    # A criterion that would exclude everything is ignored in gate mode.
    cells = collect_cells(app, [Criterion("gfp_mean_intensity", lo=0.0, hi=1.0)])
    # gate keeps gfp in [500,800] → nids 11 (600) and 12 (700).
    assert sorted(c.nucleus_id for c in cells) == ["11", "12"]


def test_scatter_gate_mode_empty_when_no_gates_drawn():
    app = _app(_df())
    app._scatter_gates = []
    app._cell_browser_state = SimpleNamespace(
        label_spot_count=False, scatter_gate_mode=True)
    assert collect_cells(app, []) == []


def test_scatter_gate_mode_empty_timepoints_relax_to_all():
    # Outside gate mode an empty timepoint include-list yields nothing; in gate
    # mode it relaxes to "all" so the gated cells aren't hidden by the default.
    app = _app(_df())
    app._scatter_gates = [_gate("gfp_mean_intensity", "area_px", 0.0, 9999.0, 0.0, 9999.0)]
    app._cell_browser_state = SimpleNamespace(
        label_spot_count=False, scatter_gate_mode=True)
    cells = collect_cells(app, [], timepoints=[])
    assert len(cells) == 4


def test_scatter_gate_choices_filter_to_named_gates():
    # Two gates drawn; the sidebar picks only "G1" → only G1's cells appear.
    app = _app(_df())
    app._scatter_gates = [
        _gate("gfp_mean_intensity", "area_px", 500.0, 800.0, 0.0, 1000.0, name="G1"),
        _gate("gfp_mean_intensity", "area_px", 0.0, 60.0, 0.0, 1000.0, name="G2"),
    ]
    app._cell_browser_state = SimpleNamespace(
        label_spot_count=False, scatter_gate_mode=True)
    app._cell_browser_gate_choices = {"G1"}  # exclude G2 (which would add nid 13, gfp 50)
    cells = collect_cells(app, [])
    assert sorted(c.nucleus_id for c in cells) == ["11", "12"]  # G1 only, not 13
    # None ⇒ all gates → G1 ∪ G2 brings in nid 13 (gfp 50) and 10 (gfp 100? no, 100>60)
    app._cell_browser_gate_choices = None
    cells = collect_cells(app, [])
    assert "13" in {c.nucleus_id for c in cells}  # G2 contributes the low-gfp cell


def test_gate_mode_sources_from_live_selection():
    # Gate mode sources from the LIVE shared selection (so the Cell Browser and
    # Scatter stay synced in Split View) — NOT a frozen per-gate well stamp.
    app = _app(_df())
    app._app_state.dataset.well_paths = {"A01": None, "B02": None}
    app._app_state.selection.selected_wells = ["A01", "B02"]
    g = _gate("gfp_mean_intensity", "area_px", 0.0, 9999.0, 0.0, 9999.0)
    g["wells"] = ["A01"]  # stale stamp must be ignored
    app._scatter_gates = [g]
    app._cell_browser_state = SimpleNamespace(
        label_spot_count=False, scatter_gate_mode=True)
    app._cell_browser_gate_choices = None
    cells = collect_cells(app, [])
    assert {c.well for c in cells} == {"A01", "B02"}  # follows the live selection
