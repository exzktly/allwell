"""Reusable square-region crop tool.

Originally lived in Movie Montage as a tangle of ``_montage_crop*``
methods on the runtime app. Extracted here so any tab that lays out
microscopy images in QLabels can reuse the same UI + interaction:

- A toggle button enters "crop mode"; the next click-drag on a wired
  thumbnail rubber-bands a square region.
- On release, the square (in source-image pixels) is stored as
  ``tool.crop = (y0, x0, y1, x1)`` and the consumer's ``on_change``
  callback fires (typically a redraw).
- A status label shows the current crop size; a Reset button clears it.

Each thumbnail QLabel that should be drag-croppable must carry a
``_raw_arr`` attribute (the source image) and optionally a ``_crop``
attribute reflecting any pre-applied crop (so coordinate conversion
maps label-local pixels back to full-image pixels even when the label
already shows a sub-region).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QRubberBand, QWidget

try:
    import numpy as _np
    _NP_AVAILABLE = True
except Exception:  # pragma: no cover
    _np = None  # type: ignore[assignment]
    _NP_AVAILABLE = False

from well_viewer.core.ui_helpers import btn_secondary

CropRect = Tuple[int, int, int, int]  # (y0, x0, y1, x1) in source pixels


class CropTool:
    """Encapsulated square-region crop UI + drag handling.

    Parameters
    ----------
    on_change:
        Called with no arguments whenever the crop state changes
        (mode toggled, crop set, crop cleared). Typically the consumer's
        redraw entry point.
    button_label, reset_label, idle_status:
        Customisable user-facing strings.
    """

    def __init__(
        self,
        on_change: Optional[Callable[[], None]] = None,
        *,
        button_label: str = "Crop",
        reset_label: str = "Reset Crop",
        idle_status: str = "(full FOV)",
    ) -> None:
        self._on_change = on_change
        self._button_label = button_label
        self._reset_label = reset_label
        self._idle_status = idle_status

        self._crop: Optional[CropRect] = None
        self._mode: bool = False
        self._drag: Optional[Dict[str, Any]] = None

        self._button: Optional[QPushButton] = None
        self._reset_button: Optional[QPushButton] = None
        self._status_label: Optional[QLabel] = None

    # ── public state ────────────────────────────────────────────────────────

    @property
    def crop(self) -> Optional[CropRect]:
        return self._crop

    @property
    def mode(self) -> bool:
        return self._mode

    @property
    def is_dragging(self) -> bool:
        return self._drag is not None

    def set_crop(self, crop: Optional[CropRect], *, notify: bool = True) -> None:
        self._crop = crop
        self._refresh_indicator()
        if notify:
            self._fire_change()

    def clear(self, *, notify: bool = True) -> None:
        self._crop = None
        self._refresh_indicator()
        if notify:
            self._fire_change()

    def toggle_mode(self, *, notify: bool = True) -> None:
        self._mode = not self._mode
        self._refresh_indicator()
        if notify:
            self._fire_change()

    # ── UI builders ─────────────────────────────────────────────────────────

    def make_button(self, parent: QWidget) -> QPushButton:
        btn = QPushButton(self._button_label, parent)
        btn.setProperty("variant", "toggle")
        btn.setCheckable(True)
        btn.setToolTip(self._tooltip_for(False))
        btn.clicked.connect(lambda _=False: self.toggle_mode())
        self._button = btn
        return btn

    def make_reset_button(self, parent: QWidget) -> QPushButton:
        btn = btn_secondary(parent, self._reset_label, self.clear)
        self._reset_button = btn
        return btn

    def make_status_label(self, parent: QWidget) -> QLabel:
        lbl = QLabel(self._idle_status, parent)
        lbl.setObjectName("Muted")
        self._status_label = lbl
        return lbl

    # ── array helpers ───────────────────────────────────────────────────────

    def apply_to_array(self, arr):
        """Return ``arr`` sliced to the current crop, or unchanged when None."""
        if self._crop is None or arr is None or not _NP_AVAILABLE:
            return arr
        a = _np.asarray(arr)
        ih, iw = a.shape[:2]
        y0, x0, y1, x1 = self._crop
        y0 = max(0, min(int(y0), ih))
        y1 = max(y0, min(int(y1), ih))
        x0 = max(0, min(int(x0), iw))
        x1 = max(x0, min(int(x1), iw))
        if y1 <= y0 or x1 <= x0:
            return a
        return a[y0:y1, x0:x1]

    # ── label coordinate conversion ─────────────────────────────────────────

    def label_to_image_xy(
        self, label: QLabel, lx: int, ly: int,
    ) -> Optional[Tuple[int, int]]:
        """Translate label-local pixels to source-image (y, x) pixels.

        Reads ``label._raw_arr`` (full source) and ``label._crop`` (any
        pre-applied crop the label is currently showing).
        """
        if not _NP_AVAILABLE:
            return None
        pm = label.pixmap()
        arr = getattr(label, "_raw_arr", None)
        if pm is None or arr is None or pm.width() <= 0 or pm.height() <= 0:
            return None
        crop = getattr(label, "_crop", None)
        full_h, full_w = _np.asarray(arr).shape[:2]
        if crop is not None:
            y0, x0, y1, x1 = crop
            view_h = max(1, int(y1) - int(y0))
            view_w = max(1, int(x1) - int(x0))
        else:
            y0 = x0 = 0
            view_h, view_w = full_h, full_w
        pw, ph = pm.width(), pm.height()
        lw, lh = label.width(), label.height()
        offset_x = (lw - pw) // 2
        offset_y = (lh - ph) // 2
        px = max(0, min(pw, lx - offset_x))
        py = max(0, min(ph, ly - offset_y))
        img_x = int(round(x0 + px * view_w / pw))
        img_y = int(round(y0 + py * view_h / ph))
        img_x = max(0, min(full_w, img_x))
        img_y = max(0, min(full_h, img_y))
        return (img_y, img_x)

    # ── drag handling (called from event hooks) ─────────────────────────────

    def begin_drag(self, label: QLabel, event) -> bool:
        """Start a rubber-band drag if the tool is in crop mode.

        Returns True when the event was consumed by the crop tool.
        """
        if not self._mode:
            return False
        if event.button() != Qt.LeftButton:
            return False
        pos = event.position()
        lx, ly = int(pos.x()), int(pos.y())
        rb = QRubberBand(QRubberBand.Rectangle, label)
        rb.setGeometry(lx, ly, 1, 1)
        rb.show()
        self._drag = {"label": label, "x0": lx, "y0": ly, "rb": rb}
        return True

    def update_drag(self, event) -> bool:
        if self._drag is None:
            return False
        pos = event.position()
        lx, ly = int(pos.x()), int(pos.y())
        x0, y0 = self._drag["x0"], self._drag["y0"]
        side = max(abs(lx - x0), abs(ly - y0))
        rb = self._drag["rb"]
        if side <= 0:
            rb.setGeometry(x0, y0, 1, 1)
            return True
        sign_x = 1 if lx >= x0 else -1
        sign_y = 1 if ly >= y0 else -1
        x1 = x0 + sign_x * side
        y1 = y0 + sign_y * side
        rb.setGeometry(min(x0, x1), min(y0, y1), side, side)
        return True

    def end_drag(self, _event) -> bool:
        if self._drag is None:
            return False
        drag = self._drag
        self._drag = None
        rb = drag["rb"]
        geom = rb.geometry()
        rb.hide()
        rb.deleteLater()
        if geom.width() < 4 or geom.height() < 4:
            return True  # tiny drag → leave existing crop alone
        label = drag["label"]
        tl = self.label_to_image_xy(label, geom.left(), geom.top())
        br = self.label_to_image_xy(label, geom.right(), geom.bottom())
        if tl is None or br is None:
            return True
        y0, x0 = tl
        y1, x1 = br
        if y1 <= y0 or x1 <= x0:
            return True
        side = max(2, max(y1 - y0, x1 - x0))
        if _NP_AVAILABLE:
            arr = getattr(label, "_raw_arr", None)
            if arr is not None:
                full_h, full_w = _np.asarray(arr).shape[:2]
                side = min(side, full_h - y0, full_w - x0)
        if side < 2:
            return True
        self._crop = (int(y0), int(x0), int(y0 + side), int(x0 + side))
        self._refresh_indicator()
        self._fire_change()
        return True

    # ── full-takeover event installer ───────────────────────────────────────

    def install_events(self, label: QLabel) -> None:
        """Wire press/move/release on ``label`` to the crop tool.

        For consumers that want the simplest behaviour: the label's mouse
        events are completely owned by the crop tool. Tabs that need to
        share the label with other interactions (tooltip, click-to-pick)
        should call :meth:`begin_drag`/:meth:`update_drag`/:meth:`end_drag`
        from their own event dispatchers instead.
        """
        label.setMouseTracking(True)
        label.setCursor(Qt.CrossCursor)

        def _press(ev, _self=self, _lbl=label):
            _self.begin_drag(_lbl, ev)

        def _move(ev, _self=self):
            _self.update_drag(ev)

        def _release(ev, _self=self):
            _self.end_drag(ev)

        label.mousePressEvent = _press
        label.mouseMoveEvent = _move
        label.mouseReleaseEvent = _release

    # ── UI sync ─────────────────────────────────────────────────────────────

    def _refresh_indicator(self) -> None:
        btn = self._button
        if btn is not None:
            on = self._mode
            btn.setChecked(on)
            btn.setText(f"{self._button_label} ✓" if on else self._button_label)
            btn.setProperty("variant", "toggle")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.setToolTip(self._tooltip_for(on))
        lbl = self._status_label
        if lbl is not None:
            if self._crop is None:
                lbl.setText(self._idle_status)
            else:
                y0, x0, y1, x1 = self._crop
                lbl.setText(f"Crop: {x1 - x0}×{y1 - y0} px @ ({x0},{y0})")

    def _tooltip_for(self, on: bool) -> str:
        if on:
            return (
                "Crop-selection mode is ON.\n"
                "Click and drag on a thumbnail to define a square region; "
                "every image zooms into that region."
            )
        return (
            "Click to enter crop-selection mode.\n"
            "Then click and drag on a thumbnail to define a square region "
            "that every image will zoom into."
        )

    def _fire_change(self) -> None:
        if self._on_change is not None:
            self._on_change()
