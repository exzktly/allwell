"""The Nanowells page's widgets: the control band, the page figure and the site-detail pane (V1b, V3).

Every control writes the page state through :mod:`.controller`, which redraws; nothing here computes. The
refresh closures registered on ``ctx.widgets`` keep the controls in step with state the controller changes
(a dataset loaded, a metric that needs a channel, the timepoints of the loaded tables).

The view switch lists only the views this build draws (decision U3): a segment that did nothing would offer
a control that does not work (CLAUDE.md §7.6).
"""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSplitter, QToolButton,
    QVBoxLayout, QWidget,
)

from review.features.export.service import export_cell_data_from
from review.features.nanowell import controller as nwc
from review.features.nanowell.cell_scope_compute import cell_scope
from review.features.nanowell.distribution_compute import (
    BASELINE_METRICS, METRICS, PER_NANOWELL, format_cut,
)
from review.features.nanowell.identity_compute import worst_verdict
from review.features.nanowell.anchor_queue_view import (
    build_queue_controls, refresh_queue_controls,
)
from review.features.nanowell.exclude_view import build_exclude_controls
from review.features.nanowell.rim_focus_view import build_rim_focus_controls
from review.features.nanowell.variables_view import build_variable_controls
from review.features.nanowell.identity_view import build_identity_controls, refresh_identity_controls
from review.features.nanowell.qc_view import build_qc_controls, refresh_qc_controls
from review.features.nanowell.display_range_view import (
    build_display_range_button,
    refresh_display_range_buttons,
)
from review.features.nanowell.series_compute import baseline_at, baseline_timepoint
from review.features.nanowell.subset_view import build_subset_control
from review.features.nanowell import moving_compute as mvc
from review.features.nanowell.moving_compute import MAX_WINDOW
from review.features.nanowell.state import EXTREMES_MAX_N
from review.features.nanowell.statistics_view import (
    build_statistics_controls, fill_control_combo, refresh_statistics_controls,
)
from widgets.control_band import ControlBand
from widgets.labeled_combo import LabeledComboBox
from widgets.owned_action import owned_action
from widgets.spin_box import SpinBox

#: The views the page draws, in the order the switch lists them.
VIEWS = (("distribution", "Distribution"), ("qc", "QC"), ("array", "Array"), ("trajectories", "Trajectories"),
         ("position", "Position"), ("extremes", "Extremes"), ("identity", "Identity"), ("statistics", "Statistics"))
#: The views that show one well, and so offer the well picker and the timepoint slider.
ONE_WELL_VIEWS = ("array", "identity")
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
_STRIP_H = 1.7
_METRIC_CB_MIN_W = 220

#: Each view's tab tooltip, keyed as :data:`VIEWS` keys them.
_VIEW_TIPS = {
    "distribution": "One dot per nanowell, per well and timepoint.",
    "qc": ("Every well and timepoint of the plate at once: which the run could not address, which have an anchor\n"
           "waiting for a re-run, and what the identity check said about the rest. Click a cell to open it on\n"
           "Identity, or on Array to set its anchor. The Anchor queue starts here."),
    "array": ("One well's nanowells over its montage or at their lattice places — every element a layer\n"
              "you turn on or off (Image, Positions, Layers)."),
    "trajectories": "Each nanowell's metric over time, with the median of each occupancy class.",
    "position": "The metric by ring from the central nanowell — an edge-effect check.",
    "extremes": ("One well's nanowells with the largest and the smallest fold change of the Metric at the\n"
                 "Timepoint, each a row of its crops over every timepoint. Click a row for its detail."),
    "identity": ("Whether one well's addresses name the same nanowells at the Timepoint as at its reference\n"
                 "timepoint — the identity check's distances, the montages blinked or side by side, and its "
                 "registration."),
    "statistics": ("Test whether wells (their nanowells as values) or groups (their wells' medians) differ at the\n"
                   "Timepoint — t-tests, Mann-Whitney, Kolmogorov-Smirnov or Dunnett, with an omnibus, adjusted p "
                   "and effect sizes."),
}
_EXTREMES_TIP = ("How many nanowells each block shows: this many with the largest fold change and this many with the "
                 "smallest. A well with fewer shares them between the blocks, never showing one twice.")
_COHORT_TIP = ("Use only the nanowells with a value at every timepoint of their well, so each line over time follows "
               "one set of nanowells. Off, each timepoint's median is over whichever nanowells could be measured "
               "then. The others are counted as outside the fixed cohort. See also Subset, which chooses nanowells "
               "by how many cells they held at the baseline, a metric, their position or their imaging field.")
_MOVING_TIP = ("Moving average: each nanowell's value at a timepoint becomes the mean of its values at that timepoint "
               "and the ones before it, this many in all (timepoints, not hours). A value is averaged only when the "
               "nanowell has a value at every timepoint of its window, so the first timepoints and any window with "
               "a gap have none; they are counted among the exclusions. For a fold change the window starts after "
               "the baseline, so the baseline's own value of 1 is never averaged in. Off draws each timepoint as "
               "measured.")
_BASELINE_MOVING_TIP = (
    "Baseline average: a fold change's baseline becomes the mean of the nanowell's measured values at the baseline "
    "timepoint and the ones before it, this many in all — never after it, which may already carry the treatment. "
    "Off, the baseline is its one measured value and only the endpoint is averaged. Like the endpoint's window it is "
    "used only when every timepoint of it has a value; the others are counted among the exclusions.")
