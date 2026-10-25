"""The Nanowells page's widgets: the control band, the page figure and the site-detail pane (V1b, V3).

Every control writes the page state through :mod:`.controller`, which redraws; nothing here computes. The
refresh closures registered on ``ctx.widgets`` keep the controls in step with state the controller changes
(a dataset loaded, a metric that needs a channel, the timepoints of the loaded tables).

The view switch lists only the views this build draws (decision U3): a segment that did nothing would offer
a control that does not work (CLAUDE.md §7.6).
"""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSlider, QSpinBox, QSplitter, QToolButton,
    QVBoxLayout, QWidget,
)

from review.features.export.service import export_cell_data_from
from review.features.nanowell import controller as nwc
from review.features.nanowell.cell_scope_compute import cell_scope
from review.features.nanowell.distribution_compute import (
    BASELINE_METRICS, METRICS, PER_NANOWELL, format_cut, match_channel,
)
from review.features.nanowell.state import EXTREMES_MAX_N
from widgets.control_band import ControlBand
from widgets.owned_action import owned_action

#: The views the page draws, in the order the switch lists them.
VIEWS = (("distribution", "Distribution"), ("array", "Array"), ("trajectories", "Trajectories"),
         ("position", "Position"), ("extremes", "Extremes"))
#: The views that show one well, and so offer the well picker and the timepoint slider.
ONE_WELL_VIEWS = ("array",)
#: The views that show one well at one timepoint chosen from a list: the well picker and the timepoint combo.
RANKED_WELL_VIEWS = ("extremes",)
#: The views that draw values over time, where a metric with one value per nanowell has nothing to draw.
_OVER_TIME_VIEWS = ("trajectories", "extremes")
#: The Array view's image layer, as its combo lists it.
_IMAGES = (("off", "Off"), ("montage", "Run montage"), ("tiles", "Full resolution"))
_POSITIONS = (("measured", "Measured"), ("lattice", "Lattice"))
#: The Array view's switchable layers, in the order its Layers menu lists them, with what each draws.
_LAYERS = (("nanowells", "Nanowells", "Each nanowell, filled by the metric or outlined by its state."),
           ("addresses", "Addresses", "Each nanowell's q,r, wherever it is drawn large enough to hold it."),
           ("values", "Values", "Each nanowell's value of the Metric, written inside it (under its q,r when "
                                "Addresses is on) wherever it is drawn large enough to hold it."),
           ("cells", "Cells", "A dot at the centre of each cell the run counted, from its cell table. The run keeps "
                              "no cell outlines, so a dot is all there is to draw."),
           ("tiles", "Tiles", "Each tile's placement, coloured by how the run placed it, with its residual."),
           ("anchor", "Anchor", "The anchor set by hand, and what the grid record says about addressing this "
                                "timepoint; with it on, Set anchor is offered."),
           ("record", "Run record", "The grid record of this timepoint and a line for every other, beside the figure."))
#: The Layers menu's presets: every layer at once, as the views this one replaced drew them.
_PRESETS = (("map", "Array map — lattice places, filled by the metric"),
            ("qc", "Array QC — montage, tiles and nanowell states, run record"),
            ("full", "Full montage — raw tiles at full resolution, addresses, anchor"))
_AXES = (("time", "Time"), ("wells", "Wells"))
_BASELINES = (("earliest", "Earliest"), ("first_measurable", "First measurable"))
_FIG_W, _FIG_H = 8.0, 6.4
_DETAIL_W, _DETAIL_H = 4.2, 5.0
#: The crop strip under the site detail, below its **Pop out** button.
_STRIP_H = 1.4
_METRIC_CB_MIN_W = 220

_VIEW_TIP = ("Distribution: one dot per nanowell, per well and timepoint.\n"
             "Array: one well's nanowells over its montage or at their lattice places — every element a layer\n"
             "you turn on or off (Image, Positions, Layers).\n"
             "Trajectories: each nanowell's metric over time, with the median of each occupancy class.\n"
             "Position: the metric by ring from the central nanowell — an edge-effect check.\n"
             "Extremes: one well's nanowells with the largest and the smallest fold change of the Metric at the\n"
             "Timepoint, each a row of its crops over every timepoint. Click a row for its detail.")
_EXTREMES_TIP = ("How many nanowells each block shows: this many with the largest fold change and this many with the "
                 "smallest. A well with fewer shares them between the blocks, never showing one twice.")
