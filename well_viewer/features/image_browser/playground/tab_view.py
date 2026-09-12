"""Playground tab — Qt builder.

``build_playground_tab(ctx, parent)`` assembles the single-frame correction
testbed: a zoomable canvas with its controls in a right-hand dock (frame
selectors, correction method + params, display options). The dock takes the
same fill, hairline and width as the Properties sidebar, and sits on the same
side, so every per-tab inspector in the pane reads as one kind of panel rather
than two. Per ARCHITECTURE.md §5.3 it reads
the app handle directly (no ``app =`` unwrap) and wires every signal to the
controller via ``functools.partial`` — no new ``runtime_app`` proxies.
"""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from ui import theme
from pipeline.stardist import NMS_THRESH_HELP, PROB_THRESH_HELP
from process_microscopy import BACKGROUND_METHODS, FLATFIELD_MODES
from widgets.info_icon import info_icon
from widgets.page_body import build_page_body
from widgets.separator import build_hline_separator
from well_viewer.features.image_browser.playground.controller import (
    correction_controls_enabled, playground_clear_loose_image, playground_on_change,
    playground_open_loose_image, playground_repopulate_dropdowns,
    playground_segment_now,
)
from well_viewer.features.image_browser.playground.renderer import render_playground_display

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_LMAX_W = 56
_LMIN_W = 56
_LUT_AUTO_W = 54


def _row(parent, label_text: str, *, tooltip: str = "") -> QWidget:
    """A two-column label+widget row; returns the right-hand cell.

    *tooltip* lands on the row **and its label**, not only on what the caller
    puts in the cell — the label is the word being read and the bigger target,
    and an explanation that answers only over a narrow entry box is one most
    people never find.
    """
    row = QWidget(parent)
    rl = QHBoxLayout(row)
    rl.setContentsMargins(0, 1, 0, 1)
    rl.setSpacing(6)
    lbl = QLabel(label_text, row)
    lbl.setObjectName("Muted")
    lbl.setFixedWidth(theme.Metrics.stepper_w)
    if tooltip:
        row.setToolTip(tooltip)
        lbl.setToolTip(tooltip)
    rl.addWidget(lbl)
    cell = QWidget(row)
    QHBoxLayout(cell).setContentsMargins(0, 0, 0, 0)
    rl.addWidget(cell, 1)
    parent.layout().addWidget(row)
    return cell


