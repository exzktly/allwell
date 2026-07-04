"""Scatter Plot: Aggregate tab builder (Qt port) — single scatter in a v2 PlotCard."""

from __future__ import annotations

from well_viewer.core.plot_card_actions import (
    copy_active_card_as_svg, save_active_card_figure,
)

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout,
    QWidget, QWidgetAction,
)

from well_viewer.core.ui_helpers import (
    make_band_controls, make_plot_with_right_dock, make_popover_button,
    make_plot_view_switcher)
from well_viewer.features.scatter.controller import redraw_scatter_agg_orchestrate
from well_viewer.features.scatter.controller import on_scatter_agg_axis_change
from well_viewer.features.scatter.controller import update_tp_selection_display
from well_viewer.features.export.service import export_scatter_agg_data


def build_scatter_agg_tab(ctx, parent: QWidget) -> None:
    from widgets.plot_card import PlotCard

    # A8 burn-down: widget handles go through the WidgetRegistry (``ctx.widgets``),
    # which also stamps ``app._<name>`` so legacy ``app._scatter_agg_*`` readers
    # keep resolving. Non-widget state (tp selections) and behaviour calls still
    # reach the app — a later layer.
    reg = ctx.widgets

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)

    plot_area, layout, export_dock = make_plot_with_right_dock(parent)
    reg.register("_scatter_agg_export_dock", export_dock)
    parent = plot_area

    ctrl = QWidget(parent)
    ctrl.setObjectName("TabCtrl")
    cl = QHBoxLayout(ctrl)
    cl.setContentsMargins(10, 6, 10, 6)

    # Per-axis channel + property selectors mirror the cells scatter tab.
    # The previous monolithic stat-string combo ("Mean Fluorescence GFP")
    # is replaced; the canonical (channel, metric) pair drives column
    # resolution and aggregation downstream. The legacy hidden
    # ``_scatter_agg_stat_x/y_cb`` combos stay as compatibility shims —
    # ``_update_scatter_menus`` still populates them so older code paths
    # (export_service, plot_orchestrator) keep working.
    from well_viewer.data.metric_labels import METRIC_ORDER as _BASE_METRIC_ORDER
    AGG_METRIC_ORDER = list(_BASE_METRIC_ORDER) + ["Fraction above threshold"]

    cl.addWidget(QLabel("X-axis:", ctrl))
    cl.addWidget(QLabel("Channel:", ctrl))
    agg_ch_x_cb = reg.register("_scatter_agg_ch_x_cb", QComboBox(ctrl))
    agg_ch_x_cb.addItems(["GFP"])
    agg_ch_x_cb.currentIndexChanged.connect(lambda _i: on_scatter_agg_axis_change(ctx, "x"))
    cl.addWidget(agg_ch_x_cb)

    cl.addWidget(QLabel("Property:", ctrl))
    agg_metric_x_cb = reg.register("_scatter_agg_metric_x_cb", QComboBox(ctrl))
    agg_metric_x_cb.addItems(AGG_METRIC_ORDER)
    agg_metric_x_cb.currentIndexChanged.connect(lambda _i: redraw_scatter_agg_orchestrate(ctx))
    cl.addWidget(agg_metric_x_cb)

    cl.addWidget(QLabel("Y-axis:", ctrl))
    cl.addWidget(QLabel("Channel:", ctrl))
    agg_ch_y_cb = reg.register("_scatter_agg_ch_y_cb", QComboBox(ctrl))
    agg_ch_y_cb.addItems(["GFP"])
    agg_ch_y_cb.currentIndexChanged.connect(lambda _i: on_scatter_agg_axis_change(ctx, "y"))
    cl.addWidget(agg_ch_y_cb)

    cl.addWidget(QLabel("Property:", ctrl))
    agg_metric_y_cb = reg.register("_scatter_agg_metric_y_cb", QComboBox(ctrl))
    agg_metric_y_cb.addItems(AGG_METRIC_ORDER)
    agg_metric_y_cb.currentIndexChanged.connect(lambda _i: redraw_scatter_agg_orchestrate(ctx))
    cl.addWidget(agg_metric_y_cb)

    # Legacy stat combos — hidden, kept in sync for back-compat. Export
    # / save-figure code still reads from these by name (+ ``_var`` aliases).
    stat_x_cb = reg.register("_scatter_agg_stat_x_cb", QComboBox(ctrl))
    stat_x_cb.hide()
    reg.register("_scatter_agg_stat_x_var", stat_x_cb)
    stat_y_cb = reg.register("_scatter_agg_stat_y_cb", QComboBox(ctrl))
    stat_y_cb.hide()
    reg.register("_scatter_agg_stat_y_var", stat_y_cb)

    cl.addWidget(QLabel("Timepoints:", ctrl))
    agg_tp_button = reg.register("_scatter_agg_tp_button", QPushButton("Select Timepoints", ctrl))
    agg_tp_button.setProperty("variant", "secondary")
    agg_tp_button.clicked.connect(lambda _=False: _open_timepoint_selector(ctx))
    cl.addWidget(agg_tp_button)
    agg_tp_label = reg.register("_scatter_agg_tp_label", QLabel("(0 selected)", ctrl))
    agg_tp_label.setObjectName("Muted")
    cl.addWidget(agg_tp_label)
    ctx.runtime.scatter.agg_tp_selections = {}

    # Error band (SEM/SD) for the aggregate's mean±spread points collapses into
    # a "Display ▾" popover (matches line/bar), instead of a card-controls row.
    _disp_btn, _disp_panel, _disp_lay = make_popover_button("Display ▾", ctrl)
    _disp_btn.setToolTip("Error band (SEM/SD) for the aggregate points")
    _disp_lay.addWidget(make_band_controls(ctx, _disp_panel, with_fov=False))
    cl.addWidget(_disp_btn)

    cl.addStretch(1)

    from widgets.icon_button import IconButton as _IconButton
    _export_btn = _IconButton("download", ctrl, tooltip="Export CSV")
    _export_btn.clicked.connect(lambda _=False: export_scatter_agg_data(ctx))
    cl.addWidget(_export_btn)
    _copy_btn = _IconButton("copy", ctrl, tooltip="Copy SVG")
    _copy_btn.clicked.connect(lambda _=False: copy_active_card_as_svg(ctx))
    cl.addWidget(_copy_btn)
    _save_btn = _IconButton("save", ctrl, tooltip="Save figure")
    _save_btn.clicked.connect(lambda _=False: save_active_card_figure(ctx))
    cl.addWidget(_save_btn)
    style_btn = _IconButton(
        "sliders-horizontal", ctrl,
        tooltip="Show / hide the figure properties panel",
    )
    style_btn.clicked.connect(lambda _=False: ctx.open_export_style_panel("scatter_agg"))
    cl.addWidget(style_btn)
    layout.addWidget(ctrl)

    card = PlotCard(parent, figsize=(8, 6), constrained=False)
    from well_viewer.features.saved_plots.controller import wire_save_to_gallery
    wire_save_to_gallery(ctx, card, "Scatter Plot")
    _sw = make_plot_view_switcher(ctx, 'Scatter Plot')
    if _sw is not None:
        card.setLeftHeaderWidget(_sw)
    # Hide the per-card header — the ctxbar above already carries the same controls.
    card.setHeaderVisible(False)
    card.setFigureTitle("")
    reg.register("_scatter_agg_card", card)
    fig = reg.register("_scatter_agg_fig", card.figure)
    reg.register("_ax_scatter_agg", fig.add_subplot(1, 1, 1))
    fig.subplots_adjust(hspace=0.3, top=0.95, bottom=0.12, left=0.12, right=0.97)
    reg.register("_scatter_agg_canvas", card.canvas)
    # Error band now lives in the "Display ▾" popover on the control row above.
    card.plotThemeChanged.connect(lambda _m: redraw_scatter_agg_orchestrate(ctx))
    card.setStatsChipVisible(False)
    from well_viewer.core.ui_helpers import wrap_with_empty_state
    layout.addWidget(wrap_with_empty_state(
        ctx.window, card, icon="scatter-chart",
    ), 1)


