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


@pytest.mark.qt
def test_set_spacing_rerenders_once_a_table_is_built(image_table_app, monkeypatch):
    """A Properties tweak must take effect as soon as a table has been built —
    not only after a first Generate (the old ``last_render`` gate silently
    dropped the first adjustment)."""
    from well_viewer.features.image_browser.image_table import controller as C

    ctx = image_table_app._ctx
    calls = []
    monkeypatch.setattr(C, "image_table_generate", lambda a: calls.append(a))

    # No cells yet → nothing to re-render.
    ctx.runtime.image_table.cells = []
    ctx.state.image_table.last_render = {}
    C.image_table_set_spacing(ctx, "cell_gap", 12)
    assert ctx.state.image_table.cell_gap == 12
    assert calls == []

    # Table built but never Generated (last_render still empty) → must re-render.
    C.image_table_apply_preset(ctx, "channel", "timepoint")
    assert ctx.runtime.image_table.cells
    ctx.state.image_table.last_render = {}
    C.image_table_set_spacing(ctx, "row_group_gap", 30)
    assert ctx.state.image_table.row_group_gap == 30
    assert calls, "set_spacing must re-render once cells exist, even pre-Generate"