#: Why the baseline's window is greyed, when it is.
_BASELINE_MOVING_OFF = {
    "moving": "Turn on the Moving average first: the baseline's window refines it.",
    "metric": "Only a fold change has a baseline to average.",
    "earliest": "The Earliest and First measurable baselines have no measured timepoint before them to average: "
                "every timepoint before the first measurable one has no value. Choose a timepoint as the baseline.",
}
_WELL_TIP = "The well the Array view shows (one of the wells the page draws)."
_GO_TO_TIP = ("Show this nanowell on Identity ▸ Blink: its well's two montages alternating, zoomed onto it, with its "
              "address ringed — to see whether its address names the same nanowell over time.")
_METRIC_TIP = ("What each dot is.\n"
               "Brightness: how bright the cells are — the average of the counted cells' mean intensities, the same "
               "for one cell or ten equally bright ones; blank for a nanowell with no counted cell.\n"
               "Nanowell fluorescence: how much light the whole nanowell gives off above its bare floor — every "
               "interior pixel summed minus the tile's floor level, no segmentation needed; near zero when empty, "
               "and it grows with both the number of cells and their brightness.\n"
               "Both, and their fold changes, are of the channel chosen beside this (Channel); they are greyed when "
               "the run measured no fluorescence.")
_CHANNEL_TIP = ("The fluorescence channel Brightness and Nanowell fluorescence (and their fold changes) are drawn for, on every "
                "view and in the site detail. A channel the run read but did not measure in the nanowells is listed "
                "greyed, with why.")
_IMAGE_CHANNEL_TIP = ("The channel the crops are cut from — the site detail's strip, Pop out, the Extremes montage — "
                      "and the Array view's Full resolution montage: the phase image, or a fluorescence channel the run "
                      "read. One setting wherever it is offered. The run montage is phase only.")
_AXIS_TIP = ("Time: one panel per well (or replicate set), timepoints along x, the well medians joined.\n"
             "Wells: one timepoint, wells and replicate sets along x, with each group's centre and 95% "
             "interval over wells — wells are the replicate unit, nanowells are not.")
_BASELINE_TIP = ("The timepoint a fold change and the occupancy class are measured from.\n"
                 "Earliest: the well's first timepoint; a nanowell unmeasurable there has no fold change.\n"
                 "First measurable: each nanowell's first timepoint at which the measurement exists.\n"
                 "A timepoint: that timepoint for every nanowell — a well without it has no fold change.")
_EXTREMES_TPS_TIP = ("The timepoints the montage shows as columns. Fewer columns give larger crops; the ranking "
                     "timepoint is always shown.")
#: How long the page's canvas must hold one size before the Array view is re-fitted to it, in milliseconds.
_REFIT_MS = 60
#: How long, in ms, the selection must be still before the page reads the wells it adds: long enough to take a few
#: quick clicks on the plate as one change, short enough that a single click reads at once to the eye.
LOAD_DEBOUNCE_MS = 250
_CROP_METRICS_TIP = ("Write each crop's numbers on it: its fold change in the corner and a line of its metrics along "
                     "its foot. Off leaves the crops bare; the row labels still give each nanowell's rank and value.")
_POPOUT_METRICS_TIP = ("Write each crop's measurements under it — the same setting as the Extremes view's Metrics.")
_POPOUT_LABELS_TIP = ("Write each crop's timepoint over it and the line under the figure saying where the crops were "
                      "cut from and on what brightness scale. Off leaves the crops bare; the title still names the "
                      "nanowell and channel.")
_CROP_CELLS_TIP = ("Draw a dot at the centre of each cell the run counted in the crop's own nanowell on every crop "
                   "— the strip, the pop-out and the Extremes montage. Cells of the neighbouring nanowells that the "
                   "crop's margin takes in are not drawn. The run keeps no cell outlines, so a dot is all there is "
                   "to draw.")
_TP_TIP = ("The timepoint drawn on Along x: Wells and Position (the latest by default), the one Extremes ranks the "
           "nanowells at (the well's latest by default), and the one the Array view shows (the well's first by "
           "default).")
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
               "an anchor can change the result: a timepoint addressed or left unaddressed. To walk every timepoint "
               "waiting for an anchor, use Anchor queue on the QC view.")
_CONTROL_TIP = "Compare each group's centre with this group's, computed the same way (× ratio, or a difference on log₂)."


def baseline_moving_greyed(st, column: str | None) -> str | None:
    """Why the Baseline average control is greyed (a key of ``_BASELINE_MOVING_OFF``), or ``None`` when it reaches
    the figure: the moving average on, a fold change drawn, and a baseline with timepoints before it — exactly
    where ``moving_compute.baseline_window_for`` lets the window act, so a greyed control never changes a figure."""
    if not mvc.is_on(st.moving):
        return "moving"
    if not column or mvc.base_value(column) is None:
        return "metric"
    if mvc.baseline_window_for(st.moving, MAX_WINDOW, st.baseline) == 0:
        return "earliest"
    return None


