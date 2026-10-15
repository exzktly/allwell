"""The Nanowells page's widgets: the control band, the page figure and the site-detail pane (V1b, V3).

Every control writes the page state through :mod:`.controller`, which redraws; nothing here computes. The
refresh closures registered on ``ctx.widgets`` keep the controls in step with state the controller changes
(a dataset loaded, a metric that needs a channel, the timepoints of the loaded tables).

The view switch lists only the views this build draws (decision U3): a segment that did nothing would offer
a control that does not work (CLAUDE.md §7.6).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QLabel, QPushButton, QSlider, QSplitter, QVBoxLayout, QWidget

from review.features.export.service import export_cell_data_from
from review.features.nanowell import controller as nwc
from review.features.nanowell.cell_scope_compute import cell_scope
from review.features.nanowell.distribution_compute import BASELINE_METRICS, METRICS, PER_NANOWELL, match_channel
from widgets.control_band import ControlBand

#: The views the page draws, in the order the switch lists them.
VIEWS = (("distribution", "Distribution"), ("array_map", "Array map"), ("trajectories", "Trajectories"),
         ("position", "Position"), ("array_qc", "Array QC"))
#: The views that show one well, and so offer the well picker and the timepoint slider.
ONE_WELL_VIEWS = ("array_map", "array_qc")
_AXES = (("time", "Time"), ("wells", "Wells"))
_BASELINES = (("earliest", "Earliest"), ("first_measurable", "First measurable"))
_FIG_W, _FIG_H = 8.0, 6.4
_DETAIL_W, _DETAIL_H = 4.2, 6.4
_METRIC_CB_MIN_W = 220

_VIEW_TIP = ("Distribution: one dot per nanowell, per well and timepoint.\n"
             "Array map: one well's nanowells in their lattice places, coloured by the metric.\n"
             "Trajectories: each nanowell's metric over time, with the median of each occupancy class.\n"
             "Position: the metric by ring from the central nanowell — an edge-effect check.\n"
             "Array QC: the montage, how each tile was placed, and the grid record of every timepoint.")
_WELL_TIP = "The well the Array map and Array QC show (one of the wells the page draws)."
_SLIDER_TIP = "The timepoint the Array map and Array QC show; drag to step through the well's timepoints."
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
    grp_metric = band.add_scope("Metric", metric_cb)

    axis_seg = SegmentedControl()
    for key, label in _AXES:
        axis_seg.addSegment(text=label, data=key)
    axis_seg.setToolTip(_AXIS_TIP)
    axis_seg.currentChanged.connect(lambda idx: nwc.set_axis(ctx, _AXES[idx][0]))
    reg.register("_nw_axis_seg", axis_seg)
    grp_axis = band.add_scope("Along x", axis_seg)

    baseline_cb = _combo(_BASELINES, _BASELINE_TIP)
    baseline_cb.currentIndexChanged.connect(
        lambda _i: nwc.set_page_baseline(ctx, baseline_cb.currentData() or "earliest"))
    reg.register("_nw_baseline_cb", baseline_cb)
    grp_baseline = band.add_scope("Baseline", baseline_cb)

    well_cb = QComboBox()
    well_cb.setToolTip(_WELL_TIP)
    well_cb.currentIndexChanged.connect(lambda _i: nwc.set_well(ctx, well_cb.currentData() or ""))
    reg.register("_nw_well_cb", well_cb)
    grp_well = band.add_scope("Well", well_cb)

    tp_slider = QSlider(Qt.Horizontal)
    tp_slider.setToolTip(_SLIDER_TIP)
    tp_slider.setMinimum(0)
    tp_slider.setPageStep(1)
    tp_slider_label = QLabel("")
    tp_slider_label.setObjectName("NanowellSliderLabel")
    slider_tps: list = []
    tp_slider.valueChanged.connect(
        lambda v: nwc.set_timepoint(ctx, slider_tps[v]) if 0 <= v < len(slider_tps) else None)
    reg.register("_nw_tp_slider", tp_slider)
    grp_slider = band.add_scope("Timepoint", tp_slider, tp_slider_label)

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

    controls = (metric_cb, axis_seg, baseline_cb, tp_cb, control_cb, well_cb, tp_slider)

    def refresh_controls() -> None:
        channel = match_channel(ctx.state.channel.active_channel, st.data.channels) if st.data else None
        blocked = [w.blockSignals(True) for w in controls]
        try:
            _select(metric_cb, st.metric)
            view = st.view if st.view in dict(VIEWS) else VIEWS[0][0]
            view_seg.setCurrentByData(view)
            for i in range(metric_cb.count()):
                key = metric_cb.itemData(i)
                has_channel = not METRICS[key][1] or channel is not None
                over_time = not (view == "trajectories" and key in PER_NANOWELL)
                item = metric_cb.model().item(i)
                item.setEnabled(has_channel and over_time)
                item.setToolTip("" if has_channel and over_time else
                                "One value per nanowell: nothing to follow over time." if has_channel else
                                f"No site intensities for the shared channel "
                                f"({ctx.state.channel.active_channel}); pick a fluorescence channel.")
            per_nanowell = st.metric in PER_NANOWELL
            axis_seg.setCurrentByData("wells" if per_nanowell else st.axis)
            axis_seg.setSegmentEnabled(0, not per_nanowell)
            axis_seg.setToolTip(_AXIS_TIP + ("\n\n(Time is greyed: this metric has one value per nanowell.)"
                                             if per_nanowell else ""))
            _select(baseline_cb, st.baseline)
            baseline_cb.setEnabled(st.metric in BASELINE_METRICS)
            tps, shown = nwc.shown_timepoint(ctx)
            tp_cb.clear()
            for tp in reversed(tps):
                tp_cb.addItem(tp, tp)
            _select(tp_cb, shown)
            slider_tps[:] = tps
            tp_slider.setMaximum(max(len(tps) - 1, 0))
            tp_slider.setValue(tps.index(shown) if shown in tps else 0)
            tp_slider.setEnabled(len(tps) > 1)
            tp_slider_label.setText(shown)
            well_cb.clear()
            for well in nwc.page_wells(ctx) if st.data else []:
                well_cb.addItem(well, well)
            _select(well_cb, nwc.page_well(ctx))
            control_cb.clear()
            control_cb.addItem("— none", "")
            for name in nwc.page_groups(ctx) if st.data else {}:
                control_cb.addItem(name, name)
            _select(control_cb, st.control)
            distribution = view == "distribution"
            wells_axis = distribution and (st.axis == "wells" or per_nanowell)
            grp_metric.setVisible(view != "array_qc")
            grp_baseline.setVisible(view != "array_qc")
            grp_axis.setVisible(distribution)
            grp_control.setVisible(wells_axis)
            grp_tp.setVisible((wells_axis and not per_nanowell) or view == "position")
            grp_well.setVisible(view in ONE_WELL_VIEWS)
            grp_slider.setVisible(view in ONE_WELL_VIEWS)
        finally:
            for w, b in zip(controls, blocked):
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
