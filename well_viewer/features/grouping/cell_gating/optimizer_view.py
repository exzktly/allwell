"""The ThreshFracOn optimizer, in its own window (Qt).

Twelve controls that answer one occasional question — which cut best separates
two wells — used to sit permanently between the gating controls and the CDFs
they are read against, pushing the plot roughly 700px below the numbers it
explains. It is a tool, not tab state, so it lives in a window the tab opens on
request.

The window is **non-modal**: the point of running the optimizer is to compare
its answer against the distributions on the tab behind it, which a modal dialog
would hide. It is built once, hidden, when the tab builds — so every widget
handle registers at the same moment as before and the refreshers that repopulate
these combos on a data change (``_cell_gating_refresh_optimizer_controls`` and
friends) need no build-state guard.

* :func:`build_optimizer_window` — construct it hidden; returns the window.
* :func:`show_optimizer_window` — raise it, building nothing.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QDialog, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from widgets.flow_layout import FlowLayout, flow_group
from well_viewer.core.ui_helpers import build_section_header
from well_viewer.features.grouping.cell_gating.controller import (
    _cell_gating_autofill_opt_range,
    _cell_gating_on_opt_scope_changed,
    cell_gating_optimize_thresh_frac_on,
)
import theme

# Pixel geometry for this module's controls (ARCHITECTURE §8: named once, never
# written inline at a call site).
_FC_BASELINE_MIN_W = 110
_OPT_TP_MIN_W = 110
_OPT_WELL_A_MIN_W = 90
_OPT_WELL_B_MIN_W = 90
_WINDOW_MIN_W = 640


def build_optimizer_window(ctx, parent: QWidget) -> QDialog:
    """Build the optimizer window, hidden, and register its widget handles."""
    reg = ctx.widgets

    win = QDialog(parent)
    win.setWindowTitle("Optimize ON cutoff (ThreshFracOn)")
    win.setModal(False)
    win.setMinimumWidth(_WINDOW_MIN_W)
    opt_layout = QVBoxLayout(win)

    opt_layout.addWidget(build_section_header(
        win, "Optimize ThreshFracOn between two wells",
        hint="Scans thresholds across a range and applies the one that best "
             "separates the two wells' fraction-on.",
    ))

    opt_row = QWidget(win)
    # FlowLayout + flow_group: each label stays glued to its control, and the row
    # wraps onto extra lines at narrow width instead of clipping controls off-edge.
    opt_row_layout = FlowLayout(opt_row)
    opt_row_layout.setContentsMargins(0, 0, 0, 0)
    opt_channel = reg.register("_cell_gating_opt_channel_combo", QComboBox(opt_row))
    opt_channel.setMinimumWidth(theme.Metrics.combo_min_w)
    opt_row_layout.addWidget(flow_group(opt_row, "Channel:", opt_channel))
    opt_well_a = reg.register("_cell_gating_opt_well_a_combo", QComboBox(opt_row))
    opt_well_a.setMinimumWidth(_OPT_WELL_A_MIN_W)
    opt_row_layout.addWidget(flow_group(opt_row, "Well A:", opt_well_a))
    opt_well_b = reg.register("_cell_gating_opt_well_b_combo", QComboBox(opt_row))
    opt_well_b.setMinimumWidth(_OPT_WELL_B_MIN_W)
    opt_row_layout.addWidget(flow_group(opt_row, "Well B:", opt_well_b))
    opt_tp = reg.register("_cell_gating_opt_timepoint_combo", QComboBox(opt_row))
    opt_tp.setMinimumWidth(_OPT_TP_MIN_W)
    opt_tp.setToolTip(
        "Restrict the fraction-on comparison to one timepoint, or pool all "
        "timepoints. Pooling across timepoints where the wells are both 'off' "
        "early dilutes the difference, so picking the relevant timepoint gives a "
        "sharper threshold."
    )
    opt_row_layout.addWidget(flow_group(opt_row, "Timepoint:", opt_tp))
    opt_min = reg.register("_cell_gating_opt_min_edit", QLineEdit("0.0", opt_row))
    opt_min.setFixedWidth(theme.Metrics.num_field_w_lg)
    opt_row_layout.addWidget(flow_group(opt_row, "Min:", opt_min))
    opt_max = reg.register("_cell_gating_opt_max_edit", QLineEdit("100.0", opt_row))
    opt_max.setFixedWidth(theme.Metrics.num_field_w_lg)
    opt_row_layout.addWidget(flow_group(opt_row, "Max:", opt_max))
    opt_steps = reg.register("_cell_gating_opt_steps_edit", QLineEdit("101", opt_row))
    opt_steps.setFixedWidth(theme.Metrics.short_field_w)
    opt_steps.setToolTip(
        "Number of thresholds scanned across the Min–Max range, evenly "
        "spaced via numpy.linspace. NOT a step size — \"101\" scans 101 "
        "thresholds (every 1 unit across a 0–100 range). Higher values "
        "give a finer scan at the cost of a little compute."
    )
    opt_row_layout.addWidget(flow_group(opt_row, "# Points:", opt_steps))
    opt_btn = QPushButton("Optimize ThreshFracOn", opt_row)
    opt_btn.setProperty("variant", "secondary")
    opt_btn.setToolTip(
        "Scan # Points thresholds between Min and Max for the selected "
        "channel, compute fraction-on in Well A and Well B at each "
        "threshold, then apply the threshold that maximizes their "
        "relative fraction-on gap, |Δ| / max(fa, fb)."
    )
    opt_btn.clicked.connect(lambda _=False: cell_gating_optimize_thresh_frac_on(ctx))
    reg.register("_cell_gating_opt_btn", opt_btn)
    opt_row_layout.addWidget(opt_btn)
    opt_row_layout.addStretch(1)
    opt_layout.addWidget(opt_row)

    # Optional fold-change row: optimise over a fold-change-adjusted fraction-on
    # (the same vs-control / vs-t0 normalisation the plot tabs use), so the
    # threshold that best separates A and B is chosen on the *normalised* metric.
    # These controls are INDEPENDENT of the plotting tabs' fold-change state —
    # they live only here and are read at optimise time, so configuring them
    # never disturbs the Bar / Line plots.
    fc_row = QWidget(win)
    fc_row_layout = FlowLayout(fc_row)
    fc_row_layout.setContentsMargins(0, 0, 0, 0)
    fc_control = reg.register("_cell_gating_opt_fc_control_combo", QComboBox(fc_row))
    fc_control.setMinimumWidth(theme.Metrics.combo_min_w_sm)
    fc_control.setToolTip(
        "Divide (or subtract) each well's fraction-on by a control well / "
        "replicate set's fraction-on at the same scanned threshold. \"—\" "
        "optimises over the raw fraction-on (the original behaviour).\n\n"
        "Note: with the Ratio operator a shared control cancels in the "
        "relative gap, so it only narrows the scan to where the control still "
        "has signal — use Subtract, or a Baseline, to actually reshape the "
        "A-vs-B separation."
    )
    fc_ctrl_op = reg.register("_cell_gating_opt_fc_ctrl_op_combo", QComboBox(fc_row))
    fc_ctrl_op.addItem("Ratio", "ratio")
    fc_ctrl_op.addItem("Subtract", "subtract")
    fc_row_layout.addWidget(flow_group(fc_row, "Fold change —  Control:", fc_control, fc_ctrl_op))
    fc_baseline = reg.register("_cell_gating_opt_fc_baseline_combo", QComboBox(fc_row))
    fc_baseline.setMinimumWidth(_FC_BASELINE_MIN_W)
    fc_baseline.setToolTip(
        "Divide (or subtract) each well's fraction-on by its own fraction-on at "
        "a baseline timepoint. \"t0\" uses each well's earliest timepoint."
    )
    fc_t0_op = reg.register("_cell_gating_opt_fc_t0_op_combo", QComboBox(fc_row))
    fc_t0_op.addItem("Ratio", "ratio")
    fc_t0_op.addItem("Subtract", "subtract")
    fc_row_layout.addWidget(flow_group(fc_row, "Baseline:", fc_baseline, fc_t0_op))
    fc_order = reg.register("_cell_gating_opt_fc_order_combo", QComboBox(fc_row))
    fc_order.addItem("Control first", "ctrl_first")
    fc_order.addItem("Baseline first", "t0_first")
    fc_row_layout.addWidget(flow_group(fc_row, "Order:", fc_order))
    fc_row_layout.addStretch(1)
    opt_layout.addWidget(fc_row)

    # Changing the channel or either well re-derives the available timepoints and
    # auto-fills Min/Max from the data range (so the scan actually spans the
    # values — the hardcoded 0–100 default sits below typical MFI data, which is
    # why a fresh Optimize used to return the range minimum). Changing only the
    # timepoint just re-derives the range.
    for combo in (opt_channel, opt_well_a, opt_well_b):
        combo.currentIndexChanged.connect(
            lambda _idx=0: _cell_gating_on_opt_scope_changed(ctx)
        )
    opt_tp.currentIndexChanged.connect(
        lambda _idx=0: _cell_gating_autofill_opt_range(ctx)
    )

    opt_result_label = reg.register("_cell_gating_opt_result_label", QLabel(
        "Choose two wells and a threshold range, then optimize.", win))
    opt_result_label.setObjectName("Muted")
    opt_result_label.setWordWrap(True)
    opt_layout.addWidget(opt_result_label)
    opt_layout.addStretch(1)

    win.hide()
    return win


def show_optimizer_window(ctx) -> None:
    """Raise the already-built optimizer window. No-op before the tab is built."""
    win = ctx.runtime.cell_gating.optimizer_window
    if win is None:
        return
    win.show()
    win.raise_()
    win.activateWindow()
