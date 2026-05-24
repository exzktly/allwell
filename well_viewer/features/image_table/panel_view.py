"""Reusable image-display panel widget (Qt port).

``_ImagePanel`` is a toggleable QLabel-based widget that renders a numpy
array with an optional LUT min/max editor and pixel-value tooltip.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QToolTip, QVBoxLayout, QWidget,
)

from well_viewer.core.ui_helpers import btn_card, btn_secondary


_LABEL_PALETTE = None


def _label_to_rgb(arr):
    global _LABEL_PALETTE
    import numpy as _np
    if _LABEL_PALETTE is None:
        _LABEL_PALETTE = _np.array([
            [255, 255, 255], [31, 119, 180], [255, 127, 14], [44, 160, 44],
            [214, 39, 40], [148, 103, 189], [140, 86, 75], [227, 119, 194],
            [127, 127, 127], [188, 189, 34], [23, 190, 207], [57, 220, 205],
            [255, 187, 51], [166, 206, 227], [178, 223, 138], [251, 154, 153],
            [253, 191, 111], [202, 178, 214], [106, 61, 154], [177, 89, 40],
        ], dtype=_np.uint8)
    h, w = arr.shape[:2]
    rgb = _np.zeros((h, w, 3), dtype=_np.uint8)
    for uid in _np.unique(arr):
        rgb[arr == uid] = _LABEL_PALETTE[int(uid) % len(_LABEL_PALETTE)]
    return rgb


class _ImagePanel:
    """Toggleable image panel with optional LUT editor and pixel tooltip."""

    def __init__(self, parent: QWidget, title: str, tooltip,
                 show_lut: bool = False) -> None:
        self._tooltip = tooltip  # legacy shim; Qt uses QToolTip directly
        self._pixmap: Optional[QPixmap] = None
        self._raw_arr = None
        self._img_x0 = 0
        self._img_y0 = 0
        self._img_scale = 1.0
        self._full_path = ""
        self._colourmap: Optional[str] = None
        self._show_lut = show_lut
        self._lut_min = 0.0
        self._lut_max = 65535.0

        # Root container on parent layout
        parent_layout = parent.layout()
        if parent_layout is None:
            parent_layout = QVBoxLayout(parent)
            parent.setLayout(parent_layout)

        container = QWidget(parent)
        cl = QVBoxLayout(container)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(2)
        parent_layout.addWidget(container, 1)

        # Header
        hdr = QWidget(container)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(10, 6, 10, 2)
        title_lbl = QLabel(title, hdr)
        title_lbl.setProperty("role", "section")
        hl.addWidget(title_lbl)
        hl.addStretch(1)
        self._toggle_btn = btn_secondary(hdr, "Hide", self._toggle)
        hl.addWidget(self._toggle_btn)
        cl.addWidget(hdr)

        # Filename label
        self._file_lbl = QLabel("", container)
        self._file_lbl.setObjectName("Muted")
        self._file_lbl.setWordWrap(True)
        self._file_lbl.setAlignment(Qt.AlignLeft)
        cl.addWidget(self._file_lbl)

        # Body (collapsible)
        self._body = QWidget(container)
        bl = QVBoxLayout(self._body)
        bl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self._body, 1)

        if show_lut:
            row = QWidget(self._body)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(10, 2, 10, 0)
            rl.addWidget(QLabel("LUT min", row))
            self._lut_min_edit = QLineEdit("0", row)
            self._lut_min_edit.setFixedWidth(70)
            self._lut_min_edit.editingFinished.connect(self._lut_commit)
            rl.addWidget(self._lut_min_edit)
            rl.addWidget(QLabel("max", row))
            self._lut_max_edit = QLineEdit("65535", row)
            self._lut_max_edit.setFixedWidth(70)
            self._lut_max_edit.editingFinished.connect(self._lut_commit)
            rl.addWidget(self._lut_max_edit)
            rl.addWidget(btn_card(row, "Auto", self._lut_auto))
            rl.addStretch(1)
            bl.addWidget(row)

        self._canvas = QLabel(self._body)
        self._canvas.setAlignment(Qt.AlignCenter)
        self._canvas.setMouseTracking(True)
        self._canvas.setFrameShape(QFrame.Box)
        self._canvas.mouseMoveEvent = self._canvas_motion
        self._canvas.leaveEvent = lambda _e: QToolTip.hideText()
        self._canvas.resizeEvent = lambda _e: self._render()
        bl.addWidget(self._canvas, 1)

    def _toggle(self) -> None:
        if self._body.isVisible():
            self._body.hide()
            self._toggle_btn.setText("Show")
        else:
            self._body.show()
            self._toggle_btn.setText("Hide")

    def _lut_commit(self) -> None:
        if not self._show_lut:
            return
        try:
            lo = float(self._lut_min_edit.text())
        except ValueError:
            lo = self._lut_min
        try:
            hi = float(self._lut_max_edit.text())
        except ValueError:
            hi = self._lut_max
        if hi <= lo:
            hi = lo + 1.0
        self._lut_min = lo
        self._lut_max = hi
        self._lut_min_edit.setText(f"{lo:.0f}")
        self._lut_max_edit.setText(f"{hi:.0f}")
        self._render()

    def _lut_auto(self) -> None:
        import numpy as _np
        if self._raw_arr is None:
            return
        arr = _np.asarray(self._raw_arr, dtype=_np.float32)
        lo, hi = float(arr.min()), float(arr.max())
        if hi <= lo:
            hi = lo + 1.0
        self._lut_min = lo
        self._lut_max = hi
        if self._show_lut:
            self._lut_min_edit.setText(f"{lo:.0f}")
            self._lut_max_edit.setText(f"{hi:.0f}")
        self._render()

    def render_arr(self, arr, filename: str,
                   full_path: str = "", colourmap: Optional[str] = None) -> None:
        import numpy as _np
        self._file_lbl.setText(filename)
        self._full_path = full_path or filename
        self._file_lbl.setToolTip(self._full_path)
        self._raw_arr = arr
        self._colourmap = colourmap
        if self._show_lut:
            lo, hi = float(_np.asarray(arr).min()), float(_np.asarray(arr).max())
            if hi <= lo:
                hi = lo + 1.0
            self._lut_min = lo
            self._lut_max = hi
            self._lut_min_edit.setText(f"{lo:.0f}")
            self._lut_max_edit.setText(f"{hi:.0f}")
        self._render()

    def show_message(self, text: str, colour: str = "") -> None:
        self._pixmap = None
        self._raw_arr = None
        self._file_lbl.setText("")
        self._full_path = ""
        self._canvas.setText(text)

    def clear(self, message: str = "") -> None:
        self._raw_arr = None
        self._pixmap = None
        self._file_lbl.setText("")
        self._full_path = ""
        self._canvas.clear()
        if message:
            self.show_message(message)

    def _render(self) -> None:
        if self._raw_arr is None:
            return
        try:
            import numpy as _np
        except ImportError:
            return
        cw = max(1, self._canvas.width())
        ch = max(1, self._canvas.height())
        try:
            arr = _np.asarray(self._raw_arr, dtype=_np.float32)
            if self._colourmap == "label":
                rgb = _label_to_rgb(arr.astype(_np.int32))
            else:
                lo = self._lut_min if self._show_lut else float(arr.min())
                hi = self._lut_max if self._show_lut else float(arr.max())
                if hi <= lo:
                    hi = lo + 1.0
                clipped = (_np.clip(arr, lo, hi) - lo) / (hi - lo) * 255.0
                gray = clipped.astype(_np.uint8)
                rgb = _np.stack([gray, gray, gray], axis=-1)
            ih, iw = rgb.shape[:2]
            rgb_c = _np.ascontiguousarray(rgb)
            qimg = QImage(rgb_c.data, iw, ih, iw * 3, QImage.Format_RGB888)
            qimg = qimg.copy()
            pix = QPixmap.fromImage(qimg)
            scale = min(cw / iw, ch / ih, 1.0)
            nw = max(1, int(iw * scale))
            nh = max(1, int(ih * scale))
            self._img_scale = scale
            self._img_x0 = (cw - nw) // 2
            self._img_y0 = (ch - nh) // 2
            self._pixmap = pix.scaled(
                nw, nh, Qt.KeepAspectRatio, Qt.SmoothTransformation,
            )
            self._canvas.setPixmap(self._pixmap)
        except Exception as exc:
            self._canvas.setText(f"Render error:\n{exc}")

    def _canvas_motion(self, event) -> None:
        import numpy as _np
        if self._raw_arr is None or self._img_scale <= 0:
            QToolTip.hideText()
            return
        pos = event.position().toPoint()
        ix = (pos.x() - self._img_x0) / self._img_scale
        iy = (pos.y() - self._img_y0) / self._img_scale
        arr = _np.asarray(self._raw_arr)
        h, w = arr.shape[:2]
        if not (0 <= ix < w and 0 <= iy < h):
            QToolTip.hideText()
            return
        px, py = int(ix), int(iy)
        val = arr[py, px]
        vstr = (
            "  ".join(f"{v:.0f}" for v in val)
            if hasattr(val, "__len__")
            else f"{float(val):.1f}"
        )
        global_pos = self._canvas.mapToGlobal(pos)
        QToolTip.showText(global_pos, f"x={px}  y={py}   value: {vstr}", self._canvas)
