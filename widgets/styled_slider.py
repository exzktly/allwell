"""StyledSlider — a fully custom-painted QSlider.

QSS sub-control styling of ``QSlider`` (groove / handle / sub-page) is
notoriously platform-flaky — depending on the active ``QStyle`` the handle can
fall back to the native look or get squished. So this subclass paints the groove
(token ``border_subtle``), the filled portion (token ``accent``) and a circular
handle itself, with an accent halo on keyboard focus, and drives the value from
the pointer x-position so the hit area matches the painting.

Behaves like ``QSlider`` (same API / signals). Optimised for horizontal use;
vertical sliders fall back to the default rendering.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import QRectF, QSize, Qt  # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import QSizePolicy, QSlider  # noqa: E402

import theme  # noqa: E402
from widgets._support import with_alpha  # noqa: E402


class StyledSlider(QSlider):
    def __init__(self, orientation: Qt.Orientation = Qt.Horizontal, parent=None) -> None:
        super().__init__(orientation, parent)
        self.setObjectName("StyledSlider")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        # Strip any inherited QSS sub-control rules; we paint everything.
        self.setStyleSheet("QSlider#StyledSlider { background: transparent; }")
        if orientation == Qt.Horizontal:
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.setMinimumHeight(self._handle_d() + 6)
        else:
            self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
            self.setMinimumWidth(self._handle_d() + 6)

    def bindingAdapter(self):
        """``(getter, setter, change_signal)`` for binding-driven panels."""
        return (self.value, self.setValue, self.valueChanged)

    # ── metrics (font relative → DPI aware) ──────────────────────────────
    def _groove_h(self) -> int:
        return max(4, round(self.fontMetrics().height() * 0.28))

    def _handle_d(self) -> int:
        return max(14, round(self.fontMetrics().height() * 1.05))

    def _margin(self) -> float:
        return self._handle_d() / 2.0 + 1.0

    def sizeHint(self) -> QSize:
        hd = self._handle_d()
        if self.orientation() == Qt.Horizontal:
            return QSize(max(120, super().sizeHint().width()), hd + 6)
        return QSize(hd + 6, max(120, super().sizeHint().height()))

    def minimumSizeHint(self) -> QSize:
        hd = self._handle_d()
        if self.orientation() == Qt.Horizontal:
            return QSize(hd * 3, hd + 6)
        return QSize(hd + 6, hd * 3)

    # ── value <-> pixel mapping ──────────────────────────────────────────
    def _track_bounds(self) -> tuple[float, float]:
        m = self._margin()
        lo = m
        hi = max(m + 1.0, self.width() - m)
        return lo, hi

    def _fraction(self) -> float:
        span = (self.maximum() - self.minimum()) or 1
        frac = (self.value() - self.minimum()) / span
        if self.invertedAppearance():
            frac = 1.0 - frac
        return min(1.0, max(0.0, frac))

    def _value_from_x(self, x: float) -> int:
        lo, hi = self._track_bounds()
        frac = (x - lo) / (hi - lo) if hi > lo else 0.0
        frac = min(1.0, max(0.0, frac))
        if self.invertedAppearance():
            frac = 1.0 - frac
        return round(self.minimum() + frac * (self.maximum() - self.minimum()))

    # ── input ────────────────────────────────────────────────────────────
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.orientation() == Qt.Horizontal:
            self.setSliderDown(True)
            self.setValue(self._value_from_x(event.position().x()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if (event.buttons() & Qt.LeftButton) and self.orientation() == Qt.Horizontal:
            self.setValue(self._value_from_x(event.position().x()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self.isSliderDown():
            self.setSliderDown(False)
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        # Default QSlider wheel handling is fine; keep it.
        super().wheelEvent(event)

    # ── painting ─────────────────────────────────────────────────────────
    def paintEvent(self, event) -> None:  # noqa: N802
        if self.orientation() != Qt.Horizontal:
            return super().paintEvent(event)

        c = theme.Colors
        enabled = self.isEnabled()
        gh = self._groove_h()
        hd = self._handle_d()
        lo, hi = self._track_bounds()
        cy = self.height() / 2.0
        handle_cx = lo + self._fraction() * (hi - lo)

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)

        # groove (full extent)
        p.setBrush(QColor(c.border_subtle))
        p.drawRoundedRect(QRectF(lo, cy - gh / 2.0, hi - lo, gh), gh / 2.0, gh / 2.0)

        # filled portion up to the handle
        fill_w = handle_cx - lo
        if fill_w > 0.5:
            p.setBrush(QColor(c.accent if enabled else c.accent_dim))
            p.drawRoundedRect(QRectF(lo, cy - gh / 2.0, fill_w, gh), gh / 2.0, gh / 2.0)

        # handle
        handle_rect = QRectF(handle_cx - hd / 2.0, cy - hd / 2.0, hd, hd)
        p.setBrush(QColor(c.panel_elevated if enabled else c.panel))
        p.setPen(QPen(QColor(c.rail if enabled else c.border_subtle), 2.0))
        p.drawEllipse(handle_rect)

        if enabled and self.hasFocus():
            grow = max(2.0, hd * 0.2)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(with_alpha(c.accent, 0.45), max(1.0, hd * 0.16)))
            p.drawEllipse(handle_rect.adjusted(-grow, -grow, grow, grow))
        p.end()


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import (
        QApplication, QFormLayout, QHBoxLayout, QLabel, QVBoxLayout,
        QWidget as _QW,
    )

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    root = _QW()
    root.setWindowTitle("StyledSlider — demo")
    pad = theme.Spacing.lg
    outer = QVBoxLayout(root)
    outer.setContentsMargins(pad, pad, pad, pad)
    outer.setSpacing(theme.Spacing.md)

    title = QLabel("StyledSlider")
    title.setObjectName("Title")
    outer.addWidget(title)

    form = QFormLayout()
    form.setSpacing(theme.Spacing.md)

    s1 = StyledSlider()
    s1.setRange(0, 100)
    s1.setValue(40)
    s2 = StyledSlider()
    s2.setRange(0, 100)
    s2.setValue(72)
    s3 = StyledSlider()
    s3.setRange(0, 100)
    s3.setValue(20)
    s3.setEnabled(False)

    val_lbl = QLabel("40")
    val_lbl.setObjectName("Mono")
    row = QHBoxLayout()
    row.addWidget(s1, 1)
    row.addWidget(val_lbl)
    form.addRow("Threshold:", row)
    form.addRow("Opacity:", s2)
    form.addRow("Disabled:", s3)
    outer.addLayout(form)

    s1.valueChanged.connect(lambda v: val_lbl.setText(str(v)))
    outer.addStretch(1)

    root.resize(400, 240)
    root.show()
    _sys.exit(app.exec())
