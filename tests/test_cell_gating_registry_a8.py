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

from PySide6.QtWidgets import QApplication, QWidget

from well_viewer.features.home.cell_gating.tab_view import build_cell_gating_tab
from tests.widget_registry_helpers import assert_handles_registered

_HANDLES = (
    "_cell_gating_frame", "_cell_gating_use_scatter_gates_cb", "_cell_gating_gating_gate_btn",
    "_cell_gating_use_scatter_thresh_cb", "_cell_gating_thresh_gate_btn",
    "_cell_gating_gate_summary", "_cell_gating_included_count",
    "_cell_gating_funnel_label",
    "_cell_gating_auto_btn", "_cell_gating_open_optimizer_btn",
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

    assert_handles_registered(app, _HANDLES)

    # Let the canvas paint before the test ends. The builder draws the themed
    # empty state, which schedules an idle draw, and nothing here runs the event
    # loop -- so it was still pending when the test returned. pytest-qt then
    # calls `processEvents()` in its post-call hook, by which point the test's
    # locals are released; `qtbot.addWidget` holds `parent` only weakly, so the
    # widget tree is already collected and matplotlib drives `_draw_idle` on a
    # deleted C++ object (`RuntimeError: Internal C++ object
    # (FigureCanvasQTAgg) already deleted`). Draining it here resolves the draw
    # while the canvas is still alive.
    #
    # `qtbot.wait(0)` is not a substitute: measured, it leaves the draw pending
    # and the file still fails. Not a product bug and not this test's subject,
    # but it failed on every run in the slow lane, where the `slow` mark keeps
    # CI from ever seeing it.
    QApplication.processEvents()
