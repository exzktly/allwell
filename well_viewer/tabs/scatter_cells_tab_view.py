"""Scatter Plot: Cells tab builder (Qt port) — single scatter in a v2 PlotCard."""

from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout,
    QWidget, QWidgetAction,
)

from well_viewer.ui_helpers import (
    btn_primary, btn_secondary, make_band_controls, make_plot_with_right_dock,
    make_plot_view_switcher)
from well_viewer.scatter_controller import redraw_scatter_orchestrate
from functools import partial
from well_viewer.export_service import export_scatter_data
from well_viewer.scatter_controller import on_scatter_click
from well_viewer.scatter_controller import on_scatter_motion


def build_scatter_cells_tab(ctx, parent: QWidget) -> None:
    from widgets.plot_card import PlotCard

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)

    plot_area, layout, ctx.app._scatter_export_dock = make_plot_with_right_dock(parent)
    parent = plot_area

    ctrl = QWidget(parent)
    ctrl.setObjectName("TabCtrl")
    cl = QHBoxLayout(ctrl)
    cl.setContentsMargins(10, 6, 10, 6)

    # Per-axis channel + property selectors. The scatter tab overrides
    # the global ctxbar channel/property combos (those are hidden when
    # Scatter Plot is the active plotting subtab) because each axis
    # needs to render an independent column.
    from well_viewer.metric_labels import METRIC_ORDER as _METRIC_ORDER
    cl.addWidget(QLabel("X-axis:", ctrl))
    cl.addWidget(QLabel("Channel:", ctrl))
    ctx.app._scatter_ch_x_cb = QComboBox(ctrl)
    ctx.app._scatter_ch_x_cb.addItems(["gfp"])
    ctx.app._scatter_ch_x_cb.currentIndexChanged.connect(lambda _i: ctx.app._on_scatter_axis_change("x"))
    cl.addWidget(ctx.app._scatter_ch_x_cb)
    ctx.app._scatter_ch_x_var = ctx.app._scatter_ch_x_cb

    cl.addWidget(QLabel("Property:", ctrl))
    ctx.app._scatter_metric_x_cb = QComboBox(ctrl)
    ctx.app._scatter_metric_x_cb.addItems(_METRIC_ORDER)
    ctx.app._scatter_metric_x_cb.currentIndexChanged.connect(lambda _i: redraw_scatter_orchestrate(ctx.app))
    cl.addWidget(ctx.app._scatter_metric_x_cb)
    ctx.app._scatter_metric_x_var = ctx.app._scatter_metric_x_cb

    cl.addWidget(QLabel("Y-axis:", ctrl))
    cl.addWidget(QLabel("Channel:", ctrl))
    ctx.app._scatter_ch_y_cb = QComboBox(ctrl)
    ctx.app._scatter_ch_y_cb.addItems(["gfp"])
    ctx.app._scatter_ch_y_cb.currentIndexChanged.connect(lambda _i: ctx.app._on_scatter_axis_change("y"))
    cl.addWidget(ctx.app._scatter_ch_y_cb)
    ctx.app._scatter_ch_y_var = ctx.app._scatter_ch_y_cb

    cl.addWidget(QLabel("Property:", ctrl))
    ctx.app._scatter_metric_y_cb = QComboBox(ctrl)
    ctx.app._scatter_metric_y_cb.addItems(_METRIC_ORDER)
    ctx.app._scatter_metric_y_cb.currentIndexChanged.connect(lambda _i: redraw_scatter_orchestrate(ctx.app))
    cl.addWidget(ctx.app._scatter_metric_y_cb)
    ctx.app._scatter_metric_y_var = ctx.app._scatter_metric_y_cb

    cl.addWidget(QLabel("Timepoints:", ctrl))
    ctx.app._scatter_tp_button = QPushButton("Select Timepoints", ctrl)
    ctx.app._scatter_tp_button.setProperty("variant", "secondary")
    ctx.app._scatter_tp_button.clicked.connect(lambda _=False: _open_timepoint_selector(ctx.app))
    cl.addWidget(ctx.app._scatter_tp_button)
    ctx.app._scatter_tp_label = QLabel("(0 selected)", ctrl)
    ctx.app._scatter_tp_label.setObjectName("Muted")
    cl.addWidget(ctx.app._scatter_tp_label)
    # Hidden legacy single-tp combo — kept on ctx.app so export / save-figure
    # paths that still read ``_scatter_tp_var.currentText()`` see a valid
    # timepoint string (the first selected one).
    ctx.app._scatter_tp_cb = QComboBox(ctrl)
    ctx.app._scatter_tp_cb.addItems(["0"])
    ctx.app._scatter_tp_cb.hide()
    ctx.app._scatter_tp_var = ctx.app._scatter_tp_cb
    ctx.app._scatter_tp_selections: dict[str, bool] = {}
    cl.addStretch(1)

    _export_btn = btn_primary(ctrl, "", partial(export_scatter_data, ctx.app),
                              icon="download")
    _export_btn.setToolTip("Export CSV")
    cl.addWidget(_export_btn)
    _b = btn_secondary(ctrl, "", lambda: ctx.app._copy_active_card_as_svg(), icon="copy")
    _b.setToolTip("Copy SVG")
    cl.addWidget(_b)
    _b = btn_secondary(ctrl, "", lambda: ctx.app._save_active_card_figure(), icon="save")
    _b.setToolTip("Save figure")
    cl.addWidget(_b)
    # Properties button last in the row so it sits flush with the right
    # edge of the plot area — adjacent to where the dock slides out.
    style_btn = btn_secondary(
        ctrl, "",
        lambda: ctx.app._open_export_style_panel("scatter_cells"),
        icon="sliders-horizontal",
    )
    style_btn.setToolTip("Show / hide the figure properties panel")
    cl.addWidget(style_btn)
    layout.addWidget(ctrl)

    card = PlotCard(parent, figsize=(8, 6), constrained=False)
    _sw = make_plot_view_switcher(ctx.app, 'Scatter Plot')
    if _sw is not None:
        card.setLeftHeaderWidget(_sw)
    # Hide the per-card header — the ctxbar above already carries the same controls.
    card.setHeaderVisible(False)
    card.setFigureTitle("")
    ctx.app._scatter_card = card
    ctx.app._scatter_fig = card.figure
    ctx.app._ax_scatter = ctx.app._scatter_fig.add_subplot(1, 1, 1)
    ctx.app._scatter_fig.subplots_adjust(hspace=0.3, top=0.95, bottom=0.12, left=0.12, right=0.97)
    ctx.app._scatter_canvas = card.canvas
    card.setControlsWidget(make_band_controls(ctx.app, card, with_fov=False))
    card.plotThemeChanged.connect(lambda _m: redraw_scatter_orchestrate(ctx.app))
    card.setStatsChipVisible(False)
    from well_viewer.ui_helpers import wrap_with_empty_state
    layout.addWidget(wrap_with_empty_state(
        ctx.app, card, icon="scatter-chart",
    ), 1)

    ctx.app._scatter_canvas.mpl_connect("button_press_event", partial(on_scatter_click, ctx.app))
    ctx.app._scatter_canvas.mpl_connect("motion_notify_event", partial(on_scatter_motion, ctx.app))


