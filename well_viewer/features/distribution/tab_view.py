"""Distribution Analysis tab builder.

Per-cell histogram / KDE / violin views of the active value column at a
chosen timepoint, grouped per replicate set or per well — in a v2 PlotCard.
"""

from __future__ import annotations

from well_viewer.core.plot_card_actions import (
    copy_active_card_as_svg, save_active_card_figure,
)

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QSpinBox,
    QVBoxLayout, QWidget,
)

from well_viewer.core.ui_helpers import (
    make_band_controls, make_plot_with_right_dock,
    make_plot_view_switcher)
from well_viewer.features.export.service import export_distribution_data
from well_viewer.data.data_loading import get_all_timepoints


_MODE_OPTIONS = ["Histogram", "Histogram + KDE", "KDE only", "CDF", "Violin (per group)"]
_LAYOUT_OPTIONS = ["Overlay", "Faceted (one per group)"]


def build_distribution_tab(ctx, parent: QWidget) -> None:
    from widgets.plot_card import PlotCard

    # A8 burn-down: widget handles go through the WidgetRegistry (``ctx.widgets``),
    # which also stamps ``app._<name>`` so legacy ``app._distribution_*`` readers
    # keep resolving. Non-widget state (bins / log flags) and behaviour calls
    # still reach the app — a later layer.
    reg = ctx.widgets

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)

    plot_area, layout, export_dock = make_plot_with_right_dock(parent)
    reg.register("_distribution_export_dock", export_dock)
    parent = plot_area

    ctrl = QWidget(parent)
    ctrl.setObjectName("TabCtrl")
    cl = QHBoxLayout(ctrl)
    cl.setContentsMargins(10, 6, 10, 6)

    # Channel combo hidden — global ctxbar combo is the only visible
    # channel control. See line_graphs_tab_view.py for rationale.
    chan_cb = reg.register("_chan_cb_distribution", QComboBox())
    chan_cb.addItems(["GFP"])
    chan_cb.currentIndexChanged.connect(
        lambda _i, _src=chan_cb: ctx.app._on_plot_channel_selected(_src)
    )
    chan_cb.hide()

    cl.addWidget(QLabel("t (h):", ctrl))
    tp_cb = reg.register("_distribution_tp_cb", QComboBox(ctrl))
    tp_cb.setMinimumContentsLength(8)
    tp_cb.currentIndexChanged.connect(lambda _i: _on_tp_changed_dist(ctx))
    cl.addWidget(tp_cb)
    reg.register("_distribution_tp_var", tp_cb)   # legacy alias → same widget

    cl.addWidget(QLabel("Mode:", ctrl))
    mode_cb = reg.register("_distribution_mode_cb", QComboBox(ctrl))
    mode_cb.addItems(_MODE_OPTIONS)
    mode_cb.setCurrentText("Histogram + KDE")
    mode_cb.currentIndexChanged.connect(lambda _i: _on_changed(ctx))
    cl.addWidget(mode_cb)
    reg.register("_distribution_mode_var", mode_cb)   # legacy alias → same widget

    cl.addWidget(QLabel("Bins:", ctrl))
    bins_spin = reg.register("_distribution_bins_spin", QSpinBox(ctrl))
    # No practical upper bound — fine-grained / over-sampled histograms are
    # a legitimate request when distributions are concentrated in a narrow
    # window of the channel's dynamic range. Qt's QSpinBox tops out at
    # ``INT_MAX`` so this is as close to "no upper limit" as the widget
    # supports.
    bins_spin.setRange(2, 2_147_483_647)
    bins_spin.setValue(40)

    def _on_bins_changed(val: int) -> None:
        ctx.state.distribution.bins = int(val)
        _on_changed(ctx)
    bins_spin.valueChanged.connect(_on_bins_changed)
    cl.addWidget(bins_spin)

    # Histogram layout: overlay every group on one axis (default), or
    # face one axis per group stacked vertically with minimal padding.
    # The combo is always visible but only has an effect for the
    # ``Histogram`` / ``Histogram + KDE`` modes; the renderer ignores it
    # for KDE-only, CDF, and Violin layouts.
    cl.addWidget(QLabel("Layout:", ctrl))
    layout_cb = reg.register("_distribution_layout_cb", QComboBox(ctrl))
    layout_cb.addItems(_LAYOUT_OPTIONS)
    layout_cb.setCurrentText("Overlay")
    layout_cb.currentIndexChanged.connect(lambda _i: _on_changed(ctx))
    cl.addWidget(layout_cb)
    reg.register("_distribution_layout_var", layout_cb)   # legacy alias → same widget

    log_x_cb = reg.register("_distribution_log_x_cb", QCheckBox("log x", ctrl))

    def _on_log_changed(state: int) -> None:
        ctx.state.distribution.log_x = bool(state)
        _on_changed(ctx)
    log_x_cb.stateChanged.connect(_on_log_changed)
    cl.addWidget(log_x_cb)

    log_y_cb = reg.register("_distribution_log_y_cb", QCheckBox("log y", ctrl))

    def _on_log_y_changed(state: int) -> None:
        ctx.state.distribution.log_y = bool(state)
        _on_changed(ctx)
    log_y_cb.stateChanged.connect(_on_log_y_changed)
    cl.addWidget(log_y_cb)

    cl.addStretch(1)

    from widgets.icon_button import IconButton as _IconButton
    _export_btn = _IconButton("download", ctrl, tooltip="Export CSV")
    _export_btn.clicked.connect(lambda _=False: export_distribution_data(ctx.app))
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
    style_btn.clicked.connect(lambda _=False: ctx.open_export_style_panel("distribution"))
    cl.addWidget(style_btn)
    layout.addWidget(ctrl)

    card = PlotCard(parent, figsize=(7.2, 5.4), constrained=False)
    from well_viewer.features.saved_plots.controller import wire_save_to_gallery
    wire_save_to_gallery(ctx, card, "Distribution")
    _sw = make_plot_view_switcher(ctx, 'Distribution')
    if _sw is not None:
        card.setLeftHeaderWidget(_sw)
    # Hide the per-card header — the ctxbar above already carries the same controls.
    card.setHeaderVisible(False)
    card.setFigureTitle("")
    reg.register("_distribution_card", card)
    fig = reg.register("_distribution_fig", card.figure)
    reg.register("_distribution_ax", fig.add_subplot(1, 1, 1))
    fig.subplots_adjust(top=0.93, bottom=0.12, left=0.10, right=0.97)
    reg.register("_distribution_canvas", card.canvas)
    card.setControlsWidget(make_band_controls(ctx.app, card, with_fov=False))
    # NOTE: don't trigger a redraw on theme change — the distribution renderer
    # doesn't use plot_style.apply_ax_style, so ax.clear() would wipe the
    # widget-side theme styling that setPlotTheme already applied. Let
    # PlotCard.setPlotTheme's own apply_axes_style walk handle spines / grid /
    # ticks / title / labels for the existing axes.
    card.setStatsChipVisible(False)
    from well_viewer.core.ui_helpers import wrap_with_empty_state
    layout.addWidget(wrap_with_empty_state(
        ctx.app, card, icon="line-chart",
    ), 1)

    refresh_distribution_timepoints(ctx)


