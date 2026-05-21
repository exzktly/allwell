"""Scatter Plot: Aggregate tab builder (Qt port) — single scatter in a v2 PlotCard."""

from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout,
    QWidget, QWidgetAction,
)

from well_viewer.ui_helpers import (
    btn_primary, btn_secondary, make_band_controls, make_plot_with_right_dock,
    make_plot_view_switcher)


def build_scatter_agg_tab(ctx, parent: QWidget) -> None:
    app = ctx.app  # transitional DI seam
    from widgets.plot_card import PlotCard

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)

    plot_area, layout, app._scatter_agg_export_dock = make_plot_with_right_dock(parent)
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
    from well_viewer.metric_labels import METRIC_ORDER as _BASE_METRIC_ORDER
    AGG_METRIC_ORDER = list(_BASE_METRIC_ORDER) + ["Fraction above threshold"]

    cl.addWidget(QLabel("X-axis:", ctrl))
    cl.addWidget(QLabel("Channel:", ctrl))
    app._scatter_agg_ch_x_cb = QComboBox(ctrl)
    app._scatter_agg_ch_x_cb.addItems(["gfp"])
    app._scatter_agg_ch_x_cb.currentIndexChanged.connect(lambda _i: app._on_scatter_agg_axis_change("x"))
    cl.addWidget(app._scatter_agg_ch_x_cb)

    cl.addWidget(QLabel("Property:", ctrl))
    app._scatter_agg_metric_x_cb = QComboBox(ctrl)
    app._scatter_agg_metric_x_cb.addItems(AGG_METRIC_ORDER)
    app._scatter_agg_metric_x_cb.currentIndexChanged.connect(lambda _i: app._redraw_scatter_agg())
    cl.addWidget(app._scatter_agg_metric_x_cb)

    cl.addWidget(QLabel("Y-axis:", ctrl))
    cl.addWidget(QLabel("Channel:", ctrl))
    app._scatter_agg_ch_y_cb = QComboBox(ctrl)
    app._scatter_agg_ch_y_cb.addItems(["gfp"])
    app._scatter_agg_ch_y_cb.currentIndexChanged.connect(lambda _i: app._on_scatter_agg_axis_change("y"))
    cl.addWidget(app._scatter_agg_ch_y_cb)

    cl.addWidget(QLabel("Property:", ctrl))
    app._scatter_agg_metric_y_cb = QComboBox(ctrl)
    app._scatter_agg_metric_y_cb.addItems(AGG_METRIC_ORDER)
    app._scatter_agg_metric_y_cb.currentIndexChanged.connect(lambda _i: app._redraw_scatter_agg())
    cl.addWidget(app._scatter_agg_metric_y_cb)

    # Legacy stat combos — hidden, kept in sync for back-compat. Export
    # / save-figure code still reads from these by name.
    app._scatter_agg_stat_x_cb = QComboBox(ctrl)
    app._scatter_agg_stat_x_cb.hide()
    app._scatter_agg_stat_x_var = app._scatter_agg_stat_x_cb
    app._scatter_agg_stat_y_cb = QComboBox(ctrl)
    app._scatter_agg_stat_y_cb.hide()
    app._scatter_agg_stat_y_var = app._scatter_agg_stat_y_cb

    cl.addWidget(QLabel("Timepoints:", ctrl))
    app._scatter_agg_tp_button = QPushButton("Select Timepoints", ctrl)
    app._scatter_agg_tp_button.setProperty("variant", "secondary")
    app._scatter_agg_tp_button.clicked.connect(lambda _=False: _open_timepoint_selector(app))
    cl.addWidget(app._scatter_agg_tp_button)
    app._scatter_agg_tp_label = QLabel("(0 selected)", ctrl)
    app._scatter_agg_tp_label.setObjectName("Muted")
    cl.addWidget(app._scatter_agg_tp_label)
    app._scatter_agg_tp_selections = {}

    cl.addStretch(1)

    _export_btn = btn_primary(ctrl, "", app._export_scatter_agg_data,
                              icon="download")
    _export_btn.setToolTip("Export CSV")
    cl.addWidget(_export_btn)
    _b = btn_secondary(ctrl, "", lambda: app._copy_active_card_as_svg(), icon="copy")
    _b.setToolTip("Copy SVG")
    cl.addWidget(_b)
    _b = btn_secondary(ctrl, "", lambda: app._save_active_card_figure(), icon="save")
    _b.setToolTip("Save figure")
    cl.addWidget(_b)
    # Properties button last in the row so it sits flush with the right
    # edge of the plot area — adjacent to where the dock slides out.
    style_btn = btn_secondary(
        ctrl, "",
        lambda: app._open_export_style_panel("scatter_agg"),
        icon="sliders-horizontal",
    )
    style_btn.setToolTip("Show / hide the figure properties panel")
    cl.addWidget(style_btn)
    layout.addWidget(ctrl)

    card = PlotCard(parent, figsize=(8, 6), constrained=False)
    _sw = make_plot_view_switcher(app, 'Scatter Plot')
    if _sw is not None:
        card.setLeftHeaderWidget(_sw)
    # Hide the per-card header — the ctxbar above already carries the same controls.
    card.setHeaderVisible(False)
    card.setFigureTitle("")
    app._scatter_agg_card = card
    app._scatter_agg_fig = card.figure
    app._ax_scatter_agg = app._scatter_agg_fig.add_subplot(1, 1, 1)
    app._scatter_agg_fig.subplots_adjust(hspace=0.3, top=0.95, bottom=0.12, left=0.12, right=0.97)
    app._scatter_agg_canvas = card.canvas
    card.setControlsWidget(make_band_controls(app, card, with_fov=False))
    card.plotThemeChanged.connect(lambda _m: app._redraw_scatter_agg())
    card.setStatsChipVisible(False)
    from well_viewer.ui_helpers import wrap_with_empty_state
    layout.addWidget(wrap_with_empty_state(
        app, card, icon="scatter-chart",
    ), 1)


def _open_timepoint_selector(app) -> None:
    """Pop up a QMenu of checkboxes below the Timepoints button."""
    menu = QMenu(app._scatter_agg_tp_button)

    checkboxes: dict[str, QCheckBox] = {}
    for tp_str in sorted(app._scatter_agg_tp_selections.keys(), key=float):
        cb = QCheckBox(tp_str, menu)
        cb.setChecked(bool(app._scatter_agg_tp_selections[tp_str]))
        checkboxes[tp_str] = cb

        def _make_handler(tp=tp_str, checkbox=cb):
            def on_toggled(checked: bool) -> None:
                app._scatter_agg_tp_selections[tp] = bool(checked)
                app._update_tp_selection_display()
                app._redraw_scatter_agg()
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
            app._scatter_agg_tp_selections[tp_str] = value
        app._update_tp_selection_display()
        app._redraw_scatter_agg()

    all_action = QAction("All", menu)
    all_action.triggered.connect(lambda _=False: _set_all(True))
    menu.addAction(all_action)
    none_action = QAction("None", menu)
    none_action.triggered.connect(lambda _=False: _set_all(False))
    menu.addAction(none_action)

    btn = app._scatter_agg_tp_button
    menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
