"""Image Table Properties inspector.

It must be a right-side slide-out dock toggled by a button — matching the
figure-properties panel every other tab uses — not a panel stuffed into the left
sidebar.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


@pytest.mark.qt
def test_properties_dock_slides_out_not_in_sidebar(image_table_app):
    from widgets.stepper import Stepper
    from well_viewer.features.image_browser.image_table import tab_view as TV

    ctx = image_table_app._ctx
    dock = ctx.runtime.image_table.props_dock
    assert dock is not None
    assert dock.isHidden()                          # closed by default
    # 3 spacing gaps + 2 label font sizes
    assert len(dock.findChildren(Stepper)) == 5

    TV._toggle_props_dock(ctx)
    assert not dock.isHidden()                      # the button slides it out
    TV._toggle_props_dock(ctx)
    assert dock.isHidden()                          # and closes it again

    # The controls live in the dock, not the left sidebar.
    sframe = ctx.widgets.get("_sidebar_image_table_frame")
    assert not sframe.findChildren(Stepper)