def _refresh_baseline_moving(spin, st, column: str | None) -> None:
    """The Baseline average spin box: the state's window, enabled only where it reaches the figure, and its tooltip
    naming the reason when it does not."""
    spin.setValue(max(st.baseline_moving, 1))
    greyed = baseline_moving_greyed(st, column)
    spin.setEnabled(greyed is None)
    spin.setToolTip(_BASELINE_MOVING_TIP + (f"\n\n({_BASELINE_MOVING_OFF[greyed]})" if greyed else ""))


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


def view_index(view: str) -> int:
    """The tab index of *view* on the view bar; the first view for a key this build does not draw."""
    keys = [k for k, _label in VIEWS]
    return keys.index(view) if view in keys else 0


def build_view_bar(ctx, view: str):
    """The page's view switch: a tab strip in the shell's mode band, like every other page's sub-tabs.

    The views are a page selector, so they sit where Plotting's, Home's, Image Browser's and Batch Export's
    sub-tabs sit — beside the Review / Analyze / Datasets selector — and carry the ``PlottingSubTabs`` object
    name that theme.qss styles every nested page bar with, so the strips cannot drift apart. The views stay one
    page with one state (``NanowellState.view``) rather than becoming sub-leaves: they share one figure, one band
    and one controller, and a tab click only writes the view through :func:`controller.set_view`.
    """
    from PySide6.QtWidgets import QTabBar

    from review.core.app_shell_view import register_page_tabs

    bar = QTabBar()
    bar.setObjectName("PlottingSubTabs")
    bar.setExpanding(False)
    bar.setElideMode(Qt.ElideNone)
    bar.setUsesScrollButtons(True)
    bar.setDrawBase(False)
    for i, (key, label) in enumerate(VIEWS):
        bar.addTab(label)
        bar.setTabData(i, key)
        bar.setTabToolTip(i, _VIEW_TIPS.get(key, ""))
    bar.setCurrentIndex(view_index(view))
    bar.currentChanged.connect(lambda idx: nwc.set_view(ctx, VIEWS[idx][0]) if 0 <= idx < len(VIEWS) else None)
    ctx.widgets.register("_nw_view_bar", bar)
    register_page_tabs(ctx.window, "Nanowells", bar)
    return bar


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

    view_bar = build_view_bar(ctx, st.view)
    # The anchor walk's controls first: the band's left edge is the one place that never wraps away.
    queue = build_queue_controls(ctx, band)

    metric_cb = _combo([(k, label) for k, (label, _ch) in METRICS.items()], _METRIC_TIP)
    metric_cb.setMinimumWidth(_METRIC_CB_MIN_W)
    metric_cb.currentIndexChanged.connect(lambda _i: nwc.set_metric(ctx, metric_cb.currentData() or "n"))
    reg.register("_nw_metric_cb", metric_cb)
    grp_metric = band.add_scope("Metric", metric_cb)

    channel_cb = QComboBox()
    channel_cb.setToolTip(_CHANNEL_TIP)
    channel_cb.currentIndexChanged.connect(lambda _i: nwc.set_channel(ctx, channel_cb.currentData() or ""))
    reg.register("_nw_channel_cb", channel_cb)
    grp_channel = band.add_scope("Channel", channel_cb)

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

    start = build_subset_control(ctx, band, timepoint_text)

    cohort_box = QCheckBox("Fixed cohort")
    cohort_box.setToolTip(_COHORT_TIP)
    cohort_box.toggled.connect(lambda on: nwc.set_cohort(ctx, on))
    reg.register("_nw_cohort_box", cohort_box)
    grp_cohort = band.add_scope("", cohort_box)

    rim_focus = build_rim_focus_controls(ctx)
    band.add_scope("", rim_focus.box, rim_focus.spin)

    moving_spin = SpinBox()
    # 1 is off: a window of one timepoint averages nothing. The box holds the bare number and its label says what
    # it counts — no "Off" or unit inside the box, which Qt's spin box refuses to type over.
    moving_spin.setRange(1, MAX_WINDOW)
    moving_spin.setToolTip(_MOVING_TIP)
    moving_spin.valueChanged.connect(lambda value: nwc.set_moving(ctx, value))
    reg.register("_nw_moving_spin", moving_spin)
    grp_moving = band.add_scope("Moving average (timepoints, 1 = off)", moving_spin)

    baseline_moving_spin = SpinBox()
    baseline_moving_spin.setRange(1, MAX_WINDOW)     # 1 is off, as on the endpoint's window
    baseline_moving_spin.setToolTip(_BASELINE_MOVING_TIP)
    baseline_moving_spin.valueChanged.connect(lambda value: nwc.set_baseline_moving(ctx, value))
    reg.register("_nw_baseline_moving_spin", baseline_moving_spin)
    grp_baseline_moving = band.add_scope("Baseline average (timepoints, 1 = off)", baseline_moving_spin)

    well_cb = QComboBox()
    well_cb.setToolTip(_WELL_TIP)
    well_cb.currentIndexChanged.connect(lambda _i: nwc.set_well(ctx, well_cb.currentData() or ""))
    reg.register("_nw_well_cb", well_cb)
    exclusion = build_exclude_controls(ctx, well_cb)
    grp_well = band.add_scope("Well", well_cb, exclusion.exclude)
    exclusion.excluded_group = band.add_scope("", exclusion.excluded)

    array_controls = _build_array_controls(ctx, band)
    identity = build_identity_controls(ctx, band)
    statistics = build_statistics_controls(ctx, band)
    qc_controls = build_qc_controls(ctx, band)

    # The Plotting tabs' timepoint control (Distribution, Heat Map): a LabeledComboBox painting its own
    # "Timepoint:" prefix, the timepoints in time order and named by their hours. One control for every view that
    # draws one timepoint — the Array view's included, which had a slider of its own.
    tp_cb = LabeledComboBox("Timepoint")
    tp_cb.setToolTip(_TP_TIP)
    tp_cb.currentIndexChanged.connect(lambda _i: nwc.set_timepoint(ctx, tp_cb.currentData() or ""))
    reg.register("_nw_tp_cb", tp_cb)
    grp_tp = band.add_scope("", tp_cb)

    extremes_spin = SpinBox()
    extremes_spin.setRange(1, EXTREMES_MAX_N)
    extremes_spin.setValue(st.extremes_n)
    extremes_spin.setToolTip(_EXTREMES_TIP)
    extremes_spin.valueChanged.connect(lambda value: nwc.set_extremes_n(ctx, value))
    reg.register("_nw_extremes_spin", extremes_spin)
    extremes_cells = _cells_box(ctx, "_nw_extremes_cells_box")
    extremes_image = _image_channel_combo(ctx, "_nw_extremes_image_channel_cb")
    extremes_tps = _extremes_timepoints_button(ctx, band)
    extremes_numbers = QCheckBox("Metrics")
    extremes_numbers.setToolTip(_CROP_METRICS_TIP)
    extremes_numbers.setChecked(st.crop_metrics)
    extremes_numbers.toggled.connect(lambda on: nwc.set_crop_metrics(ctx, on))
    reg.register("_nw_extremes_metrics_box", extremes_numbers)
    grp_extremes = band.add_scope("Largest & smallest", extremes_spin, extremes_tps.button, extremes_cells,
                                  extremes_numbers, extremes_image,
                                  build_display_range_button(ctx, "_nw_extremes_range_btn"))

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

    variables = build_variable_controls(ctx, band)

    export_btn, _menu, _act = build_plot_save_menu(
        ctx, band, card_key="_nw_card", data_label="Save nanowell data…",
        data_cb=lambda: nwc.export_page_csv(ctx),
        data_tooltip="Every nanowell, timepoint and metric of the wells the page draws, one row each",
        cells_cb=lambda: export_cell_data_from(
            ctx, cell_scope, unavailable="Select wells with nanowell tables first."))
    band.add_action(export_btn)
    band.add_action(make_properties_button(ctx, band, "nanowell"))
    layout.addWidget(band)
    layout.addWidget(start.editor)                  # the Subset's conditions, under the band when shown

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
    # The Array view fits the region it shows to its axes' shape when it draws, so square pixels and round nanowells
    # hold only at that size; a resize (the window, the site detail opening beside it, the band re-flowing) re-fits
    # it, once the resizing has settled.
    refit = QTimer(card)
    refit.setSingleShot(True)
    refit.setInterval(_REFIT_MS)
    refit.timeout.connect(lambda: nwc.on_canvas_resized(ctx))
    card.canvas.mpl_connect("resize_event", lambda _event: refit.start())
    reg.register("_nw_refit_timer", refit)
    computing = QLabel("Computing…", card)
    computing.setObjectName("NanowellComputing")
    computing.setAlignment(Qt.AlignCenter)
    computing.hide()
    # It covers the whole card as a scrim, so it follows the card's size while it shows, not only when shown.
    card.canvas.mpl_connect("resize_event", lambda _event: computing.resize(card.size()))
    reg.register("_nw_computing_label", computing)
    # A selection change reads the wells it adds once the selection has been still this long, so a run of clicks on
    # the plate starts one load rather than one per click (``controller.queue_load``).
    load_timer = QTimer(card)
    load_timer.setSingleShot(True)
    load_timer.setInterval(LOAD_DEBOUNCE_MS)
    load_timer.timeout.connect(lambda: nwc.run_queued_load(ctx))
    reg.register("_nw_load_timer", load_timer)

    detail = QWidget(splitter)
    detail_layout = QVBoxLayout(detail)
    detail_layout.setContentsMargins(0, 0, 0, 0)
    detail_card = PlotCard(detail, figsize=(_DETAIL_W, _DETAIL_H), constrained=False, header=False)
    detail_card.setFigureTitle("")
    detail_card.setToolTip("The selected nanowells over time. Click a dot to select one, Shift-click to add one, "
                           "and click empty space on the plot or ✕ above to clear the selection.")
    reg.register("_nw_detail_card", detail_card)
    reg.register("_nw_detail_fig", detail_card.figure)
    reg.register("_nw_detail_canvas", detail_card.canvas)
    detail_layout.addLayout(_detail_header(ctx, detail))
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
        computing.setVisible(nwc.computing_shown(ctx))
        computing.resize(card.size())
        computing.raise_()

    def page_visible() -> bool:
        return band.isVisible()          # the band shows whenever the page does, empty state or not

    controls = (view_bar, metric_cb, channel_cb, axis_seg, baseline_cb, tp_cb, control_cb, well_cb, cohort_box, moving_spin,
                baseline_moving_spin,
                responding_edit, extremes_spin, extremes_cells, extremes_numbers, *identity.signalling, *start.signalling,
                *array_controls.signalling, *statistics.signalling, *rim_focus.signalling,
                *qc_controls.signalling)

    def refresh_controls() -> None:
        channel = nwc.page_channel(ctx)
        blocked = [w.blockSignals(True) for w in controls]
        try:
            _select(metric_cb, st.metric)
            view = st.view if st.view in dict(VIEWS) else VIEWS[0][0]
            view_bar.setCurrentIndex(view_index(view))
            _grey_metrics(metric_cb, view, channel, nwc.page_channel_name(ctx))
            _fill_channels(channel_cb, st.data, channel)
            channel_cb.setEnabled(channel_cb.count() > 0 and METRICS.get(st.metric, ("", False))[1]
                                  and _metric_reaches_figure(st, view))
            _refresh_image_channels(ctx, st)
            refresh_display_range_buttons(ctx)
            per_nanowell = st.metric in PER_NANOWELL
            axis_seg.setCurrentByData("wells" if per_nanowell else st.axis)
            axis_seg.setSegmentEnabled(0, not per_nanowell)
            axis_seg.setToolTip(_AXIS_TIP + ("\n\n(Time is greyed: this metric has one value per nanowell.)"
                                             if per_nanowell else ""))
            _fill_baselines(baseline_cb, st.data, st.baseline)
            # Extremes ranks every metric by its fold change, so the baseline always matters there.
            baseline_cb.setEnabled((st.metric in BASELINE_METRICS or view in RANKED_WELL_VIEWS)
                                   and _metric_reaches_figure(st, view))
            tps, shown = nwc.shown_timepoint(ctx)
            _fill_timepoints(tp_cb, tps, st.data.times_h if st.data else {})
            _select(tp_cb, shown)
            well_cb.clear()
            for well in nwc.page_wells(ctx) if st.data else []:
                # On the Identity view each well says its worst verdict, so a contradicted one is found at a glance.
                worst = worst_verdict(st.data, well) if view == "identity" else None
                name = nwc.well_caption(ctx, well)        # its label as the plot tabs name it, and the well
                well_cb.addItem(f"{name} · {worst.replace('_', ' ')}" if worst else name, well)
            _select(well_cb, nwc.page_well(ctx))
            stats_view = view == "statistics"
            fill_control_combo(ctx, st, control_cb, on_statistics=stats_view, tip=_CONTROL_TIP)
            refresh_statistics_controls(st, statistics, stats_view)
            distribution = view == "distribution"
            wells_axis = distribution and (st.axis == "wells" or per_nanowell)
            compares = wells_axis or stats_view             # the views that compare wells at one timepoint
            array = view == "array"
            # Shown on every view that has a metric at all, and only greyed on the Array view when no layer draws
            # it: hidden, they came and went with the Layers menu and every control after them jumped sideways.
            by_metric = _metric_reaches_figure(st, view)
            metric_cb.setEnabled(by_metric)
            grp_metric.setVisible(True)
            grp_channel.setVisible(True)          # never hidden: a greyed control says why, an absent one cannot
            grp_baseline.setVisible(True)
            grp_axis.setVisible(distribution)
            cohort_box.setChecked(st.cohort)
            rim_focus.refresh(ctx)
            start.refresh(st)
            _refresh_array_controls(ctx, st, array, array_controls)
            refresh_identity_controls(st, identity, view == "identity")
            refresh_qc_controls(ctx, st, view == "qc", qc_controls)
            refresh_queue_controls(ctx, array, queue)
            # Only where values over time are drawn: a per-nanowell metric has one, and a one-well view is one time.
            # Trajectories draws Cell count in place of a per-nanowell metric (``resolve_metric``), and the cohort
            # applies to what it draws, so its box stays where it can be turned off.
            # The moving average is offered where values over time are drawn, and both it and the cohort also on
            # Statistics, which tests one timepoint's values as Distribution draws them: the cohort's choice of
            # nanowells, each value averaged over the window.
            over_time = (distribution and not per_nanowell) or view == "trajectories"
            over_time_or_tested = over_time or (stats_view and not per_nanowell)
            grp_cohort.setVisible(over_time_or_tested)
            moving_spin.setValue(max(st.moving, 1))
            grp_moving.setVisible(over_time_or_tested)
            _refresh_baseline_moving(baseline_moving_spin, st, nwc.drawn_metric_column(ctx))
            grp_baseline_moving.setVisible(over_time_or_tested)
            grp_control.setVisible(compares)
            column = nwc.drawn_metric_column(ctx)
            cut = st.thresholds.get(column) if column else None
            responding_edit.setText("" if cut is None else format_cut(cut))
            grp_responding.setVisible(distribution)
            grp_tp.setVisible((compares and not per_nanowell) or view == "position"
                              or view in RANKED_WELL_VIEWS or view in ONE_WELL_VIEWS)
            grp_well.setVisible(view in ONE_WELL_VIEWS or view in RANKED_WELL_VIEWS)
            exclusion.refresh(st, well_cb)
            extremes_spin.setValue(st.extremes_n)
            extremes_cells.setChecked(st.crop_cells)
            extremes_numbers.setChecked(st.crop_metrics)
            extremes_tps.refresh()
            _sync_crop_boxes(ctx, st)
            grp_extremes.setVisible(view in RANKED_WELL_VIEWS)
            variables.refresh(view)
        finally:
            for w, b in zip(controls, blocked):
                w.blockSignals(b)

    reg.register("_nw_show_detail", show_detail)
    reg.register("_nw_show_computing", show_computing)
    reg.register("_nw_page_visible", page_visible)
    reg.register("_nw_refresh_controls", refresh_controls)
    refresh_controls()


