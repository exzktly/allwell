"""The Scatter tab's single-cell viewer: the cell a clicked point stands for.

A slide-out in the Scatter tab's right dock (shared with the Properties
sidebar, one panel at a time). It shows one channel of the cell at a time with
the cell's boundary drawn over it.

**One image path.** The cell is addressed by its well, field, timepoint and
nucleus id, read from the clicked row, and every frame comes through
:func:`review.data.cell_crop.load_cell` → :mod:`review.data.frame_load` — the
resolver the Image Table and the Cell Browser use. The popup used to carry its
own: it guessed a zip member from the row's ``filename``, and when several
matched it took the first, saying so only in a diagnostics box. That box is
gone; a cell that cannot be shown says why, in the image area.

**One brightness rule.** A channel is shown with the shared per-channel display
window (``ImageViewState.lut_by_channel``, the one the Image Table and the Cell
Browser use), else the crop's 1st–99th percentile
(:func:`review.data.cell_crop.display_window`). Typing a window writes the
shared one; *Auto* clears it. The caption under the image says which rule drew
it, so a brighter-looking cell is never mistaken for brighter data.

**Off the GUI thread.** Loading reads up to one field per channel from the
well's archives. It runs through ``core.worker_bridge.run_off_thread``, and a
load that finishes after another point was clicked is dropped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from review.data.cell_crop import AUTO_WINDOW, display_window, normalise
from review.data.data_loading import get_rows
from ui import theme

_logger = logging.getLogger(__name__)

# Pixel geometry for this module's controls, named once (ARCHITECTURE §8).
_IMG_LABEL_MIN_W = 300
_IMG_LABEL_MIN_H = 300

#: Width of the slide-out (the Properties sidebar's order of magnitude, so the
#: shared dock does not jump when the two swap).
_CELL_VIEWER_WIDTH = 380


@dataclass(frozen=True)
class CellAddress:
    """Which cell the popup shows, as the image lookup needs it."""

    well: str
    fov: str
    tp: str
    nucleus_id: int

    @property
    def caption(self) -> str:
        parts = [self.well, self.fov, self.tp, f"#{self.nucleus_id}"]
        return " · ".join(p for p in parts if p)


def address_from_row(ctx, well: str, row_idx: int, nuclear_id: str) -> tuple[CellAddress | None, str]:
    """The clicked row's cell, or ``None`` and the sentence saying why not.

    The point carries the row's position in the well's rows and its nucleus
    id. The two are checked against each other: a row whose id is not the one
    the point was drawn with means the rows changed under the plot, and the
    popup says so rather than showing whichever cell sits there now.
    """
    if well not in ctx.state.dataset.well_paths:
        return None, f"Well {well} is no longer loaded."
    rows = get_rows(ctx, well)
    if not 0 <= int(row_idx) < len(rows):
        return None, f"Row {row_idx} is not in {well}'s rows any more; redraw the plot."
    row = rows.iloc[int(row_idx)]
    try:
        nid = int(float(row.get("nucleus_id")))
    except (TypeError, ValueError):
        return None, f"The row for this point in {well} carries no nucleus id."
    if str(nuclear_id).strip():
        try:
            if int(float(nuclear_id)) != nid:
                return None, (f"The plotted point is nucleus {nuclear_id} but that row of {well} now "
                              f"holds nucleus {nid}; redraw the plot.")
        except (TypeError, ValueError):
            pass
    fov = str(row.get("fov") if row.get("fov") is not None else "").strip()
    tp = str(row.get("timepoint") if row.get("timepoint") is not None else "").strip()
    return CellAddress(well=well, fov=fov, tp=tp, nucleus_id=nid), ""


def popup_channels(ctx) -> list[str]:
    """The channels offered: the quantified ones, then the nuclear stain."""
    chans = [str(c) for c in sorted(ctx.state.channel.fluor_channels or ())]
    info = ctx.state.dataset.pipeline_info or {}
    nuclear = str(info.get("nuclear_token") or "").strip()
    if nuclear and nuclear.lower() not in {c.lower() for c in chans}:
        chans.append(nuclear)
    return chans


def _grey_with_outline(crop: np.ndarray, window, outline_mask) -> np.ndarray:
    """The crop in grey against *window*, with the cell's boundary in the outline ink."""
    disp = (normalise(crop, window) * 255.0).astype(np.uint8)
    rgb = np.stack([disp, disp, disp], axis=-1)
    if outline_mask is not None and np.shape(outline_mask) == rgb.shape[:2]:
        ink = theme.Colors.cell_outline.lstrip("#")
        rgb[np.asarray(outline_mask, dtype=bool)] = [int(ink[i:i + 2], 16) for i in (0, 2, 4)]
    return rgb