def build_playground_tab(ctx, parent: QWidget) -> None:
    # The Playground is an un-migrated dev/debug builder that still stamps its
    # widgets onto the app and drives an app-native on_change. It reaches that
    # app instance through the ``window`` handle (the QMainWindow *is* the app)
    # until it gets its own ctx migration; the generic app escape hatch is gone.
    app = ctx.window
    # The band is empty on this tab — every Playground control lives in the
    # right-hand dock — but it is added anyway so this page's top edge is drawn
    # level with every other page's (widgets/page_body.py). The body is a row:
    # the canvas, then the dock.
    _band, body = build_page_body(parent, content_margins=(12, 12, 12),
                                  horizontal=True)
    on_change = partial(playground_on_change, ctx)

    # ── The controls dock (mounted on the right, below) ──────────────────────
    # Dressed as a right-hand inspector, matching ``ExportStyleSidebar``: the
    # rail fill, a hairline down its left edge, and the shared dock width.
    controls = QWidget(parent)
    controls.setObjectName("PropertyPanel")
    controls.setAttribute(Qt.WA_StyledBackground, True)
    controls.setStyleSheet(
        f"#PropertyPanel {{ background-color: {theme.Colors.rail};"
        f" border-left: 1px solid {theme.Colors.border_subtle}; }}"
    )
    controls.setFixedWidth(theme.Metrics.dock_w)
    cl = QVBoxLayout(controls)
    cl.setContentsMargins(10, 10, 10, 10)
    cl.setSpacing(4)

    def _section(title: str) -> None:
        lbl = QLabel(title, controls)
        lbl.setObjectName("SectionTitle")
        cl.addWidget(lbl)
        cl.addWidget(build_hline_separator(controls))

    def _combo(cell, items, attr: str) -> QComboBox:
        cb = QComboBox(cell)
        if items:
            cb.addItems(list(items))
        cb.currentIndexChanged.connect(lambda _i: on_change())
        cell.layout().addWidget(cb)
        setattr(app, attr, cb)
        return cb

    def _edit(cell, default: str, attr: str, width: int = 70) -> QLineEdit:
        e = QLineEdit(default, cell)
        e.setFixedWidth(width)
        e.editingFinished.connect(on_change)
        cell.layout().addWidget(e)
        cell.layout().addStretch(1)
        setattr(app, attr, e)
        return e

    # Frame selection
    _section("Frame")
    # The frame's well is chosen on the ever-present sidebar plate (single-well
    # on this tab — see the Playground descriptor's ``single_well_selection`` +
    # ``on_selection_changed`` hook in ``features/registry.py``). No in-tab plate:
    # the one on the left rail is the picker, so we don't duplicate it here.
    chan_cb = _combo(_row(controls, "Channel"), [], "_playground_chan_cb")
    tp_cb = _combo(_row(controls, "Timepoint"), [], "_playground_tp_cb")
    fov_cb = _combo(_row(controls, "FOV"), [], "_playground_fov_cb")

    # A second frame source: any image file on disk. The four selectors above
    # address a loaded dataset, so without this the tab — and the StarDist
    # preview below — is unusable until a plate has been analysed, which is
    # backwards for a tool whose job is choosing the settings to analyse it with.
    file_row = QWidget(controls)
    frl = QHBoxLayout(file_row)
    frl.setContentsMargins(0, 2, 0, 2)
    open_btn = QPushButton("Open image…", file_row)
    open_btn.setProperty("variant", "secondary")
    open_btn.setToolTip(
        "Load a single image file straight from disk, instead of a frame from "
        "the loaded dataset. Works with no dataset open at all — the intended "
        "way to tune segmentation before running a plate.")
    clear_btn = QPushButton("Use dataset", file_row)
    clear_btn.setProperty("variant", "secondary")
    clear_btn.setToolTip("Go back to the frame selected above.")
    frl.addWidget(open_btn)
    frl.addWidget(clear_btn)
    frl.addStretch(1)
    cl.addWidget(file_row)

    def _sync_frame_source() -> None:
        """Grey the dataset selectors while a loose file is the frame."""
        loose = bool(str(app._app_state.playground.loose_image_path or "").strip())
        for cb in (chan_cb, tp_cb, fov_cb):
            cb.setEnabled(not loose)
        clear_btn.setEnabled(loose)

    def _on_open_image() -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _filt = QFileDialog.getOpenFileName(
            controls, "Open an image to work on", "",
            "Images (*.tif *.tiff *.png *.jpg *.jpeg);;All files (*)")
        if not path:
            return
        playground_open_loose_image(ctx, path)
        _sync_frame_source()

    def _on_clear_image() -> None:
        playground_clear_loose_image(ctx)
        _sync_frame_source()
    open_btn.clicked.connect(lambda _=False: _on_open_image())
    clear_btn.clicked.connect(lambda _=False: _on_clear_image())

    # Correction method
    _section("Correction")
    # This control is the master switch for everything below it: on Raw the
    # controller returns before any correction runs, and on Flat-field only the
    # background half is skipped. It used to live in the Display section two
    # groups down, dressed as a view option, so the controls it silently
    # disabled looked broken instead of inactive. It heads this section now, and
    # _sync_method_rows greys out whatever the chosen stage does not consult.
    #
    # It is labelled "Correct up to", not "Stage", and carries a caption saying
    # what it governs. "Stage" names a step of the pipeline — true, but it
    # describes the value rather than the effect, and the effect (half this
    # panel does nothing) is the part a reader needs before hovering anything.
    # (Its signal is connected further down, where the per-stage LUT memory that
    # the handler needs is defined.)
    from widgets.segmented_control import SegmentedControl
    stage_row = _row(controls, "Correct up to")
    source_seg = SegmentedControl(stage_row)
    for _lbl, _data in (("Raw", "raw"), ("Flat-field only", "flatfield"),
                        ("Corrected", "corrected")):
        source_seg.addSegment(_lbl, data=_data)
    source_seg.setCurrentByData("corrected")
    source_seg.setToolTip(
        "Which pipeline stage is computed and shown. The controls below apply "
        "only to the stage selected here:\n"
        "• Raw — the unprocessed frame; no correction runs, so nothing below "
        "has any effect.\n"
        "• Flat-field only — the flat-field is applied; the background method "
        "and its parameters are not used.\n"
        "• Corrected — flat-field + background, what a pipeline run produces.")
    stage_row.layout().addWidget(source_seg)
    stage_row.layout().addStretch(1)
    app._playground_source_seg = source_seg
    stage_note = QLabel("decides which controls below run — Raw runs none",
                        controls)
    stage_note.setObjectName("Muted")
    stage_note.setWordWrap(True)
    cl.addWidget(stage_note)

    ff_cb = _combo(_row(controls, "Flat-field"), FLATFIELD_MODES, "_playground_ff_cb")
    bg_cb = _combo(_row(controls, "Background"), BACKGROUND_METHODS, "_playground_bg_cb")
    bg_cb.setCurrentText("sliding_disk")

    radius_row = _row(controls, "Radius")
    _edit(radius_row, "50", "_playground_radius_edit")
    fft_row = _row(controls, "Small px")
    _edit(fft_row, "0", "_playground_fft_small_edit")
    mesh_ratio_row = _row(controls, "Mesh ratio")
    _edit(mesh_ratio_row, "3.0", "_playground_mesh_ratio_edit")
    mesh_sigma_row = _row(controls, "Mesh sigma")
    _edit(mesh_sigma_row, "3.0", "_playground_mesh_sigma_edit")
    bgwell_row = _row(controls, "Bg well")
    bgwell_edit = QLineEdit("", bgwell_row)
    bgwell_edit.setPlaceholderText("e.g. H12")
    bgwell_edit.editingFinished.connect(on_change)
    bgwell_row.layout().addWidget(bgwell_edit)
    app._playground_bgwell_edit = bgwell_edit

    # Answers "when do things get executed?" on the panel instead of leaving it
    # to be inferred: the two halves of this tab run on completely different
    # triggers.
    run_note = QLabel("Correction re-runs as you change these. Segmentation runs "
                      "only when you press Segment.", controls)
    run_note.setObjectName("Muted")
    run_note.setWordWrap(True)
    cl.addWidget(run_note)

    # Live StarDist preview — the same segmentation a pipeline run performs,
    # against the frame on screen, so the two thresholds can be chosen by
    # looking at the result instead of by re-running a plate.
    _section("Segmentation")
    seg_live_cb = QCheckBox("Live StarDist", controls)
    seg_live_cb.setToolTip(
        "Show StarDist labels computed here, instead of the ones a pipeline run "
        "already produced.\n"
        "The first Segment of a session loads TensorFlow into the application "
        "(~10-30 s, and roughly 1-2 GB that stays in use until you quit); after "
        "that each frame takes a few seconds.")

    def _on_seg_live(v: bool) -> None:
        app._app_state.playground.seg_live = bool(v)
        on_change()
    seg_live_cb.toggled.connect(_on_seg_live)
    cl.addWidget(seg_live_cb)
    app._playground_seg_live_cb = seg_live_cb

    # One help text per threshold, defined in pipeline.stardist and shown by both
    # panels that expose these two numbers — the two used to keep private copies
    # and had already drifted into describing the same knobs differently. Applied
    # to the whole row, to the entry, and to an ⓘ marker, so the explanation is
    # both findable and reachable from anywhere on the line.
    prob_row = _row(controls, "Probability", tooltip=PROB_THRESH_HELP)
    prob_edit = _edit(prob_row, "", "_playground_seg_prob_edit")
    prob_edit.setPlaceholderText("auto")
    prob_edit.setToolTip(PROB_THRESH_HELP)
    prob_row.layout().addWidget(info_icon(prob_row, PROB_THRESH_HELP))
    nms_row = _row(controls, "Overlap (NMS)", tooltip=NMS_THRESH_HELP)
    nms_edit = _edit(nms_row, "", "_playground_seg_nms_edit")
    nms_edit.setPlaceholderText("auto")
    nms_edit.setToolTip(NMS_THRESH_HELP)
    nms_row.layout().addWidget(info_icon(nms_row, NMS_THRESH_HELP))

    seg_btn_row = QWidget(controls)
    sbl = QHBoxLayout(seg_btn_row)
    sbl.setContentsMargins(0, 2, 0, 2)
    seg_btn = QPushButton("Segment", seg_btn_row)
    seg_btn.setToolTip("Run StarDist on the frame currently displayed.")
    hint = QLabel("Tuned here, then run the plate: type the same two values into "
                  "Analyze → Channel Tokens → Nucleus probability / Overlap.",
                  controls)
    hint.setObjectName("Muted")
    hint.setWordWrap(True)

    def _on_segment() -> None:
        seg_live_cb.setChecked(True)
        playground_segment_now(ctx)
    seg_btn.clicked.connect(lambda _=False: _on_segment())
    sbl.addWidget(seg_btn)
    sbl.addStretch(1)
    cl.addWidget(seg_btn_row)

    # The count, directly under the controls that change it — this is the number
    # the two thresholds are being tuned against, and reading it out of the
    # paragraph read-out below is not the same as seeing it. Written by
    # ``controller._count_text`` from the facts of the render that just ran, so
    # it can never describe a different picture than the one on screen.
    count = QLabel("Cells: —", controls)
    count.setWordWrap(True)
    count.setToolTip(
        "How many labels the current render actually drew, and — in binary mode "
        "with a usable mask — how many of them clear the threshold.\n"
        "Reads “—” rather than 0 when there is no count to give: no mask at all, "
        "or live labels that no longer describe this frame.")
    cl.addWidget(count)
    ctx.widgets.register("_playground_count", count)

    cl.addWidget(hint)

    # Display options
    _section("Display")
    lut_row = _row(controls, "LUT min/max")
    lmin = QLineEdit("auto", lut_row)
    lmin.setFixedWidth(_LMIN_W)
    lmax = QLineEdit("auto", lut_row)
    lmax.setFixedWidth(_LMAX_W)
    app._playground_lmin_entry = lmin
    app._playground_lmax_entry = lmax

    def _set_lut_entries(lo_txt: str, hi_txt: str) -> None:
        for e, t in ((lmin, lo_txt), (lmax, hi_txt)):
            blocked = e.blockSignals(True)
            try:
                e.setText(t)
            finally:
                e.blockSignals(blocked)

    def _remember_lut() -> None:
        st = app._app_state.playground
        st.lut_by_source[st.source] = (lmin.text(), lmax.text())

    def _on_source(_i: int) -> None:
        st = app._app_state.playground
        new_source = source_seg.currentData() or "corrected"
        if new_source != st.source:
            _remember_lut()                       # save the stage we're leaving
            st.source = new_source
            lo_txt, hi_txt = st.lut_by_source.get(new_source, ("auto", "auto"))
            _set_lut_entries(lo_txt, hi_txt)       # restore this stage's LUT
        _sync_method_rows()                        # grey what this stage ignores
        on_change()
    source_seg.currentChanged.connect(_on_source)

    def _on_lut_edit() -> None:
        _remember_lut()
        on_change()
    lmin.editingFinished.connect(_on_lut_edit)
    lmax.editingFinished.connect(_on_lut_edit)
    lut_auto = QPushButton("Auto", lut_row)
    lut_auto.setFixedWidth(_LUT_AUTO_W)   # 46 clipped "Auto" to "Autc" under the button padding
    lut_auto.setToolTip("Reset the LUT min/max to the displayed frame's range.")

    def _on_lut_auto() -> None:
        _set_lut_entries("auto", "auto")
        _remember_lut()
        on_change()
    lut_auto.clicked.connect(lambda _=False: _on_lut_auto())
    lut_row.layout().addWidget(lmin)
    lut_row.layout().addWidget(lmax)
    lut_row.layout().addWidget(lut_auto)
    lut_row.layout().addStretch(1)

    cmap_row = _row(controls, "Colormap")
    from widgets.lut_selector import LutSelector
    cmap = LutSelector(cmap_row, lut="Greys", reversed=True, default="Greys")
    cmap.lutChanged.connect(lambda *_a: on_change())
    cmap_row.layout().addWidget(cmap)
    app._playground_cmap_cb = cmap

    # Frame-local binary threshold — drives binary mode; defaults to the
    # channel's app-wide ThreshFracOn but editing it here never writes back.
    from widgets.stepper import Stepper
    thr_row = _row(controls, "Threshold")
    thr_stepper = Stepper(value=50.0, minimum=0.0, maximum=1e9, single_step=1.0,
                          decimals=1)
    thr_stepper.setToolTip(
        "Binary-mode threshold for THIS frame (cells whose mean corrected "
        "intensity exceeds it are shown). Local to the Playground — does not "
        "change the app-wide ThreshFracOn.")

    def _on_threshold(v: float) -> None:
        app._app_state.playground.threshold = float(v)
        on_change()
    thr_stepper.valueChanged.connect(_on_threshold)
    thr_row.layout().addWidget(thr_stepper)
    thr_row.layout().addStretch(1)
    app._playground_threshold_stepper = thr_stepper

    # QC size-based cleanup (px², 0 = no bound) — previews the pipeline's debris
    # gate by dropping out-of-range mask labels from the overlay / binary.
    qc_min_row = _row(controls, "QC min area")
    _edit(qc_min_row, "0", "_playground_qc_min_area_edit")
    qc_max_row = _row(controls, "QC max area")
    _edit(qc_max_row, "0", "_playground_qc_max_area_edit")

    toggles = QWidget(controls)
    tl = QHBoxLayout(toggles)
    tl.setContentsMargins(0, 2, 0, 2)
    binary_cb = QCheckBox("Binary", toggles)
    binary_cb.setToolTip(
        "Fill the cells the mask found, on black, instead of showing the image.")
    colors_cb = QCheckBox("Colour cells", toggles)
    colors_cb.setToolTip(
        "In Binary mode, give every label its own colour instead of painting "
        "them all white — two nuclei that touch are otherwise one white shape, "
        "and whether the segmentation split them is the thing you are looking "
        "for.\n"
        "The colour is a fixed function of the label id, so it does not change "
        "between redraws. It has no effect where there is no usable mask: that "
        "fallback thresholds pixels, and pixels have no labels to colour.")
    colors_cb.setChecked(True)
    overlay_cb = QCheckBox("Outlines", toggles)
    overlay_cb.setToolTip(
        "Draw cell outlines over the image. Which mask they come from is named "
        "in the status line:\n"
        "• pipeline mask — the mask a run already produced. It does NOT respond "
        "to the Correction controls, because changing a correction does not "
        "re-segment.\n"
        "• live StarDist — labels this tab computed; press Segment to refresh "
        "them after a change.")
    overlay_cb.setChecked(True)

    def _on_binary(v: bool) -> None:
        app._app_state.playground.binary_mask = bool(v)
        on_change()

    def _on_colors(v: bool) -> None:
        app._app_state.playground.binary_label_colors = bool(v)
        on_change()

    def _on_overlay(v: bool) -> None:
        app._app_state.playground.show_overlay = bool(v)
        on_change()
    binary_cb.toggled.connect(_on_binary)
    colors_cb.toggled.connect(_on_colors)
    overlay_cb.toggled.connect(_on_overlay)
    tl.addWidget(binary_cb)
    tl.addWidget(colors_cb)
    tl.addWidget(overlay_cb)
    tl.addStretch(1)
    cl.addWidget(toggles)
    app._playground_binary_cb = binary_cb
    ctx.widgets.register("_playground_binary_colors_cb", colors_cb)
    app._playground_overlay_cb = overlay_cb

    # Zoom buttons
    zoom_row = QWidget(controls)
    zl = QHBoxLayout(zoom_row)
    zl.setContentsMargins(0, 2, 0, 2)
    zl.addWidget(QLabel("Zoom", zoom_row))

    def _zoom(delta: float) -> None:
        st = app._app_state.playground
        st.zoom = max(0.1, min(8.0, st.zoom * delta))
        render_playground_display(app)
    zout = QPushButton("−", zoom_row)
    zout.setFixedWidth(theme.Metrics.icon_btn_w)
    zout.clicked.connect(lambda _=False: _zoom(1 / 1.25))
    zin = QPushButton("+", zoom_row)
    zin.setFixedWidth(theme.Metrics.icon_btn_w)
    zin.clicked.connect(lambda _=False: _zoom(1.25))
    zfit = QPushButton("Fit", zoom_row)
    zfit.clicked.connect(lambda _=False: (setattr(app._app_state.playground, "zoom", 1.0),
                                          render_playground_display(app)))
    zl.addWidget(zout)
    zl.addWidget(zin)
    zl.addWidget(zfit)
    zl.addStretch(1)
    cl.addWidget(zoom_row)

    # The read-out. Everything this tab can be ambiguous about is stated here in
    # words, because none of it is visible in the picture: which mask the
    # outlines came from and whether it even fits the frame, how many labels the
    # QC filter threw away, which of binary mode's three rules drew what is on
    # screen, and the data range (the LUT auto-scales, so a correction can move
    # the data without moving the image). Selectable so a line can be pasted
    # into a bug report.
    _section("What you're looking at")
    status = QLabel("Select a frame to begin.", controls)
    status.setObjectName("Muted")
    status.setWordWrap(True)
    status.setTextInteractionFlags(Qt.TextSelectableByMouse)
    cl.addWidget(status)
    app._playground_status = status
    cl.addStretch(1)

    # ── The canvas, then the dock — the dock is on the right ─────────────────
    canvas = QScrollArea(body)
    canvas.setWidgetResizable(False)
    canvas.setAlignment(Qt.AlignCenter)
    label = QLabel(canvas)
    label.setAlignment(Qt.AlignCenter)
    label.setMouseTracking(True)
    canvas.setWidget(label)
    body.layout().addWidget(canvas, 1)
    body.layout().addWidget(controls)
    app._playground_image_canvas = canvas
    app._playground_image_label = label

    # Auto-fit: re-blit (fit-to-viewport × zoom) whenever the canvas resizes —
    # including the first real layout pass, so the image isn't stuck tiny.
    _orig_canvas_resize = canvas.resizeEvent

    def _on_canvas_resize(ev) -> None:
        _orig_canvas_resize(ev)
        render_playground_display(app)
    canvas.resizeEvent = _on_canvas_resize

    # Click-drag to pan; hover (no button) shows the pixel intensity tooltip.
    from well_viewer.features.image_browser.playground.controller import (
        playground_hover, playground_pan_move, playground_pan_press,
    )

    def _on_press(ev) -> None:
        playground_pan_press(ctx, ev)

    def _on_move(ev) -> None:
        from PySide6.QtCore import Qt as _Qt
        if ev.buttons() & _Qt.LeftButton:
            playground_pan_move(ctx, ev)
        else:
            playground_hover(ctx, ev)
    label.mousePressEvent = _on_press
    label.mouseMoveEvent = _on_move

    # Show/hide param rows by method (mirrors analyze.tab._sync_bg), and grey out
    # whatever the selected STAGE does not consult. Visibility answers "does this
    # method take this parameter"; enablement answers "does this stage read it at
    # all" — without the second one, a stage that ignores a control leaves it
    # looking live, which is what made the panel read as broken.
    def _sync_method_rows(_=None) -> None:
        bg = app._playground_bg_cb.currentText()
        ff = app._playground_ff_cb.currentText()
        radius_row.parentWidget().setVisible(
            bg in ("top_hat", "sliding_disk", "robust_mesh", "fft_bandpass"))
        fft_row.parentWidget().setVisible(bg == "fft_bandpass")
        for r in (mesh_ratio_row, mesh_sigma_row):
            r.parentWidget().setVisible(bg == "robust_mesh")
        bgwell_row.parentWidget().setVisible(
            ff in ("empty_well", "basic") or bg == "empty_well")

        enabled = correction_controls_enabled(app._app_state.playground.source)
        ff_cb.setEnabled(enabled["flatfield"])
        bg_cb.setEnabled(enabled["background"])
        for r in (radius_row, fft_row, mesh_ratio_row, mesh_sigma_row):
            r.parentWidget().setEnabled(enabled["background_params"])
        bgwell_row.parentWidget().setEnabled(enabled["background_well"])
    bg_cb.currentIndexChanged.connect(_sync_method_rows)
    ff_cb.currentIndexChanged.connect(_sync_method_rows)
    _sync_method_rows()
    _sync_frame_source()

    playground_repopulate_dropdowns(app._ctx)
    # No trailing on_change() here: build_playground_tab is only ever reached
    # through image_browser.controller.on_image_browser_subtab_changed, which
    # always follows with registry._activate_playground -> playground_activate
    # -> its own on_change() call. A second call here just repeated the same
    # (expensive, uncached) correction pass back to back on every first open.
