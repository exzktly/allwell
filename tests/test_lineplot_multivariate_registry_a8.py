"""A8 burn-down — lineplot widget handles into the registry.

The lineplot tab builder stashed its Qt widgets on the app god-object. It now
registers through ``ctx.widgets`` (which stamps ``app._<name>``) so the legacy
readers keep resolving. lineplot also folds its channel/metric combos + the
``_plot_chan_var`` / ``_chan_var`` / ``_metric_var`` aliases into the registry.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QWidget

from well_viewer.features.lineplot.tab_view import build_line_graphs_tab

_LINE = (
    "_line_export_dock", "_chan_cb_line", "_plot_chan_var", "_chan_var",
    "_metric_selector_frame", "_metric_cb", "_metric_var",
    "_line_card", "_line_fig", "_line_ax_mean", "_line_ax_frac",
    "_line_ax_n", "_line_ax_cdf", "_line_canvas",
)


def _check(app, names):
    for name in names:
        assert app._widget_registry.has(name), f"{name} not registered"
        assert getattr(app, name, None) is not None, f"{name} not on app"
        assert getattr(app, name) is app._widget_registry.get(name)


def test_lineplot_build_registers_handles(viewer_app, qtbot):
    app = viewer_app
    parent = QWidget()
    qtbot.addWidget(parent)
    build_line_graphs_tab(app._ctx, parent)
    _check(app, _LINE)
