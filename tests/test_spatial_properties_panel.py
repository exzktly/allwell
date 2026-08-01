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

    # The dock was pre-allocated for the shared sidebar.
    dock = app._ctx.widgets.get_or_none("_spatial_export_dock")
    assert dock is not None

    # Opening resolves the Export-Style sidebar to the Spatial figure and mounts
    # it in the dock. (``isHidden``/``isVisible`` differ under the offscreen QPA:
    # the top-level window is never shown, so ``isVisible()`` is always False;
    # ``isHidden()`` still reflects the explicit show/hide state.)
    app._ctx.figures.open_style_panel("spatial")
    fig = app._ctx.widgets.get_or_none("_sp_fig")
    sidebars = getattr(app, "_export_style_sidebars", {}) or {}
    sb = sidebars.get(id(fig))
    assert sb is not None, "Properties sidebar was not created for the Spatial figure"
    assert not sb.isHidden(), "sidebar should be shown after opening"
    assert not dock.isHidden(), "dock should be shown after opening"
    # The sidebar is mounted inside the Spatial dock (not floating / elsewhere).
    assert dock.layout().indexOf(sb) >= 0

    # Closing the dock hides the sidebar again (the toggle affordance's close
    # path, invoked directly since the offscreen window reports not-visible).
    sb._close_dock()
    assert sb.isHidden()


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
