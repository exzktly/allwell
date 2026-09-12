"""A8 burn-down — barplot widget handles into the WidgetRegistry.

The Bar Plots tab builder stashed ~22 Qt widgets (hidden channel/metric combos,
reset-order + timepoint + grouping/pooling/beeswarm/violin/significance buttons,
violin slider, PlotCard, 3 axes, canvas, scroll area, export dock) plus the
``_bar_tp_var`` alias on the god-object. They now register through ``ctx.widgets``
(which stamps ``app._bar_*`` / ``app._chan_cb_bar`` etc.) so the barplot
controller's legacy readers keep resolving; the swarm/violin flags + drag state
stay on the app.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QWidget

from review.features.barplot.tab_view import build_bar_plots_tab
from tests.widget_registry_helpers import assert_handles_registered

_HANDLES = (
    "_bar_export_dock", "_chan_cb_bar", "_metric_selector_frame_bar", "_metric_cb_bar",
    "_bar_reset_order_btn", "_bar_tp_button", "_bar_tp_cb", "_bar_tp_var",
    "_bar_group_mode_btn", "_bar_pool_reps_btn",
    "_swarm_btn", "_violin_btn", "_violin_slider", "_bar_significance_btn",
    "_bar_significance_label_cb",
    "_bar_card", "_bar_fig", "_ax_bar_mean", "_ax_bar_frac", "_ax_bar_n",
    "_bar_canvas", "_bar_scroll_canvas",
)


def test_build_registers_handles_and_stamps_app(viewer_app, qtbot):
    app = viewer_app
    parent = QWidget()
    qtbot.addWidget(parent)
    build_bar_plots_tab(app._ctx, parent)

    assert_handles_registered(app, _HANDLES)

    assert app._bar_tp_var is app._bar_tp_cb
