"""Bar Plots tab builder (Qt port) — the 3-stacked-subplot figure in a v2 PlotCard."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSlider, QVBoxLayout, QWidget,
)

from well_viewer.ui_helpers import (
    btn_primary, btn_secondary, install_canvas_wheel_scroll, make_band_controls,
    make_plot_with_right_dock, make_plot_view_switcher)

# The Bar Plots figure: three stacked subplots (mean / fraction / n).
_FIG_W, _FIG_H, _DPI = 6.2, 11.0, 100


def build_bar_plots_tab(ctx, parent: QWidget) -> None:
    app = ctx.app  # transitional DI seam — narrow to ctx.state / ctx.widgets over time
    from widgets.plot_card import PlotCard

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)

    plot_area, outer_layout, app._bar_export_dock = make_plot_with_right_dock(parent)
    parent = plot_area

    bar_right = QWidget(parent)
    right_l = QVBoxLayout(bar_right)
    right_l.setContentsMargins(0, 0, 0, 0)
    outer_layout.addWidget(bar_right, 1)

    # ── controls bar ─────────────────────────────────────────────────────────
    bar_ctrl = QWidget(bar_right)
    bar_ctrl.setObjectName("TabCtrl")
    cl = QHBoxLayout(bar_ctrl)
    cl.setContentsMargins(10, 6, 10, 6)

    # Channel combo hidden — the global ctxbar combo is the only visible
    # channel control. See line_graphs_tab_view.py for rationale.
    app._chan_cb_bar = QComboBox()
    app._chan_cb_bar.addItems(["GFP"])
    app._chan_cb_bar.currentIndexChanged.connect(
        lambda _i, _src=app._chan_cb_bar: app._on_plot_channel_selected(_src)
    )
    app._chan_cb_bar.hide()

    # Hidden per-tab metric combo — back-compat shim only. The global
    # plotting ctxbar's ``_plotting_metric_cb`` is the single visible
    # source of truth for the active intensity property.
    from well_viewer.metric_labels import METRIC_ORDER as _METRIC_ORDER
    app._metric_selector_frame_bar = QWidget(bar_ctrl)
    mf_l = QHBoxLayout(app._metric_selector_frame_bar)
    mf_l.setContentsMargins(0, 0, 0, 0)
    mf_l.addWidget(QLabel("Property:", app._metric_selector_frame_bar))
    app._metric_cb_bar = QComboBox(app._metric_selector_frame_bar)
    app._metric_cb_bar.addItems(_METRIC_ORDER)
    app._metric_cb_bar.currentIndexChanged.connect(lambda _i: app._on_metric_selected())
    mf_l.addWidget(app._metric_cb_bar)
    cl.addWidget(app._metric_selector_frame_bar)
    app._metric_selector_frame_bar.hide()

    # Fold-change normalization sits on the main controls row, left-aligned;
    # the stretch below pushes the action buttons to the right.
    from well_viewer.tabs.fold_change_controls import install_fold_change_controls
    install_fold_change_controls(app, bar_ctrl, cl, scope="bar")

    cl.addStretch(1)

    app._bar_reset_order_btn = QPushButton("Reset Order", bar_ctrl)
    app._bar_reset_order_btn.setProperty("variant", "toggle_muted")
    app._bar_reset_order_btn.clicked.connect(lambda _=False: app._bar_reset_order())
    cl.addWidget(app._bar_reset_order_btn)

    _export_btn = btn_primary(bar_ctrl, "", app._export_bar_plot_data,
                              icon="download")
    _export_btn.setToolTip("Export CSV")
    cl.addWidget(_export_btn)
    _b = btn_secondary(bar_ctrl, "", lambda: app._copy_active_card_as_svg(), icon="copy")
    _b.setToolTip("Copy SVG")
    cl.addWidget(_b)
    _b = btn_secondary(bar_ctrl, "", lambda: app._save_active_card_figure(), icon="save")
    _b.setToolTip("Save figure")
    cl.addWidget(_b)
    # Properties button last in the row so it sits flush with the right
    # edge of the plot area — adjacent to where the dock slides out.
    style_btn = btn_secondary(
        bar_ctrl, "",
        lambda: app._open_export_style_panel("bar"),
        icon="sliders-horizontal",
    )
    style_btn.setToolTip("Show / hide the figure properties panel")
    cl.addWidget(style_btn)
    right_l.addWidget(bar_ctrl)

    # ── plot-shape row (Timepoint / Beeswarm / Violin / Smooth) ──────────────
    # Split off the controls that change WHAT is plotted from the controls
    # that change HOW it's exported — keeps the main row uncluttered.
    shape_ctrl = QWidget(bar_right)
    shape_ctrl.setObjectName("TabCtrl")
    sl = QHBoxLayout(shape_ctrl)
    sl.setContentsMargins(10, 2, 10, 6)

    sl.addWidget(QLabel("Timepoint:", shape_ctrl))
    app._bar_tp_cb = QComboBox(shape_ctrl)
    app._bar_tp_cb.addItems(["—"])
    app._bar_tp_cb.currentIndexChanged.connect(lambda _i: app._redraw_bars())
    sl.addWidget(app._bar_tp_cb)
    app._bar_tp_var = app._bar_tp_cb

    app._swarm_btn = QPushButton("Beeswarm", shape_ctrl)
    app._swarm_btn.setProperty("variant", "toggle")
    app._swarm_btn.setCheckable(True)
    app._swarm_btn.clicked.connect(lambda _=False: app._toggle_swarm())
    sl.addWidget(app._swarm_btn)
    app._bar_swarm = False

    app._violin_btn = QPushButton("Violin", shape_ctrl)
    app._violin_btn.setProperty("variant", "toggle")
    app._violin_btn.setCheckable(True)
    app._violin_btn.clicked.connect(lambda _=False: app._toggle_violin())
    sl.addWidget(app._violin_btn)
    app._bar_violin = False

    sl.addWidget(QLabel("Smooth:", shape_ctrl))
    from widgets.styled_slider import StyledSlider as _StyledSlider
    app._violin_slider = _StyledSlider(Qt.Horizontal, shape_ctrl)
    app._violin_slider.setRange(5, 200)
    app._violin_slider.setValue(100)
    app._violin_slider.setFixedWidth(80)
    app._violin_slider.valueChanged.connect(
        lambda _v: (app._redraw_bars() if app._bar_violin else None)
    )
    app._violin_slider.setEnabled(False)
    sl.addWidget(app._violin_slider)

    sl.addStretch(1)
    right_l.addWidget(shape_ctrl)

    # ── the figure, in a v2 PlotCard (card chrome + MplToolbar) ──────────────
    card = PlotCard(bar_right, figsize=(_FIG_W, _FIG_H), constrained=False)
    _sw = make_plot_view_switcher(app, 'Bar Plots')
    if _sw is not None:
        card.setLeftHeaderWidget(_sw)
    # Hide the per-card header — the ctxbar above already carries the same controls.
    card.setHeaderVisible(False)
    card.setFigureTitle("")
    app._bar_card = card
    app._bar_fig = card.figure
    app._ax_bar_mean = app._bar_fig.add_subplot(3, 1, 1)
    app._ax_bar_frac = app._bar_fig.add_subplot(3, 1, 2)
    app._ax_bar_n = app._bar_fig.add_subplot(3, 1, 3)
    app._bar_fig.subplots_adjust(
        hspace=0.65, top=0.96, bottom=0.10, left=0.15, right=0.97,
    )
    app._bar_canvas = card.canvas
    app._bar_canvas.setMinimumHeight(int(_FIG_H * _DPI))
    card.setMinimumHeight(int(_FIG_H * _DPI) + 110)

    card.setControlsWidget(make_band_controls(app, card, with_fov=True))
    card.plotThemeChanged.connect(lambda _m: app._redraw_bars())
    card.setStatsChipVisible(False)

    app._bar_scroll_canvas = QScrollArea(bar_right)
    app._bar_scroll_canvas.setWidgetResizable(True)
    app._bar_scroll_canvas.setFrameShape(QFrame.NoFrame)
    app._bar_scroll_canvas.setWidget(card)
    install_canvas_wheel_scroll(app._bar_canvas, app._bar_scroll_canvas)
    from well_viewer.ui_helpers import wrap_with_empty_state
    right_l.addWidget(wrap_with_empty_state(
        app, app._bar_scroll_canvas, icon="bar-chart-horizontal",
    ), 1)

    # Drag-to-reorder bars
    app._bar_drag_state = {"active": False, "src_idx": -1, "cur_idx": -1}
    app._bar_canvas.mpl_connect("button_press_event", app._on_bar_drag_press)
    app._bar_canvas.mpl_connect("motion_notify_event", app._on_bar_drag_motion)
    app._bar_canvas.mpl_connect("button_release_event", app._on_bar_drag_release)
