"""Image Table smart-layout builder: axis value lists + per-cell mapping."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from well_viewer.features.image_table import controller as C  # noqa: E402


def _fake_app(*, wells, channels, active_wells=None, active_channels=None,
              timepoints=None, fovs=None):
    info = {
        "available_timepoints": timepoints or [],
        "available_fovs": fovs or [],
        "nuclear_token": "",
    }
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths={w: w for w in wells}, pipeline_info=info,
                                cache={}),
        channel=SimpleNamespace(fluor_channels=list(channels), smfish_channels=set(),
                                ratio_metrics=[]),
    )
    # parse_rc: sort key "A01" -> (row, col)
    app._parse_rc = lambda w: (w[0], int(w[1:]))
    app._image_table_active_wells = set(active_wells or [])
    app._image_table_active_channels = set(active_channels or [])
    return app


def test_axis_values_wells_uses_selection_then_falls_back_to_all():
    app = _fake_app(wells=["A01", "A02", "B01"], channels=["gfp"],
                    active_wells=["B01", "A01"])
    # selection, sorted by parse_rc
    assert C._axis_values(app, "well") == ["A01", "B01"]
    # no selection → all wells
    app._image_table_active_wells = set()
    assert C._axis_values(app, "well") == ["A01", "A02", "B01"]


def test_axis_values_channels_filters_to_ticked():
    chans = C._channel_options(_fake_app(wells=["A01"], channels=["gfp", "rfp"]))
    assert chans, "channel options should be non-empty"
    # tick exactly one channel → only it appears, in option order
    app = _fake_app(wells=["A01"], channels=["gfp", "rfp"],
                    active_channels={chans[0]})
    assert C._axis_values(app, "channel") == [chans[0]]
    # none ticked → all channels
    app._image_table_active_channels = set()
    assert C._axis_values(app, "channel") == chans


def test_axis_values_timepoints_and_fovs_delegate_to_options():
    app = _fake_app(wells=["A01"], channels=["gfp"],
                    timepoints=[6, 24], fovs=[1, 2, 3])
    # the axis just surfaces the canonical option lists
    assert C._axis_values(app, "timepoint") == C._timepoint_options(app)
    assert C._axis_values(app, "fov") == C._fov_options(app)
    assert C._axis_values(app, "fov") == ["1", "2", "3"]


def test_layout_cell_values_maps_axes_and_holds_fixed():
    fixed = {"well": "A01", "channel": "GFP", "timepoint": "0", "fov": "1"}
    rows = ["GFP", "RFP"]          # row axis = channel
    cols = ["0", "24", "48"]       # col axis = timepoint
    # (r=1, c=2): channel from row, timepoint from col, well/fov stay fixed
    v = C._layout_cell_values("channel", "timepoint", rows, cols, fixed, 1, 2)
    assert v == {"well": "A01", "channel": "RFP", "timepoint": "48", "fov": "1"}
    # (r=0, c=0): top-left
    v0 = C._layout_cell_values("channel", "timepoint", rows, cols, fixed, 0, 0)
    assert v0["channel"] == "GFP" and v0["timepoint"] == "0"


def test_layout_cell_values_wells_x_channels():
    fixed = {"well": "", "channel": "", "timepoint": "0", "fov": "1"}
    rows = ["A01", "A02"]   # rows = wells
    cols = ["GFP", "RFP"]   # cols = channels
    v = C._layout_cell_values("well", "channel", rows, cols, fixed, 1, 0)
    assert v["well"] == "A02" and v["channel"] == "GFP"
    assert v["timepoint"] == "0" and v["fov"] == "1"
