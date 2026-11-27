"""LUT editor on the Cell Browser tab edits the shared per-channel LUT."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")


def test_lut_editor_rows_built_and_edit_shared_lut(loaded_app):
    from review.features.image_browser.cell_browser import tab_view as cbt

    app = loaded_app
    from review.features.image_browser.controller import show_image_browser_subtab
    assert show_image_browser_subtab(app._ctx, "Cell Browser")
    cbt.cell_browser_refresh_lut_rows(app._ctx)   # takes ctx (A8)
    rows = app._ctx.runtime.cell_browser.lut_rows
    assert rows, "expected per-channel LUT rows"
    chan = next(iter(rows))

    # Typing min/max writes the shared LUT.
    cbt._apply_cell_browser_lut(app._ctx, chan, "10", "200")
    assert app._app_state.image_view.get_lut(chan) == (10.0, 200.0)

    # Auto / blank clears it back to per-crop auto.
    cbt._apply_cell_browser_lut(app._ctx, chan, "", "")
    assert app._app_state.image_view.get_lut(chan) is None


def test_each_channel_s_brightness_is_the_range_page_and_its_buttons_reach_the_shared_lut(loaded_app):
    """The Brightness page is one ``RangePage`` per channel, not a pair of
    fields of its own: typing a range, Auto and Reset each reach the shared
    per-channel LUT through the page's real controls."""
    from review.features.image_browser.cell_browser import tab_view as cbt
    from review.features.image_browser.controller import show_image_browser_subtab
    from widgets.range_page import RangePage

    ctx = loaded_app._ctx
    assert show_image_browser_subtab(ctx, "Cell Browser")
    iv = loaded_app._app_state.image_view
    chan = next(iter(cbt.cell_browser_channels(ctx)))
    iv.set_lut(chan, 120.0, 3400.0)                    # the page opens on this range
    cbt.cell_browser_refresh_lut_rows(ctx)
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)     # the rows the rebuild replaced
    panel = ctx.runtime.cell_browser.lut_panel
    page = panel.findChild(RangePage, f"CellBrowserRange_{chan}")
    assert page is not None and page.caption.text() == chan.upper()
    assert ctx.runtime.cell_browser.lut_rows[chan] == (page.low_edit, page.high_edit)

    page.low_edit.setText("10")
    page.high_edit.setText("200")
    page.high_edit.editingFinished.emit()
    assert iv.get_lut(chan) == (10.0, 200.0)
    page.auto_button.click()
    assert iv.get_lut(chan) is None
    page.reset_button.click()
    assert iv.get_lut(chan) == (120.0, 3400.0), "Reset did not return to the range the page opened with"
