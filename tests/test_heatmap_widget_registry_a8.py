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
from tests.widget_registry_helpers import assert_handles_registered

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

    assert_handles_registered(app, _HANDLES)