def _open_timepoint_selector(ctx) -> None:
    """Pop up a QMenu of checkboxes below the Timepoints button."""
    agg_tp_selections = ctx.runtime.scatter.agg_tp_selections
    btn = ctx.widgets.get_or_none("_scatter_agg_tp_button")
    menu = QMenu(btn)

    checkboxes: dict[str, QCheckBox] = {}
    for tp_str in sorted(agg_tp_selections.keys(), key=float):
        cb = QCheckBox(tp_str, menu)
        cb.setChecked(bool(agg_tp_selections[tp_str]))
        checkboxes[tp_str] = cb

        def _make_handler(tp=tp_str, checkbox=cb):
            def on_toggled(checked: bool) -> None:
                agg_tp_selections[tp] = bool(checked)
                update_tp_selection_display(ctx)
                redraw_scatter_agg_orchestrate(ctx)
            return on_toggled

        cb.toggled.connect(_make_handler())
        wa = QWidgetAction(menu)
        wa.setDefaultWidget(cb)
        menu.addAction(wa)

    menu.addSeparator()

    def _set_all(value: bool) -> None:
        for tp_str, cb in checkboxes.items():
            cb.blockSignals(True)
            try:
                cb.setChecked(value)
            finally:
                cb.blockSignals(False)
            agg_tp_selections[tp_str] = value
        update_tp_selection_display(ctx)
        redraw_scatter_agg_orchestrate(ctx)

    all_action = QAction("All", menu)
    all_action.triggered.connect(lambda _=False: _set_all(True))
    menu.addAction(all_action)
    none_action = QAction("None", menu)
    none_action.triggered.connect(lambda _=False: _set_all(False))
    menu.addAction(none_action)

    menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
