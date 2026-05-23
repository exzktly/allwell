"""Distribution Analysis tab builder.

Per-cell histogram / KDE / violin views of the active value column at a
chosen timepoint, grouped per replicate set or per well — in a v2 PlotCard.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)

from well_viewer.ui_helpers import (
    btn_primary, btn_secondary, make_band_controls, make_plot_with_right_dock,
    make_plot_view_switcher)


_MODE_OPTIONS = ["Histogram", "Histogram + KDE", "KDE only", "CDF", "Violin (per group)"]
_LAYOUT_OPTIONS = ["Overlay", "Faceted (one per group)"]


def build_distribution_tab(ctx, parent: QWidget) -> None:
    from widgets.plot_card import PlotCard

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)

    plot_area, layout, ctx.app._distribution_export_dock = make_plot_with_right_dock(parent)
    parent = plot_area

    ctrl = QWidget(parent)
    ctrl.setObjectName("TabCtrl")
    cl = QHBoxLayout(ctrl)
    cl.setContentsMargins(10, 6, 10, 6)

    # Channel combo hidden — global ctxbar combo is the only visible
    # channel control. See line_graphs_tab_view.py for rationale.
    ctx.app._chan_cb_distribution = QComboBox()
    ctx.app._chan_cb_distribution.addItems(["GFP"])
    ctx.app._chan_cb_distribution.currentIndexChanged.connect(
        lambda _i, _src=ctx.app._chan_cb_distribution: ctx.app._on_plot_channel_selected(_src)
    )
    ctx.app._chan_cb_distribution.hide()

    cl.addWidget(QLabel("t (h):", ctrl))
    ctx.app._distribution_tp_cb = QComboBox(ctrl)
    ctx.app._distribution_tp_cb.setMinimumContentsLength(8)
    ctx.app._distribution_tp_cb.currentIndexChanged.connect(lambda _i: _on_changed(ctx.app))
    cl.addWidget(ctx.app._distribution_tp_cb)
    ctx.app._distribution_tp_var = ctx.app._distribution_tp_cb

    cl.addWidget(QLabel("Mode:", ctrl))
    ctx.app._distribution_mode_cb = QComboBox(ctrl)
    ctx.app._distribution_mode_cb.addItems(_MODE_OPTIONS)
    ctx.app._distribution_mode_cb.setCurrentText("Histogram + KDE")
    ctx.app._distribution_mode_cb.currentIndexChanged.connect(lambda _i: _on_changed(ctx.app))
    cl.addWidget(ctx.app._distribution_mode_cb)
    ctx.app._distribution_mode_var = ctx.app._distribution_mode_cb

    cl.addWidget(QLabel("Bins:", ctrl))
    ctx.app._distribution_bins_spin = QSpinBox(ctrl)
    # No practical upper bound — fine-grained / over-sampled histograms are
    # a legitimate request when distributions are concentrated in a narrow
    # window of the channel's dynamic range. Qt's QSpinBox tops out at
    # ``INT_MAX`` so this is as close to "no upper limit" as the widget
    # supports.
    ctx.app._distribution_bins_spin.setRange(2, 2_147_483_647)
    ctx.app._distribution_bins_spin.setValue(40)
    ctx.app._distribution_bins = 40

    def _on_bins_changed(val: int) -> None:
        ctx.app._distribution_bins = int(val)
        _on_changed(ctx.app)
    ctx.app._distribution_bins_spin.valueChanged.connect(_on_bins_changed)
    cl.addWidget(ctx.app._distribution_bins_spin)

    # Histogram layout: overlay every group on one axis (default), or
    # face one axis per group stacked vertically with minimal padding.
    # The combo is always visible but only has an effect for the
    # ``Histogram`` / ``Histogram + KDE`` modes; the renderer ignores it
    # for KDE-only, CDF, and Violin layouts.
    cl.addWidget(QLabel("Layout:", ctrl))
    ctx.app._distribution_layout_cb = QComboBox(ctrl)
    ctx.app._distribution_layout_cb.addItems(_LAYOUT_OPTIONS)
    ctx.app._distribution_layout_cb.setCurrentText("Overlay")
    ctx.app._distribution_layout_cb.currentIndexChanged.connect(lambda _i: _on_changed(ctx.app))
    cl.addWidget(ctx.app._distribution_layout_cb)
    ctx.app._distribution_layout_var = ctx.app._distribution_layout_cb

    ctx.app._distribution_log_x_cb = QCheckBox("log x", ctrl)
    ctx.app._distribution_log_x = False

    def _on_log_changed(state: int) -> None:
        ctx.app._distribution_log_x = bool(state)
        _on_changed(ctx.app)
    ctx.app._distribution_log_x_cb.stateChanged.connect(_on_log_changed)
    cl.addWidget(ctx.app._distribution_log_x_cb)

    cl.addStretch(1)

    _export_btn = btn_primary(ctrl, "", ctx.app._export_distribution_data,
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
        lambda: ctx.app._open_export_style_panel("distribution"),
        icon="sliders-horizontal",
    )
    style_btn.setToolTip("Show / hide the figure properties panel")
    cl.addWidget(style_btn)
    layout.addWidget(ctrl)

    card = PlotCard(parent, figsize=(7.2, 5.4), constrained=False)
    _sw = make_plot_view_switcher(ctx.app, 'Distribution')
    if _sw is not None:
        card.setLeftHeaderWidget(_sw)
    # Hide the per-card header — the ctxbar above already carries the same controls.
    card.setHeaderVisible(False)
    card.setFigureTitle("")
    ctx.app._distribution_card = card
    ctx.app._distribution_fig = card.figure
    ctx.app._distribution_ax = ctx.app._distribution_fig.add_subplot(1, 1, 1)
    ctx.app._distribution_fig.subplots_adjust(top=0.93, bottom=0.12, left=0.10, right=0.97)
    ctx.app._distribution_canvas = card.canvas
    card.setControlsWidget(make_band_controls(ctx.app, card, with_fov=False))
    # NOTE: don't trigger a redraw on theme change — the distribution renderer
    # doesn't use plot_style.apply_ax_style, so ax.clear() would wipe the
    # widget-side theme styling that setPlotTheme already applied. Let
    # PlotCard.setPlotTheme's own apply_axes_style walk handle spines / grid /
    # ticks / title / labels for the existing axes.
    card.setStatsChipVisible(False)
    from well_viewer.ui_helpers import wrap_with_empty_state
    layout.addWidget(wrap_with_empty_state(
        ctx.app, card, icon="line-chart",
    ), 1)

    refresh_distribution_timepoints(ctx.app)


def _on_changed(app) -> None:
    """Common callback for any control change in the Distribution tab."""
    from well_viewer.distribution_controller import redraw_distribution
    redraw_distribution(app)


def refresh_distribution_timepoints(app) -> None:
    """Populate the timepoint dropdown from the loaded data."""
    cb = getattr(app, "_distribution_tp_cb", None)
    if cb is None:
        return
    try:
        from well_viewer.scatter_controller import get_all_timepoints
        tps = get_all_timepoints(app)
    except Exception:
        tps = []
    items = [f"{tp:g}" for tp in tps] if tps else []
    blocked = cb.blockSignals(True)
    try:
        cb.clear()
        if items:
            cb.addItems(items)
        else:
            cb.addItem("—")
    finally:
        cb.blockSignals(blocked)
