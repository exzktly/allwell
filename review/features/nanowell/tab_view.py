"""The Nanowells page's widgets: the control band, the Distribution figure and the site-detail pane (V1b, V3).

Every control writes the page state through :mod:`.controller`, which redraws; nothing here computes. The
refresh closures registered on ``ctx.widgets`` keep the controls in step with state the controller changes
(a dataset loaded, a metric that needs a channel, the timepoints of the loaded tables).

The view switch lists only the views this build draws (decision U3): a segment that did nothing would offer
a control that does not work (CLAUDE.md §7.6).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QLabel, QPushButton, QSplitter, QVBoxLayout, QWidget

from review.features.export.service import export_cell_data_from
from review.features.nanowell import controller as nwc
from review.features.nanowell.cell_scope_compute import cell_scope
from review.features.nanowell.distribution_compute import BASELINE_METRICS, METRICS, PER_NANOWELL, match_channel
from widgets.control_band import ControlBand

#: The views this build draws, in order (the rest of §3.10.3 is Section V's PR 10b).
VIEWS = (("distribution", "Distribution"),)
_AXES = (("time", "Time"), ("wells", "Wells"))
_BASELINES = (("earliest", "Earliest"), ("first_measurable", "First measurable"))
_FIG_W, _FIG_H = 8.0, 6.4
_DETAIL_W, _DETAIL_H = 4.2, 6.4
_METRIC_CB_MIN_W = 220

_VIEW_TIP = "Distribution: one dot per nanowell, per well and timepoint. More views follow."
_METRIC_TIP = ("What each dot is. Brightness and site signal use the channel chosen in the app's channel "
               "selector; they are greyed when that channel has no site intensities.")
_AXIS_TIP = ("Time: one panel per well (or replicate set), timepoints along x, the well medians joined.\n"
             "Wells: one timepoint, wells and replicate sets along x, with each group's centre and 95% "
             "interval over wells — wells are the replicate unit, nanowells are not.")
_BASELINE_TIP = ("The timepoint a fold change and the occupancy class are measured from.\n"
                 "Earliest: the well's first timepoint; a nanowell unmeasurable there has no fold change.\n"
                 "First measurable: each nanowell's first timepoint at which the measurement exists.")
_TP_TIP = "The timepoint drawn on Along x: Wells (the latest by default)."
_CONTROL_TIP = "Compare each group's centre with this group's, computed the same way (× ratio, or a difference on log₂)."


def _combo(items, tip: str) -> QComboBox:
    cb = QComboBox()
    for data, label in items:
        cb.addItem(label, data)
    cb.setToolTip(tip)
    return cb


def _select(combo, value) -> None:
    for i in range(combo.count()):
        if combo.itemData(i) == value:
            combo.setCurrentIndex(i)
            return


def build_nanowell_tab(ctx, parent: QWidget) -> None:
    from review.core.ui_helpers import (
        apply_plot_card_decorators, build_plot_save_menu, make_plot_with_right_dock, make_properties_button,
        wrap_with_empty_state,
    )
    from widgets.plot_card import PlotCard
    from widgets.segmented_control import SegmentedControl

    st = ctx.state.feature_slice("nanowell")
    reg = ctx.widgets
    plot_area, layout, export_dock = make_plot_with_right_dock(parent)
    reg.register("_nanowell_export_dock", export_dock)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    band = ControlBand(plot_area)

    view_seg = SegmentedControl()
    for key, label in VIEWS:
        view_seg.addSegment(text=label, data=key)
    view_seg.setCurrentByData(st.view if st.view in dict(VIEWS) else VIEWS[0][0])
    view_seg.setToolTip(_VIEW_TIP)
    view_seg.currentChanged.connect(lambda idx: nwc.set_view(ctx, VIEWS[idx][0]))
    reg.register("_nw_view_seg", view_seg)
    band.add_scope("View", view_seg)

    metric_cb = _combo([(k, label) for k, (label, _ch) in METRICS.items()], _METRIC_TIP)
    metric_cb.setMinimumWidth(_METRIC_CB_MIN_W)
    metric_cb.currentIndexChanged.connect(lambda _i: nwc.set_metric(ctx, metric_cb.currentData() or "n"))
    reg.register("_nw_metric_cb", metric_cb)
    band.add_scope("Metric", metric_cb)

    axis_seg = SegmentedControl()
    for key, label in _AXES:
        axis_seg.addSegment(text=label, data=key)
    axis_seg.setToolTip(_AXIS_TIP)
    axis_seg.currentChanged.connect(lambda idx: nwc.set_axis(ctx, _AXES[idx][0]))
    reg.register("_nw_axis_seg", axis_seg)
    band.add_scope("Along x", axis_seg)

    baseline_cb = _combo(_BASELINES, _BASELINE_TIP)
    baseline_cb.currentIndexChanged.connect(
        lambda _i: nwc.set_page_baseline(ctx, baseline_cb.currentData() or "earliest"))
    reg.register("_nw_baseline_cb", baseline_cb)
    band.add_scope("Baseline", baseline_cb)

    tp_cb = QComboBox()
    tp_cb.setToolTip(_TP_TIP)
    tp_cb.currentIndexChanged.connect(lambda _i: nwc.set_timepoint(ctx, tp_cb.currentData() or ""))
    reg.register("_nw_tp_cb", tp_cb)
    grp_tp = band.add_scope("Timepoint", tp_cb)

    control_cb = QComboBox()
    control_cb.setToolTip(_CONTROL_TIP)
    control_cb.currentIndexChanged.connect(lambda _i: nwc.set_control(ctx, control_cb.currentData() or ""))
    reg.register("_nw_control_cb", control_cb)
    grp_control = band.add_scope("vs", control_cb)

    export_btn, _menu, _act = build_plot_save_menu(
        ctx, band, card_key="_nw_card", data_label="Save nanowell data…",
        data_cb=lambda: nwc.export_page_csv(ctx),
        data_tooltip="Every nanowell, timepoint and metric of this dataset, one row each",
        cells_cb=lambda: export_cell_data_from(
            ctx, cell_scope, unavailable="Select wells with nanowell tables first."))
    band.add_action(export_btn)
    band.add_action(make_properties_button(ctx, band, "nanowell"))
    layout.addWidget(band)

    # ── the figures ─────────────────────────────────────────────────────────
    splitter = QSplitter(Qt.Horizontal, plot_area)
    card = PlotCard(splitter, figsize=(_FIG_W, _FIG_H), constrained=False, header=False)
    card.setFigureTitle("")
    reg.register("_nw_card", card)
    reg.register("_nw_fig", card.figure)
    reg.register("_nw_canvas", card.canvas)
    apply_plot_card_decorators(ctx, card, "Nanowells")
    card.plotThemeChanged.connect(lambda _m: nwc.redraw(ctx))
    card.canvas.mpl_connect("button_press_event", lambda event: nwc.on_click(ctx, event))
    computing = QLabel("Computing…", card)
    computing.setObjectName("NanowellComputing")
    computing.setAlignment(Qt.AlignCenter)
    computing.hide()
    reg.register("_nw_computing_label", computing)

    detail = QWidget(splitter)
    detail_layout = QVBoxLayout(detail)
    detail_layout.setContentsMargins(0, 0, 0, 0)
    clear_btn = QPushButton("Clear selection", detail)
    clear_btn.setProperty("variant", "secondary")
    clear_btn.setToolTip("Clear the nanowells selected for the site detail (the plate selection is not touched).")
    clear_btn.clicked.connect(lambda _=False: _clear_sites(ctx))
    reg.register("_nw_clear_btn", clear_btn)
    detail_card = PlotCard(detail, figsize=(_DETAIL_W, _DETAIL_H), constrained=False, header=False)
    detail_card.setFigureTitle("")
    reg.register("_nw_detail_card", detail_card)
    reg.register("_nw_detail_fig", detail_card.figure)
    reg.register("_nw_detail_canvas", detail_card.canvas)
    detail_layout.addWidget(clear_btn, 0, Qt.AlignRight)
    detail_layout.addWidget(detail_card, 1)
    detail.hide()
    reg.register("_nw_detail_pane", detail)
    splitter.addWidget(card)
    splitter.addWidget(detail)
    splitter.setStretchFactor(0, 3)
    splitter.setStretchFactor(1, 2)
    layout.addWidget(wrap_with_empty_state(ctx.window, splitter, icon="hexagon", reason=nwc.nanowell_empty_reason), 1)

    def show_detail(on: bool) -> None:
        detail.setVisible(on)

    def show_computing() -> None:
        computing.setVisible(bool(st.computing) and st.metrics is not None)
        computing.resize(card.size())
        computing.raise_()

    def page_visible() -> bool:
        return band.isVisible()          # the band shows whenever the page does, empty state or not

    def refresh_controls() -> None:
        channel = match_channel(ctx.state.channel.active_channel, st.data.channels) if st.data else None
        blocked = [w.blockSignals(True) for w in (metric_cb, axis_seg, baseline_cb, tp_cb, control_cb)]
        try:
            _select(metric_cb, st.metric)
            for i in range(metric_cb.count()):
                key = metric_cb.itemData(i)
                usable = not METRICS[key][1] or channel is not None
                item = metric_cb.model().item(i)
                item.setEnabled(usable)
                item.setToolTip("" if usable else
                                f"No site intensities for the shared channel "
                                f"({ctx.state.channel.active_channel}); pick a fluorescence channel.")
            per_nanowell = st.metric in PER_NANOWELL
            axis_seg.setCurrentByData("wells" if per_nanowell else st.axis)
            axis_seg.setSegmentEnabled(0, not per_nanowell)
            axis_seg.setToolTip(_AXIS_TIP + ("\n\n(Time is greyed: this metric has one value per nanowell.)"
                                             if per_nanowell else ""))
            _select(baseline_cb, st.baseline)
            baseline_cb.setEnabled(st.metric in BASELINE_METRICS)
            tp_cb.clear()
            tps = st.data.timepoints() if st.data else []
            for tp in reversed(tps):
                tp_cb.addItem(tp, tp)
            _select(tp_cb, st.timepoint or (tps[-1] if tps else ""))
            control_cb.clear()
            control_cb.addItem("— none", "")
            for name in nwc.page_groups(ctx) if st.data else {}:
                control_cb.addItem(name, name)
            _select(control_cb, st.control)
            wells_axis = (st.axis == "wells" or per_nanowell)
            grp_tp.setVisible(wells_axis and not per_nanowell)
            grp_control.setVisible(wells_axis)
        finally:
            for w, b in zip((metric_cb, axis_seg, baseline_cb, tp_cb, control_cb), blocked):
                w.blockSignals(b)

    reg.register("_nw_show_detail", show_detail)
    reg.register("_nw_show_computing", show_computing)
    reg.register("_nw_page_visible", page_visible)
    reg.register("_nw_refresh_controls", refresh_controls)
    refresh_controls()


def _clear_sites(ctx) -> None:
    st = ctx.state.feature_slice("nanowell")
    if st is not None and st.selected_sites:
        st.selected_sites = []
        nwc.selection_changed(ctx)
