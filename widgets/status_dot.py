"""StatusDot — a small filled circle with a soft translucent halo.

Used for the "Saved" indicator in the title bar, the ``● Connected`` marker in
the status bar, and (at small sizes) condition dots. The diameter derives from
the font (DPI-aware); colours come from ``theme`` tokens.

API
---
* ``StatusDot(status="neutral", parent=None, *, diameter=None)``
* ``setStatus(name)`` where *name* ∈ ``{"success", "warn", "danger", "accent",
  "neutral"}`` — also accepts an arbitrary token attribute name or a hex string.
* ``setLabel(text)`` — optional inline text drawn after the dot.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

import theme
from widgets._support import with_alpha

_STATUS_TOKENS = {
    "success": "success",
    "ok": "success",
    "warn": "warn",
    "warning": "warn",
    "danger": "danger",
    "error": "danger",
    "accent": "accent",
    "neutral": "text_muted",
    "muted": "text_muted",
}


class StatusDot(QWidget):
    def __init__(self, status: str = "neutral", parent=None, *,
                 diameter: int | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusDot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._explicit_d = int(diameter) if diameter else None
        self._label = ""
        self._color = self._resolve(status)
        self.setStyleSheet("#StatusDot { background: transparent; }")

    # ── API ──────────────────────────────────────────────────────────────
    def setStatus(self, status: str) -> None:
        self._color = self._resolve(status)
        self.update()

    def setLabel(self, text: str) -> None:
        self._label = text or ""
        self.updateGeometry()
        self.update()

    def color(self) -> QColor:
        return QColor(self._color)

    # ── geometry ─────────────────────────────────────────────────────────
    def _dot_d(self) -> int:
        if self._explicit_d:
            return self._explicit_d
        return max(6, round(self.fontMetrics().height() * 0.45))

    def _halo_d(self) -> int:
        return round(self._dot_d() * 2.0)

    def sizeHint(self) -> QSize:
        h = max(self._halo_d(), self.fontMetrics().height())
        w = self._halo_d()
        if self._label:
            w += theme.Spacing.xs + self.fontMetrics().horizontalAdvance(self._label)
        return QSize(w, h)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    # ── painting ─────────────────────────────────────────────────────────
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        d = self._dot_d()
        halo = self._halo_d()
        cx = halo / 2.0
        cy = self.height() / 2.0
        # halo
        p.setPen(Qt.NoPen)
        p.setBrush(with_alpha(self._color, 0.18))
        p.drawEllipse(QRectF(cx - halo / 2.0, cy - halo / 2.0, halo, halo))
        # dot
        p.setBrush(QColor(self._color))
        p.drawEllipse(QRectF(cx - d / 2.0, cy - d / 2.0, d, d))
        # optional label
        if self._label:
            p.setPen(QColor(theme.Colors.text_secondary))
            p.setFont(self.font())
            tx = halo + theme.Spacing.xs
            p.drawText(QRectF(tx, 0, self.width() - tx, self.height()),
                       int(Qt.AlignVCenter | Qt.AlignLeft), self._label)

    # ── helpers ──────────────────────────────────────────────────────────
    @staticmethod
    def _resolve(status) -> QColor:
        if isinstance(status, str):
            token = _STATUS_TOKENS.get(status.lower())
            if token is None and not status.startswith("#") and hasattr(theme.Colors, status):
                token = status
            if token is not None:
                return QColor(getattr(theme.Colors, token))
        c = QColor(status)
        return c if c.isValid() else QColor(theme.Colors.text_muted)


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget as _QW

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    root = _QW()
    root.setWindowTitle("StatusDot — demo")
    pad = theme.Spacing.lg
    lay = QVBoxLayout(root)
    lay.setContentsMargins(pad, pad, pad, pad)
    lay.setSpacing(theme.Spacing.md)

    title = QLabel("StatusDot")
    title.setObjectName("Title")
    lay.addWidget(title)

    row = QHBoxLayout()
    row.setSpacing(theme.Spacing.lg)
    for st in ("success", "warn", "danger", "accent", "neutral"):
        d = StatusDot(st)
        d.setLabel(st)
        row.addWidget(d)
    row.addStretch(1)
    lay.addLayout(row)

    big_row = QHBoxLayout()
    saved = StatusDot("success", diameter=10)
    saved_lbl = QLabel("Saved")
    big_row.addWidget(saved)
    big_row.addWidget(saved_lbl)
    big_row.addSpacing(theme.Spacing.lg)
    conn = StatusDot("success")
    conn.setLabel("Connected")
    big_row.addWidget(conn)
    big_row.addStretch(1)
    lay.addLayout(big_row)
    lay.addStretch(1)

    root.resize(420, 200)
    root.show()
    _sys.exit(app.exec())
