"""The builder LUT panel lists each adjustable channel once, and names the rest.

A channel shown as BOTH its fluorescence and its ≥Thresh binary previously appeared
twice (keyed by ``axis_token`` → ``GFP`` and ``GFP (binary)``). A binary/mask render
has no display range, so it must not get a LUT row; the fluorescence instance drives
the single row. The ``NUC+SEG`` overlay is the same case: the render seam passes a
pre-rendered RGB straight through, so a LUT row for it would repaint nothing.

Every channel the panel cannot govern still draws an image, so a LUT edit that
leaves those cells alone reads as "only part of the grid updated". The panel names
them instead of staying silent.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from well_viewer.features.image_browser.image_table.state import (
    Axis, ChannelSpec, TableSpec,
)
from well_viewer.features.image_browser.image_table.builder_view import (
    _lut_inert_channels,
    _table_channels,
)
from well_viewer.features.image_browser.image_table.image_compose import (
    NUC_SEG_TOKEN,
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


def test_nuc_seg_gets_no_lut_row():
    """NUC+SEG is a pre-rendered RGB overlay: the render seam ignores the LUT for
    it, so offering an editable min/max/colormap row is a control that does
    nothing."""
    spec = TableSpec(rows=Axis(channels=[
        ChannelSpec("GFP", "corrected"), ChannelSpec(NUC_SEG_TOKEN, "corrected"),
    ]))
    assert list(_table_channels(spec)) == ["GFP"]


def test_inert_channels_are_named():
    """Every channel the LUT cannot govern is listed, so a partial repaint is
    explained by the panel rather than looking like a bug."""
    spec = TableSpec(
        rows=Axis(root="well", channels=[
            ChannelSpec("GFP", "corrected"),
            ChannelSpec("GFP", "threshold"),
            ChannelSpec("DAPI", "mask"),
        ]),
        cols=Axis(channels=[ChannelSpec(NUC_SEG_TOKEN, "corrected")]),
    )
    assert list(_table_channels(spec)) == ["GFP"]
    assert _lut_inert_channels(spec) == [
        "GFP (binary)", "DAPI (mask)", NUC_SEG_TOKEN,
    ]


def test_no_inert_channels_when_every_channel_is_fluorescence():
    spec = TableSpec(rows=Axis(channels=[
        ChannelSpec("GFP", "corrected"), ChannelSpec("DAPI", "raw"),
    ]))
    assert _lut_inert_channels(spec) == []
