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
    app._ctx.open_export_style_panel("spatial")
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