_COHORT_TIP = ("Use only the nanowells with a value at every timepoint of their well, so each line over time follows "
               "one set of nanowells. Off, each timepoint's median is over whichever nanowells could be measured "
               "then. The others are counted as outside the fixed cohort.")
_WELL_TIP = "The well the Array view shows (one of the wells the page draws)."
_SLIDER_TIP = "The timepoint the Array view shows; drag to step through the well's timepoints."
_METRIC_TIP = ("What each dot is. Brightness and site signal use the channel chosen in the app's channel "
               "selector; they are greyed when that channel has no site intensities.")
_AXIS_TIP = ("Time: one panel per well (or replicate set), timepoints along x, the well medians joined.\n"
             "Wells: one timepoint, wells and replicate sets along x, with each group's centre and 95% "
             "interval over wells — wells are the replicate unit, nanowells are not.")
_BASELINE_TIP = ("The timepoint a fold change and the occupancy class are measured from.\n"
                 "Earliest: the well's first timepoint; a nanowell unmeasurable there has no fold change.\n"
                 "First measurable: each nanowell's first timepoint at which the measurement exists.")
_TP_TIP = ("The timepoint drawn on Along x: Wells and Position, and the one Extremes ranks the nanowells at (the "
           "latest by default).")
_RESPONDING_TIP = ("A nanowell counts as responding when its value is above this number. Set one for each metric — "
                   "a fold change, a count, a confluence, an intensity — and the figure states each group's fraction "
                   "responding at it; empty, no fraction is computed. Kept per metric and channel.")
_RESPONDING_W = 90
_IMAGE_TIP = ("What the Array view draws under the nanowells (measured positions only).\n"
              "Run montage: the downsampled montage the run kept.\n"
              "Full resolution: the raw tiles where the run placed them, read as you zoom in — drag to pan, the "
              "wheel zooms.")
_POSITIONS_TIP = ("Measured: where the run found each nanowell, over the image, tiles and anchor.\n"
                  "Lattice: each nanowell at its address on an ideal lattice, so it keeps its place from one "
                  "timepoint to the next even when the plate was put back rotated.")
_LAYERS_TIP = "Turn the Array view's elements on and off, colour the nanowells, or set them all from a preset."
_OVERLAYS_TIP = ("Show or hide every coloured overlay at once — the nanowells, the tiles, the anchor and the cells — to see the "
                 "image beneath. The Layers menu's choices are kept: showing them again restores exactly those.")
_ZOOM_TIP = ("How far the view is zoomed against Fit. The wheel zooms about the pointer (over VNC from a phone, a "
             "two-finger swipe); − and + zoom about the centre.")
