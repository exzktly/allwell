"""Bar Plots tab builder (Qt port) — the 3-stacked-subplot figure in a v2 PlotCard."""

from __future__ import annotations

from well_viewer.core.channel_state_controller import (
    on_metric_selected, on_plot_channel_selected,
)

from well_viewer.core.plot_card_actions import (
    copy_active_card_as_svg, save_active_card_figure,
)

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from well_viewer.core.ui_helpers import (
    install_canvas_wheel_scroll, make_band_controls,
    make_plot_with_right_dock, make_plot_view_switcher)
from well_viewer.features.barplot.controller import (
    bar_reset_order,
    on_bar_drag_motion,
    on_bar_drag_press,
    on_bar_drag_release,
    open_bar_timepoint_selector,
    toggle_bar_group_mode,
    toggle_bar_pool_replicates,
    toggle_swarm,
    toggle_violin,
)
from well_viewer.features.barplot.renderer import redraw_bars
from functools import partial
from well_viewer.features.export.service import export_bar_plot_data
from ui.theme import ACCENT

# The Bar Plots figure: three stacked subplots (mean / fraction / n).
_FIG_W, _FIG_H, _DPI = 6.2, 11.0, 100


from widgets.flow_layout import FlowLayout

def build_bar_plots_tab(ctx, parent: QWidget) -> None:
    from widgets.plot_card import PlotCard

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)

    # A8 burn-down: Qt widget handles go through the WidgetRegistry (``ctx.widgets``),
    # which stamps ``app._<name>`` so legacy ``app._bar_*`` / ``app._chan_cb_bar``
    # readers in the barplot controller keep resolving. Non-widget state (swarm /
    # violin flags, drag state) and behaviour calls still reach the app.
    reg = ctx.widgets

    plot_area, outer_layout, export_dock = make_plot_with_right_dock(parent)
    reg.register("_bar_export_dock", export_dock)
    parent = plot_area

    bar_right = QWidget(parent)
    right_l = QVBoxLayout(bar_right)
    right_l.setContentsMargins(0, 0, 0, 0)
    outer_layout.addWidget(bar_right, 1)

    # ── controls bar ─────────────────────────────────────────────────────────
    bar_ctrl = QWidget(bar_right)
    bar_ctrl.setObjectName("TabCtrl")
    cl = FlowLayout(bar_ctrl)
    cl.setContentsMargins(10, 6, 10, 6)

    # Channel combo hidden — the global ctxbar combo is the only visible
    # channel control. See line_graphs_tab_view.py for rationale.
    chan_cb = reg.register("_chan_cb_bar", QComboBox())
    chan_cb.addItems(["GFP"])
    chan_cb.currentIndexChanged.connect(
        lambda _i, _src=chan_cb: on_plot_channel_selected(ctx, _src)
    )
    chan_cb.hide()

    # Hidden per-tab metric combo — back-compat shim only. The global
    # plotting ctxbar's ``_plotting_metric_cb`` is the single visible
    # source of truth for the active intensity property.
    from well_viewer.data.metric_labels import METRIC_ORDER as _METRIC_ORDER
    metric_frame = reg.register("_metric_selector_frame_bar", QWidget(bar_ctrl))
    mf_l = QHBoxLayout(metric_frame)
    mf_l.setContentsMargins(0, 0, 0, 0)
    mf_l.addWidget(QLabel("Property:", metric_frame))
    metric_cb = reg.register("_metric_cb_bar", QComboBox(metric_frame))
    metric_cb.addItems(_METRIC_ORDER)
    metric_cb.currentIndexChanged.connect(lambda _i: on_metric_selected(ctx))
    mf_l.addWidget(metric_cb)
    cl.addWidget(metric_frame)
    metric_frame.hide()

    # Fold-change normalization collapses into a popover button (C1) so it no
    # longer needs its own row; the stretch pushes the action buttons right.
    from well_viewer.domain.fold_change_controls import install_fold_change_controls
    from well_viewer.core.ui_helpers import make_popover_button
    _fc_btn, _fc_panel, _fc_lay = make_popover_button("Fold change ▾", bar_ctrl)
    _fc_btn.setToolTip("Fold-change normalization (vs a control well/set or a baseline timepoint)")
    install_fold_change_controls(ctx, _fc_panel, _fc_lay, scope="bar")
    cl.addWidget(_fc_btn)

    # Multi-timepoint picker — mirrors the scatter plot's
    # ``_open_timepoint_selector`` popup so the bar plot can show bars/
    # violins/beeswarms over multiple timepoints simultaneously. Shares the
    # main control row with the other buttons (previously its own second row);
    # the FlowLayout wraps them onto a new line only when the pane is too narrow.
    cl.addWidget(QLabel("Timepoints:", bar_ctrl))
    tp_button = reg.register("_bar_tp_button", QPushButton("Select Timepoints", bar_ctrl))
    tp_button.setProperty("variant", "secondary")
    tp_button.clicked.connect(
        lambda _=False: open_bar_timepoint_selector(ctx)
    )
    cl.addWidget(tp_button)
    tp_label = reg.register("_bar_tp_label", QLabel("(0 selected)", bar_ctrl))
    tp_label.setObjectName("Muted")
    cl.addWidget(tp_label)
    # Hidden legacy single-tp combo — kept so export / save-figure code that
    # reads ``_bar_tp_cb.currentText()`` (export/service.py, view_state.py,
    # batch_export/bar_panel.py, plot_orchestrator.py) sees the first
    # selected tp string. Same shim the scatter tab uses for
    # ``_scatter_tp_cb``. The authoritative multi-select state lives on
    # ``BarPlotState.tp_selections`` (well_viewer/features/barplot/state.py).
    bar_tp_cb = reg.register("_bar_tp_cb", QComboBox(bar_ctrl))
    bar_tp_cb.addItems(["—"])
    bar_tp_cb.hide()
    reg.register("_bar_tp_var", bar_tp_cb)   # legacy alias → same widget

    # C1: the display toggles + error band collapse into a "Display ▾" popover so
    # the bar control area stays a single compact row. ``_disp_lay`` is the popover
    # body; the widgets are added there instead of inline (addWidget reparents them).
    from well_viewer.core.ui_helpers import make_popover_button as _make_popover_button
    _disp_btn, _disp_panel, _disp_lay = _make_popover_button("Display ▾", bar_ctrl)
    _disp_btn.setToolTip("Bar/point display options: grouping, pooling, beeswarm, violin, error band")
    cl.addWidget(_disp_btn)

    # Layout toggle: cluster bars by well or by timepoint.
    group_mode_btn = reg.register("_bar_group_mode_btn", QPushButton("Grouping: Well", bar_ctrl))
    group_mode_btn.setProperty("variant", "toggle")
    group_mode_btn.setCheckable(True)
    group_mode_btn.setToolTip(
        "Cluster the bars by well/group vs by timepoint.")
    group_mode_btn.clicked.connect(
        lambda _=False: toggle_bar_group_mode(ctx)
    )
    _disp_lay.addWidget(group_mode_btn)

    # Replicate-pool toggle: collapse rset wells into one column or
    # expand them. Hidden when no replicate sets are active.
    pool_reps_btn = reg.register("_bar_pool_reps_btn", QPushButton("Pooled: Off", bar_ctrl))
    pool_reps_btn.setProperty("variant", "toggle")
    pool_reps_btn.setCheckable(True)
    pool_reps_btn.setToolTip(
        "Collapse each replicate set's wells into one pooled column "
        "(off = one column per well).")
    pool_reps_btn.clicked.connect(
        lambda _=False: toggle_bar_pool_replicates(ctx)
    )
    # Always present in the Display ▾ popover; enable-gated on active replicate
    # sets by refresh_bar_layout_btn_state. Toggling a child's visibility inside
    # a closed QMenu widget-action does not surface reliably, so we keep it
    # visible and gate usability with setEnabled instead.
    _disp_lay.addWidget(pool_reps_btn)

    swarm_btn = reg.register("_swarm_btn", QPushButton("Beeswarm: Off", bar_ctrl))
    swarm_btn.setProperty("variant", "toggle")
    swarm_btn.setCheckable(True)
    swarm_btn.setToolTip(
        "Overlay the individual per-cell points on each bar.")
    swarm_btn.clicked.connect(lambda _=False: toggle_swarm(ctx))
    _disp_lay.addWidget(swarm_btn)
    # swarm/violin state lives on the typed BarPlotState slice (defaults False).

    violin_btn = reg.register("_violin_btn", QPushButton("Violin: Off", bar_ctrl))
    violin_btn.setProperty("variant", "toggle")
    violin_btn.setCheckable(True)
    violin_btn.setToolTip(
        "Draw a violin (kernel-density) shape for each group. "
        "Enables the Smooth control.")
    violin_btn.clicked.connect(lambda _=False: toggle_violin(ctx))
    _disp_lay.addWidget(violin_btn)

    _disp_lay.addWidget(QLabel("Smooth:", bar_ctrl))
    from widgets.styled_slider import StyledSlider as _StyledSlider
    violin_slider = reg.register("_violin_slider", _StyledSlider(Qt.Horizontal, bar_ctrl))
    violin_slider.setRange(5, 200)
    violin_slider.setValue(100)
    violin_slider.setFixedWidth(80)
    violin_slider.setToolTip(
        "Violin kernel bandwidth — higher is smoother. "
        "Active only when Violin is on.")
    violin_slider.valueChanged.connect(
        lambda _v: (redraw_bars(ctx) if ctx.state.bar.violin else None)
    )
    violin_slider.setEnabled(False)
    _disp_lay.addWidget(violin_slider)

    # Significance brackets (N7): draw the Statistics tab's pairwise results onto
    # the Mean panel. Toggle stages PlotState.show_significance and redraws.
    sig_btn = reg.register("_bar_significance_btn", QPushButton("Significance: Off", bar_ctrl))
    sig_btn.setProperty("variant", "toggle")
    sig_btn.setCheckable(True)
    sig_btn.setChecked(bool(ctx.state.plot.show_significance))
    sig_btn.setToolTip(
        "Draw significance brackets between group bars from the Statistics tab's "
        "pairwise tests. Run a test in Statistics first; brackets appear for the "
        "compared groups that match the current bars (grouped / pooled mode).")

    def _toggle_bar_significance(_=False):
        _sig = ctx.widgets.get_or_none("_bar_significance_btn")
        on = bool(_sig.isChecked())
        ctx.state.plot.show_significance = on
        _sig.setText(f"Significance: {'On' if on else 'Off'}")
        redraw_bars(ctx)
    sig_btn.clicked.connect(_toggle_bar_significance)
    _disp_lay.addWidget(sig_btn)

    # Error band (SEM/SD + Spread) joins the same Display popover (C1) instead
    # of a separate card-controls row.
    _band_sep = QFrame(_disp_panel)
    _band_sep.setFrameShape(QFrame.HLine)
    _band_sep.setObjectName("HRule")
    _disp_lay.addWidget(_band_sep)
    _disp_lay.addWidget(make_band_controls(ctx, _disp_panel, with_fov=True))

    # ── action buttons, pushed to the right edge of the single control row ────
    cl.addStretch(1)

    reset_order_btn = reg.register("_bar_reset_order_btn", QPushButton("Reset Order", bar_ctrl))
    reset_order_btn.setProperty("variant", "toggle_muted")
    reset_order_btn.clicked.connect(lambda _=False: bar_reset_order(ctx))
    cl.addWidget(reset_order_btn)

    # IconButton — matches Sample Definitions top toolbar style; see
    # equivalent block in features/lineplot/tab_view.py for rationale.
    from widgets.icon_button import IconButton as _IconButton
    _export_btn = _IconButton("download", bar_ctrl, tooltip="Export CSV")
    _export_btn.clicked.connect(lambda _=False: export_bar_plot_data(ctx))
    cl.addWidget(_export_btn)
    _copy_btn = _IconButton("copy", bar_ctrl, tooltip="Copy SVG")
    _copy_btn.clicked.connect(lambda _=False: copy_active_card_as_svg(ctx))
    cl.addWidget(_copy_btn)
    _save_btn = _IconButton("save", bar_ctrl, tooltip="Save figure")
    _save_btn.clicked.connect(lambda _=False: save_active_card_figure(ctx))
    cl.addWidget(_save_btn)
    # Pin the figure-properties button to the right edge of the plot area (where
    # the export-style dock slides out) — the FlowLayout control row can't
    # right-align it, so wrap the row.
    from well_viewer.core.ui_helpers import wrap_ctrl_row_with_properties
    right_l.addWidget(wrap_ctrl_row_with_properties(
        bar_ctrl, lambda: ctx.figures.open_style_panel("bar")))

    # ── the figure, in a v2 PlotCard (card chrome + MplToolbar) ──────────────
    card = PlotCard(bar_right, figsize=(_FIG_W, _FIG_H), constrained=False)
    from well_viewer.core.ui_helpers import apply_plot_card_decorators
    apply_plot_card_decorators(ctx, card, "Bar Plots")
    _sw = make_plot_view_switcher(ctx, 'Bar Plots')
    if _sw is not None:
        card.setLeftHeaderWidget(_sw)
    # Hide the per-card header — the ctxbar above already carries the same controls.
    card.setHeaderVisible(False)
    card.setFigureTitle("")
    reg.register("_bar_card", card)
    fig = reg.register("_bar_fig", card.figure)
    reg.register("_ax_bar_mean", fig.add_subplot(3, 1, 1))
    reg.register("_ax_bar_frac", fig.add_subplot(3, 1, 2))
    reg.register("_ax_bar_n", fig.add_subplot(3, 1, 3))
    fig.subplots_adjust(
        hspace=0.65, top=0.96, bottom=0.10, left=0.15, right=0.97,
    )
    canvas = reg.register("_bar_canvas", card.canvas)
    canvas.setMinimumHeight(int(_FIG_H * _DPI))
    card.setMinimumHeight(int(_FIG_H * _DPI) + 110)

    # Error band now lives in the "Display ▾" popover above (C1); no card row.
    card.plotThemeChanged.connect(lambda _m: redraw_bars(ctx))
    # Per-panel copy (hover a subplot → small copy icon) echoes to the status bar.
    card.panelCopied.connect(ctx.notify.set_status)
    card.setStatsChipVisible(False)

    scroll_canvas = reg.register("_bar_scroll_canvas", QScrollArea(bar_right))
    scroll_canvas.setWidgetResizable(True)
    scroll_canvas.setFrameShape(QFrame.NoFrame)
    scroll_canvas.setWidget(card)
    install_canvas_wheel_scroll(canvas, scroll_canvas)
    from well_viewer.core.ui_helpers import wrap_with_empty_state
    right_l.addWidget(wrap_with_empty_state(
        ctx.window, scroll_canvas, icon="bar-chart-horizontal",
    ), 1)

    # Drag-to-reorder bars
    ctx.runtime.barplot.drag_state = {"active": False, "src_idx": -1, "cur_idx": -1}
    canvas.mpl_connect("button_press_event", partial(on_bar_drag_press, ctx))
    canvas.mpl_connect(
        "motion_notify_event",
        partial(on_bar_drag_motion, ctx, accent_color=ACCENT),
    )
    canvas.mpl_connect("button_release_event", partial(on_bar_drag_release, ctx))
