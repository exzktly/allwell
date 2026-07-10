"""The builder LUT panel lists each channel once, and never lists a binary render.

A channel shown as BOTH its fluorescence and its ≥Thresh binary previously appeared
twice (keyed by ``axis_token`` → ``GFP`` and ``GFP (binary)``). A binary/mask render
has no display range, so it must not get a LUT row; the fluorescence instance drives
the single row.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from well_viewer.features.image_browser.image_table.state import (  # noqa: E402
    Axis, ChannelSpec, TableSpec,
)
from well_viewer.features.image_browser.image_table.builder_view import (  # noqa: E402
    _table_channels,
)


def test_fluor_plus_threshold_channel_appears_once():
    spec = TableSpec(rows=Axis(root="well", channels=[
        ChannelSpec("GFP", "corrected"),
        ChannelSpec("GFP", "threshold"),   # ≥Thresh binary of the same channel
    ]))
    lut = _table_channels(spec)
    assert list(lut) == ["GFP"], "GFP must appear once, not GFP + GFP (binary)"
    assert len(lut["GFP"]) == 1
    assert lut["GFP"][0].render == "corrected"


def test_binary_only_channel_gets_no_lut_row():
    spec = TableSpec(
        rows=Axis(root="well", channels=[ChannelSpec("DAPI", "mask")]),
        cols=Axis(channels=[ChannelSpec("GFP", "threshold")]),
    )
    assert _table_channels(spec) == {}, "mask / threshold renders have no LUT"


def test_distinct_fluor_channels_each_get_a_row():
    spec = TableSpec(rows=Axis(channels=[
        ChannelSpec("GFP", "corrected"), ChannelSpec("DAPI", "raw"),
    ]))
    assert set(_table_channels(spec)) == {"GFP", "DAPI"}
