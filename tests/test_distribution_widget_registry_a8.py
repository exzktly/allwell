"""A8 burn-down — distribution widget handles moved into the WidgetRegistry.

The Distribution tab builder stashed its Qt widgets (channel/tp/mode/layout
combos, bins spinbox, log-x/log-y checkboxes, PlotCard, figure/axes/canvas,
export dock) plus three legacy ``_*_var`` aliases on the app god-object. They now
register through ``ctx.widgets`` (which also stamps ``app._<name>``), so the
distribution controller's legacy ``app._distribution_*`` readers keep resolving.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QWidget

from review.features.distribution.tab_view import build_distribution_tab
from tests.widget_registry_helpers import assert_handles_registered

_HANDLES = (
    "_distribution_export_dock", "_chan_cb_distribution", "_distribution_tp_cb",
    "_distribution_tp_var", "_distribution_mode_cb", "_distribution_mode_var",
    "_distribution_bins_spin", "_distribution_layout_cb", "_distribution_layout_var",
    "_distribution_log_x_cb", "_distribution_log_y_cb",
    "_distribution_card", "_distribution_fig", "_distribution_ax",
    "_distribution_canvas",
)


def test_build_registers_handles_and_stamps_app(viewer_app, qtbot):
    app = viewer_app
    parent = QWidget()
    qtbot.addWidget(parent)
    build_distribution_tab(app._ctx, parent)

    assert_handles_registered(app, _HANDLES)

    # The `_var` aliases must point at the same widget as their primary handle.
    assert app._distribution_tp_var is app._distribution_tp_cb
    assert app._distribution_mode_var is app._distribution_mode_cb
    assert app._distribution_layout_var is app._distribution_layout_cb