def _open_timepoint_selector(app) -> None:
    """Pop up a QMenu of checkboxes below the per-cell Timepoints button.

    Mirrors the aggregate scatter's multi-tp picker so a single config
    pattern covers both tabs.
    """
    menu = QMenu(app._scatter_tp_button)

    checkboxes: dict[str, QCheckBox] = {}
    for tp_str in sorted(app._scatter_tp_selections.keys(), key=float):
        cb = QCheckBox(tp_str, menu)
        cb.setChecked(bool(app._scatter_tp_selections[tp_str]))
        checkboxes[tp_str] = cb

        def _make_handler(tp=tp_str, checkbox=cb):
            def on_toggled(checked: bool) -> None:
                app._scatter_tp_selections[tp] = bool(checked)
                app._update_scatter_tp_selection_display()
                redraw_scatter_orchestrate(app)
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
            app._scatter_tp_selections[tp_str] = value
        app._update_scatter_tp_selection_display()
        redraw_scatter_orchestrate(app)

    all_action = QAction("All", menu)
    all_action.triggered.connect(lambda _=False: _set_all(True))
    menu.addAction(all_action)
    none_action = QAction("None", menu)
    none_action.triggered.connect(lambda _=False: _set_all(False))
    menu.addAction(none_action)

    btn = app._scatter_tp_button
    menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
