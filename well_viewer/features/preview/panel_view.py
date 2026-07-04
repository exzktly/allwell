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

from well_viewer.core.ui_helpers import btn_card, btn_danger, btn_secondary
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
refresh_review_image = _lazy(_RI_RENDER, "refresh_review_image")
render_review_image_display = _lazy(_RI_RENDER, "render_review_image_display")


def build_review_image_panel(self, parent: QWidget) -> None:
    # Register the widgets the ctx-native render pipeline reads back
    # (``update_preview_ctx`` resolves them via ``ctx.widgets``). ``register``
    # still stamps ``self._<name>`` so the app-native readers (refresh / draw)
    # are unaffected.
    reg = self._ctx.widgets
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
    cl = QHBoxLayout(ctrl)
    cl.setContentsMargins(10, 6, 10, 6)
    self._review_image_well_lbl = reg.register(
        "_review_image_well_lbl", QLabel("No well selected", ctrl))
    cl.addWidget(self._review_image_well_lbl)

    cl.addWidget(QLabel("Channel:", ctrl))
    self._review_image_chan_cb = QComboBox(ctrl)
    self._review_image_chan_cb.addItems(["GFP"])
    self._review_image_chan_cb.currentIndexChanged.connect(
        lambda _i: on_review_image_channel_selected(self, None)
    )
    cl.addWidget(self._review_image_chan_cb)
    self._review_image_chan_var = self._review_image_chan_cb

    cl.addWidget(QLabel("FOV:", ctrl))
    self._review_image_fov_menu = reg.register(
        "_review_image_fov_menu", QComboBox(ctrl))
    self._review_image_fov_menu.addItems(["—"])
    self._review_image_fov_menu.currentIndexChanged.connect(
        lambda _i: refresh_review_image(self)
    )
    cl.addWidget(self._review_image_fov_menu)

    cl.addWidget(QLabel("Timepoint:", ctrl))
    self._review_image_tp_menu = reg.register(
        "_review_image_tp_menu", QComboBox(ctrl))
    self._review_image_tp_menu.addItems(["—"])
    self._review_image_tp_menu.currentIndexChanged.connect(
        lambda _i: refresh_review_image(self)
    )
    cl.addWidget(self._review_image_tp_menu)
    self._review_image_tp_var = self._review_image_tp_menu
    self._review_image_tp_cb = self._review_image_tp_menu  # alias for review_image_controller
    cl.addStretch(1)

    from PySide6.QtWidgets import QPushButton
    self._review_image_delete_btn = QPushButton("Delete Cell", ctrl)
    self._review_image_delete_btn.setProperty("variant", "toggle")
    self._review_image_delete_btn.setCheckable(True)
    self._review_image_delete_btn.setChecked(False)
    self._review_image_delete_btn.setToolTip(
        "Enter drag-delete mode.\n"
        "• Click a cell to mark it Included=0.\n"
        "• Drag a rectangle to bulk-delete all cells inside."
    )
    self._review_image_delete_btn.clicked.connect(
        lambda _=False: self._toggle_selected_review_cell()
    )
    cl.addWidget(self._review_image_delete_btn)

    # Crop region: drag a rectangle to keep only cells whose centroid is inside;
    # applies to every FOV/timepoint/channel/well. Clear crop removes just the
    # box; Reset restores every cell (incl. manual deletes) and removes the crop.
    self._review_image_crop_btn = QPushButton("Crop Region", ctrl)
    self._review_image_crop_btn.setProperty("variant", "toggle")
    self._review_image_crop_btn.setCheckable(True)
    self._review_image_crop_btn.setChecked(False)
    self._review_image_crop_btn.setToolTip(
        "Enter crop mode, then drag a rectangle.\n"
        "• Cells whose centroid is inside are kept; cells outside are excluded.\n"
        "• Applies to all FOVs / timepoints / channels / wells.\n"
        "Note: the box is drawn in the current FOV's pixel space and applied to all."
    )
    self._review_image_crop_btn.clicked.connect(
        lambda _=False: toggle_crop_edit_mode(self)
    )
    cl.addWidget(self._review_image_crop_btn)

    cl.addWidget(btn_secondary(
        ctrl, "Clear crop", lambda: clear_crop_region(self)))
    cl.addWidget(btn_danger(
        ctrl, "Reset", lambda: reset_crop_and_overrides(self)))

    # Separate the edit cluster (Delete / Crop / Clear / Reset) from the
    # image-stage selector so they don't read as one undifferentiated row.
    cl.addWidget(_toolbar_separator(ctrl))

    # Image-stage selector: Raw / Flat-field (recomputed) / Corrected (final).
    # Lets the user validate each correction stage in isolation. Defaults to
    # Corrected (the saved final image). Signal wired AFTER initial setup so
    # building the control doesn't fire the handler.
    from widgets.segmented_control import SegmentedControl as _SegmentedControl
    self._review_image_source_seg = _SegmentedControl(ctrl)
    for _lbl, _data in (("Raw", "raw"), ("Flat-field", "flatfield"),
                        ("Corrected", "corrected")):
        self._review_image_source_seg.addSegment(_lbl, data=_data)
    self._review_image_source_seg.setCurrentByData(
        self._app_state.review_image.image_source)
    self._review_image_source_seg.setToolTip(
        "Image stage shown:\n"
        "• Raw — unprocessed fluorescence.\n"
        "• Flat-field — raw with only the selected flat-field applied "
        "(recomputed live), before any background/top-hat. 'basic' flat-field "
        "is approximated from the current well.\n"
        "• Corrected — the saved final image (flat-field + background)."
    )
    self._review_image_source_seg.currentChanged.connect(
        lambda _i: self._set_review_image_source(
            self._review_image_source_seg.currentData())
    )
    cl.addWidget(self._review_image_source_seg)

    # Separate the zoom cluster (Fit / − / +) from the stage selector.
    cl.addWidget(_toolbar_separator(ctrl))

    cl.addWidget(btn_secondary(ctrl, "Fit", self._review_image_zoom_fit))
    cl.addWidget(btn_card(ctrl, "−", lambda: self._review_image_zoom_step(-1)))
    cl.addWidget(btn_card(ctrl, "+", lambda: self._review_image_zoom_step(+1)))
    il.addWidget(ctrl)

    # The QC debris-gate controls now live on the Sample Definitions → Cell
    # Gating tab (a fundamental inclusion layer alongside FluorGating / scatter
    # gates). This overlay still *renders* the QC verdict — excluded cells show
    # here. The per-channel ON/OFF intensity threshold (ThreshFracOn) IS editable
    # here again, via the quick-edit Stepper added to the LUT row below.
    self._review_image_status = reg.register(
        "_review_image_status", QLabel("", inner))
    self._review_image_status.setObjectName("Muted")
    il.addWidget(self._review_image_status)

    lut_row = QWidget(inner)
    lut_row.setObjectName("TabCtrl")  # data-gated with the rest of the controls
    lr = QHBoxLayout(lut_row)
    lr.setContentsMargins(8, 2, 8, 2)
    self._review_lut_chan_lbl = QLabel(
        f"{self._app_state.channel.active_image_channel.upper()} LUT min:", lut_row,
    )
    lr.addWidget(self._review_lut_chan_lbl)

    self._review_lmin_entry = QLineEdit("auto", lut_row)
    self._review_lmin_entry.setFixedWidth(80)
    self._review_lmin_entry.editingFinished.connect(self._review_image_commit_lut)
    lr.addWidget(self._review_lmin_entry)

    lr.addWidget(QLabel("max:", lut_row))
    self._review_lmax_entry = QLineEdit("auto", lut_row)
    self._review_lmax_entry.setFixedWidth(80)
    self._review_lmax_entry.editingFinished.connect(self._review_image_commit_lut)
    lr.addWidget(self._review_lmax_entry)

    lr.addWidget(btn_secondary(lut_row, "Auto LUT", self._review_image_auto_lut))

    # ON/OFF intensity threshold quick-edit (the editor this row used to host;
    # see the comment above). Edits the active channel's ThreshFracOn — the same
    # value the Cell Gating tab and the Plotting quick-edit own — and re-renders,
    # so the Binary (≥ ON threshold) overlay updates live without leaving the tab.
    lr.addSpacing(12)
    lr.addWidget(QLabel("ON threshold:", lut_row))
    self._review_thresh_frac_stepper = Stepper(
        lut_row, value=50.0, minimum=0.0, maximum=1e9, single_step=1.0, decimals=1)
    self._review_thresh_frac_stepper.setFixedWidth(96)
    self._review_thresh_frac_stepper.setFocusPolicy(Qt.ClickFocus)
    _rt_edit = getattr(self._review_thresh_frac_stepper, "_edit", None)
    if _rt_edit is not None:
        _rt_edit.setFocusPolicy(Qt.ClickFocus)
    self._review_thresh_frac_stepper.setToolTip(
        "ON/OFF intensity threshold for the active channel — drives the Binary "
        "(≥ ON threshold) overlay. Same value as the Cell Gating tab's "
        "ThreshFracOn and the plot tabs' Threshold quick-edit."
    )
    # Edit the active channel's ThreshFracOn (single source of truth, shared
    # with Cell Gating / the plot quick-edit) and re-render so the binary
    # overlay updates live.
    self._review_thresh_frac_stepper.valueChanged.connect(
        lambda v: (self._set_thresh_frac_on(v), refresh_review_image(self))
    )
    lr.addWidget(self._review_thresh_frac_stepper)
    lr.addStretch(1)
    il.addWidget(lut_row)

    # ── Color customization + binary-mask toggle row ───────────────────
    from PySide6.QtWidgets import QPushButton as _QPushButton
    color_row = QWidget(inner)
    color_row.setObjectName("TabCtrl")  # data-gated with the rest of the controls
    cr = QHBoxLayout(color_row)
    cr.setContentsMargins(8, 2, 8, 2)
    self._review_image_color_swatches = {}

    def _add_swatch(label_text: str, key: str, tooltip: str) -> None:
        cr.addWidget(QLabel(label_text, color_row))
        sw = _QPushButton("", color_row)
        sw.setFixedSize(28, 18)
        sw.setToolTip(tooltip)
        sw.clicked.connect(
            lambda _=False, k=key: pick_review_image_color(self, k)
        )
        cr.addWidget(sw)
        self._review_image_color_swatches[key] = sw
        self._review_image_refresh_color_swatch(key)

    _add_swatch("Boundary:", "boundary",
                "Color used for included cell boundaries.")
    _add_swatch("Selected:", "selected",
                "Color used for the selected cell highlight.")
    _add_swatch("Tint:", "tint",
                "Tints the grayscale fluorescence image. White (default) "
                "leaves it pure grayscale; pick a color to multiply each\n"
                "channel by the chosen RGB.")
    cr.addWidget(btn_secondary(color_row, "Reset colors",
                               self._reset_review_image_colors))

    cr.addSpacing(12)

    self._review_image_outline_btn = _QPushButton("Outline: On", color_row)
    self._review_image_outline_btn.setProperty("variant", "toggle")
    self._review_image_outline_btn.setCheckable(True)
    self._review_image_outline_btn.setChecked(
        bool(self._app_state.review_image.show_outline)
    )
    self._review_image_outline_btn.clicked.connect(
        lambda _=False: self._toggle_review_image_outline()
    )
    cr.addWidget(self._review_image_outline_btn)
    self._refresh_review_image_outline_btn()

    self._review_image_binary_btn = _QPushButton("Binary: Off", color_row)
    self._review_image_binary_btn.setProperty("variant", "toggle")
    self._review_image_binary_btn.setCheckable(True)
    self._review_image_binary_btn.setChecked(
        bool(self._app_state.review_image.binary_mask)
    )
    self._review_image_binary_btn.clicked.connect(
        lambda _=False: self._toggle_review_image_binary_mask()
    )
    cr.addWidget(self._review_image_binary_btn)
    self._refresh_review_image_binary_btn()

    cr.addStretch(1)
    il.addWidget(color_row)

    self._review_image_canvas = QScrollArea(inner)
    self._review_image_canvas.setWidgetResizable(False)
    self._review_image_canvas.setAlignment(Qt.AlignCenter)
    self._review_image_canvas.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    self._review_image_canvas.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    self._review_image_label = QLabel(self._review_image_canvas)
    self._review_image_label.setAlignment(Qt.AlignCenter)
    self._review_image_label.setMouseTracking(True)
    self._review_image_canvas.setWidget(self._review_image_label)
    il.addWidget(self._review_image_canvas, 1)

    # Re-fit the image whenever the scroll area is (re)sized. The lazy first
    # build draws before Qt has laid out the canvas, so the initial
    # render_review_image_display sees a 0×0 viewport and scales the image
    # tiny; the first real layout fires resizeEvent with the correct size and
    # re-fits it (and keeps it fitted on window/splitter resizes). Mirrors the
    # Image Table / Playground canvases. No recursion: the render resizes the
    # inner label, not this scroll area.
    _orig_canvas_resize = self._review_image_canvas.resizeEvent

    def _on_review_image_canvas_resize(ev) -> None:
        _orig_canvas_resize(ev)
        render_review_image_display(self._ctx)

    self._review_image_canvas.resizeEvent = _on_review_image_canvas_resize

    # Install wheel + mouse hooks for zoom / drag
    def _wheel(event):
        delta = event.angleDelta().y()
        if delta > 0:
            self._review_image_zoom_step(+1)
        elif delta < 0:
            self._review_image_zoom_step(-1)
    self._review_image_label.wheelEvent = _wheel
    self._review_image_label.mousePressEvent = partial(on_review_image_press, self)
    self._review_image_label.mouseMoveEvent = partial(on_review_image_move, self)
    self._review_image_label.mouseReleaseEvent = partial(on_review_image_release, self)

    def _leave(_ev):
        try:
            QToolTip.hideText()
        except Exception:
            swallowed()  # was: pass (silent except)
    self._review_image_label.leaveEvent = _leave

    # Default cursor reflects the initial Include-edit-mode state
    # (ForbiddenCursor when remove-cell mode is active).
    self._apply_review_image_cursor()

# ``refresh_fov_btn_state`` moved to ``well_viewer.core.plot_toggles`` (A8
# enabler E3) alongside the SEM/FOV toggles it pairs with, so any feature can
# refresh the shared per-FOV button bank through ctx with a core import.

