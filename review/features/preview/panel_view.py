"""Review-image (Segmentation tab) view builder (Qt port).

The Movie Montage tab this module used to also build was folded into the
Image Table tab; its orphaned ``build_right_panel`` builder (and the montage
top-hat-subtraction controls it hosted) has been removed.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QScrollArea,
    QToolTip, QVBoxLayout, QWidget,
)

from review.core.ui_helpers import (
    add_menu_action, btn_card, btn_secondary,
    make_action_menu_button, make_popover_button,
)
from widgets.control_band import ControlBand
from review.features.preview.crop_scope_view import (
    build_crop_scope_control,
)
from review.features.preview.delete_scope_view import (
    build_delete_scope_control,
)
from review.features.crop.controller import (
    clear_crop_region, reset_crop_and_overrides, toggle_crop_edit_mode,
)
from widgets.stepper import Stepper
from widgets.labeled_combo import LabeledComboBox
from functools import partial
from core.safe import swallowed
from review.core.lazy import lazy as _lazy
from ui import theme

# Height of the colour swatch chip beside a control (its width is the
# shared theme.Metrics.swatch_w).
_SWATCH_H = 18
# This panel builds the Image Browser review-image canvas, so it is only ever
# imported by image_browser/controller; bind its review_image delegates lazily
# so importing this module never imports image_browser (keeps it deletable).
_RI_CTRL = "review.features.image_browser.review_image.controller"
_RI_RENDER = "review.features.image_browser.review_image.renderer"
on_review_image_channel_selected = _lazy(_RI_CTRL, "on_review_image_channel_selected")
on_review_image_move = _lazy(_RI_CTRL, "on_review_image_move")
on_review_image_press = _lazy(_RI_CTRL, "on_review_image_press")
on_review_image_release = _lazy(_RI_CTRL, "on_review_image_release")
pick_review_image_color = _lazy(_RI_CTRL, "pick_review_image_color")
refresh_review_image_ctx = _lazy(_RI_RENDER, "refresh_review_image_ctx")
render_review_image_display = _lazy(_RI_RENDER, "render_review_image_display")
# ctx-native review-image callbacks the builder wires to Qt signals (A8 teardown:
# the whole callback web is ctx now, so the builder names no god-object).
apply_review_image_cursor = _lazy(_RI_RENDER, "apply_review_image_cursor")
review_image_auto_lut = _lazy(_RI_RENDER, "review_image_auto_lut")
review_image_commit_lut = _lazy(_RI_RENDER, "review_image_commit_lut")
review_image_refresh_color_swatch = _lazy(_RI_RENDER, "review_image_refresh_color_swatch")
review_image_zoom_fit = _lazy(_RI_RENDER, "review_image_zoom_fit")
review_image_zoom_step = _lazy(_RI_RENDER, "review_image_zoom_step")
reset_review_image_colors = _lazy(_RI_RENDER, "reset_review_image_colors")
set_review_image_source = _lazy(_RI_RENDER, "set_review_image_source")
toggle_review_image_binary_mask = _lazy(_RI_RENDER, "toggle_review_image_binary_mask")
refresh_review_image_binary_btn = _lazy(_RI_RENDER, "refresh_review_image_binary_btn")
toggle_review_image_outline = _lazy(_RI_RENDER, "toggle_review_image_outline")
refresh_review_image_outline_btn = _lazy(_RI_RENDER, "refresh_review_image_outline_btn")
toggle_selected_review_cell = _lazy(_RI_RENDER, "toggle_selected_review_cell")
set_on_cutoff = _lazy("review.core.channel_state_controller", "set_on_cutoff")


def build_review_image_panel(ctx, parent: QWidget) -> None:
    # A8 teardown: fully ctx-native builder. Widgets that the render pipeline
    # reads back go through ``ctx.widgets`` (which stamps ``app._<name>`` so
    # app-native readers are unaffected); the live scratch handles the callbacks
    # need (buttons, canvas, label, swatch dict) go through
    # ``ctx.runtime.review_image`` (1:1 with the legacy ``app._review_image_*``
    # stamps). No god-object is named here.
    reg = ctx.widgets
    rt = ctx.runtime.review_image
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)

    inner = QWidget(parent)
    il = QVBoxLayout(inner)
    il.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(inner, 1)

    ctrl = ControlBand(inner)
    ctrl.add_scope("", reg.register("_review_image_well_lbl",
                                    QLabel("No well selected")))

    chan_cb = reg.register("_review_image_chan_cb", QComboBox())
    chan_cb.addItems(["GFP"])
    chan_cb.currentIndexChanged.connect(
        lambda _i: on_review_image_channel_selected(ctx, None)
    )
    ctrl.add_scope("Channel", chan_cb)
    # ``_review_image_chan_var`` is a legacy alias of the same combo; register it
    # under both names so ctx.widgets resolves either.
    reg.register("_review_image_chan_var", chan_cb)

    fov_menu = reg.register("_review_image_fov_menu", LabeledComboBox("FOV"))
    fov_menu.addItems(["—"])
    fov_menu.currentIndexChanged.connect(
        lambda _i: refresh_review_image_ctx(ctx)
    )
    ctrl.add_scope("", fov_menu)

    tp_menu = reg.register("_review_image_tp_menu", LabeledComboBox("Timepoint"))
    tp_menu.addItems(["—"])
    tp_menu.currentIndexChanged.connect(
        lambda _i: refresh_review_image_ctx(ctx)
    )
    ctrl.add_scope("", tp_menu)
    rt.tp_var = tp_menu
    rt.tp_cb = tp_menu  # alias for review_image_controller

    # ── Zoom — inline on the control row ────────────────────────────────
    # Zoom is a constant-use control while reviewing segmentation, so it sits on
    # the strip itself rather than inside a popover. The readout shows the
    # current factor relative to fit-to-window (100% = Fit) and tracks every
    # zoom path — buttons, scroll wheel, and the jump-to-nucleus zoom the
    # Review-CSV double-click performs — because the renderer refreshes it on
    # each display pass. The four go in as ONE scope group, so a wrap can never
    # split the readout from the buttons that drive it. No "Zoom:" caption —
    # the readout reads "100%", which says what the buttons either side do.
    zoom_out_btn = btn_card(ctrl, "−", lambda: review_image_zoom_step(ctx, -1))
    zoom_out_btn.setToolTip("Zoom out (scroll wheel works on the image too)")
    zoom_lbl = reg.register("_review_image_zoom_lbl", QLabel("100%"))
    zoom_lbl.setObjectName("Muted")
    zoom_lbl.setToolTip("Current zoom, relative to fit-to-window (100% = Fit)")
    zoom_in_btn = btn_card(ctrl, "+", lambda: review_image_zoom_step(ctx, +1))
    zoom_in_btn.setToolTip("Zoom in (scroll wheel works on the image too)")
    zoom_fit_btn = btn_secondary(ctrl, "Fit", lambda: review_image_zoom_fit(ctx))
    zoom_fit_btn.setToolTip("Fit the whole image in the view (100%) and recentre")
    ctrl.add_scope("", zoom_out_btn, zoom_lbl, zoom_in_btn, zoom_fit_btn)

    from PySide6.QtWidgets import QPushButton
    delete_btn = QPushButton("Delete Cell", ctrl)
    rt.delete_btn = delete_btn
    delete_btn.setProperty("variant", "toggle")
    delete_btn.setCheckable(True)
    delete_btn.setChecked(False)
    delete_btn.setToolTip(
        "Enter drag-delete mode.\n"
        "• Click a cell to mark it Included=0.\n"
        "• Drag a rectangle to bulk-delete all cells inside.\n"
        "By default the delete lands on the displayed frame only; the "
        "Delete scope control widens it to more wells / FOVs / timepoints."
    )
    delete_btn.clicked.connect(
        lambda _=False: toggle_selected_review_cell(ctx)
    )
    ctrl.add_action(delete_btn)

    # Which frames the next drag-delete reaches. Sits next to the button it
    # modifies, and carries the armed scope in its own label.
    ctrl.add_action(build_delete_scope_control(ctx, ctrl))

    # Crop cells: drag a rectangle to keep only cells whose centroid is inside.
    # Every FOV/timepoint/channel/well by default; the Crop scope control beside
    # it narrows that to chosen wells / FOVs / timepoints. Clear crop removes
    # just the box; Reset restores every cell (incl. manual deletes) and removes
    # the crop.
    #
    # Named for what it does to the *data*. The Image Table has a "Crop view"
    # that sounds the same and is not: that one crops the pixels shown, this one
    # excludes cells from every downstream number.
    crop_btn = QPushButton("Crop cells", ctrl)
    rt.crop_btn = crop_btn
    crop_btn.setProperty("variant", "toggle")
    crop_btn.setCheckable(True)
    crop_btn.setChecked(False)
    crop_btn.setToolTip(
        "Enter crop mode, then drag a rectangle.\n"
        "• Cells whose centroid is inside are kept; cells outside are excluded "
        "from every plot, statistic and export.\n"
        "• Applies to all FOVs / timepoints / channels / wells, unless the Crop "
        "scope control narrows it.\n"
        "Note: the box is drawn in the current FOV's pixel space and applied to "
        "every frame in scope."
    )
    crop_btn.clicked.connect(
        lambda _=False: toggle_crop_edit_mode(ctx)
    )
    ctrl.add_action(crop_btn)

    # Which frames the crop filters. Same control as the Delete scope above, and
    # for the same reason it sits next to the button it modifies and carries the
    # armed scope in its own label — an off-screen scope silently deciding which
    # cells count is exactly what this readout exists to prevent.
    ctrl.add_action(build_crop_scope_control(ctx, ctrl))

    # ── Edit ▾ menu — the one-shot crop / reset actions ─────────────────
    edit_btn, edit_menu = make_action_menu_button(
        "Edit ▾", ctrl, tooltip="Crop and reset actions")
    add_menu_action(edit_menu, "Clear crop", lambda: clear_crop_region(ctx))
    # Same scope, same words as the Curation entry in the title bar's Reset
    # analysis checklist — one action a user can find from either place.
    add_menu_action(edit_menu, "Reset crop + curation",
                    lambda: reset_crop_and_overrides(ctx))
    ctrl.add_action(edit_btn)

    # ── View ▾ popover — image stage · zoom · overlay toggles ───────────
    from widgets.segmented_control import SegmentedControl as _SegmentedControl
    view_btn, view_panel, view_lay = make_popover_button("View ▾", ctrl)
    view_btn.setToolTip("Image stage, zoom, and overlay toggles")

    # Image-stage selector: Raw / Flat-field (recomputed) / Corrected (final).
    # Lets the user validate each correction stage in isolation. Defaults to
    # Corrected (the saved final image). Signal wired AFTER initial setup so
    # building the control doesn't fire the handler.
    stage_row = QWidget(view_panel)
    stage_lay = QHBoxLayout(stage_row)
    stage_lay.setContentsMargins(0, 0, 0, 0)
    stage_lay.setSpacing(6)
    stage_lay.addWidget(QLabel("Stage:", stage_row))
    source_seg = _SegmentedControl(stage_row)
    rt.source_seg = source_seg
    for _lbl, _data in (("Raw", "raw"), ("Flat-field", "flatfield"),
                        ("Corrected", "corrected")):
        source_seg.addSegment(_lbl, data=_data)
    source_seg.setCurrentByData(ctx.state.review_image.image_source)
    source_seg.setToolTip(
        "Image stage shown:\n"
        "• Raw — unprocessed fluorescence.\n"
        "• Flat-field — raw with only the selected flat-field applied "
        "(recomputed live), before any background/top-hat. 'basic' flat-field "
        "is approximated from the current well.\n"
        "• Corrected — the saved final image (flat-field + background)."
    )
    source_seg.currentChanged.connect(
        lambda _i: set_review_image_source(ctx, source_seg.currentData())
    )
    stage_lay.addWidget(source_seg)
    view_lay.addWidget(stage_row)

    # Zoom lives inline on the control row above, not here — it is used far too
    # often to sit two clicks deep inside a popover.

    from PySide6.QtWidgets import QPushButton as _QPushButton
    outline_btn = _QPushButton("Outline: On", view_panel)
    rt.outline_btn = outline_btn
    outline_btn.setProperty("variant", "toggle")
    outline_btn.setCheckable(True)
    outline_btn.setChecked(bool(ctx.state.review_image.show_outline))
    outline_btn.clicked.connect(
        lambda _=False: toggle_review_image_outline(ctx)
    )
    view_lay.addWidget(outline_btn)
    refresh_review_image_outline_btn(ctx)

    binary_btn = _QPushButton("Binary: Off", view_panel)
    rt.binary_btn = binary_btn
    binary_btn.setProperty("variant", "toggle")
    binary_btn.setCheckable(True)
    binary_btn.setChecked(bool(ctx.state.review_image.binary_mask))
    binary_btn.clicked.connect(
        lambda _=False: toggle_review_image_binary_mask(ctx)
    )
    view_lay.addWidget(binary_btn)
    refresh_review_image_binary_btn(ctx)

    # "Why was this cell removed" -- the one surface in the app that answers
    # it. Beside the other two overlay toggles because it is the same kind of
    # control: it changes what the picture says, not what is included.
    from review.features.image_browser.review_image.renderer import (
        refresh_review_image_layers_btn, toggle_review_image_exclusion_layers,
    )
    layers_btn = _QPushButton("Why removed: Off", view_panel)
    rt.layers_btn = layers_btn
    layers_btn.setProperty("variant", "toggle")
    layers_btn.setCheckable(True)
    layers_btn.setChecked(bool(ctx.state.review_image.show_exclusion_layers))
    layers_btn.clicked.connect(
        lambda _=False: toggle_review_image_exclusion_layers(ctx)
    )
    view_lay.addWidget(layers_btn)
    refresh_review_image_layers_btn(ctx)
    ctrl.add_action(view_btn)
    il.addWidget(ctrl)

    # The QC debris-gate controls now live on the Home → Cell
    # Gating tab (a fundamental inclusion layer alongside FluorGating / scatter
    # gates). This overlay still *renders* the QC verdict — excluded cells show
    # here. The per-channel ON cutoff IS editable here again, via the inline
    # quick-edit on the control row above — ThreshFracOn for an intensity
    # Property, ThreshCountOn for a spot count.
    status = reg.register("_review_image_status", QLabel("", inner))
    status.setObjectName("Muted")
    il.addWidget(status)

    # ── LUT ▾ popover — display brightness (min/max) ────────────────────
    lut_btn, lut_panel, lut_lay = make_popover_button("LUT ▾", ctrl)
    lut_btn.setToolTip("Display brightness (min/max) for the active channel")

    lut_range_row = QWidget(lut_panel)
    lut_range_lay = QHBoxLayout(lut_range_row)
    lut_range_lay.setContentsMargins(0, 0, 0, 0)
    lut_range_lay.setSpacing(6)
    lut_range_lay.addWidget(reg.register("_review_lut_chan_lbl", QLabel(
        f"{ctx.state.channel.active_image_channel.upper()} LUT min:", lut_range_row,
    )))
    lmin_entry = reg.register("_review_lmin_entry", QLineEdit("auto", lut_range_row))
    lmin_entry.setFixedWidth(theme.Metrics.num_field_w_lg)
    lmin_entry.editingFinished.connect(lambda: review_image_commit_lut(ctx))
    lut_range_lay.addWidget(lmin_entry)
    lut_range_lay.addWidget(QLabel("max:", lut_range_row))
    lmax_entry = reg.register("_review_lmax_entry", QLineEdit("auto", lut_range_row))
    lmax_entry.setFixedWidth(theme.Metrics.num_field_w_lg)
    lmax_entry.editingFinished.connect(lambda: review_image_commit_lut(ctx))
    lut_range_lay.addWidget(lmax_entry)
    lut_range_lay.addWidget(btn_secondary(lut_range_row, "Auto LUT",
                                          lambda: review_image_auto_lut(ctx)))
    lut_lay.addWidget(lut_range_row)

    ctrl.add_action(lut_btn)

    # ON-cutoff quick-edit — inline on the control row (its original home), not
    # tucked inside the LUT ▾ popover. Edits the active channel's cutoff for the
    # selected Property — the same value the Cell Gating tab and the Plotting
    # quick-edit own — and re-renders, so the Binary (≥ ON threshold) overlay
    # updates live without leaving the tab. The caption follows the target, so a
    # spot-count Property reads "ON spots".
    thr_row = QWidget()
    thr_lay = QHBoxLayout(thr_row)
    thr_lay.setContentsMargins(0, 0, 0, 0)
    thr_lay.setSpacing(6)
    # Registered because the caption follows the cutoff the selected Property
    # uses — ``refresh_thresh_frac_quick_edit`` rewrites it alongside the value.
    _thr_lbl = reg.register("_review_thresh_frac_lbl", QLabel("ON threshold", thr_row))
    _thr_lbl.setObjectName("Muted")   # the band's own captions read this way
    thr_lay.addWidget(_thr_lbl)
    thresh_stepper = reg.register(
        "_review_thresh_frac_stepper",
        Stepper(thr_row, value=50.0, minimum=0.0, maximum=1e9, single_step=1.0, decimals=1))
    thresh_stepper.setFixedWidth(theme.Metrics.stepper_w)
    thresh_stepper.setFocusPolicy(Qt.ClickFocus)
    _rt_edit = getattr(thresh_stepper, "_edit", None)
    if _rt_edit is not None:
        _rt_edit.setFocusPolicy(Qt.ClickFocus)
    thresh_stepper.setToolTip(
        "ON/OFF intensity threshold for the active channel — drives the Binary "
        "(≥ ON threshold) overlay. Same value as the Cell Gating tab's "
        "ThreshFracOn and the plot tabs' Threshold quick-edit."
    )
    thresh_stepper.valueChanged.connect(
        lambda v: (set_on_cutoff(ctx, v), refresh_review_image_ctx(ctx))
    )
    thr_lay.addWidget(thresh_stepper)
    ctrl.add_scope("", thr_row)

    # ── Colors ▾ popover — per-element display colours + reset ──────────
    colors_btn, colors_panel, colors_lay = make_popover_button("Colors ▾", ctrl)
    colors_btn.setToolTip("Boundary / Selected / Tint colours and reset")
    swatches: dict = {}
    rt.color_swatches = swatches

    def _add_swatch(label_text: str, key: str, tooltip: str) -> None:
        row = QWidget(colors_panel)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        rl.addWidget(QLabel(label_text, row))
        sw = _QPushButton("", row)
        sw.setFixedSize(theme.Metrics.swatch_w, _SWATCH_H)
        sw.setToolTip(tooltip)
        sw.clicked.connect(
            lambda _=False, k=key: pick_review_image_color(ctx, k)
        )
        rl.addWidget(sw)
        colors_lay.addWidget(row)
        swatches[key] = sw
        review_image_refresh_color_swatch(ctx, key)

    _add_swatch("Boundary:", "boundary",
                "Color used for included cell boundaries.")
    _add_swatch("Selected:", "selected",
                "Color used for the selected cell highlight.")
    _add_swatch("Tint:", "tint",
                "Tints the grayscale fluorescence image. White (default) "
                "leaves it pure grayscale; pick a color to multiply each\n"
                "channel by the chosen RGB.")
    colors_lay.addWidget(btn_secondary(colors_panel, "Reset colors",
                                       lambda: reset_review_image_colors(ctx)))
    ctrl.add_action(colors_btn)

    canvas = QScrollArea(inner)
    rt.canvas = canvas
    canvas.setWidgetResizable(False)
    canvas.setAlignment(Qt.AlignCenter)
    canvas.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    canvas.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    label = QLabel(canvas)
    rt.label = label
    label.setAlignment(Qt.AlignCenter)
    label.setMouseTracking(True)
    canvas.setWidget(label)
    il.addWidget(canvas, 1)

    # Re-fit the image whenever the scroll area is (re)sized. The lazy first
    # build draws before Qt has laid out the canvas, so the initial
    # render_review_image_display sees a 0×0 viewport and scales the image
    # tiny; the first real layout fires resizeEvent with the correct size and
    # re-fits it (and keeps it fitted on window/splitter resizes). Mirrors the
    # Image Table / Playground canvases. No recursion: the render resizes the
    # inner label, not this scroll area.
    _orig_canvas_resize = canvas.resizeEvent

    def _on_review_image_canvas_resize(ev) -> None:
        _orig_canvas_resize(ev)
        render_review_image_display(ctx)

    canvas.resizeEvent = _on_review_image_canvas_resize

    # Install wheel + mouse hooks for zoom / drag
    def _wheel(event):
        delta = event.angleDelta().y()
        if delta > 0:
            review_image_zoom_step(ctx, +1)
        elif delta < 0:
            review_image_zoom_step(ctx, -1)
    label.wheelEvent = _wheel
    label.mousePressEvent = partial(on_review_image_press, ctx)
    label.mouseMoveEvent = partial(on_review_image_move, ctx)
    label.mouseReleaseEvent = partial(on_review_image_release, ctx)

    def _leave(_ev):
        try:
            QToolTip.hideText()
        except Exception:
            swallowed()  # was: pass (silent except)
    label.leaveEvent = _leave

    # Default cursor reflects the initial Include-edit-mode state
    # (ForbiddenCursor when remove-cell mode is active).
    apply_review_image_cursor(ctx)

# ``refresh_fov_btn_state`` moved to ``review.core.plot_toggles`` (A8
# enabler E3) alongside the SEM/FOV toggles it pairs with, so any feature can
# refresh the shared per-FOV button bank through ctx with a core import.