def timepoint_text(tp: str, times_h: dict) -> str:
    """How the timepoint control names *tp*: by its hours, as the Plotting tabs' control does (``24 h``), or by its
    own name when the acquisition record gives it no time."""
    hours = times_h.get(tp)
    return f"{hours:g} h" if isinstance(hours, (int, float)) and hours == hours else str(tp)


def _fill_timepoints(combo, tps, times_h: dict) -> None:
    """Fill the timepoint control with *tps* in time order, each named by its hours and carrying its own name as
    data (what the page's state holds) and as its tooltip."""
    combo.clear()
    for tp in tps:
        combo.addItem(timepoint_text(tp, times_h), tp)
        combo.setItemData(combo.count() - 1, f"Timepoint {tp}", Qt.ToolTipRole)
    combo.setEnabled(len(tps) > 1)


def _fill_baselines(combo, data, baseline: str) -> None:
    """The *Baseline* control: the two rules, then every timepoint of the dataset in time order, named as the
    timepoint control names them; *baseline* is selected. A named timepoint the dataset does not have (a restore
    onto another dataset) is listed too, marked, so the control shows what the figure is measured from."""
    combo.clear()
    for key, label in _BASELINES:
        combo.addItem(label, key)
    tps = list(data.timepoints()) if data is not None else []
    times_h = data.times_h if data is not None else {}
    for tp in tps:
        combo.addItem(timepoint_text(tp, times_h), baseline_at(tp))
        combo.setItemData(combo.count() - 1, f"Every fold change measured from timepoint {tp}", Qt.ToolTipRole)
    chosen = baseline_timepoint(baseline)
    if chosen is not None and chosen not in {str(tp) for tp in tps}:
        combo.addItem(f"{chosen} (not in this dataset)", baseline)
    _select(combo, baseline)


