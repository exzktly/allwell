"""Review-image (Segmentation tab) view builder (Qt port).

The Movie Montage tab this module used to also build was folded into the
Image Table tab; its orphaned ``build_right_panel`` builder (and the montage
top-hat-subtraction controls it hosted) has been removed.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea,
    QToolTip, QVBoxLayout, QWidget,
)


def _toolbar_separator(parent: QWidget) -> QFrame:
    """A thin vertical hairline that visually groups toolbar clusters.

    Uses the shared ``vline`` QSS rule (theme.qss) so it tracks the border
    token; no hardcoded colour.
    """
    sep = QFrame(parent)
    sep.setProperty("vline", True)
    sep.setFixedWidth(1)
    sep.setFixedHeight(20)
    return sep

from well_viewer.core.ui_helpers import (
    btn_card, btn_danger, btn_secondary, make_popover_button,
)
from widgets.flow_layout import FlowLayout
from well_viewer.features.crop.controller import (
    clear_crop_region, reset_crop_and_overrides, toggle_crop_edit_mode,
)
from widgets.stepper import Stepper
from functools import partial
from well_viewer.core.safe import swallowed
from well_viewer.core.lazy import lazy as _lazy
# This panel builds the Image Browser review-image canvas, so it is only ever
# imported by image_browser/controller; bind its review_image delegates lazily
# so importing this module never imports image_browser (keeps it deletable).
_RI_CTRL = "well_viewer.features.image_browser.review_image.controller"
_RI_RENDER = "well_viewer.features.image_browser.review_image.renderer"
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
set_thresh_frac_on = _lazy("well_viewer.core.channel_state_controller", "set_thresh_frac_on")


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

    ctrl = QWidget(inner)
    ctrl.setObjectName("TabCtrl")  # data-gated: greyed out until a dataset loads
    # FlowLayout so this dense control row wraps onto extra lines instead of
    # clipping its right-most controls (zoom / stage selector) when Split View
    # halves the pane width — the reported "options collapsed and not visible".
    cl = FlowLayout(ctrl, h_spacing=6, v_spacing=4)
    cl.setContentsMargins(10, 6, 10, 6)
    cl.addWidget(reg.register("_review_image_well_lbl", QLabel("No well selected", ctrl)))

    cl.addWidget(QLabel("Channel:", ctrl))
    chan_cb = reg.register("_review_image_chan_cb", QComboBox(ctrl))
    chan_cb.addItems(["GFP"])
    chan_cb.currentIndexChanged.connect(
        lambda _i: on_review_image_channel_selected(ctx, None)
    )
    cl.addWidget(chan_cb)
    # ``_review_image_chan_var`` is a legacy alias of the same combo; register it
    # under both names so ctx.widgets resolves either.
    reg.register("_review_image_chan_var", chan_cb)

    cl.addWidget(QLabel("FOV:", ctrl))
    fov_menu = reg.register("_review_image_fov_menu", QComboBox(ctrl))
    fov_menu.addItems(["—"])
    fov_menu.currentIndexChanged.connect(
        lambda _i: refresh_review_image_ctx(ctx)
    )
    cl.addWidget(fov_menu)

    cl.addWidget(QLabel("Timepoint:", ctrl))
    tp_menu = reg.register("_review_image_tp_menu", QComboBox(ctrl))
    tp_menu.addItems(["—"])
    tp_menu.currentIndexChanged.connect(
        lambda _i: refresh_review_image_ctx(ctx)
    )
    cl.addWidget(tp_menu)
    rt.tp_var = tp_menu
    rt.tp_cb = tp_menu  # alias for review_image_controller

    from PySide6.QtWidgets import QPushButton
    delete_btn = QPushButton("Delete Cell", ctrl)
    rt.delete_btn = delete_btn
    delete_btn.setProperty("variant", "toggle")
    delete_btn.setCheckable(True)
    delete_btn.setChecked(False)
    delete_btn.setToolTip(
        "Enter drag-delete mode.\n"
        "• Click a cell to mark it Included=0.\n"
        "• Drag a rectangle to bulk-delete all cells inside."
    )
    delete_btn.clicked.connect(
        lambda _=False: toggle_selected_review_cell(ctx)
    )
    cl.addWidget(delete_btn)

    # Crop region: drag a rectangle to keep only cells whose centroid is inside;
    # applies to every FOV/timepoint/channel/well. Clear crop removes just the
    # box; Reset restores every cell (incl. manual deletes) and removes the crop.
    crop_btn = QPushButton("Crop Region", ctrl)
    rt.crop_btn = crop_btn
    crop_btn.setProperty("variant", "toggle")
    crop_btn.setCheckable(True)
    crop_btn.setChecked(False)
    crop_btn.setToolTip(
        "Enter crop mode, then drag a rectangle.\n"
        "• Cells whose centroid is inside are kept; cells outside are excluded.\n"
        "• Applies to all FOVs / timepoints / channels / wells.\n"
        "Note: the box is drawn in the current FOV's pixel space and applied to all."
    )
    crop_btn.clicked.connect(
        lambda _=False: toggle_crop_edit_mode(ctx)
    )
    cl.addWidget(crop_btn)

    cl.addWidget(btn_secondary(
        ctrl, "Clear crop", lambda: clear_crop_region(ctx)))
    cl.addWidget(btn_danger(
        ctrl, "Reset", lambda: reset_crop_and_overrides(ctx)))

    # Separate the edit cluster (Delete / Crop / Clear / Reset) from the
    # image-stage selector so they don't read as one undifferentiated row.
    cl.addWidget(_toolbar_separator(ctrl))

    # Image-stage selector: Raw / Flat-field (recomputed) / Corrected (final).
    # Lets the user validate each correction stage in isolation. Defaults to
    # Corrected (the saved final image). Signal wired AFTER initial setup so
    # building the control doesn't fire the handler.
    from widgets.segmented_control import SegmentedControl as _SegmentedControl
    source_seg = _SegmentedControl(ctrl)
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
    cl.addWidget(source_seg)

    # Separate the zoom cluster (Fit / − / +) from the stage selector.
    cl.addWidget(_toolbar_separator(ctrl))

    cl.addWidget(btn_secondary(ctrl, "Fit", lambda: review_image_zoom_fit(ctx)))
    cl.addWidget(btn_card(ctrl, "−", lambda: review_image_zoom_step(ctx, -1)))
    cl.addWidget(btn_card(ctrl, "+", lambda: review_image_zoom_step(ctx, +1)))
    il.addWidget(ctrl)

    # The QC debris-gate controls now live on the Sample Definitions → Cell
    # Gating tab (a fundamental inclusion layer alongside FluorGating / scatter
    # gates). This overlay still *renders* the QC verdict — excluded cells show
    # here. The per-channel ON/OFF intensity threshold (ThreshFracOn) IS editable
    # here again, via the quick-edit Stepper added to the LUT row below.
    status = reg.register("_review_image_status", QLabel("", inner))
    status.setObjectName("Muted")
    il.addWidget(status)

    lut_row = QWidget(inner)
    lut_row.setObjectName("TabCtrl")  # data-gated with the rest of the controls
    lr = FlowLayout(lut_row, h_spacing=6, v_spacing=4)
    lr.setContentsMargins(8, 2, 8, 2)
    lr.addWidget(reg.register("_review_lut_chan_lbl", QLabel(
        f"{ctx.state.channel.active_image_channel.upper()} LUT min:", lut_row,
    )))

    lmin_entry = reg.register("_review_lmin_entry", QLineEdit("auto", lut_row))
    lmin_entry.setFixedWidth(80)
    lmin_entry.editingFinished.connect(lambda: review_image_commit_lut(ctx))
    lr.addWidget(lmin_entry)

    lr.addWidget(QLabel("max:", lut_row))
    lmax_entry = reg.register("_review_lmax_entry", QLineEdit("auto", lut_row))
    lmax_entry.setFixedWidth(80)
    lmax_entry.editingFinished.connect(lambda: review_image_commit_lut(ctx))
    lr.addWidget(lmax_entry)

    lr.addWidget(btn_secondary(lut_row, "Auto LUT", lambda: review_image_auto_lut(ctx)))

    # ON/OFF intensity threshold quick-edit (the editor this row used to host;
    # see the comment above). Edits the active channel's ThreshFracOn — the same
    # value the Cell Gating tab and the Plotting quick-edit own — and re-renders,
    # so the Binary (≥ ON threshold) overlay updates live without leaving the tab.
    lr.addWidget(QLabel("ON threshold:", lut_row))
    thresh_stepper = reg.register(
        "_review_thresh_frac_stepper",
        Stepper(lut_row, value=50.0, minimum=0.0, maximum=1e9, single_step=1.0, decimals=1))
    thresh_stepper.setFixedWidth(96)
    thresh_stepper.setFocusPolicy(Qt.ClickFocus)
    _rt_edit = getattr(thresh_stepper, "_edit", None)
    if _rt_edit is not None:
        _rt_edit.setFocusPolicy(Qt.ClickFocus)
    thresh_stepper.setToolTip(
        "ON/OFF intensity threshold for the active channel — drives the Binary "
        "(≥ ON threshold) overlay. Same value as the Cell Gating tab's "
        "ThreshFracOn and the plot tabs' Threshold quick-edit."
    )
    # Edit the active channel's ThreshFracOn (single source of truth, shared
    # with Cell Gating / the plot quick-edit) and re-render so the binary
    # overlay updates live.
    thresh_stepper.valueChanged.connect(
        lambda v: (set_thresh_frac_on(ctx, v), refresh_review_image_ctx(ctx))
    )
    lr.addWidget(thresh_stepper)
    il.addWidget(lut_row)

    # ── Color customization + binary-mask toggle row ───────────────────
    from PySide6.QtWidgets import QPushButton as _QPushButton
    color_row = QWidget(inner)
    color_row.setObjectName("TabCtrl")  # data-gated with the rest of the controls
    # FlowLayout so the row wraps rather than clipping when the pane narrows.
    cr = FlowLayout(color_row, h_spacing=6, v_spacing=4)
    cr.setContentsMargins(8, 2, 8, 2)
    swatches: dict = {}
    rt.color_swatches = swatches

    # Per-element colour swatches + Reset collapse into a "Colors ▾" popover so the
    # Outline / Binary toggles (the controls used most) stay inline and reachable.
    colors_btn, colors_panel, colors_lay = make_popover_button("Colors ▾", color_row)
    colors_btn.setToolTip("Boundary / Selected / Tint colours and reset")
    cr.addWidget(colors_btn)

    def _add_swatch(label_text: str, key: str, tooltip: str) -> None:
        row = QWidget(colors_panel)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        rl.addWidget(QLabel(label_text, row))
        sw = _QPushButton("", row)
        sw.setFixedSize(28, 18)
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

    outline_btn = _QPushButton("Outline: On", color_row)
    rt.outline_btn = outline_btn
    outline_btn.setProperty("variant", "toggle")
    outline_btn.setCheckable(True)
    outline_btn.setChecked(bool(ctx.state.review_image.show_outline))
    outline_btn.clicked.connect(
        lambda _=False: toggle_review_image_outline(ctx)
    )
    cr.addWidget(outline_btn)
    refresh_review_image_outline_btn(ctx)

    binary_btn = _QPushButton("Binary: Off", color_row)
    rt.binary_btn = binary_btn
    binary_btn.setProperty("variant", "toggle")
    binary_btn.setCheckable(True)
    binary_btn.setChecked(bool(ctx.state.review_image.binary_mask))
    binary_btn.clicked.connect(
        lambda _=False: toggle_review_image_binary_mask(ctx)
    )
    cr.addWidget(binary_btn)
    refresh_review_image_binary_btn(ctx)

    il.addWidget(color_row)

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

# ``refresh_fov_btn_state`` moved to ``well_viewer.core.plot_toggles`` (A8
# enabler E3) alongside the SEM/FOV toggles it pairs with, so any feature can
# refresh the shared per-FOV button bank through ctx with a core import.

