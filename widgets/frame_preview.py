"""A zoomable, pannable canvas for one RGB frame.

Takes an ``(H, W, 3)`` uint8 array and puts it on screen fit-to-viewport × zoom,
with click-drag panning and a hover signal carrying the *image* pixel under the
cursor. It knows nothing about wells, channels, corrections or datasets — it is
handed pixels and reports coordinates — so it satisfies ``widgets/``'s
no-app-coupling contract (``tests/test_architecture_contracts.py::
test_widgets_do_not_import_the_app``) and can be mounted by any pane.

Zoom and pan are the widget's own state rather than a caller's, which is the
difference from ``playground.renderer.render_playground_display``: that one
reads ``PlaygroundState.zoom`` / ``pan_x`` / ``pan_y`` and stashes the display
geometry back on the app for its hover handler to re-derive. Owning it here means
the geometry never has to be published, and the hover mapping — the only piece of
arithmetic in this file — has exactly one home.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

_MIN_ZOOM = 0.1
_MAX_ZOOM = 8.0


class FramePreview(QWidget):
    """Scrollable canvas showing one RGB frame at fit-to-viewport × zoom."""

    #: (x, y) in image pixel coordinates, emitted as the cursor moves over the
    #: image. Out-of-image positions are not emitted at all.
    pixelHovered = Signal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rgb: np.ndarray | None = None
        self._zoom = 1.0
        self._pan = (0.0, 0.0)
        self._drag: tuple[float, float, float, float] | None = None
        # Display geometry from the last blit: (scaled_w, scaled_h, img_w, img_h).
        # Kept private — the hover mapping is the only reader.
        self._disp: tuple[int, int, int, int] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._canvas = QScrollArea(self)
        self._canvas.setWidgetResizable(False)
        self._canvas.setAlignment(Qt.AlignCenter)
        self._label = QLabel(self._canvas)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setMouseTracking(True)
        self._canvas.setWidget(self._label)
        layout.addWidget(self._canvas)

        self._label.mousePressEvent = self._on_press
        self._label.mouseMoveEvent = self._on_move
        # Re-blit whenever the viewport resizes — including the first real
        # layout pass, without which the image is stuck at its initial tiny size.
        orig_resize = self._canvas.resizeEvent

        def _on_resize(ev) -> None:
            orig_resize(ev)
            self._blit()
        self._canvas.resizeEvent = _on_resize

    # ── content ──────────────────────────────────────────────────────────────

    def set_rgb(self, rgb) -> None:
        """Show *rgb* (an (H, W, 3) uint8 array), or clear the canvas on None."""
        self._rgb = None if rgb is None else np.ascontiguousarray(rgb)
        self._blit()

    def clear(self) -> None:
        self.set_rgb(None)

    # ── view ─────────────────────────────────────────────────────────────────

    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, factor: float) -> None:
        self._zoom = max(_MIN_ZOOM, min(_MAX_ZOOM, float(factor)))
        self._blit()

    def zoom_in(self) -> None:
        self.set_zoom(self._zoom * 1.25)

    def zoom_out(self) -> None:
        self.set_zoom(self._zoom / 1.25)

    def fit(self) -> None:
        """Back to fit-to-viewport, centred."""
        self._zoom = 1.0
        self._pan = (0.0, 0.0)
        self._blit()

    # ── mouse ────────────────────────────────────────────────────────────────

    @staticmethod
    def _event_xy(event) -> tuple[float, float]:
        try:
            pos = event.position()          # Qt6 QMouseEvent
            return float(pos.x()), float(pos.y())
        except AttributeError:
            return float(event.x()), float(event.y())

    def _on_press(self, event) -> None:
        mx, my = self._event_xy(event)
        self._drag = (mx, my, self._pan[0], self._pan[1])

    def _on_move(self, event) -> None:
        if event.buttons() & Qt.LeftButton:
            if self._drag is None:
                return
            mx, my = self._event_xy(event)
            sx, sy, ox, oy = self._drag
            self._pan = (ox + (mx - sx), oy + (my - sy))
            self._blit()
            return
        xy = self.image_xy_at(*self._event_xy(event))
        if xy is not None:
            self.pixelHovered.emit(*xy)

    def image_xy_at(self, mx: float, my: float) -> tuple[int, int] | None:
        """Map a label-space position to an image pixel, or None if outside.

        The pixmap is centred inside a label that may be larger than it, so the
        centring offset has to come out before scaling back to image space.
        """
        if self._disp is None or self._rgb is None:
            return None
        nw, nh, iw, ih = self._disp
        off_x = max(0, (self._label.width() - nw) // 2)
        off_y = max(0, (self._label.height() - nh) // 2)
        px = int((mx - off_x) * iw / max(nw, 1))
        py = int((my - off_y) * ih / max(nh, 1))
        if 0 <= px < iw and 0 <= py < ih:
            return px, py
        return None

    # ── blit ─────────────────────────────────────────────────────────────────

    def _blit(self) -> None:
        if self._rgb is None:
            self._label.clear()
            self._disp = None
            return
        rgb = self._rgb
        ih, iw = rgb.shape[:2]
        vp = self._canvas.viewport()
        cw, ch = max(1, vp.width()), max(1, vp.height())
        fit = min(cw / max(iw, 1), ch / max(ih, 1))
        scale = max(0.05, fit * self._zoom)
        nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))

        qimg = QImage(rgb.data, iw, ih, 3 * iw, QImage.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(qimg)
        if (nw, nh) != (iw, ih):
            pixmap = pixmap.scaled(nw, nh, Qt.IgnoreAspectRatio,
                                   Qt.FastTransformation)
        self._label.setPixmap(pixmap)
        self._label.resize(max(nw, cw), max(nh, ch))
        self._disp = (nw, nh, iw, ih)

        cx = max(0, (max(nw, cw) - cw) // 2) - int(self._pan[0])
        cy = max(0, (max(nh, ch) - ch) // 2) - int(self._pan[1])
        hbar, vbar = self._canvas.horizontalScrollBar(), self._canvas.verticalScrollBar()
        hbar.setValue(max(hbar.minimum(), min(hbar.maximum(), cx)))
        vbar.setValue(max(vbar.minimum(), min(vbar.maximum(), cy)))