def _fill_channels(combo, data, channel) -> None:
    """The *Channel* control: every fluorescence channel with site intensities, then — greyed, with why as the
    tooltip — each channel the run read and did not measure in the nanowells. *channel* is the one drawn."""
    combo.clear()
    if data is None:
        return
    for name in data.channels:
        combo.addItem(name, name)
    for name, why in (getattr(data, "unmeasured_channels", None) or {}).items():
        combo.addItem(f"{name} (not measured)", name)
        item = combo.model().item(combo.count() - 1)
        item.setEnabled(False)
        item.setToolTip(why)
    if channel is not None:
        _select(combo, channel)
    combo.setToolTip(_CHANNEL_TIP if data.channels or getattr(data, "unmeasured_channels", None) else
                     _CHANNEL_TIP + "\n\nThis run measured no fluorescence channel in the nanowells.")


#: The image-channel controls, one setting (``NanowellState.image_channel``) offered beside the crop strip, on the
#: Extremes view and beside the Array view's Image.
_IMAGE_CHANNEL_COMBOS = ("_nw_strip_image_channel_cb", "_nw_extremes_image_channel_cb", "_nw_array_image_channel_cb",
                         "_nw_popout_image_channel_cb")
#: The crop checkboxes built outside the band (beside the strip, in the pop-out), each with the setting it shows:
#: ``refresh_controls`` puts every one that exists back on the state.
_CROP_BOXES = (("_nw_strip_cells_box", "crop_cells"), ("_nw_popout_cells_box", "crop_cells"),
               ("_nw_popout_metrics_box", "crop_metrics"), ("_nw_popout_labels_box", "popout_labels"))


