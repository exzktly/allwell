"""A8 burn-down — heatmap widget handles moved into the WidgetRegistry.

The Heat Map tab builder stashed its ~17 Qt widgets (combos, edits, checkboxes,
slider, PlotCard, figure/axes/canvas) on the app god-object. They now go through
`ctx.widgets.register`, which also stamps `app._<name>` so the heatmap
controller's legacy `app._heatmap_*` readers keep resolving. This test proves
the tab builds and every migrated handle is present on the app afterwards.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QWidget

from well_viewer.features.heatmap.tab_view import build_heatmap_tab

_HANDLES = (
    "_heatmap_export_dock", "_chan_cb_heatmap", "_heatmap_metric_cb",
    "_heatmap_cmap_cb", "_heatmap_scale_cb", "_heatmap_vmin_edit",
    "_heatmap_vmax_edit", "_heatmap_repset_avg_cb", "_heatmap_log_scale_cb",
    "_heatmap_status_lbl", "_heatmap_tp_slider", "_heatmap_tp_label",
    "_heatmap_card", "_heatmap_fig", "_heatmap_ax", "_heatmap_cax",
    "_heatmap_canvas",
)


def test_build_registers_handles_and_stamps_app(viewer_app, qtbot):
    app = viewer_app
    parent = QWidget()
    qtbot.addWidget(parent)
    build_heatmap_tab(app._ctx, parent)
    # The heat-map builder ends with a redraw, and matplotlib's Qt backend
    # services that through ``draw_idle`` — a *queued* draw. Unlike the other
    # tab builders in this family, that leaves work outstanding when the test
    # body returns, and pytest-qt's post-test ``_process_events`` then runs it
    # after the canvas's C++ object is gone ("Internal C++ object
    # (FigureCanvasQTAgg) already deleted"). Let the queued draw settle while
    # the canvas is still alive. ``canvas.draw()`` is not enough — it renders
    # but does not cancel the pending timer.
    qtbot.wait(1)

    for name in _HANDLES:
        assert app._widget_registry.has(name), f"{name} not registered"
        assert getattr(app, name, None) is not None, f"{name} not on app"
        assert getattr(app, name) is app._widget_registry.get(name)
