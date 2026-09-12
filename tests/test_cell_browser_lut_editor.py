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
