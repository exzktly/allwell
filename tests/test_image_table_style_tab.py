"""The Image Table's style options are the inspector's Style tab.

Spacing, label fonts, the scale bar and cell labels are opened by the page's
**Style** button as the first page of the inspector's Style tab — beside the
page, like every plot's style panel — not in a dock of the page's own and not
in the left sidebar.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


@pytest.mark.qt
def test_style_opens_in_the_inspector_not_in_a_dock_or_the_sidebar(image_table_app):
    from review.core import inspector_service as svc
    from widgets.stepper import Stepper

    ctx = image_table_app._ctx
    assert image_table_app._notebook.setCurrentByName("Image Browser")
    button = ctx.widgets.get("_image_table_style_btn")
    panel = ctx.widgets.get("_image_table_style_panel")
    assert button.text() == "Style"
    assert not svc.is_open(ctx)                       # closed by default
    # 3 spacing gaps + 2 axis label font sizes + scale-bar (pixel size, bar length,
    # bar thickness) + cell-label font size = 9 steppers.
    assert len(panel.findChildren(Stepper)) == 9

    button.click()
    insp = svc.inspector(ctx)
    assert svc.is_open(ctx) and insp.currentTab() == "style"
    assert insp.tabWidget("style") is panel and insp.isAncestorOf(panel)
    button.click()
    assert not svc.is_open(ctx)                       # and closes it again
    assert not insp.isAncestorOf(panel)

    # The controls live in the inspector's page, not the left sidebar.
    sframe = ctx.widgets.get("_sidebar_image_table_frame")
    assert not sframe.findChildren(Stepper)


@pytest.mark.qt
def test_set_spacing_rerenders_once_a_table_is_built(image_table_app, monkeypatch):
    """A Properties tweak must take effect as soon as a table has been built —
    not only after a first Generate (the old ``last_render`` gate silently
    dropped the first adjustment)."""
    from review.features.image_browser.image_table import controller as C

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
