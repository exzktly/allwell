"""Movie montage / review-image view builders (Qt port)."""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, QEvent
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea,
    QToolTip, QVBoxLayout, QWidget,
)

from well_viewer.ui_helpers import btn_card, btn_secondary
from well_viewer.preview_callbacks import montage_tophat_toggled
from well_viewer.review_image_controller import on_review_image_channel_selected
from well_viewer.montage_controller import montage_zoom_step
from well_viewer.montage_controller import on_montage_shift_wheel
from well_viewer.review_image_renderer import refresh_review_image


def build_right_panel(self, parent: QWidget) -> None:
    """Movie Montage tab: header + scrollable thumbnail grid + LUT / zoom row."""
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    inner = QWidget(parent)
    il = QVBoxLayout(inner)
    il.setContentsMargins(0, 0, 0, 0)
    il.setSpacing(0)
    layout.addWidget(inner, 1)

    ctrl = QWidget(inner)
    cl = QHBoxLayout(ctrl)
    cl.setContentsMargins(10, 6, 10, 6)
    self._preview_well_lbl = QLabel("No well selected", ctrl)
    cl.addWidget(self._preview_well_lbl)

    cl.addWidget(QLabel("Channel:", ctrl))
    self._chan_cb_preview = QComboBox(ctrl)
    self._chan_cb_preview.addItems(["GFP"])
    self._chan_cb_preview.currentIndexChanged.connect(
        lambda _i: self._on_preview_channel_selected(None)
    )
    cl.addWidget(self._chan_cb_preview)
    self._montage_chan_var = self._chan_cb_preview

    cl.addWidget(QLabel("FOV:", ctrl))
    self._fov_menu = QComboBox(ctrl)
    self._fov_menu.addItems(["—"])
    self._fov_menu.currentIndexChanged.connect(
        lambda _i: self._refresh_preview_montage()
    )
    cl.addWidget(self._fov_menu)
    self._preview_fov_var = self._fov_menu
    self._preview_fov_cb = self._fov_menu  # alias used by preview / export callers
    cl.addStretch(1)
    cl.addWidget(btn_secondary(ctrl, "Save Montage…", self._save_montage_figure))
    il.addWidget(ctrl)

    sep = QFrame(inner)
    sep.setObjectName("Separator")
    sep.setFrameShape(QFrame.HLine)
    sep.setFixedHeight(1)
    il.addWidget(sep)

    self._montage_status = QLabel("", inner)
    self._montage_status.setObjectName("Muted")
    il.addWidget(self._montage_status)

    self._montage_canvas = QScrollArea(inner)
    self._montage_canvas.setWidgetResizable(True)
    self._montage_canvas.setFrameShape(QFrame.NoFrame)
    self._montage_inner = QWidget()
    # Layout is created on demand by _refresh_preview_montage (QGridLayout).
    self._montage_canvas.setWidget(self._montage_inner)
    il.addWidget(self._montage_canvas, 1)
    self._montage_win = None

    # LUT + zoom row
    lut_row = QWidget(inner)
    lr = QHBoxLayout(lut_row)
    lr.setContentsMargins(8, 4, 8, 4)
    self._mon_lut_chan_lbl = QLabel(
        f"{self._app_state.channel.active_image_channel.upper()} LUT min:", lut_row,
    )
    lr.addWidget(self._mon_lut_chan_lbl)

    self._mon_lmin_entry = QLineEdit("auto", lut_row)
    self._mon_lmin_entry.setFixedWidth(80)
    self._mon_lmin_entry.editingFinished.connect(self._montage_redraw_at_zoom)
    lr.addWidget(self._mon_lmin_entry)
    self._mon_lmin_edit = self._mon_lmin_entry  # alias used by montage / export callers

    lr.addWidget(QLabel("max:", lut_row))
    self._mon_lmax_entry = QLineEdit("auto", lut_row)
    self._mon_lmax_entry.setFixedWidth(80)
    self._mon_lmax_entry.editingFinished.connect(self._montage_redraw_at_zoom)
    lr.addWidget(self._mon_lmax_entry)
    self._mon_lmax_edit = self._mon_lmax_entry  # alias used by montage / export callers

    lr.addWidget(btn_secondary(lut_row, "Auto LUT", self._montage_auto_lut))

    # Overlay LUT — tune brightness/contrast of the segmentation overlay.
    ov_sep = QFrame(lut_row)
    ov_sep.setFrameShape(QFrame.VLine)
    ov_sep.setFixedWidth(1)
    lr.addWidget(ov_sep)

    lr.addWidget(QLabel("Overlay LUT min:", lut_row))
    self._mon_ov_lmin_entry = QLineEdit("auto", lut_row)
    self._mon_ov_lmin_entry.setFixedWidth(80)
    self._mon_ov_lmin_entry.editingFinished.connect(self._montage_redraw_at_zoom)
    lr.addWidget(self._mon_ov_lmin_entry)
    self._mon_ov_lmin_edit = self._mon_ov_lmin_entry

    lr.addWidget(QLabel("max:", lut_row))
    self._mon_ov_lmax_entry = QLineEdit("auto", lut_row)
    self._mon_ov_lmax_entry.setFixedWidth(80)
    self._mon_ov_lmax_entry.editingFinished.connect(self._montage_redraw_at_zoom)
    lr.addWidget(self._mon_ov_lmax_entry)
    self._mon_ov_lmax_edit = self._mon_ov_lmax_entry

    lr.addWidget(QLabel("Zoom:", lut_row))
    lr.addWidget(btn_card(lut_row, "−", lambda: montage_zoom_step(self, -1)))
    self._montage_zoom_lbl = QLabel("100%", lut_row)
    self._montage_zoom_lbl.setFixedWidth(50)
    lr.addWidget(self._montage_zoom_lbl)
    lr.addWidget(btn_card(lut_row, "+", lambda: montage_zoom_step(self, +1)))
    lr.addWidget(btn_secondary(lut_row, "Fit", self._montage_zoom_fit))

    # Region-of-interest crop controls (built by the shared CropTool).
    # The Crop toggle puts the next click-drag on a fluorescence thumbnail
    # into rubber-band mode; once released, the resulting square zooms
    # every timepoint of the film strip into that region.
    crop_sep = QFrame(lut_row)
    crop_sep.setFrameShape(QFrame.VLine)
    crop_sep.setFixedWidth(1)
    lr.addWidget(crop_sep)

    self._montage_crop_btn = self._montage_crop_tool.make_button(lut_row)
    lr.addWidget(self._montage_crop_btn)
    lr.addWidget(self._montage_crop_tool.make_reset_button(lut_row))
    self._montage_crop_status_lbl = self._montage_crop_tool.make_status_label(lut_row)
    lr.addWidget(self._montage_crop_status_lbl)

    lr.addStretch(1)
    il.addWidget(lut_row)

    # Top-hat controls
    th_row = QWidget(inner)
    tr = QHBoxLayout(th_row)
    tr.setContentsMargins(8, 2, 8, 2)
    self._th_checkbox = QCheckBox("Top-hat background subtraction", th_row)
    self._th_checkbox.toggled.connect(
        lambda _b: montage_tophat_toggled(self)
    )
    tr.addWidget(self._th_checkbox)
    self._mon_tophat_cb = self._th_checkbox  # alias used by montage / export callers

    tr.addWidget(QLabel("   radius:", th_row))
    self._th_radius_entry = QLineEdit("50", th_row)
    self._th_radius_entry.setFixedWidth(60)
    self._th_radius_entry.editingFinished.connect(self._montage_redraw_at_zoom)
    tr.addWidget(self._th_radius_entry)
    self._mon_tophat_radius_edit = self._th_radius_entry  # alias used by montage / export callers
    tr.addWidget(QLabel("px", th_row))
    self._th_preload_badge = QLabel("", th_row)
    tr.addWidget(self._th_preload_badge)
    tr.addStretch(1)
    il.addWidget(th_row)

    self._montage_fluor_arrays: List[object] = []
    self._montage_overlay_arrays: List[object] = []
    self._montage_fluor_refs: List[object] = []
    self._montage_overlay_refs: List[object] = []
    self._montage_resize_job: Optional[str] = None
    self._montage_zoom: float = 1.0
    self._montage_base_sz: int = 120

    # Wheel zoom on the montage
    def _wheel(event):
        delta = event.angleDelta().y()
        if event.modifiers() & Qt.ShiftModifier:
            if hasattr(self, "_on_montage_shift_wheel"):
                on_montage_shift_wheel(self, event)
        elif delta > 0:
            montage_zoom_step(self, +1)
        elif delta < 0:
            montage_zoom_step(self, -1)

    self._montage_canvas.wheelEvent = _wheel