def _sync_crop_boxes(ctx, st) -> None:
    for name, attr in _CROP_BOXES:
        box = ctx.widgets.get_or_none(name)
        if box is not None:
            blocked = box.blockSignals(True)
            box.setChecked(bool(getattr(st, attr)))
            box.blockSignals(blocked)


def _image_channel_combo(ctx, name: str) -> QComboBox:
    """One of the controls that choose the channel the page's pictures are of (:data:`_IMAGE_CHANNEL_COMBOS`)."""
    combo = QComboBox()
    combo.setToolTip(_IMAGE_CHANNEL_TIP)
    combo.currentIndexChanged.connect(lambda _i: nwc.set_image_channel(ctx, combo.currentData() or ""))
    ctx.widgets.register(name, combo)
    return combo


def _refresh_image_channels(ctx, st) -> None:
    """Every image-channel control filled with the phase image and the run's fluorescence channels, on the one
    chosen. Greyed, saying so, when the run read no fluorescence channel."""
    channels = list(getattr(st.data, "image_channels", None) or []) if st.data is not None else []
    phase = (getattr(st.data, "phase_token", "") or "") if st.data is not None else ""
    chosen = nwc.page_image_channel(st)
    for name in _IMAGE_CHANNEL_COMBOS:
        combo = ctx.widgets.get_or_none(name)
        if combo is None:
            continue
        blocked = combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem(f"Phase ({phase})" if phase else "Phase", "")
            for channel in channels:
                combo.addItem(channel, channel)
            _select(combo, chosen)
            combo.setEnabled(bool(channels))
            combo.setToolTip(_IMAGE_CHANNEL_TIP if channels else
                             _IMAGE_CHANNEL_TIP + "\n\nThe run read no fluorescence channel: phase is all there is.")
        finally:
            combo.blockSignals(blocked)


