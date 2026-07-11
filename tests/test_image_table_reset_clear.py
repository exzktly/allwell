"""Image Table "Reset table" + "Clear images" actions (build_layout).

Two independent clears wired to the action strip:
- ``image_table_reset_layout`` returns the Build Layout generator to its fresh-tab
  defaults (Channels × Timepoints, 2×3, no selection / nesting) and rebuilds the
  empty selector — leaving any rendered images alone.
- ``image_table_clear_render`` wipes only the rendered-image state, keeping the
  selector layout and the decoded-image cache so a re-Generate is cheap.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from well_viewer.features.image_browser.image_table import controller as C  # noqa: E402
from well_viewer.features.image_browser.image_table import build_layout as B  # noqa: E402
from well_viewer.features.image_browser.image_table.state import ImageTableState  # noqa: E402


def _fake_app(*, wells, channels, timepoints=None, fovs=None):
    info = {"available_timepoints": timepoints or [], "available_fovs": fovs or [],
            "nuclear_token": ""}
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths={w: w for w in wells}, pipeline_info=info,
                                cache={}),
        channel=SimpleNamespace(fluor_channels=list(channels), smfish_channels=set(),
                                ratio_metrics=[], seg_channel_token=""),
        image_table=ImageTableState(),
    )
    app._parse_rc = lambda w: (w[0], int(w[1:]))
    from well_viewer.core.app_runtime import AppRuntime
    ctx = SimpleNamespace(app=app, state=app._app_state, runtime=AppRuntime(app),
                          _status=[])
    ctx.set_status = lambda msg: ctx._status.append(msg)
    app._ctx = ctx
    return app


def test_reset_layout_restores_defaults(monkeypatch):
    app = _fake_app(wells=["A01", "B02"], channels=["gfp", "rfp"],
                    timepoints=[0, 6], fovs=[1, 2])
    st = app._app_state.image_table
    # dirty every field the reset should restore
    st.row_dims = ["well", "channel"]
    st.col_dims = ["timepoint", "fov"]
    st.row_axis = "well"
    st.col_axis = "fov"
    st.rows, st.cols = 9, 5
    st.group_size, st.group_row_span = 3, 2
    st.active_wells = {"A01"}
    st.active_channels = {"GFP"}
    st.active_timepoints = {"6"}
    st.active_fovs = {"2"}

    calls = []
    monkeypatch.setattr(C, "image_table_rebuild_grid", lambda ctx: calls.append("grid"))
    monkeypatch.setattr(C, "image_table_refresh_picker", lambda ctx: calls.append("picker"))

    B.image_table_reset_layout(app._ctx)

    assert st.row_dims == [] and st.col_dims == []
    assert st.row_axis == "channel" and st.col_axis == "timepoint"
    assert (st.rows, st.cols) == (2, 3)
    assert (st.group_size, st.group_row_span) == (0, 0)
    assert st.active_wells == set() and st.active_channels == set()
    assert st.active_timepoints == set() and st.active_fovs == set()
    # rebuilds the empty selector + refreshes the sidebar
    assert "grid" in calls and "picker" in calls


def test_reset_layout_leaves_rendered_images(monkeypatch):
    app = _fake_app(wells=["A01"], channels=["gfp"])
    st = app._app_state.image_table
    st.last_render = {(0, 0): "arr"}
    monkeypatch.setattr(C, "image_table_rebuild_grid", lambda ctx: None)
    monkeypatch.setattr(C, "image_table_refresh_picker", lambda ctx: None)

    B.image_table_reset_layout(app._ctx)
    # independent of the images (only Clear images touches last_render)
    assert st.last_render == {(0, 0): "arr"}


def test_clear_render_wipes_only_the_render():
    app = _fake_app(wells=["A01"], channels=["gfp"])
    st = app._app_state.image_table
    st.last_render = {(0, 0): "arr", (0, 1): "arr2"}
    st.image_cache = {("A01", "gfp"): "decoded"}
    st.row_dims = ["well", "channel"]  # selector layout must survive

    B.image_table_clear_render(app._ctx)

    assert st.last_render == {}
    # the decoded-image cache and the selector layout are preserved
    assert st.image_cache == {("A01", "gfp"): "decoded"}
    assert st.row_dims == ["well", "channel"]


def test_reset_on_dataset_load_resets_layout_and_render(monkeypatch):
    # Fires from the Image Table on_dataset_loaded hook (and so on the "Reset all
    # saved state" trash-can, which deletes persistence.json then reloads): a
    # (re)loaded dataset must not inherit the previous grid — layout defaults AND
    # rendered images AND per-channel opts / builder cache all reset.
    app = _fake_app(wells=["A01", "B02"], channels=["gfp", "rfp"],
                    timepoints=[0, 6], fovs=[1, 2])
    st = app._app_state.image_table
    st.row_dims = ["well"]
    st.col_axis = "fov"
    st.rows, st.cols = 9, 5
    st.active_wells = {"A01"}
    st.active_channels = {"GFP"}
    st.channel_opts = {"GFP": {"render": "corrected"}}
    st.last_render = {(0, 0): "arr"}
    app._ctx.runtime.image_table.builder_spec = object()

    calls = []
    monkeypatch.setattr(C, "image_table_rebuild_grid", lambda ctx: calls.append("grid"))
    monkeypatch.setattr(C, "image_table_refresh_picker", lambda ctx: calls.append("picker"))

    B.image_table_reset_on_dataset_load(app._ctx)

    # fresh-tab layout defaults
    assert st.row_dims == [] and st.col_axis == "timepoint"
    assert (st.rows, st.cols) == (2, 3)
    assert st.active_wells == set() and st.active_channels == set()
    # …plus the extras the dataset-load reset owns (beyond "Reset table"):
    assert st.channel_opts == {}
    assert app._ctx.runtime.image_table.builder_spec is None
    assert st.last_render == {}
    # rebuilds the empty selector + refreshes the sidebar
    assert "grid" in calls and "picker" in calls


def test_reset_table_button_keeps_channel_opts(monkeypatch):
    # Scope boundary: the "Reset table" button clears the layout but leaves the
    # per-channel render options (⚙) intact — only the dataset-load reset drops
    # those. Locks the two actions apart so they don't silently converge.
    app = _fake_app(wells=["A01"], channels=["gfp"])
    st = app._app_state.image_table
    st.channel_opts = {"GFP": {"render": "corrected"}}
    monkeypatch.setattr(C, "image_table_rebuild_grid", lambda ctx: None)
    monkeypatch.setattr(C, "image_table_refresh_picker", lambda ctx: None)

    B.image_table_reset_layout(app._ctx)

    assert st.channel_opts == {"GFP": {"render": "corrected"}}
