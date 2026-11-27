"""Spatial tab Properties (Export-Style) side panel.

The Spatial tab pre-allocates a right-side dock (like the plotting tabs) and its
Properties button opens the shared ``ExportStyleSidebar`` mounted in that dock.
This asserts the wiring: the dock exists, the panel resolves to the Spatial
figure, and opening / toggling it shows and hides the dock.
"""

from __future__ import annotations

import pytest


def _open_spatial(app):
    app._set_selected_wells(set(app._app_state.dataset.well_paths))
    assert app._notebook.setCurrentByName("Spatial")
    assert hasattr(app, "_sp_canvas"), "Spatial tab did not build"


@pytest.mark.slow
def test_spatial_properties_panel_opens_and_toggles(loaded_app_shape):
    app = loaded_app_shape
    _open_spatial(app)

    # Opening resolves the style panel to the Spatial figure and shows it as
    # the inspector's Style tab — beside the page, not in a dock of the plot's.
    from review.core import inspector_service as svc
    app._ctx.figures.open_style_panel("spatial")
    fig = app._ctx.widgets.get_or_none("_sp_fig")
    sidebars = getattr(app, "_export_style_sidebars", {}) or {}
    sb = sidebars.get(id(fig))
    assert sb is not None, "the style panel was not created for the Spatial figure"
    insp = svc.inspector(app._ctx)
    assert svc.is_open(app._ctx) and insp.currentTab() == "style"
    assert insp.tabWidget("style") is sb and insp.isAncestorOf(sb)
    assert not sb.isHidden(), "the panel should be shown after opening"

    # The same press again closes it, and the panel goes back to its park.
    app._ctx.figures.open_style_panel("spatial")
    assert not svc.is_open(app._ctx)
    assert not insp.holds(sb) and sb.isHidden()

    # And the panel's own close path closes the inspector too.
    app._ctx.figures.open_style_panel("spatial")
    assert svc.is_open(app._ctx)
    sb._close_dock()
    assert not svc.is_open(app._ctx) and sb.isHidden()


def _flow_host(widget):
    """The nearest ancestor of *widget* whose layout is a ``FlowLayout``.

    A scope control is not a direct child of the flow: ``ControlBand.add_scope``
    bundles a labelled control with its caption into a ``flow_group`` container
    (a ``QHBoxLayout``, so the label can never wrap away from its control) and
    adds that container to the flow. Walking up rather than reading
    ``parentWidget()`` keeps this assertion about the property that matters —
    the control sits inside a zone that wraps — instead of about how many
    containers currently sit between the two.
    """
    from widgets.flow_layout import FlowLayout
    node = widget.parentWidget()
    while node is not None:
        if isinstance(node.layout(), FlowLayout):
            return node
        node = node.parentWidget()
    return None


@pytest.mark.slow
def test_control_rows_use_reflowing_flow_layout(loaded_app_shape):
    """The Spatial controls live in a FlowLayout-backed zone, so they wrap onto
    extra lines when the pane narrows — e.g. when the Properties dock opens or in
    Split View — instead of crushing / overlapping the dense combos. (The
    wrapping geometry itself is covered by tests/test_flow_layout.py; here we
    assert the controls are wired to it, which the offscreen harness can verify
    without shown widgets.)

    Both the always-present controls (Level) and the parameter groups shown only
    for the relevant level (Colonies) go through the same zone: the scope side of
    the tab's shared ``ControlBand``.
    """
    app = loaded_app_shape
    _open_spatial(app)
    reg = app._ctx.widgets
    level_host = _flow_host(reg.get_or_none("_sp_level_seg"))
    method_host = _flow_host(reg.get_or_none("_sp_method_cb"))
    assert level_host is not None, "Level control is not inside a flowing zone"
    assert method_host is not None, "Colonies control is not inside a flowing zone"
    # One band, one scope zone — not a control row per group.
    assert level_host is method_host
    assert level_host.objectName() == "ControlBandScope"
