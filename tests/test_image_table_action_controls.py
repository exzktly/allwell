"""Image Table action-strip controls.

Covers the ON-threshold channel picker (choose which channel the threshold
applies to) and the removal of the old Advanced selector grid — the render model
is now headless plain data (no per-cell selector-grid widgets), so there is no
Advanced UI surface and no ``_image_table_selector_grid`` handle at all.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


@pytest.mark.qt
def test_on_threshold_has_a_channel_dropdown(image_table_app):
    from well_viewer.features.image_browser.image_table import controller as C

    ctx = image_table_app._ctx
    C.image_table_repopulate_dropdowns(ctx)
    cb = ctx.widgets.get("_image_table_thresh_chan_cb")
    assert cb is not None
    items = [cb.itemText(i) for i in range(cb.count())]
    # Real fluor channels, no "(binary)" render variants.
    assert items
    assert not any("(binary)" in i for i in items)


@pytest.mark.qt
def test_render_model_is_headless_plain_data(image_table_app):
    from PySide6.QtWidgets import QToolButton

    ctx = image_table_app._ctx
    # The old Advanced per-cell selector grid is gone entirely — not hidden.
    assert ctx.widgets.get_or_none("_image_table_selector_grid") is None
    assert ctx.widgets.get_or_none("_image_table_lut_container") is None

    # The render model is a rows × cols grid of plain value dicts (no Qt widgets):
    # that IS the render contract the renderer reads.
    C = _controller()
    C.image_table_rebuild_grid(ctx)
    cells = ctx.runtime.image_table.cells
    assert cells and all(
        isinstance(cell, dict) and set(cell) == {"well", "chan", "tp", "fov"}
        and all(isinstance(v, str) for v in cell.values())
        for row in cells for cell in row
    )

    # And there is no "Advanced" disclosure button anywhere in the tab.
    sub = ctx.runtime.image_browser.subtabs
    idx = next(i for i in range(sub.count()) if sub.tabText(i) == "Image Table")
    tab = sub.widget(idx)
    assert not [b for b in tab.findChildren(QToolButton)
                if "Advanced" in (b.text() or "")]


def _controller():
    from well_viewer.features.image_browser.image_table import controller as C
    return C