def _grey_metrics(metric_cb, view: str, channel, active_channel) -> None:
    """Grey each Metric entry *view* cannot draw, with the reason as its tooltip: a channel metric with no site
    intensities for the page's channel, and a metric with one value per nanowell on a view of values over time."""
    for i in range(metric_cb.count()):
        key = metric_cb.itemData(i)
        has_channel = not METRICS[key][1] or channel is not None
        over_time = not (view in _OVER_TIME_VIEWS and key in PER_NANOWELL)
        item = metric_cb.model().item(i)
        item.setEnabled(has_channel and over_time)
        item.setToolTip("" if has_channel and over_time else
                        "One value per nanowell: no fold change to follow over time." if has_channel else
                        f"No site intensities for the channel ({active_channel or 'none'}): this run measured no "
                        f"fluorescence in the nanowells.")


def _extremes_timepoints_button(ctx, parent) -> SimpleNamespace:
    """The Extremes view's **Timepoints** menu: which of the well's timepoints the montage shows as columns, each
    named by its hours as the Timepoint control names it. Fewer columns give larger crops. The ranking timepoint is
    always shown, so its entry is checked and greyed; *All timepoints* clears the choice."""
    button = QToolButton(parent)
    button.setToolButtonStyle(Qt.ToolButtonTextOnly)
    button.setPopupMode(QToolButton.InstantPopup)
    button.setToolTip(_EXTREMES_TPS_TIP)
    button.setProperty("variant", "secondary")
    button.setProperty("hasMenu", True)
    menu = QMenu(button)
    button.setMenu(menu)
    ctx.widgets.register("_nw_extremes_tps_btn", button)

    def state():
        st = ctx.state.feature_slice("nanowell")
        tps, ranked = nwc.shown_timepoint(ctx) if st is not None and st.data is not None else ([], "")
        return st, list(tps), ranked

    def toggle(tp: str, on: bool) -> None:
        st, tps, ranked = state()
        shown = set(nwc.extremes_columns(tps, ranked, st.extremes_tps))
        shown = shown | {tp} if on else shown - {tp}
        nwc.set_extremes_timepoints(ctx, [] if shown >= set(tps) else [t for t in tps if t in shown])

    def rebuild() -> None:
        st, tps, ranked = state()
        menu.clear()
        every = owned_action(menu, "All timepoints")
        every.triggered.connect(lambda _=False: nwc.set_extremes_timepoints(ctx, []))
        menu.addSeparator()
        shown = set(nwc.extremes_columns(tps, ranked, st.extremes_tps if st is not None else []))
        times = st.data.times_h if st is not None and st.data is not None else {}
        for tp in tps:
            action = owned_action(menu, timepoint_text(tp, times) + (" (ranked)" if tp == ranked else ""))
            action.setCheckable(True)
            action.setChecked(tp in shown)
            action.setEnabled(tp != ranked)
            action.setData(tp)
            action.toggled.connect(lambda on, tp=tp: toggle(tp, on))

    def refresh() -> None:
        st, tps, ranked = state()
        shown = nwc.extremes_columns(tps, ranked, st.extremes_tps if st is not None else [])
        button.setText("Timepoints: all" if len(shown) >= len(tps) else f"Timepoints: {len(shown)} of {len(tps)}")

    menu.aboutToShow.connect(rebuild)
    ctx.widgets.register("_nw_extremes_tps_menu", menu)
    ctx.widgets.register("_nw_extremes_tps_rebuild", rebuild)
    return SimpleNamespace(button=button, refresh=refresh)


def _cells_box(ctx, name: str) -> QCheckBox:
    """**Cells**: the counted cells drawn as dots on every crop the page shows — the strip, the pop-out and the
    Extremes montage — as the Array view's Cells layer draws them. One setting (``NanowellState.crop_cells``), offered
    beside the strip and on the Extremes view; ``refresh_controls`` keeps both boxes on it."""
    box = QCheckBox("Cells")
    box.setToolTip(_CROP_CELLS_TIP)
    box.setChecked(bool(ctx.state.feature_slice("nanowell").crop_cells))
    box.toggled.connect(lambda on: nwc.set_crop_cells(ctx, on))
    ctx.widgets.register(name, box)
    return box