class ScatterCellViewer(QWidget):
    """The slide-out: header, channel and source, the image, its brightness."""

    def __init__(self, parent, ctx) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.address: CellAddress | None = None
        self._crops = None
        self._request = 0
        self._bridge = None
        self._build_ui()

    # ── layout ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 10)

        head = QHBoxLayout()
        self._title_label = QLabel("Cell")
        tfont = self._title_label.font()
        tfont.setBold(True)
        self._title_label.setFont(tfont)
        head.addWidget(self._title_label, 1)
        close_btn = QPushButton("✕")
        close_btn.setObjectName("Ghost")
        close_btn.setFixedWidth(theme.Metrics.icon_btn_w)
        close_btn.setToolTip("Close the cell viewer")
        close_btn.clicked.connect(self._close_dock)
        head.addWidget(close_btn, 0)
        layout.addLayout(head)

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("Channel:"))
        self._channel_dropdown = QComboBox()
        self._channel_dropdown.currentIndexChanged.connect(self._display_current_image)
        ctrl.addWidget(self._channel_dropdown)
        ctrl.addSpacing(10)
        ctrl.addWidget(QLabel("Source:"))
        self._source_dropdown = QComboBox()
        self._source_dropdown.addItem("Raw", "raw")
        self._source_dropdown.addItem("Corrected", "corrected")
        self._source_dropdown.setToolTip(
            "Raw: the unprocessed input frame.\n"
            "Corrected: the pipeline's top-hat output (raw where a field was not corrected).")
        self._source_dropdown.currentIndexChanged.connect(lambda *_a: self.reload())
        ctrl.addWidget(self._source_dropdown)
        ctrl.addStretch(1)
        layout.addLayout(ctrl)

        self._img_label = QLabel("")
        self._img_label.setAlignment(Qt.AlignCenter)
        self._img_label.setWordWrap(True)
        self._img_label.setMinimumSize(_IMG_LABEL_MIN_W, _IMG_LABEL_MIN_H)
        layout.addWidget(self._img_label, 1)

        self._window_note = QLabel("")
        self._window_note.setObjectName("Muted")
        self._window_note.setWordWrap(True)
        layout.addWidget(self._window_note)

        lut = QHBoxLayout()
        lut.addWidget(QLabel("Brightness min:"))
        self._lut_min_edit = QLineEdit("")
        self._lut_min_edit.setFixedWidth(theme.Metrics.num_field_w)
        self._lut_min_edit.editingFinished.connect(self._on_lut_typed)
        lut.addWidget(self._lut_min_edit)
        lut.addWidget(QLabel("max:"))
        self._lut_max_edit = QLineEdit("")
        self._lut_max_edit.setFixedWidth(theme.Metrics.num_field_w)
        self._lut_max_edit.editingFinished.connect(self._on_lut_typed)
        lut.addWidget(self._lut_max_edit)
        self._auto_btn = QPushButton("Auto")
        self._auto_btn.setToolTip("Forget this channel's shared brightness and stretch each cell "
                                  "to its own 1st–99th percentile")
        self._auto_btn.clicked.connect(self._auto_lut)
        lut.addWidget(self._auto_btn)
        lut.addStretch(1)
        layout.addLayout(lut)

        jump = QHBoxLayout()
        self._show_in_seg_btn = QPushButton("Show in Segmentation")
        self._show_in_seg_btn.setProperty("variant", "secondary")
        self._show_in_seg_btn.setToolTip("Open this cell's field in Image Browser → Segmentation, "
                                         "with the cell outlined")
        self._show_in_seg_btn.clicked.connect(self._show_in_segmentation)
        self._show_in_seg_btn.setEnabled(False)
        jump.addWidget(self._show_in_seg_btn)
        jump.addStretch(1)
        layout.addLayout(jump)

    def _show_in_segmentation(self) -> None:
        a = self.address
        if a is None:
            return
        shown = self.ctx.features.query("image_browser.show_cell", self.ctx, a.well, a.fov, a.tp,
                                        str(a.nucleus_id), default=False)
        if not shown:
            self.ctx.notify.set_status("This build has no Segmentation page to open the cell in.")

    def _close_dock(self) -> None:
        from review.core.ui_helpers import hide_plot_dock
        hide_plot_dock(self.parentWidget())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._crops is not None:
            self._display_current_image()

    # ── loading ─────────────────────────────────────────────────────────────

    def show_cell(self, well: str, row_idx: int, nuclear_id: str) -> None:
        """Show the cell behind a clicked point (well, row position, nucleus id)."""
        address, problem = address_from_row(self.ctx, well, row_idx, nuclear_id)
        self.address = address
        self._show_in_seg_btn.setEnabled(address is not None)
        self._title_label.setText(f"Cell · {address.caption}" if address else f"Cell · {well}")
        if address is None:
            self._show_problem(problem)
            return
        from review.core.cell_focus import focus_cell
        focus_cell(self.ctx, address.well, address.fov, address.tp, address.nucleus_id)
        self.reload()

    def reload(self) -> None:
        """(Re)load every channel of the current cell, off the GUI thread."""
        if self.address is None:
            return
        from core.worker_bridge import run_off_thread
        from review.data.cell_crop import load_cell

        self._request += 1
        request = self._request
        self._crops = None
        self._img_label.setPixmap(QPixmap())
        self._img_label.setText("Loading…")
        self._window_note.setText("")
        address, ctx = self.address, self.ctx
        channels = popup_channels(ctx)
        use_tophat = self._source_dropdown.currentData() == "corrected"

        def _load():
            return load_cell(ctx, well=address.well, fov=address.fov, tp=address.tp,
                             nucleus_id=address.nucleus_id, channels=channels,
                             use_tophat=use_tophat)

        def _done(crops, _request=request):
            if _request != self._request:
                return            # another point was clicked while this one loaded
            self._on_loaded(crops)

        def _failed(message, _request=request):
            if _request != self._request:
                return
            _logger.debug("scatter cell load failed: %s", message)
            self._show_problem(f"The cell could not be loaded: {message}")

        self._bridge = run_off_thread(_load, on_done=_done, on_failed=_failed)

    def _on_loaded(self, crops) -> None:
        self._crops = crops
        if crops.problem:
            self._show_problem(crops.problem)
            return
        current = self._channel_dropdown.currentText()
        blocked = self._channel_dropdown.blockSignals(True)
        try:
            self._channel_dropdown.clear()
            self._channel_dropdown.addItems(list(crops.crops))
            if current in crops.crops:
                self._channel_dropdown.setCurrentText(current)
        finally:
            self._channel_dropdown.blockSignals(blocked)
        if not crops.crops:
            self._show_problem("No channel could be shown: "
                               + "; ".join(crops.missing.values()) + ".")
            return
        self._display_current_image()

    def _show_problem(self, text: str) -> None:
        self._img_label.setPixmap(QPixmap())
        self._img_label.setText(text)
        self._window_note.setText("")

    # ── drawing ─────────────────────────────────────────────────────────────

    def _shared_window(self, channel: str):
        return self.ctx.state.image_view.get_lut(channel)

    def _display_current_image(self, *_args) -> None:
        crops = self._crops
        channel = self._channel_dropdown.currentText()
        if crops is None or crops.problem or channel not in crops.crops:
            return
        crop = crops.crops[channel]
        window, rule = display_window(crop, self._shared_window(channel))
        self._lut_min_edit.setText(f"{window[0]:.1f}")
        self._lut_max_edit.setText(f"{window[1]:.1f}")
        note = ("Brightness: automatic for this cell (1st–99th percentile)"
                if rule == AUTO_WINDOW else
                f"Brightness: the shared {channel} window, as in the Cell Browser and Image Table")
        missing = [f"{c}: {why}" for c, why in crops.missing.items()]
        if missing:
            note += "\nNot shown — " + "; ".join(missing)
        self._window_note.setText(note)
        rgb = np.ascontiguousarray(_grey_with_outline(crop, window, crops.outline))
        h, w, _ = rgb.shape
        qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        size_w = max(self._img_label.width(), _IMG_LABEL_MIN_W)
        size_h = max(self._img_label.height(), _IMG_LABEL_MIN_H)
        self._img_label.setText("")
        self._img_label.setPixmap(QPixmap.fromImage(qimg).scaled(
            size_w, size_h, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _on_lut_typed(self) -> None:
        """A typed window becomes the channel's shared one."""
        channel = self._channel_dropdown.currentText()
        try:
            lo, hi = float(self._lut_min_edit.text()), float(self._lut_max_edit.text())
        except ValueError:
            self._display_current_image()     # put the window in force back in the boxes
            return
        if not channel or hi <= lo:
            self._display_current_image()
            return
        self.ctx.state.image_view.set_lut(channel, lo, hi)
        self._display_current_image()

    def _auto_lut(self) -> None:
        """Forget the channel's shared window; each crop stretches to itself."""
        channel = self._channel_dropdown.currentText()
        if channel:
            self.ctx.state.image_view.lut_by_channel.pop(channel.strip().lower(), None)
        self._display_current_image()


def open_scatter_cell_viewer(ctx, well_label: str, filename: str, nuclear_id: str,
                             row_idx: int) -> None:
    """Open (or update) the cell viewer in the Scatter tab's right dock.

    *filename* is accepted for the callers' sake and not used: the cell is found
    by its well, field, timepoint and nucleus id, never by guessing a file.
    """
    from review.core.ui_helpers import mount_widget_in_plot_dock

    del filename
    dock = ctx.widgets.get_or_none("_scatter_export_dock")
    viewer = ctx.runtime.scatter.cell_viewer
    if viewer is None:
        viewer = ScatterCellViewer(dock, ctx)
        ctx.runtime.scatter.cell_viewer = viewer
    viewer.show_cell(well_label, row_idx, nuclear_id)
    if dock is not None:
        mount_widget_in_plot_dock(dock, viewer, _CELL_VIEWER_WIDTH)
    else:  # pragma: no cover — the per-cell scatter tab always builds a dock
        viewer.show()
        viewer.raise_()
