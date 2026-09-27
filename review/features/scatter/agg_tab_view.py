"""Scatter Plot: Aggregate tab builder (Qt port) — single scatter in a v2 PlotCard."""

from __future__ import annotations


from PySide6.QtWidgets import (
    QComboBox, QPushButton, QVBoxLayout,
    QWidget,
)

from review.core.ui_helpers import (
    make_band_controls,
    build_plot_save_menu, make_plot_with_right_dock, make_popover_button,
    make_properties_button, plotting_band_slot)
from review.features.scatter.controller import redraw_scatter_agg_orchestrate
from review.features.scatter.tp_selector_view import open_timepoint_selector
from review.features.scatter.controller import on_scatter_agg_axis_change
from review.features.scatter.controller import update_tp_selection_display
from review.features.export.service import (
    export_cell_data_from, export_scatter_agg_data)
from review.features.scatter.cell_scope_compute import agg_cell_scope


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

    ctrl = plotting_band_slot(ctx, "Scatter Plot", "aggregate")

    # Per-axis channel + property selectors mirror the cells scatter tab.
    # The previous monolithic stat-string combo ("Mean Fluorescence GFP")
    # is replaced; the canonical (channel, metric) pair drives column
    # resolution and aggregation downstream. The legacy hidden
    # ``_scatter_agg_stat_x/y_cb`` combos stay as compatibility shims —
    # ``_update_scatter_menus`` still populates them so older code paths
    # (export_service, plot_orchestrator) keep working.
    from review.data.metric_labels import (
        SYNTHETIC_METRIC_LABEL_TO_KEY as _SYNTHETIC,
        METRIC_ORDER as _BASE_METRIC_ORDER,
    )
    AGG_METRIC_ORDER = list(_BASE_METRIC_ORDER) + list(_SYNTHETIC)

    agg_ch_x_cb = reg.register("_scatter_agg_ch_x_cb", QComboBox())
    agg_ch_x_cb.addItems(["GFP"])
    agg_ch_x_cb.currentIndexChanged.connect(lambda _i: on_scatter_agg_axis_change(ctx, "x"))
    ctrl.add_scope("X-axis · channel", agg_ch_x_cb)

    agg_metric_x_cb = reg.register("_scatter_agg_metric_x_cb", QComboBox())
    agg_metric_x_cb.addItems(AGG_METRIC_ORDER)
    agg_metric_x_cb.currentIndexChanged.connect(lambda _i: redraw_scatter_agg_orchestrate(ctx))
    ctrl.add_scope("property", agg_metric_x_cb)

    agg_ch_y_cb = reg.register("_scatter_agg_ch_y_cb", QComboBox())
    agg_ch_y_cb.addItems(["GFP"])
    agg_ch_y_cb.currentIndexChanged.connect(lambda _i: on_scatter_agg_axis_change(ctx, "y"))
    ctrl.add_scope("Y-axis · channel", agg_ch_y_cb)

    agg_metric_y_cb = reg.register("_scatter_agg_metric_y_cb", QComboBox())
    agg_metric_y_cb.addItems(AGG_METRIC_ORDER)
    agg_metric_y_cb.currentIndexChanged.connect(lambda _i: redraw_scatter_agg_orchestrate(ctx))
    ctrl.add_scope("property", agg_metric_y_cb)

    # Legacy stat combos — hidden, kept in sync for back-compat. Export
    # / save-figure code still reads from these by name (+ ``_var`` aliases).
    stat_x_cb = reg.register("_scatter_agg_stat_x_cb", QComboBox(ctrl))
    stat_x_cb.hide()
    reg.register("_scatter_agg_stat_x_var", stat_x_cb)
    stat_y_cb = reg.register("_scatter_agg_stat_y_cb", QComboBox(ctrl))
    stat_y_cb.hide()
    reg.register("_scatter_agg_stat_y_var", stat_y_cb)

    # Self-describing — the button's own text carries the "Timepoints: …
    # selected" readout (see update_tp_selection_display), so it needs no
    # separate caption or count label beside it.
    agg_tp_button = reg.register("_scatter_agg_tp_button", QPushButton("Timepoints: 0 selected"))
    agg_tp_button.setProperty("variant", "secondary")
    agg_tp_button.clicked.connect(lambda _=False: _open_timepoint_selector(ctx))
    ctrl.add_scope("", agg_tp_button)
    ctx.runtime.scatter.agg_tp_selections = {}
    # Stash the aggregate tp-selection summary refresher so view_state restore can
    # repaint the button's readout without importing this feature module.
    ctx.runtime.scatter.agg_tp_display_refresh = update_tp_selection_display

    # Error band (SEM/SD) for the aggregate's mean±spread points collapses into
    # a "Display ▾" popover (matches line/bar), instead of a card-controls row.
    _disp_btn, _disp_panel, _disp_lay = make_popover_button("Display ▾", ctrl)
    _disp_btn.setToolTip("Error band (SEM/SD) for the aggregate points")
    _disp_lay.addWidget(make_band_controls(ctx, _disp_panel, with_fov=False))
    ctrl.add_action(_disp_btn)

    # Export / copy / save collapse into one "Export ▾" menu (mirrors the
    # per-cell scatter tab).
    _export_btn, _export_menu, _export_data_act = build_plot_save_menu(
        ctx, ctrl,
        card_key="_scatter_agg_card",
        data_label="Save plot data…",
        data_cb=lambda: export_scatter_agg_data(ctx),
        cells_cb=lambda: export_cell_data_from(
            ctx, agg_cell_scope,
            unavailable="Pick both axes and a timepoint first."),
    )
    ctrl.add_action(_export_btn)
    ctrl.add_action(make_properties_button(ctx, ctrl, "scatter_agg"))

    card = PlotCard(parent, figsize=(8, 6), constrained=False, header=False)
    from review.core.ui_helpers import apply_plot_card_decorators
    apply_plot_card_decorators(ctx, card, "Scatter Plot")
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
    from review.core.ui_helpers import no_selection_reason, wrap_with_empty_state
    layout.addWidget(wrap_with_empty_state(
        ctx.window, card, icon="scatter-chart",
        reason=no_selection_reason(),
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
