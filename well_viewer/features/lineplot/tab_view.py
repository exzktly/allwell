"""Line Graphs tab builder (Qt port) — the 3-stacked-subplot figure in a v2 PlotCard."""

from __future__ import annotations

from well_viewer.core.channel_state_controller import on_metric_selected

from well_viewer.core.channel_state_controller import on_plot_channel_selected

from well_viewer.core.plot_card_actions import (
    copy_active_card_as_svg, save_active_card_figure,
)

from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QScrollArea,
    QVBoxLayout, QWidget,
)

from well_viewer.core.ui_helpers import (
    install_canvas_wheel_scroll, make_band_controls,
    make_plot_with_right_dock, make_plot_view_switcher)
from functools import partial
from well_viewer.core.plot_orchestrator import redraw_app
from well_viewer.features.export.service import export_plot_data
from well_viewer.features.lineplot.controller import on_fig_click

# The Line Graphs figure: three stacked subplots (mean / fraction / CDF).
_FIG_W, _FIG_H, _DPI = 7.2, 14.0, 100


def build_line_graphs_tab(ctx, parent: QWidget) -> None:
    from widgets.plot_card import PlotCard

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)

    # A8 burn-down: widget handles go through the WidgetRegistry (``ctx.widgets``),
    # which stamps ``app._<name>`` so legacy ``app._chan_cb_line`` / ``_line_*``
    # readers keep resolving. Non-widget state and behaviour calls still reach app.
    reg = ctx.widgets

    plot_area, layout, export_dock = make_plot_with_right_dock(parent)
    reg.register("_line_export_dock", export_dock)
    parent = plot_area

    # ── controls row above the card (Channel / Metric / style / Export CSV) ──
    line_ctrl = QWidget(parent)
    line_ctrl.setObjectName("TabCtrl")
    cl = QHBoxLayout(line_ctrl)
    cl.setContentsMargins(10, 6, 10, 6)

    # Channel combo: kept as an offscreen widget so the rest of the app can
    # still read/write the per-renderer channel through ``app._chan_cb_line``
    # and ``_plot_chan_var``; the global ctxbar combo is the only visible
    # channel control.
    chan_cb = reg.register("_chan_cb_line", QComboBox())
    chan_cb.addItems(["GFP"])
    chan_cb.currentIndexChanged.connect(
        lambda _i, _src=chan_cb: on_plot_channel_selected(ctx, _src)
    )
    chan_cb.hide()
    if not ctx.widgets.has("_plot_chan_var"):
        reg.register("_plot_chan_var", chan_cb)   # legacy aliases → same widget
        reg.register("_chan_var", chan_cb)

    # The visible metric selector now lives in the global plotting ctxbar
    # (``_plotting_metric_cb``). This hidden per-tab combo stays as a
    # back-compat shim for code that still reads ``_metric_var`` /
    # ``_metric_cb``; the global combo wins when present.
    from well_viewer.data.metric_labels import METRIC_ORDER as _METRIC_ORDER
    metric_frame = reg.register("_metric_selector_frame", QWidget(line_ctrl))
    mfl = QHBoxLayout(metric_frame)
    mfl.setContentsMargins(0, 0, 0, 0)
    mfl.addWidget(QLabel("Property:", metric_frame))
    metric_cb = reg.register("_metric_cb", QComboBox(metric_frame))
    metric_cb.addItems(_METRIC_ORDER)
    metric_cb.currentIndexChanged.connect(lambda _i: on_metric_selected(ctx))
    mfl.addWidget(metric_cb)
    _plotting_metric_cb = getattr(ctx.app, "_plotting_metric_cb", None)
    reg.register("_metric_var", _plotting_metric_cb if _plotting_metric_cb is not None else metric_cb)
    cl.addWidget(metric_frame)
    metric_frame.hide()

    # Fold-change normalization collapses into a popover button (C1) so it no
    # longer needs its own row; the stretch pushes the action buttons right.
    from well_viewer.domain.fold_change_controls import install_fold_change_controls
    from well_viewer.core.ui_helpers import make_popover_button
    _fc_btn, _fc_panel, _fc_lay = make_popover_button("Fold change ▾", line_ctrl)
    _fc_btn.setToolTip("Fold-change normalization (vs a control well/set or a baseline timepoint)")
    install_fold_change_controls(ctx, _fc_panel, _fc_lay, scope="line")
    cl.addWidget(_fc_btn)

    # Error band (SEM/SD + Spread) collapses into a "Display ▾" popover (C1)
    # rather than a separate card-controls row beneath the figure.
    _disp_btn, _disp_panel, _disp_lay = make_popover_button("Display ▾", line_ctrl)
    _disp_btn.setToolTip("Error band (SEM/SD) and per-FOV spread")
    _disp_lay.addWidget(make_band_controls(ctx, _disp_panel, with_fov=True))
    cl.addWidget(_disp_btn)

    cl.addStretch(1)

    # IconButton (QToolButton subclass) matches the Sample Definitions
    # top toolbar style — compact, auto-raise icon-only buttons rather
    # than the pill-style btn_primary / btn_secondary used pre-v2.
    from widgets.icon_button import IconButton as _IconButton
    _export_btn = _IconButton("download", line_ctrl, tooltip="Export CSV")
    _export_btn.clicked.connect(lambda _=False: export_plot_data(ctx))
    cl.addWidget(_export_btn)
    _copy_btn = _IconButton("copy", line_ctrl, tooltip="Copy SVG")
    _copy_btn.clicked.connect(lambda _=False: copy_active_card_as_svg(ctx))
    cl.addWidget(_copy_btn)
    _save_btn = _IconButton("save", line_ctrl, tooltip="Save figure")
    _save_btn.clicked.connect(lambda _=False: save_active_card_figure(ctx))
    cl.addWidget(_save_btn)
    # Properties button last in the row so it sits flush with the right
    # edge of the plot area — adjacent to where the figure-properties
    # dock slides out, instead of the old "▸" floating mid-row.
    style_btn = _IconButton(
        "sliders-horizontal", line_ctrl,
        tooltip="Show / hide the figure properties panel",
    )
    style_btn.clicked.connect(lambda _=False: ctx.open_export_style_panel("line"))
    cl.addWidget(style_btn)
    layout.addWidget(line_ctrl)

    # ── the figure, in a v2 PlotCard (card chrome + MplToolbar) ──────────────
    card = PlotCard(parent, figsize=(_FIG_W, _FIG_H), constrained=False)
    from well_viewer.features.saved_plots.controller import wire_save_to_gallery
    wire_save_to_gallery(ctx, card, "Line Graphs")
    _sw = make_plot_view_switcher(ctx, 'Line Graphs')
    if _sw is not None:
        card.setLeftHeaderWidget(_sw)
    # Hide the per-card header — the ctxbar above already carries the same controls.
    card.setHeaderVisible(False)
    card.setFigureTitle("")          # the tab name is the title; keep the header lean
    # Widget handles go through the WidgetRegistry; legacy ``app._line_*``
    # reads are forwarded to it by WellViewerApp.__getattr__.
    reg.register("_line_card", card)
    fig = reg.register("_line_fig", card.figure)
    ax_mean = reg.register("_line_ax_mean", fig.add_subplot(4, 1, 1))
    reg.register("_line_ax_frac", fig.add_subplot(4, 1, 2, sharex=ax_mean))
    reg.register("_line_ax_n",    fig.add_subplot(4, 1, 3, sharex=ax_mean))
    reg.register("_line_ax_cdf",  fig.add_subplot(4, 1, 4))
    fig.subplots_adjust(
        hspace=0.55, top=0.96, bottom=0.08, left=0.13, right=0.97,
    )
    canvas = reg.register("_line_canvas", card.canvas)
    canvas.setMinimumHeight(int(_FIG_H * _DPI))
    card.setMinimumHeight(int(_FIG_H * _DPI) + 110)   # + header / controls / toolbar rows

    # Error band now lives in the "Display ▾" popover on the control row (C1);
    # no separate card-controls row beneath the header.

    # The Publication↔Screen toggle is wired through plot_style.apply_ax_style
    # (it consults card.plotTheme() via ax.figure._plot_card). The stats chip is
    # left hidden — its statsChanged signal isn't wired to typed state yet (the
    # error band lives on the controls row above; Mean/Median is unsupported).
    card.plotThemeChanged.connect(lambda _m: redraw_app(ctx))
    # Per-panel copy (hover a subplot → small copy icon) echoes to the status bar.
    card.panelCopied.connect(ctx.set_status)
    card.setStatsChipVisible(False)

    scroll = reg.register("_line_scroll_canvas", QScrollArea(parent))
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(card)
    install_canvas_wheel_scroll(canvas, scroll)
    # Wrap the scroll area in an EmptyState stack — page 0 is the
    # placeholder shown until a dataset is loaded, page 1 is the figure.
    from well_viewer.core.ui_helpers import wrap_with_empty_state
    layout.addWidget(wrap_with_empty_state(ctx.window, scroll), 1)

    # Right-click an axes → toggle its legend. (The old left-click-drag-to-set
    # threshold on the CDF was removed — the threshold lives on the Cell Gating tab.)
    canvas.mpl_connect("button_press_event", partial(on_fig_click, ctx))
