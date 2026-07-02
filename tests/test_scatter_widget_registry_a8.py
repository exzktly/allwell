"""A8 burn-down — scatter (cells + aggregate) widget handles into the registry.

Both scatter tab builders stashed their Qt widgets (per-axis channel/property
combos, timepoint button/label, layout + colour-by-time controls, PlotCard,
figure/axes/canvas, export dock) plus several legacy ``_*_var`` aliases on the
app god-object. They now register through ``ctx.widgets`` (which stamps
``app._<name>``), so the scatter controllers' legacy ``app._scatter_*`` readers
keep resolving.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QWidget

from well_viewer.features.scatter.cells_tab_view import build_scatter_cells_tab
from well_viewer.features.scatter.agg_tab_view import build_scatter_agg_tab

_CELLS = (
    "_scatter_export_dock", "_scatter_ch_x_cb", "_scatter_ch_x_var",
    "_scatter_metric_x_cb", "_scatter_metric_x_var", "_scatter_ch_y_cb",
    "_scatter_ch_y_var", "_scatter_metric_y_cb", "_scatter_metric_y_var",
    "_scatter_tp_button", "_scatter_tp_label", "_scatter_tp_cb", "_scatter_tp_var",
    "_scatter_layout_cb", "_scatter_layout_var", "_scatter_color_by_tp_btn",
    "_scatter_card", "_scatter_fig", "_ax_scatter", "_scatter_canvas",
)

_AGG = (
    "_scatter_agg_export_dock", "_scatter_agg_ch_x_cb", "_scatter_agg_metric_x_cb",
    "_scatter_agg_ch_y_cb", "_scatter_agg_metric_y_cb", "_scatter_agg_stat_x_cb",
    "_scatter_agg_stat_x_var", "_scatter_agg_stat_y_cb", "_scatter_agg_stat_y_var",
    "_scatter_agg_tp_button", "_scatter_agg_tp_label", "_scatter_agg_card",
    "_scatter_agg_fig", "_ax_scatter_agg", "_scatter_agg_canvas",
)


def _check(app, names):
    for name in names:
        assert app._widget_registry.has(name), f"{name} not registered"
        assert getattr(app, name, None) is not None, f"{name} not on app"
        assert getattr(app, name) is app._widget_registry.get(name)


def test_cells_build_registers_handles(viewer_app, qtbot):
    app = viewer_app
    parent = QWidget()
    qtbot.addWidget(parent)
    build_scatter_cells_tab(app._ctx, parent)
    _check(app, _CELLS)
    assert app._scatter_tp_var is app._scatter_tp_cb


def test_agg_build_registers_handles(viewer_app, qtbot):
    app = viewer_app
    parent = QWidget()
    qtbot.addWidget(parent)
    build_scatter_agg_tab(app._ctx, parent)
    _check(app, _AGG)
    assert app._scatter_agg_stat_x_var is app._scatter_agg_stat_x_cb