def _detail_header(ctx, parent) -> QHBoxLayout:
    """The site detail's title row, with a close button (✕) that clears the nanowell selection — which is what hides
    the pane (``controller.redraw_detail`` shows it only while a selected nanowell has a series). Without it the only
    way to dismiss the pane was a click on empty space in the plot, which nothing on screen says."""
    from ui import theme

    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    title = QLabel("Selected nanowells", parent)
    title.setObjectName("Caption")
    close = QPushButton("✕", parent)
    close.setObjectName("Ghost")
    close.setFixedWidth(theme.Metrics.icon_btn_w)
    close.setToolTip("Close the site detail and clear the nanowell selection")
    close.clicked.connect(lambda _=False: nwc.clear_selection(ctx))
    ctx.widgets.register("_nw_detail_close_btn", close)
    row.addWidget(title)
    row.addStretch(1)
    row.addWidget(close)
    return row


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
    row.addWidget(_cells_box(ctx, "_nw_strip_cells_box"))
    row.addWidget(_image_channel_combo(ctx, "_nw_strip_image_channel_cb"))
    row.addWidget(build_display_range_button(ctx, "_nw_strip_range_btn"))
    row.addWidget(_go_to_button(ctx, "_nw_strip_go_to_btn", parent))
    row.addWidget(button)
    return row


def _go_to_button(ctx, name: str, parent) -> QPushButton:
    """**Go to nanowell**: the crops' nanowell on Identity ▸ Blink (``controller.go_to_site_in_blink``)."""
    button = QPushButton("Go to nanowell", parent)
    button.setProperty("variant", "secondary")
    button.setToolTip(_GO_TO_TIP)
    button.clicked.connect(lambda _=False: nwc.go_to_site_in_blink(ctx))
    ctx.widgets.register(name, button)
    return button


def _popout_window(ctx):
    """The pop-out crops window, built on first use and kept: a non-modal dialog owned by the app's window, so it
    stays above it and closes with it, holding one :class:`PlotCard` the controller draws the crops on."""
    window = ctx.widgets.get_or_none("_nw_popout")
    if window is not None:
        return window
    from widgets.plot_card import PlotCard

    window = _PopoutWindow(ctx.window)
    window.setWindowTitle("Nanowell crops")
    layout = QVBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addLayout(_popout_controls(ctx, window))
    card = PlotCard(window, figsize=(_FIG_W, _FIG_H * 0.8), constrained=False, header=False)
    card.setFigureTitle("")
    layout.addWidget(card)
    window.resize(900, 640)
    ctx.widgets.register("_nw_popout", window)
    ctx.widgets.register("_nw_popout_fig", card.figure)
    ctx.widgets.register("_nw_popout_canvas", card.canvas)
    card.plotThemeChanged.connect(lambda _m: nwc.draw_popout(ctx))
    refresh = ctx.widgets.get_or_none("_nw_refresh_controls")
    if callable(refresh):
        refresh()                                        # the new controls start on the page's settings
    return window


def _popout_controls(ctx, window) -> QHBoxLayout:
    """The pop-out's own row of controls, so its crops are set where they are looked at: the image channel, the
    display range, the counted cells, the numbers and the labels on the crops, and **Go to nanowell**. Each is the
    page's one setting (the strip and the Extremes view show the same), kept in step by ``refresh_controls``."""
    row = QHBoxLayout()
    row.addWidget(_image_channel_combo(ctx, "_nw_popout_image_channel_cb"))
    row.addWidget(build_display_range_button(ctx, "_nw_popout_range_btn"))
    row.addWidget(_cells_box(ctx, "_nw_popout_cells_box"))
    metrics = QCheckBox("Metrics", window)
    metrics.setToolTip(_POPOUT_METRICS_TIP)
    metrics.toggled.connect(lambda on: nwc.set_crop_metrics(ctx, on))
    ctx.widgets.register("_nw_popout_metrics_box", metrics)
    labels = QCheckBox("Labels", window)
    labels.setToolTip(_POPOUT_LABELS_TIP)
    labels.toggled.connect(lambda on: nwc.set_popout_labels(ctx, on))
    ctx.widgets.register("_nw_popout_labels_box", labels)
    row.addWidget(metrics)
    row.addWidget(labels)
    row.addStretch(1)
    row.addWidget(_go_to_button(ctx, "_nw_popout_go_to_btn", window))
    return row


class _PopoutWindow(QDialog):
    """The pop-out's window: a non-modal ``QDialog`` owned by the app's window, as every other free-standing window in
    Review is (the per-sample control editor, the focus curve, the gating optimizer). A dialog is a transient window
    of its parent, so it stays above it. A plain ``QWidget`` with the ``Qt.Window`` flag carries no such owner on
    Windows, and there the window dropped behind the app the first time its **Cells** box was toggled."""

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setModal(False)
        # The title bar's minimise and maximise buttons. ``Qt.Dialog`` already holds the ``Qt.Window`` bit, so setting
        # that flag changed nothing and a dialog's title bar has neither: the hints themselves are what add them.
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinMaxButtonsHint)

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
    nanowells' fill or their written values, so not when neither is drawn; never on the Identity view, which draws
    the identity check's distances and the montages, no metric."""
    if view in ("identity", "qc"):
        return False
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
    grp_image = band.add_scope("Image", image_cb, _image_channel_combo(ctx, "_nw_array_image_channel_cb"),
                               build_display_range_button(ctx, "_nw_array_range_btn"))

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
    overlays_btn.setProperty("variant", "toggle")         # "secondary" paints checked and unchecked the same
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
    anchor_btn.setProperty("variant", "toggle")         # "secondary" paints checked and unchecked the same
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
