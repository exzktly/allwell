"""Image Table action-strip controls.

Covers the ON-threshold channel picker (choose which channel the threshold
applies to) and the retirement of the old Advanced selector grid — it stays
mounted as hidden render plumbing (the cell combos are the render contract) but
is no longer a visible UI surface.
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
def test_advanced_selector_grid_is_hidden_plumbing(image_table_app):
    from PySide6.QtWidgets import QFrame, QToolButton

    ctx = image_table_app._ctx
    # The per-cell selector grid is the render contract → still registered.
    sel = ctx.widgets.get("_image_table_selector_grid")
    assert sel is not None

    # …but it lives inside a hidden Panel — the Advanced UI is gone from view.
    frame = sel.parentWidget()
    while frame is not None and not (
            isinstance(frame, QFrame) and frame.objectName() == "Panel"):
        frame = frame.parentWidget()
    assert frame is not None and not frame.isVisible()

    # And there is no "Advanced" disclosure button anywhere in the tab.
    sub = ctx.runtime.image_browser.subtabs
    idx = next(i for i in range(sub.count()) if sub.tabText(i) == "Image Table")
    tab = sub.widget(idx)
    assert not [b for b in tab.findChildren(QToolButton)
                if "Advanced" in (b.text() or "")]
