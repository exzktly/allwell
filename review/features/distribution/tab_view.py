"""Distribution Analysis tab builder.

Per-cell histogram / KDE / violin views of the active value column at a
chosen timepoint, grouped per replicate set or per well — in a v2 PlotCard.
"""

from __future__ import annotations

from review.core.channel_state_controller import on_plot_channel_selected


from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QSpinBox,
    QVBoxLayout, QWidget,
)

from review.core.ui_helpers import (
    build_plot_save_menu, make_plot_with_right_dock, make_properties_button, plotting_band_slot)
from review.features.export.service import (
    export_cell_data_from, export_distribution_data)
from review.features.distribution.cell_scope_compute import cell_scope
from review.data.data_loading import get_all_timepoints
from widgets.labeled_combo import LabeledComboBox


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

    ctrl = plotting_band_slot(ctx, "Distribution")

    # Channel combo hidden — Plotting's band combo is the only visible
    # channel control. See line_graphs_tab_view.py for rationale.
    chan_cb = reg.register("_chan_cb_distribution", QComboBox())
    chan_cb.addItems(["GFP"])
    chan_cb.currentIndexChanged.connect(
        lambda _i, _src=chan_cb: on_plot_channel_selected(ctx, _src)
    )
    chan_cb.hide()

    tp_cb = reg.register("_distribution_tp_cb", LabeledComboBox("Timepoint", ctrl))
    tp_cb.currentIndexChanged.connect(lambda _i: _on_tp_changed_dist(ctx))
    ctrl.add_scope("", tp_cb)
    reg.register("_distribution_tp_var", tp_cb)   # legacy alias → same widget

    mode_cb = reg.register("_distribution_mode_cb", QComboBox(ctrl))
    mode_cb.addItems(_MODE_OPTIONS)
    mode_cb.setCurrentText("Histogram + KDE")
    mode_cb.currentIndexChanged.connect(lambda _i: _on_changed(ctx))
    ctrl.add_scope("Mode", mode_cb)
    reg.register("_distribution_mode_var", mode_cb)   # legacy alias → same widget

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
    ctrl.add_scope("Bins", bins_spin)

    # Histogram layout: overlay every group on one axis (default), or
    # face one axis per group stacked vertically with minimal padding.
    # The combo is always visible but only has an effect for the
    # ``Histogram`` / ``Histogram + KDE`` modes; the renderer ignores it
    # for KDE-only, CDF, and Violin layouts.
    layout_cb = reg.register("_distribution_layout_cb", QComboBox(ctrl))
    layout_cb.addItems(_LAYOUT_OPTIONS)
    layout_cb.setCurrentText("Overlay")
    layout_cb.currentIndexChanged.connect(lambda _i: _on_changed(ctx))
    ctrl.add_scope("Layout", layout_cb)
    reg.register("_distribution_layout_var", layout_cb)   # legacy alias → same widget

    log_x_cb = reg.register("_distribution_log_x_cb", QCheckBox("log x", ctrl))

    def _on_log_changed(state: int) -> None:
        # Single source of truth: the per-figure axis pref (``x_log``) — the same
        # store the Properties-sidebar "X log" toggle and the authoritative
        # export-style pass use. Mirror into the feature-state field so the
        # existing view-state persistence keeps working.
        _set_distribution_log(ctx, "x", bool(state))
        _on_changed(ctx)
    log_x_cb.stateChanged.connect(_on_log_changed)
    ctrl.add_scope("", log_x_cb)

    log_y_cb = reg.register("_distribution_log_y_cb", QCheckBox("log y", ctrl))

    def _on_log_y_changed(state: int) -> None:
        _set_distribution_log(ctx, "y", bool(state))
        _on_changed(ctx)
    log_y_cb.stateChanged.connect(_on_log_y_changed)
    ctrl.add_scope("", log_y_cb)

    export_btn, export_menu, _export_data_act = build_plot_save_menu(
        ctx, ctrl,
        card_key="_distribution_card",
        data_label="Save plot data…",
        data_cb=lambda: export_distribution_data(ctx),
        cells_cb=lambda: export_cell_data_from(
            ctx, cell_scope),
    )
    ctrl.add_action(export_btn)
    ctrl.add_action(make_properties_button(ctx, ctrl, "distribution"))

    card = PlotCard(parent, figsize=(7.2, 5.4), constrained=False, header=False)
    from review.core.ui_helpers import apply_plot_card_decorators
    apply_plot_card_decorators(ctx, card, "Distribution")
    card.setFigureTitle("")
    reg.register("_distribution_card", card)
    fig = reg.register("_distribution_fig", card.figure)
    reg.register("_distribution_ax", fig.add_subplot(1, 1, 1))
    fig.subplots_adjust(top=0.93, bottom=0.12, left=0.10, right=0.97)
    reg.register("_distribution_canvas", card.canvas)
    # NOTE: no shared SEM/SD band control here. The card's controls row stays
    # hidden because this tab draws per-group histograms / KDEs / violins and
    # never reduces to a mean, so there is no spread for a band to describe --
    # nothing in redraw_distribution reads PlotState.use_sem. The button is
    # app-global (core/plot_toggles.toggle_sem writes the flag every other plot
    # path reads), so offering it here changed Bar / Line / Scatter-aggregate /
    # Batch Export while the figure under it stayed identical.
    # NOTE: don't trigger a redraw on theme change — the distribution renderer
    # doesn't use plot_style.apply_ax_style, so ax.clear() would wipe the
    # widget-side theme styling that setPlotTheme already applied. Let
    # PlotCard.setPlotTheme's own apply_axes_style walk handle spines / grid /
    # ticks / title / labels for the existing axes.
    card.setStatsChipVisible(False)
    from review.core.ui_helpers import no_selection_reason, wrap_with_empty_state
    layout.addWidget(wrap_with_empty_state(
        ctx.window, card, icon="line-chart",
        reason=no_selection_reason(),
    ), 1)

    refresh_distribution_timepoints(ctx)


