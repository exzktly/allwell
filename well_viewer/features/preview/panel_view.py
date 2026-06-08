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

from well_viewer.core.ui_helpers import btn_card, btn_danger, btn_secondary
from well_viewer.features.crop.controller import (
    clear_crop_region, reset_crop_and_overrides, toggle_crop_edit_mode,
)
from well_viewer.features.review_image.controller import on_review_image_channel_selected
from well_viewer.features.review_image.renderer import refresh_review_image
from functools import partial
from well_viewer.features.review_image.controller import on_review_image_move
from well_viewer.features.review_image.controller import on_review_image_press
from well_viewer.features.review_image.controller import on_review_image_release
from well_viewer.features.review_image.controller import pick_review_image_color


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

    # Quality-control debris gate — the Segmentation tab is the QC editing
    # home: tuning the size/solidity bounds here re-flags cells live (they
    # render excluded in the overlay below) and applies to the whole dataset.
    try:
        from well_viewer.features.qc.tab_view import build_qc_gate_controls
        il.addWidget(build_qc_gate_controls(self, inner))
    except Exception:
        pass

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
            pass
    self._review_image_label.leaveEvent = _leave

    # Default cursor reflects the initial Include-edit-mode state
    # (ForbiddenCursor when remove-cell mode is active).
    self._apply_review_image_cursor()


def refresh_fov_btn_state(app) -> None:
    """Sync every per-FOV toggle button's text/variant state."""
    is_on = bool(app._app_state.plot.use_fov_replicates)
    text = "FOV ✓" if is_on else "FOV"
    variant = "toggle_active" if is_on else "toggle"
    tooltip = (
        "Compute error bands across per-FOV means.\n"
        "With replicate sets active, FOVs are pooled across all wells in each set;\n"
        "otherwise FOVs are pooled within each well. Pairs with the SEM/SD toggle."
    )
    for btn in list(getattr(app, "_fov_btns", []) or []):
        btn.setEnabled(True)
        btn.setText(text)
        btn.setToolTip(tooltip)
        btn.setProperty("variant", variant)
        btn.style().unpolish(btn)
        btn.style().polish(btn)
    for obs in list(getattr(app, "_fov_observers", []) or []):
        try:
            obs(is_on)
        except Exception:
            pass

