"""Bar Plots tab builder (Qt port) — the 3-stacked-subplot figure in a v2 PlotCard."""

from __future__ import annotations

from well_viewer.core.channel_state_controller import (
    on_metric_selected, on_plot_channel_selected,
)


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from well_viewer.core.ui_helpers import (
    build_hline_separator, install_canvas_wheel_scroll,
    build_plot_save_menu, make_band_controls, make_plot_with_right_dock,
    make_properties_button, plotting_band_slot)
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
from well_viewer.features.export.service import (
    export_bar_plot_data, export_cell_data_from)
from well_viewer.features.barplot.cell_scope_compute import cell_scope
import theme

# The Bar Plots figure: three stacked subplots (mean / fraction / n).
_FIG_W, _FIG_H, _DPI = 6.2, 11.0, 100



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
    bar_ctrl = plotting_band_slot(ctx, "Bar Plots")

    # Channel combo hidden — Plotting's band combo is the only visible
    # channel control. See line_graphs_tab_view.py for rationale.
    chan_cb = reg.register("_chan_cb_bar", QComboBox())
    chan_cb.addItems(["GFP"])
    chan_cb.currentIndexChanged.connect(
        lambda _i, _src=chan_cb: on_plot_channel_selected(ctx, _src)
    )
    chan_cb.hide()

    # Hidden per-tab metric combo — back-compat shim only. The global
    # Plotting band's ``_plotting_metric_cb`` is the single visible
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
    metric_frame.hide()

    # Fold-change normalization collapses into a popover button (C1) so it no
    # longer needs its own row; the stretch pushes the action buttons right.
    from well_viewer.core.fold_change_controls_view import install_fold_change_controls
    from well_viewer.core.ui_helpers import make_popover_button
    _fc_btn, _fc_panel, _fc_lay = make_popover_button("Fold change ▾", bar_ctrl)
    _fc_btn.setToolTip("Fold-change normalization (vs a control well/set or a baseline timepoint)")
    install_fold_change_controls(ctx, _fc_panel, _fc_lay, scope="bar")
    bar_ctrl.add_action(_fc_btn)

    # Multi-timepoint picker — mirrors the scatter plot's
    # ``_open_timepoint_selector`` popup so the bar plot can show bars/
    # violins/beeswarms over multiple timepoints simultaneously. Shares the
    # band's scope zone with the other selectors; the flow wraps them onto a new
    # line only when the pane is too narrow.
    # Self-describing — the button's own text carries the "Timepoints: …
    # selected" readout (see ``update_bar_tp_selection_display``), so it needs
    # no separate caption or count label beside it.
    tp_button = reg.register("_bar_tp_button", QPushButton("Timepoints: 0 selected"))
    tp_button.setProperty("variant", "secondary")
    tp_button.clicked.connect(
        lambda _=False: open_bar_timepoint_selector(ctx)
    )
    bar_ctrl.add_scope("", tp_button)
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
    violin_slider.setFixedWidth(theme.Metrics.num_field_w_lg)
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
    # The caption is seeded from the same state as the check, not left at the
    # constructor's "Off". A tab body builds lazily, so it is routinely built
    # after a restore has already turned the brackets on, and a button reading
    # "Off" over a figure carrying brackets says the opposite of what is true.
    sig_btn.setText(
        f"Significance: {'On' if ctx.state.plot.show_significance else 'Off'}")
    sig_btn.setToolTip(
        "Draw significance brackets between group bars from the Statistics tab's "
        "pairwise tests. Run a test in Statistics first; brackets appear for the "
        "compared groups that match the current bars (grouped / pooled mode).")

    # What each bracket says. Beside the toggle that draws them rather than in
    # the Properties sidebar with the value labels: this is the one surface in
    # the app that draws a bracket, and the question "stars or the number?"
    # only arises once the brackets are on — which is the control directly
    # above it.
    from widgets.labeled_combo import LabeledComboBox as _LabeledComboBox
    from well_viewer.domain.significance import (
        SIGNIFICANCE_LABEL_MODE_NAMES, SIGNIFICANCE_LABEL_MODES,
    )
    sig_label_cb = reg.register(
        "_bar_significance_label_cb", _LabeledComboBox("Label", bar_ctrl))
    for _mode in SIGNIFICANCE_LABEL_MODES:
        sig_label_cb.addItem(SIGNIFICANCE_LABEL_MODE_NAMES[_mode], _mode)
    _cur = str(ctx.state.plot.significance_label_mode or "stars")
    sig_label_cb.setCurrentIndex(
        max(0, sig_label_cb.findData(_cur)))
    sig_label_cb.setToolTip(
        "What the text over each significance bracket says — the asterisk "
        "ladder, the p-value itself, or both. A p-value that the Statistics "
        "tab adjusted for multiple comparisons is printed as 'p_BH', the same "
        "name that tab's report gives it.")

    def _on_significance_label_mode(_i=0):
        _cb = ctx.widgets.get_or_none("_bar_significance_label_cb")
        ctx.state.plot.significance_label_mode = str(_cb.currentData() or "stars")
        # Nothing to redraw when no bracket is on the figure — the mode only
        # decides what a bracket says, and the toggle below re-reads it.
        if ctx.state.plot.show_significance:
            redraw_bars(ctx)
    sig_label_cb.currentIndexChanged.connect(_on_significance_label_mode)
    # Usable only while the brackets are drawn, matching how the Smooth slider
    # above follows Violin: a live control that changes nothing on screen reads
    # as broken.
    sig_label_cb.setEnabled(bool(ctx.state.plot.show_significance))

    def _toggle_bar_significance(_=False):
        _sig = ctx.widgets.get_or_none("_bar_significance_btn")
        on = bool(_sig.isChecked())
        ctx.state.plot.show_significance = on
        _sig.setText(f"Significance: {'On' if on else 'Off'}")
        _lbl = ctx.widgets.get_or_none("_bar_significance_label_cb")
        if _lbl is not None:
            _lbl.setEnabled(on)
        redraw_bars(ctx)
    sig_btn.clicked.connect(_toggle_bar_significance)
    _disp_lay.addWidget(sig_btn)
    _disp_lay.addWidget(sig_label_cb)

    # Error band (SEM/SD + Spread) joins the same Display popover (C1) instead
    # of a separate card-controls row.
    _disp_lay.addWidget(build_hline_separator(_disp_panel))
    _disp_lay.addWidget(make_band_controls(ctx, _disp_panel, with_fov=True))

    # ── the band's action zone, flush right ─────────────────────────────────
    bar_ctrl.add_action(_disp_btn)

    reset_order_btn = reg.register("_bar_reset_order_btn", QPushButton("Reset Order", bar_ctrl))
    reset_order_btn.setProperty("variant", "toggle_muted")
    reset_order_btn.clicked.connect(lambda _=False: bar_reset_order(ctx))
    bar_ctrl.add_action(reset_order_btn)

    export_btn, export_menu, _export_data_act = build_plot_save_menu(
        ctx, bar_ctrl,
        card_key="_bar_card",
        data_label="Save plot data…",
        data_cb=lambda: export_bar_plot_data(ctx),
        cells_cb=lambda: export_cell_data_from(
            ctx, cell_scope),
    )
    bar_ctrl.add_action(export_btn)
    bar_ctrl.add_action(make_properties_button(ctx, bar_ctrl, "bar"))

    # ── the figure, in a v2 PlotCard (card chrome + MplToolbar) ──────────────
    card = PlotCard(bar_right, figsize=(_FIG_W, _FIG_H), constrained=False,
                    header=False)
    from well_viewer.core.ui_helpers import apply_plot_card_decorators
    apply_plot_card_decorators(ctx, card, "Bar Plots")
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
    # The canvas' pin plus the card's own chrome, measured rather than a
    # literal: this used to add a hardcoded 110 counting "header / controls /
    # toolbar rows", and the card no longer builds a header.
    card.setMinimumHeight(int(_FIG_H * _DPI) + card.chromeHeight())

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
    # Gated on the plate selection as well as the dataset: without a reason this
    # page painted the same two-line message onto each of its three panels. The
    # hint names them, because those panel titles are the only thing on screen
    # that says what the page will produce.
    from well_viewer.core.ui_helpers import no_selection_reason, wrap_with_empty_state
    right_l.addWidget(wrap_with_empty_state(
        ctx.window, scroll_canvas, icon="bar-chart-horizontal",
        reason=no_selection_reason(
            hint="Three panels: mean intensity, fraction above threshold, and N. "
                 "Click wells on the plate at the left.",
        ),
    ), 1)

    # Drag-to-reorder bars
    ctx.runtime.barplot.drag_state = {"active": False, "src_idx": -1, "cur_idx": -1}
    canvas.mpl_connect("button_press_event", partial(on_bar_drag_press, ctx))
    canvas.mpl_connect(
        "motion_notify_event",
        partial(on_bar_drag_motion, ctx, accent_color=theme.Colors.accent),
    )
    canvas.mpl_connect("button_release_event", partial(on_bar_drag_release, ctx))
