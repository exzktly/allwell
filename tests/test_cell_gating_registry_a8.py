"""A8 burn-down — cell_gating widget handles into the WidgetRegistry.

The (2400-line) Cell Gating tab builder stashed ~28 Qt widgets on the god-object.
Their assignments now go through ``ctx.widgets.register`` (which stamps
``app._cell_gating_*``), so the builder's many downstream widget reads — and the
controller's readers — keep resolving. Non-widget state and optimizer choices
stay on the app. This test proves the tab builds and the handles register/stamp.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QWidget

from well_viewer.features.grouping.cell_gating_tab_view import build_cell_gating_tab

_HANDLES = (
    "_cell_gating_frame", "_cell_gating_use_scatter_gates_cb", "_cell_gating_gating_gate_btn",
    "_cell_gating_use_scatter_thresh_cb", "_cell_gating_thresh_gate_btn",
    "_cell_gating_gate_summary", "_cell_gating_auto_btn", "_cell_gating_scroll",
    "_cell_gating_inner", "_cell_gating_figure", "_cell_gating_canvas",
    "_cell_gating_plot_scroll", "_cell_gating_toolbar", "_cell_gating_status_label",
    "_cell_gating_opt_channel_combo", "_cell_gating_opt_well_a_combo",
    "_cell_gating_opt_well_b_combo", "_cell_gating_opt_timepoint_combo",
    "_cell_gating_opt_min_edit", "_cell_gating_opt_max_edit", "_cell_gating_opt_steps_edit",
    "_cell_gating_opt_btn", "_cell_gating_opt_fc_control_combo",
    "_cell_gating_opt_fc_ctrl_op_combo", "_cell_gating_opt_fc_baseline_combo",
    "_cell_gating_opt_fc_t0_op_combo", "_cell_gating_opt_fc_order_combo",
    "_cell_gating_opt_result_label",
)


def test_build_registers_handles_and_stamps_app(viewer_app, qtbot):
    app = viewer_app
    parent = QWidget()
    qtbot.addWidget(parent)
    build_cell_gating_tab(app._ctx, parent)  # exercises the reads-via-stamp too

    for name in _HANDLES:
        assert app._widget_registry.has(name), f"{name} not registered"
        assert getattr(app, name, None) is not None, f"{name} not on app"
        assert getattr(app, name) is app._widget_registry.get(name)
