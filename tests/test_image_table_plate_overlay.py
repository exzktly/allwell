"""Plate mode honours the shelf's Overlay switch.

Reported: with GFP and NIR overlaid, only GFP showed — in the cells and on the LUT panel, which listed GFP alone. Plate
mode pinned every cell to the *first* channel and forced the overlay off, while the shelf still offered the switch.

The flattening (``state.spec_to_state_fields``) and its inverse are pure and run in the fast lane. The cells and the
LUT panel are checked through the real Image Table (``image_table_app``, slow lane): each plate cell names both
channels after a Generate, and the LUT panel has a row for each.
"""

from __future__ import annotations

import pytest

from review.features.image_browser.image_table.state import (
    OVERLAY_CHAN, Axis, ChannelSpec, ImageTableState, TableSpec, plate_overlays, spec_from_state,
    spec_to_state_fields,
)


def _plate(overlay: bool, channels=("GFP", "NIR")) -> TableSpec:
    return TableSpec(rows=Axis(channels=[ChannelSpec(c) for c in channels], overlay=overlay), plate=True)


def test_plate_mode_with_overlay_blends_every_channel():
    fields = spec_to_state_fields(_plate(overlay=True))
    assert fields["fixed_vals"]["channel"] == OVERLAY_CHAN
    assert fields["composite"] is True
    assert fields["active_channels"] == {"GFP", "NIR"}


def test_plate_mode_without_overlay_still_shows_the_first_channel():
    fields = spec_to_state_fields(_plate(overlay=False))
    assert fields["fixed_vals"]["channel"] == "GFP"
    assert fields["composite"] is False


def test_one_channel_has_nothing_to_blend():
    assert plate_overlays(_plate(overlay=True, channels=("GFP",))) is False
    assert spec_to_state_fields(_plate(overlay=True, channels=("GFP",)))["fixed_vals"]["channel"] == "GFP"


def test_a_saved_plate_overlay_comes_back_overlaid():
    st = ImageTableState()
    for key, value in spec_to_state_fields(_plate(overlay=True)).items():
        setattr(st, key, value)
    st.row_dims, st.col_dims = ["well_row"], ["well_col"]
    rebuilt = spec_from_state(st)
    assert rebuilt.plate and rebuilt.rows.overlay
    assert [c.channel for c in rebuilt.rows.channels] == ["GFP", "NIR"]


def _lut_rows(ctx) -> list[str]:
    from PySide6.QtWidgets import QLabel

    from review.features.image_browser.image_table.builder_view import _rt

    return [lbl.text() for lbl in _rt(ctx).lut_box_body.findChildren(QLabel)
            if lbl.objectName() != "ITBHint" and lbl.text() not in ("", "min", "max")]


@pytest.mark.qt
def test_a_plate_overlay_draws_both_channels_and_offers_both_luts(image_table_app):
    """Through the real tab, with the fixture's two fluorescence channels standing in for GFP and NIR."""
    from PySide6.QtWidgets import QLabel

    from review.features.image_browser.image_table import builder_view as BV
    from review.features.image_browser.image_table.controller import image_table_generate

    ctx = image_table_app._ctx
    spec = BV.builder_spec(ctx)
    spec.rows = Axis(channels=[BV.new_channel_spec(ctx, "GFP"), BV.new_channel_spec(ctx, "MCHERRY")], overlay=True)
    spec.cols = Axis()
    BV._set_plate(ctx, True)
    BV.builder_flush_pending(ctx)
    BV._apply_now(ctx)

    assert _lut_rows(ctx) == ["GFP", "MCHERRY"]
    cells = [cell for row in ctx.runtime.image_table.cells for cell in row if cell.get("well")]
    assert cells and {cell["chan"] for cell in cells} == {OVERLAY_CHAN}

    image_table_generate(ctx)
    grid = ctx.runtime.image_table.render_grid
    tips = {w.toolTip() for i in range(grid.count())
            if (item := grid.itemAt(i)).widget() is not None
            for w in item.widget().findChildren(QLabel) if w.toolTip()}
    assert tips and all("GFP" in t and "MCHERRY" in t for t in tips), tips