_ANCHOR_TIP = ("On: click the central square to anchor this well at this timepoint. Drag still pans. Offered where "
               "an anchor can change the result: a timepoint addressed or left unaddressed.")
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

    cohort_box = QCheckBox("Fixed cohort")
    cohort_box.setToolTip(_COHORT_TIP)
    cohort_box.toggled.connect(lambda on: nwc.set_cohort(ctx, on))
    reg.register("_nw_cohort_box", cohort_box)
    grp_cohort = band.add_scope("", cohort_box)

    well_cb = QComboBox()
    well_cb.setToolTip(_WELL_TIP)
    well_cb.currentIndexChanged.connect(lambda _i: nwc.set_well(ctx, well_cb.currentData() or ""))
    reg.register("_nw_well_cb", well_cb)
    grp_well = band.add_scope("Well", well_cb)

    array_controls = _build_array_controls(ctx, band)

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

    extremes_spin = QSpinBox()
    extremes_spin.setRange(1, EXTREMES_MAX_N)
    extremes_spin.setValue(st.extremes_n)
    extremes_spin.setToolTip(_EXTREMES_TIP)
    extremes_spin.valueChanged.connect(lambda value: nwc.set_extremes_n(ctx, value))
    reg.register("_nw_extremes_spin", extremes_spin)
    grp_extremes = band.add_scope("Largest & smallest", extremes_spin)

    responding_edit = QLineEdit()
    responding_edit.setPlaceholderText("not set")
    responding_edit.setToolTip(_RESPONDING_TIP)
    responding_edit.setFixedWidth(_RESPONDING_W)
    responding_edit.editingFinished.connect(lambda: nwc.set_responding_above(ctx, responding_edit.text()))
    reg.register("_nw_responding_edit", responding_edit)
    grp_responding = band.add_scope("Responding above", responding_edit)

    control_cb = QComboBox()
    control_cb.setToolTip(_CONTROL_TIP)
    control_cb.currentIndexChanged.connect(lambda _i: nwc.set_control(ctx, control_cb.currentData() or ""))
    reg.register("_nw_control_cb", control_cb)
    grp_control = band.add_scope("vs", control_cb)

    export_btn, _menu, _act = build_plot_save_menu(
        ctx, band, card_key="_nw_card", data_label="Save nanowell data…",
        data_cb=lambda: nwc.export_page_csv(ctx),
        data_tooltip="Every nanowell, timepoint and metric of the wells the page draws, one row each",
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
    card.canvas.mpl_connect("button_release_event", lambda event: nwc.on_release(ctx, event))
    card.canvas.mpl_connect("motion_notify_event", lambda event: nwc.on_motion(ctx, event))
    card.canvas.mpl_connect("scroll_event", lambda event: nwc.on_scroll(ctx, event))
    computing = QLabel("Computing…", card)
    computing.setObjectName("NanowellComputing")
    computing.setAlignment(Qt.AlignCenter)
    computing.hide()
    reg.register("_nw_computing_label", computing)

    detail = QWidget(splitter)
    detail_layout = QVBoxLayout(detail)
    detail_layout.setContentsMargins(0, 0, 0, 0)
    detail_card = PlotCard(detail, figsize=(_DETAIL_W, _DETAIL_H), constrained=False, header=False)
    detail_card.setFigureTitle("")
    detail_card.setToolTip("The selected nanowells over time. Click a dot to select one, Shift-click to add one, "
                           "and click empty space on the plot to clear the selection.")
    reg.register("_nw_detail_card", detail_card)
    reg.register("_nw_detail_fig", detail_card.figure)
    reg.register("_nw_detail_canvas", detail_card.canvas)
    detail_layout.addWidget(detail_card, 3)
    detail_layout.addLayout(_crops_header(ctx, detail))
    strip_card = PlotCard(detail, figsize=(_DETAIL_W, _STRIP_H), constrained=False, header=False)
    strip_card.setFigureTitle("")
    reg.register("_nw_strip_card", strip_card)
    reg.register("_nw_strip_fig", strip_card.figure)
    reg.register("_nw_strip_canvas", strip_card.canvas)
    detail_layout.addWidget(strip_card, 1)
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

    controls = (metric_cb, axis_seg, baseline_cb, tp_cb, control_cb, well_cb, tp_slider, cohort_box, responding_edit,
                extremes_spin, *array_controls.signalling)

    def refresh_controls() -> None:
        channel = match_channel(ctx.state.channel.active_channel, st.data.channels) if st.data else None
        blocked = [w.blockSignals(True) for w in controls]
        try:
            _select(metric_cb, st.metric)
            view = st.view if st.view in dict(VIEWS) else VIEWS[0][0]
            view_seg.setCurrentByData(view)
            _grey_metrics(metric_cb, view, channel, ctx.state.channel.active_channel)
            per_nanowell = st.metric in PER_NANOWELL
            axis_seg.setCurrentByData("wells" if per_nanowell else st.axis)
            axis_seg.setSegmentEnabled(0, not per_nanowell)
            axis_seg.setToolTip(_AXIS_TIP + ("\n\n(Time is greyed: this metric has one value per nanowell.)"
                                             if per_nanowell else ""))
            _select(baseline_cb, st.baseline)
            # Extremes ranks every metric by its fold change, so the baseline always matters there.
            baseline_cb.setEnabled((st.metric in BASELINE_METRICS or view in RANKED_WELL_VIEWS)
                                   and _metric_reaches_figure(st, view))
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
            array = view == "array"
            # Shown on every view that has a metric at all, and only greyed on the Array view when no layer draws
            # it: hidden, they came and went with the Layers menu and every control after them jumped sideways.
            by_metric = _metric_reaches_figure(st, view)
            metric_cb.setEnabled(by_metric)
            grp_metric.setVisible(True)
            grp_baseline.setVisible(True)
            grp_axis.setVisible(distribution)
            cohort_box.setChecked(st.cohort)
            _refresh_array_controls(ctx, st, array, array_controls)
            # Only where values over time are drawn: a per-nanowell metric has one, and a one-well view is one time.
            # Trajectories draws Cell count in place of a per-nanowell metric (``resolve_metric``), and the cohort
            # applies to what it draws, so its box stays where it can be turned off.
            grp_cohort.setVisible((distribution and not per_nanowell) or view == "trajectories")
            grp_control.setVisible(wells_axis)
            column = nwc.drawn_metric_column(ctx)
            cut = st.thresholds.get(column) if column else None
            responding_edit.setText("" if cut is None else format_cut(cut))
            grp_responding.setVisible(distribution)
            grp_tp.setVisible((wells_axis and not per_nanowell) or view == "position" or view in RANKED_WELL_VIEWS)
            grp_well.setVisible(view in ONE_WELL_VIEWS or view in RANKED_WELL_VIEWS)
            extremes_spin.setValue(st.extremes_n)
            grp_extremes.setVisible(view in RANKED_WELL_VIEWS)
            grp_slider.setVisible(view in ONE_WELL_VIEWS)
        finally:
            for w, b in zip(controls, blocked):
                w.blockSignals(b)

    reg.register("_nw_show_detail", show_detail)
    reg.register("_nw_show_computing", show_computing)
    reg.register("_nw_page_visible", page_visible)
    reg.register("_nw_refresh_controls", refresh_controls)
    refresh_controls()


def _grey_metrics(metric_cb, view: str, channel, active_channel) -> None:
    """Grey each Metric entry *view* cannot draw, with the reason as its tooltip: a channel metric with no site
    intensities for the shared channel, and a metric with one value per nanowell on a view of values over time."""
    for i in range(metric_cb.count()):
        key = metric_cb.itemData(i)
        has_channel = not METRICS[key][1] or channel is not None
        over_time = not (view in _OVER_TIME_VIEWS and key in PER_NANOWELL)
        item = metric_cb.model().item(i)
        item.setEnabled(has_channel and over_time)
        item.setToolTip("" if has_channel and over_time else
                        "One value per nanowell: no fold change to follow over time." if has_channel else
                        f"No site intensities for the shared channel ({active_channel}); pick a fluorescence "
                        f"channel.")


def _crops_header(ctx, parent) -> QHBoxLayout:
    """The row above the site detail's crop strip: what the strip is, and **Pop out**, which opens the crops large in
    a window of their own (:func:`_popout_window`, ``controller.pop_out_crops``)."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    title = QLabel("Crops over time", parent)
    title.setObjectName("Caption")
    button = QPushButton("Pop out", parent)
    button.setProperty("variant", "secondary")
    button.setToolTip("Open the first selected nanowell's crops at every timepoint, large, in a window of their own "
                      "— on one brightness scale, so changes between timepoints read as changes. The window "
                      "follows the selection.")
    button.clicked.connect(lambda _=False: nwc.pop_out_crops(ctx))
    ctx.widgets.register("_nw_popout_btn", button)
    ctx.widgets.register("_nw_open_popout", lambda: _popout_window(ctx).show_raised())
    row.addWidget(title)
    row.addStretch(1)
    row.addWidget(button)
    return row


def _popout_window(ctx):
    """The pop-out crops window, built on first use and kept: a top-level window owned by the app's window, so it
    closes with it, holding one :class:`PlotCard` the controller draws the crops on."""
    window = ctx.widgets.get_or_none("_nw_popout")
    if window is not None:
        return window
    from widgets.plot_card import PlotCard

    window = _PopoutWindow(ctx.window)
    window.setWindowTitle("Nanowell crops")
    layout = QVBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)
    card = PlotCard(window, figsize=(_FIG_W, _FIG_H * 0.8), constrained=False, header=False)
    card.setFigureTitle("")
    layout.addWidget(card)
    window.resize(900, 640)
    ctx.widgets.register("_nw_popout", window)
    ctx.widgets.register("_nw_popout_fig", card.figure)
    ctx.widgets.register("_nw_popout_canvas", card.canvas)
    card.plotThemeChanged.connect(lambda _m: nwc.draw_popout(ctx))
    return window


class _PopoutWindow(QWidget):
    def __init__(self, parent) -> None:
        super().__init__(parent, Qt.Window)

    def show_raised(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()


def _button(text: str, tip: str, on_click) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("variant", "secondary")
    button.setToolTip(tip)
    if on_click is not None:
        button.clicked.connect(lambda _=False: on_click())
    return button


def _layers_button(ctx, parent) -> tuple[QToolButton, dict, dict]:
    """The Array view's *Layers* menu: each switchable layer, the nanowells' colouring, and the presets."""
    button = QToolButton(parent)
    button.setText("Layers")
    button.setToolButtonStyle(Qt.ToolButtonTextOnly)
    button.setPopupMode(QToolButton.InstantPopup)
    button.setToolTip(_LAYERS_TIP)
    button.setProperty("variant", "secondary")
    button.setProperty("hasMenu", True)
    menu = QMenu(button)
    button.setMenu(menu)
    reg = ctx.widgets
    layer_actions = {}
    for key, label, tip in _LAYERS:
        action = owned_action(menu, label)
        action.setCheckable(True)
        action.setToolTip(tip)
        action.toggled.connect(lambda on, key=key: nwc.set_layer(ctx, key, on))
        reg.register(f"_nw_layer_{key}", action)
        layer_actions[key] = action
    menu.addSeparator()
    group = QActionGroup(menu)
    colour_actions = {}
    for key, label in (("metric", "Fill nanowells by the metric"), ("state", "Outline nanowells by state")):
        action = owned_action(menu, label)
        action.setCheckable(True)
        group.addAction(action)
        action.toggled.connect(lambda on, key=key: nwc.set_layer(ctx, "colour", key) if on else None)
        reg.register(f"_nw_colour_{key}", action)
        colour_actions[key] = action
    menu.addSeparator()
    # Built from Python with its parent, not by ``addMenu(title)``: a menu Qt creates is a top-level widget whose
    # owner the binding does not know, so deleting the page's window and then the menu deletes it twice.
    presets = QMenu("Show as", menu)
    menu.addMenu(presets)
    for key, label in _PRESETS:
        action = owned_action(presets, label)
        action.triggered.connect(lambda _=False, key=key: nwc.apply_array_preset(ctx, key))
        reg.register(f"_nw_preset_{key}", action)
    reg.register("_nw_layers_btn", button)
    return button, layer_actions, colour_actions


def _metric_reaches_figure(st, view: str) -> bool:
    """Whether the Metric and Baseline controls change what *view* draws: on the Array view only through the
    nanowells' fill or their written values, so not when neither is drawn."""
    layers = nwc.drawn_layers(st)
    return view != "array" or (layers.nanowells and layers.colour == "metric") or layers.values


def _refresh_array_controls(ctx, st, on_view: bool, c: SimpleNamespace) -> None:
    """The Array view's controls in step with its layers; shown only on that view. What the lattice frame cannot
    draw (the image, the tiles, the anchor) is greyed there and says why."""
    layers = st.layers
    lattice = layers.lattice()
    _select(c.image_cb, layers.image)
    c.image_cb.setEnabled(not lattice)
    c.positions_seg.setCurrentByData(layers.positions)
    names = {k: label for k, label, _t in _LAYERS}
    for key, action in c.layer_actions.items():
        action.setChecked(getattr(layers, key))
        well_frame_only = key in ("tiles", "anchor")
        action.setEnabled(not (lattice and well_frame_only))
        action.setText(names[key] + (" (measured positions only)" if lattice and well_frame_only else ""))
    for key, action in c.colour_actions.items():
        action.setChecked(layers.colour == key)
        action.setEnabled(layers.nanowells)
    for group in c.groups:
        group.setVisible(on_view)
    c.overlays_btn.setChecked(not st.overlays_hidden)
    can_set, can_clear, can_rerun = nwc.anchor_controls(ctx)
    # On the view whatever the layers say, its buttons greyed where they cannot act, so turning a layer on or off
    # never moves the controls beside it.
    c.grp_anchor.setVisible(on_view)
    c.one_btn.setEnabled(nwc.array_um_per_px(ctx) is not None)
    c.anchor_btn.setEnabled(can_set)
    c.anchor_btn.setChecked(st.anchor_mode and can_set)
    c.clear_anchor_btn.setEnabled(can_clear)
    c.rerun_btn.setEnabled(can_rerun)
    canvas = ctx.widgets.get_or_none("_nw_canvas")
    if canvas is not None:
        canvas.setCursor(Qt.CrossCursor if on_view and st.anchor_mode and can_set else Qt.ArrowCursor)


def _build_array_controls(ctx, band) -> SimpleNamespace:
    """The Array view's controls on the band: Image, Positions, Layers, Fit / 1:1 and the anchor's three buttons."""
    from widgets.segmented_control import SegmentedControl

    reg = ctx.widgets
    image_cb = _combo(_IMAGES, _IMAGE_TIP)
    image_cb.currentIndexChanged.connect(lambda _i: nwc.set_layer(ctx, "image", image_cb.currentData() or "off"))
    reg.register("_nw_image_cb", image_cb)
    grp_image = band.add_scope("Image", image_cb)

    positions_seg = SegmentedControl()
    for key, label in _POSITIONS:
        positions_seg.addSegment(text=label, data=key)
    positions_seg.setToolTip(_POSITIONS_TIP)
    positions_seg.currentChanged.connect(lambda idx: nwc.set_layer(ctx, "positions", _POSITIONS[idx][0]))
    reg.register("_nw_positions_seg", positions_seg)
    grp_positions = band.add_scope("Positions", positions_seg)

    layers_btn, layer_actions, colour_actions = _layers_button(ctx, band)
    overlays_btn = _button("Overlays", _OVERLAYS_TIP, None)
    overlays_btn.setCheckable(True)
    overlays_btn.setChecked(True)
    overlays_btn.toggled.connect(lambda on: nwc.set_overlays(ctx, on))
    reg.register("_nw_overlays_btn", overlays_btn)
    grp_layers = band.add_scope("", layers_btn, overlays_btn)

    out_btn = _button("−", "Zoom out, about the centre of the view.", lambda: nwc.array_zoom(ctx, 1 / nwc.BUTTON_STEP))
    zoom_lbl = QLabel("")
    zoom_lbl.setObjectName("NanowellZoomLabel")
    zoom_lbl.setToolTip(_ZOOM_TIP)
    in_btn = _button("+", "Zoom in, about the centre of the view.", lambda: nwc.array_zoom(ctx, nwc.BUTTON_STEP))
    fit_btn = _button("Fit", "Show everything drawn.", lambda: nwc.array_fit(ctx))
    one_btn = _button("1:1", "One image pixel per screen pixel, about the centre of the view.",
                      lambda: nwc.array_one_to_one(ctx))
    reg.register("_nw_zoom_out_btn", out_btn)
    reg.register("_nw_zoom_in_btn", in_btn)
    reg.register("_nw_zoom_lbl", zoom_lbl)
    reg.register("_nw_fit_btn", fit_btn)
    reg.register("_nw_one_btn", one_btn)
    grp_zoom = band.add_scope("Zoom", out_btn, zoom_lbl, in_btn, fit_btn, one_btn)

    def refresh_zoom() -> None:
        st = ctx.state.feature_slice("nanowell")
        factor = nwc.array_zoom_factor(st.drawn) if st is not None and st.view == "array" else None
        zoom_lbl.setText("" if factor is None else f"{factor:.2g}×" if factor < 10 else f"{factor:.0f}×")
        out_btn.setEnabled(factor is not None and factor > nwc.ZOOM_OUT_LIMIT * 1.0001)
        in_btn.setEnabled(factor is not None and factor < nwc.ZOOM_IN_LIMIT / 1.0001)

    reg.register("_nw_refresh_zoom", refresh_zoom)

    anchor_btn = _button("Set anchor", _ANCHOR_TIP, None)
    anchor_btn.setCheckable(True)
    anchor_btn.toggled.connect(lambda on: nwc.set_anchor_mode(ctx, on))
    clear_anchor_btn = _button("Clear anchor", "Remove this well's anchor at this timepoint.",
                               lambda: nwc.clear_anchor(ctx))
    rerun_btn = _button("Re-run anchored wells…", "Open Analyze on this dataset, limited to the wells with an "
                        "anchor, each run again in full so its timepoints are located with the anchor.",
                        lambda: nwc.rerun_anchored(ctx))
    reg.register("_nw_anchor_btn", anchor_btn)
    reg.register("_nw_clear_anchor_btn", clear_anchor_btn)
    reg.register("_nw_rerun_btn", rerun_btn)
    grp_anchor = band.add_scope("Anchor", anchor_btn, clear_anchor_btn, rerun_btn)
    return SimpleNamespace(
        image_cb=image_cb, positions_seg=positions_seg, layer_actions=layer_actions, colour_actions=colour_actions,
        groups=(grp_image, grp_positions, grp_layers, grp_zoom), grp_anchor=grp_anchor, one_btn=one_btn,
        anchor_btn=anchor_btn, clear_anchor_btn=clear_anchor_btn, rerun_btn=rerun_btn, overlays_btn=overlays_btn,
        signalling=(image_cb, positions_seg, anchor_btn, overlays_btn, *layer_actions.values(),
                    *colour_actions.values()))