def _on_changed(ctx) -> None:
    """Common callback for any control change in the Distribution tab.

    A8-teardown R3: fully ctx — ``redraw_distribution`` and every shared render
    leaf it calls now take ``ctx``, so this names no god-object."""
    from well_viewer.features.distribution.controller import redraw_distribution
    redraw_distribution(ctx)


def _on_tp_changed_dist(ctx) -> None:
    """Timepoint combo changed — publish the shared focus timepoint (N5). In
    Split View, emit so the Heat Map pane redraws live; otherwise update the
    value silently and redraw only this tab. Other tabs pick it up on next show.

    A8-teardown T3: fully on modern infrastructure — combo via ``ctx.widgets``,
    split-mode flag via ``ctx.runtime.split``, focus via the ctx timepoint
    leaves. No god-object reach."""
    from well_viewer.core.timepoint_sync import set_focus_tp
    cb = ctx.widgets.get_or_none("_distribution_tp_cb")
    txt = cb.currentText() if cb is not None else ""
    try:
        tp = float(txt) if txt not in ("", "—", None) else None
    except (ValueError, TypeError):
        tp = None
    if ctx.runtime.split.active:
        set_focus_tp(ctx, tp)
    else:
        set_focus_tp(ctx, tp, emit=False)
        _on_changed(ctx)


def refresh_distribution_timepoints(ctx) -> None:
    """Populate the timepoint dropdown from the loaded data.

    A8-teardown T3: takes ``ctx`` — the combo comes from the WidgetRegistry and
    the timepoint leaves (``get_all_timepoints`` / ``current_focus_tp``) now take
    ``ctx`` too, so this handler names no god-object."""
    cb = ctx.widgets.get_or_none("_distribution_tp_cb")
    if cb is None:
        return
    try:
        tps = get_all_timepoints(ctx)
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
        # Seed from the shared focus timepoint (N5) when one is set and present.
        from well_viewer.core.timepoint_sync import current_focus_tp, nearest_index
        i = nearest_index(tps, current_focus_tp(ctx)) if tps else None
        if i is not None:
            cb.setCurrentIndex(i)
    finally:
        cb.blockSignals(blocked)