def _set_distribution_log(ctx, axis: str, on: bool) -> None:
    """Write the unified log flag for ``axis`` ("x"/"y") to the per-figure axis
    pref (the single source of truth shared with the Properties sidebar) and
    mirror it into the feature-state field for view-state persistence.

    Uses the *core* ``plot_style.figure_prefs`` directly (not the distribution
    controller) so this on-plot handler adds no function-local feature import —
    the controller owns the same write for its own redraw path."""
    from review.core.plot_style import figure_prefs
    key = "x_log" if axis == "x" else "y_log"
    state_attr = "log_x" if axis == "x" else "log_y"
    setattr(ctx.state.distribution, state_attr, bool(on))
    fig = ctx.widgets.get_or_none("_distribution_fig")
    if fig is not None:
        figure_prefs(ctx, fig)[key] = bool(on)


def _on_changed(ctx) -> None:
    """Common callback for any control change in the Distribution tab.

    A8-teardown R3: fully ctx — ``redraw_distribution`` and every shared render
    leaf it calls now take ``ctx``, so this names no god-object."""
    from review.features.distribution.controller import redraw_distribution
    redraw_distribution(ctx)


def _on_tp_changed_dist(ctx) -> None:
    """Timepoint combo changed — publish the shared focus timepoint (N5). In
    Split View, emit so the Heat Map pane redraws live; otherwise update the
    value silently and redraw only this tab. Other tabs pick it up on next show.

    A8-teardown T3: fully on modern infrastructure — combo via ``ctx.widgets``,
    split-mode flag via ``ctx.runtime.split``, focus via the ctx timepoint
    leaves. No god-object reach."""
    from review.core.timepoint_sync import set_focus_tp
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
        from review.core.timepoint_sync import current_focus_tp, nearest_index
        i = nearest_index(tps, current_focus_tp(ctx)) if tps else None
        if i is not None:
            cb.setCurrentIndex(i)
    finally:
        cb.blockSignals(blocked)
