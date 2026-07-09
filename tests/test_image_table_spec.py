"""Qt-free unit tests for the Image Table builder value model (state.py).

The drag-and-drop shelf edits a ``TableSpec``; ``spec_to_state_fields`` flattens
it onto the existing ImageTableState fields the layout math consumes, and
``spec_from_state`` reconstructs the tree (restore + round-trip). These are pure
data — no Qt, no ctx — so they run in the fast lane.
"""

from well_viewer.features.image_browser.image_table.state import (
    Axis,
    ChannelSpec,
    ImageTableState,
    TableSpec,
    spec_from_state,
    spec_to_state_fields,
)


def _apply(spec: TableSpec) -> ImageTableState:
    st = ImageTableState()
    for k, v in spec_to_state_fields(spec).items():
        setattr(st, k, v)
    return st


def test_nested_rows_flatten_and_restore():
    spec = TableSpec(
        rows=Axis(root="well", channels=[
            ChannelSpec("DAPI", "corrected", colormap="Greys"),
            ChannelSpec("GFP", "mask", boundary=True, colormap="viridis"),
        ]),
        cols=Axis(root="timepoint"),
        fixed={"fov": "1"},
    )
    st = _apply(spec)
    # flatten
    assert st.row_dims == ["well", "channel"]
    assert st.col_dims == ["timepoint"]
    assert st.active_channels == {"DAPI", "GFP"}
    assert st.composite is False
    assert st.fixed_vals == {"fov": "1"}
    assert st.channel_opts["GFP"]["render"] == "mask"
    assert st.channel_opts["GFP"]["boundary"] is True
    # restore
    back = spec_from_state(st)
    assert back.rows.root == "well"
    assert [c.channel for c in back.rows.channels] == ["DAPI", "GFP"]
    assert back.rows.channels[1].render == "mask"
    assert back.rows.channels[1].boundary is True
    assert back.rows.channels[1].colormap == "viridis"
    assert back.cols.root == "timepoint"
    assert back.cols.channels == []
    assert back.fixed == {"fov": "1"}


def test_threshold_channel_rides_binary_token():
    spec = TableSpec(
        rows=Axis(channels=[ChannelSpec("GFP", "threshold")]),
        cols=Axis(root="timepoint"),
    )
    st = _apply(spec)
    # threshold channel is carried by the "(binary)" virtual-channel token
    assert st.active_channels == {"GFP (binary)"}
    assert st.row_dims == ["channel"]
    assert "GFP (binary)" in st.channel_opts
    back = spec_from_state(st)
    assert back.rows.channels[0].channel == "GFP"
    assert back.rows.channels[0].render == "threshold"
    assert back.rows.channels[0].axis_token() == "GFP (binary)"


def test_overlay_and_wrap_flatten():
    spec = TableSpec(
        rows=Axis(root="well", channels=[ChannelSpec("DAPI"), ChannelSpec("RFP")], overlay=True),
        cols=Axis(root="timepoint"),
        wrap=3,
    )
    st = _apply(spec)
    assert st.composite is True
    assert st.group_size == 3
    back = spec_from_state(st)
    assert back.rows.overlay is True
    assert back.wrap == 3
    # overlay only reported on the channel axis
    assert back.cols.overlay is False


def test_channels_on_columns_axis():
    spec = TableSpec(
        rows=Axis(root="well"),
        cols=Axis(root="timepoint", channels=[ChannelSpec("DAPI"), ChannelSpec("GFP")]),
    )
    st = _apply(spec)
    assert st.row_dims == ["well"]
    assert st.col_dims == ["timepoint", "channel"]
    back = spec_from_state(st)
    assert back.rows.channels == []
    assert [c.channel for c in back.cols.channels] == ["DAPI", "GFP"]


def test_root_only_axes_no_channels_is_empty_dims():
    spec = TableSpec(rows=Axis(channels=[ChannelSpec("DAPI")]), cols=Axis())
    st = _apply(spec)
    assert st.row_dims == ["channel"]
    assert st.col_dims == []
    back = spec_from_state(st)
    assert back.cols.root is None
    assert back.cols.channels == []
