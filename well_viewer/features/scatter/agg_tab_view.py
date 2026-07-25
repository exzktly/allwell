"""Scatter Plot: Aggregate tab builder (Qt port) — single scatter in a v2 PlotCard."""

from __future__ import annotations

from well_viewer.core.plot_card_actions import (
    copy_active_card_as_svg, save_active_card_figure,
)

from PySide6.QtWidgets import (
    QComboBox, QLabel, QPushButton, QVBoxLayout,
    QWidget,
)

from well_viewer.core.ui_helpers import (
    add_menu_action, make_action_menu_button, make_band_controls,
    make_plot_with_right_dock, make_popover_button, make_plot_view_switcher)
from widgets.flow_layout import FlowLayout
from well_viewer.features.scatter.controller import redraw_scatter_agg_orchestrate
from well_viewer.features.scatter.tp_selector_view import open_timepoint_selector
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
    # FlowLayout so the control row wraps rather than clipping when Split View
    # narrows the pane (see cells_tab_view for the same treatment).
    cl = FlowLayout(ctrl, h_spacing=6, v_spacing=4)
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
    # Stash the aggregate tp-selection summary refresher so view_state restore can
    # repaint the "(X selected)" label without importing this feature module.
    ctx.runtime.scatter.agg_tp_display_refresh = update_tp_selection_display

    # Error band (SEM/SD) for the aggregate's mean±spread points collapses into
    # a "Display ▾" popover (matches line/bar), instead of a card-controls row.
    _disp_btn, _disp_panel, _disp_lay = make_popover_button("Display ▾", ctrl)
    _disp_btn.setToolTip("Error band (SEM/SD) for the aggregate points")
    _disp_lay.addWidget(make_band_controls(ctx, _disp_panel, with_fov=False))
    cl.addWidget(_disp_btn)

    # Export / copy / save collapse into one "Export ▾" menu (mirrors the
    # per-cell scatter tab).
    _export_btn, _export_menu = make_action_menu_button(
        "Export ▾", ctrl, tooltip="Export, copy, and save")
    add_menu_action(_export_menu, "Export CSV…", lambda: export_scatter_agg_data(ctx))
    add_menu_action(_export_menu, "Copy SVG", lambda: copy_active_card_as_svg(ctx))
    add_menu_action(_export_menu, "Save figure…", lambda: save_active_card_figure(ctx))
    cl.addWidget(_export_btn)
    # Figure-properties button, pinned to the right edge of the plot area (where
    # the export-style dock slides out) — matching every other plot tab. The
    # FlowLayout control row can't right-align it, so wrap the row.
    from well_viewer.core.ui_helpers import wrap_ctrl_row_with_properties
    layout.addWidget(wrap_ctrl_row_with_properties(
        ctrl, lambda: ctx.figures.open_style_panel("scatter_agg")))

    card = PlotCard(parent, figsize=(8, 6), constrained=False)
    from well_viewer.core.ui_helpers import apply_plot_card_decorators
    apply_plot_card_decorators(ctx, card, "Scatter Plot")
    _sw = make_plot_view_switcher(ctx, 'Scatter Plot')
    if _sw is not None:
        card.setLeftHeaderWidget(_sw)
    # Hide the per-card header — the ctxbar above already carries the same controls.
    card.setHeaderVisible(False)
    card.setFigureTitle("")
    reg.register("_scatter_agg_card", card)
    fig = reg.register("_scatter_agg_fig", card.figure)
    reg.register("_ax_scatter_agg", fig.add_subplot(1, 1, 1))
    # top/bottom leave room for the (single-line) axes title and the x-axis
    # label — at top=0.95 / bottom=0.12 the large title clipped at the figure's
    # top edge and the x-label clipped at the bottom.
    fig.subplots_adjust(hspace=0.3, top=0.90, bottom=0.15, left=0.12, right=0.97)
    reg.register("_scatter_agg_canvas", card.canvas)
    # Error band now lives in the "Display ▾" popover on the control row above.
    card.plotThemeChanged.connect(lambda _m: redraw_scatter_agg_orchestrate(ctx))
    card.setStatsChipVisible(False)
    from well_viewer.core.ui_helpers import wrap_with_empty_state
    layout.addWidget(wrap_with_empty_state(
        ctx.window, card, icon="scatter-chart",
    ), 1)


def _open_timepoint_selector(ctx) -> None:
    """Pop up the aggregate scatter's multi-timepoint picker."""
    open_timepoint_selector(
        ctx,
        selections=ctx.runtime.scatter.agg_tp_selections,
        button_name="_scatter_agg_tp_button",
        refresh_display=update_tp_selection_display,
        redraw=redraw_scatter_agg_orchestrate,
    )
