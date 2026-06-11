"""Line Graphs tab builder (Qt port) — the 3-stacked-subplot figure in a v2 PlotCard."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QScrollArea,
    QVBoxLayout, QWidget,
)

from well_viewer.core.ui_helpers import (
    install_canvas_wheel_scroll, make_band_controls,
    make_plot_with_right_dock, make_plot_view_switcher)
from functools import partial
from well_viewer.features.export.service import export_plot_data
from well_viewer.features.lineplot.controller import on_fig_click

# The Line Graphs figure: three stacked subplots (mean / fraction / CDF).
_FIG_W, _FIG_H, _DPI = 7.2, 10.5, 100


def build_line_graphs_tab(ctx, parent: QWidget) -> None:
    from widgets.plot_card import PlotCard

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)

    plot_area, layout, export_dock = make_plot_with_right_dock(parent)
    ctx.app._widget_registry.register("_line_export_dock", export_dock)
    parent = plot_area

    # ── controls row above the card (Channel / Metric / style / Export CSV) ──
    line_ctrl = QWidget(parent)
    line_ctrl.setObjectName("TabCtrl")
    cl = QHBoxLayout(line_ctrl)
    cl.setContentsMargins(10, 6, 10, 6)

    # Channel combo: kept as an offscreen widget so the rest of the ctx.app can
    # still read/write the per-renderer channel through ``ctx.app._chan_cb_line``
    # and ``_plot_chan_var``; the global ctxbar combo is the only visible
    # channel control.
    ctx.app._chan_cb_line = QComboBox()
    ctx.app._chan_cb_line.addItems(["GFP"])
    ctx.app._chan_cb_line.currentIndexChanged.connect(
        lambda _i, _src=ctx.app._chan_cb_line: ctx.app._on_plot_channel_selected(_src)
    )
    ctx.app._chan_cb_line.hide()
    if not hasattr(ctx.app, "_plot_chan_var"):
        ctx.app._plot_chan_var = ctx.app._chan_cb_line
        ctx.app._chan_var = ctx.app._plot_chan_var

    # The visible metric selector now lives in the global plotting ctxbar
    # (``_plotting_metric_cb``). This hidden per-tab combo stays as a
    # back-compat shim for code that still reads ``_metric_var`` /
    # ``_metric_cb``; the global combo wins when present.
    from well_viewer.data.metric_labels import METRIC_ORDER as _METRIC_ORDER
    ctx.app._metric_selector_frame = QWidget(line_ctrl)
    mfl = QHBoxLayout(ctx.app._metric_selector_frame)
    mfl.setContentsMargins(0, 0, 0, 0)
    mfl.addWidget(QLabel("Property:", ctx.app._metric_selector_frame))
    ctx.app._metric_cb = QComboBox(ctx.app._metric_selector_frame)
    ctx.app._metric_cb.addItems(_METRIC_ORDER)
    ctx.app._metric_cb.currentIndexChanged.connect(lambda _i: ctx.app._on_metric_selected())
    mfl.addWidget(ctx.app._metric_cb)
    if getattr(ctx.app, "_plotting_metric_cb", None) is not None:
        ctx.app._metric_var = ctx.app._plotting_metric_cb
    else:
        ctx.app._metric_var = ctx.app._metric_cb
    cl.addWidget(ctx.app._metric_selector_frame)
    ctx.app._metric_selector_frame.hide()

    # Fold-change normalization sits on the main controls row, left-aligned;
    # the stretch below pushes the action buttons to the right.
    from well_viewer.domain.fold_change_controls import install_fold_change_controls
    install_fold_change_controls(ctx.app, line_ctrl, cl, scope="line")

    cl.addStretch(1)

    # IconButton (QToolButton subclass) matches the Sample Definitions
    # top toolbar style — compact, auto-raise icon-only buttons rather
    # than the pill-style btn_primary / btn_secondary used pre-v2.
    from widgets.icon_button import IconButton as _IconButton
    _export_btn = _IconButton("download", line_ctrl, tooltip="Export CSV")
    _export_btn.clicked.connect(lambda _=False: export_plot_data(ctx.app))
    cl.addWidget(_export_btn)
    _copy_btn = _IconButton("copy", line_ctrl, tooltip="Copy SVG")
    _copy_btn.clicked.connect(lambda _=False: ctx.app._copy_active_card_as_svg())
    cl.addWidget(_copy_btn)
    _save_btn = _IconButton("save", line_ctrl, tooltip="Save figure")
    _save_btn.clicked.connect(lambda _=False: ctx.app._save_active_card_figure())
    cl.addWidget(_save_btn)
    # Properties button last in the row so it sits flush with the right
    # edge of the plot area — adjacent to where the figure-properties
    # dock slides out, instead of the old "▸" floating mid-row.
    style_btn = _IconButton(
        "sliders-horizontal", line_ctrl,
        tooltip="Show / hide the figure properties panel",
    )
    style_btn.clicked.connect(lambda _=False: ctx.app._open_export_style_panel("line"))
    cl.addWidget(style_btn)
    layout.addWidget(line_ctrl)

    # ── the figure, in a v2 PlotCard (card chrome + MplToolbar) ──────────────
    card = PlotCard(parent, figsize=(_FIG_W, _FIG_H), constrained=False)
    _sw = make_plot_view_switcher(ctx.app, 'Line Graphs')
    if _sw is not None:
        card.setLeftHeaderWidget(_sw)
    # Hide the per-card header — the ctxbar above already carries the same controls.
    card.setHeaderVisible(False)
    card.setFigureTitle("")          # the tab name is the title; keep the header lean
    # Widget handles go through the WidgetRegistry; legacy ``ctx.app._line_*``
    # reads are forwarded to it by WellViewerApp.__getattr__.
    reg = ctx.app._widget_registry
    reg.register("_line_card", card)
    fig = reg.register("_line_fig", card.figure)
    ax_mean = reg.register("_line_ax_mean", fig.add_subplot(3, 1, 1))
    reg.register("_line_ax_frac", fig.add_subplot(3, 1, 2, sharex=ax_mean))
    reg.register("_line_ax_cdf", fig.add_subplot(3, 1, 3))
    fig.subplots_adjust(
        hspace=0.62, top=0.96, bottom=0.08, left=0.13, right=0.97,
    )
    canvas = reg.register("_line_canvas", card.canvas)
    canvas.setMinimumHeight(int(_FIG_H * _DPI))
    card.setMinimumHeight(int(_FIG_H * _DPI) + 110)   # + header / controls / toolbar rows

    # The "Error Band: SEM/SD" + "Spread: FOV" toggles (formerly on the legacy
    # plot toolbar) → the card's controls row beneath the header.
    card.setControlsWidget(make_band_controls(ctx.app, card, with_fov=True))

    # The Publication↔Screen toggle is wired through plot_style.apply_ax_style
    # (it consults card.plotTheme() via ax.figure._plot_card). The stats chip is
    # left hidden — its statsChanged signal isn't wired to ctx.app state yet (the
    # error band lives on the controls row above; Mean/Median is unsupported).
    card.plotThemeChanged.connect(lambda _m: ctx.app._redraw())
    card.setStatsChipVisible(False)

    scroll = reg.register("_line_scroll_canvas", QScrollArea(parent))
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(card)
    install_canvas_wheel_scroll(canvas, scroll)
    # Wrap the scroll area in an EmptyState stack — page 0 is the
    # placeholder shown until a dataset is loaded, page 1 is the figure.
    from well_viewer.core.ui_helpers import wrap_with_empty_state
    layout.addWidget(wrap_with_empty_state(ctx.app, scroll), 1)

    # Right-click an axes → toggle its legend. (The old left-click-drag-to-set
    # threshold on the CDF was removed — the threshold lives on the Cell Gating tab.)
    ctx.app._line_canvas.mpl_connect("button_press_event", partial(on_fig_click, ctx.app))