def build_review_image_panel(self, parent: QWidget) -> None:
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
    cl = QHBoxLayout(ctrl)
    cl.setContentsMargins(10, 6, 10, 6)
    self._review_image_well_lbl = QLabel("No well selected", ctrl)
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
    self._review_image_fov_menu = QComboBox(ctrl)
    self._review_image_fov_menu.addItems(["—"])
    self._review_image_fov_menu.currentIndexChanged.connect(
        lambda _i: refresh_review_image(self)
    )
    cl.addWidget(self._review_image_fov_menu)

    cl.addWidget(QLabel("Timepoint:", ctrl))
    self._review_image_tp_menu = QComboBox(ctrl)
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

    # Raw vs top-hat fluorescence source toggle. Defaults to top-hat.
    self._review_image_raw_btn = QPushButton("Top-hat", ctrl)
    self._review_image_raw_btn.setProperty("variant", "toggle")
    self._review_image_raw_btn.setCheckable(True)
    self._review_image_raw_btn.setChecked(bool(self._app_state.review_image.show_raw))
    self._review_image_raw_btn.setToolTip(
        "Showing the top-hat-filtered fluorescence frame (default).\n"
        "Click to switch to the unprocessed raw image."
    )
    self._review_image_raw_btn.clicked.connect(
        lambda _=False: self._toggle_review_image_source()
    )
    cl.addWidget(self._review_image_raw_btn)

    cl.addWidget(btn_secondary(ctrl, "Fit", self._review_image_zoom_fit))
    cl.addWidget(btn_card(ctrl, "−", lambda: self._review_image_zoom_step(-1)))
    cl.addWidget(btn_card(ctrl, "+", lambda: self._review_image_zoom_step(+1)))
    il.addWidget(ctrl)

    sep = QFrame(inner)
    sep.setObjectName("Separator")
    sep.setFrameShape(QFrame.HLine)
    sep.setFixedHeight(1)
    il.addWidget(sep)

    self._review_image_status = QLabel("", inner)
    self._review_image_status.setObjectName("Muted")
    il.addWidget(self._review_image_status)

    lut_row = QWidget(inner)
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
    lr.addStretch(1)
    il.addWidget(lut_row)

    # ── Color customization + binary-mask toggle row ───────────────────
    from PySide6.QtWidgets import QPushButton as _QPushButton
    color_row = QWidget(inner)
    cr = QHBoxLayout(color_row)
    cr.setContentsMargins(8, 2, 8, 2)
    self._review_image_color_swatches = {}

    def _add_swatch(label_text: str, key: str, tooltip: str) -> None:
        cr.addWidget(QLabel(label_text, color_row))
        sw = _QPushButton("", color_row)
        sw.setFixedSize(28, 18)
        sw.setToolTip(tooltip)
        sw.clicked.connect(
            lambda _=False, k=key: self._pick_review_image_color(k)
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

    # Install wheel + mouse hooks for zoom / drag
    def _wheel(event):
        delta = event.angleDelta().y()
        if delta > 0:
            self._review_image_zoom_step(+1)
        elif delta < 0:
            self._review_image_zoom_step(-1)
    self._review_image_label.wheelEvent = _wheel
    self._review_image_label.mousePressEvent = self._on_review_image_press
    self._review_image_label.mouseMoveEvent = self._on_review_image_move
    self._review_image_label.mouseReleaseEvent = self._on_review_image_release

    def _leave(_ev):
        try:
            QToolTip.hideText()
        except Exception:
            pass
    self._review_image_label.leaveEvent = _leave

    # Default cursor reflects the initial Include-edit-mode state
    # (ForbiddenCursor when remove-cell mode is active).
    self._apply_review_image_cursor()
