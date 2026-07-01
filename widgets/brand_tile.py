"""BrandTile — the small four-quadrant brand mark from the mockup titlebar.

A rounded tile with four quadrant dots in the trace-colour palette. Purely
decorative; size derives from the font (DPI-aware); colours from ``theme``.

API
---
* ``BrandTile(parent=None, *, side=None)`` — ``side`` overrides the auto size.
* ``setColors(list_of_colors)`` — defaults to ``theme.Colors.trace``.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import QRectF, QSize, Qt  # noqa: E402
from PySide6.QtGui import QColor, QPainter  # noqa: E402
from PySide6.QtWidgets import QSizePolicy, QWidget  # noqa: E402

import theme  # noqa: E402


class BrandTile(QWidget):
    def __init__(self, parent=None, *, side: int | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("BrandTile")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._explicit = int(side) if side else None
        # Wrap each token in QColor up front — recent PySide6 builds
        # refuse to coerce ``str`` to ``QColor`` implicitly inside
        # ``QPainter.setBrush(...)``, so the raw hex strings from
        # ``theme.Colors.trace`` crash the paintEvent.
        self._colors = [QColor(c) for c in theme.Colors.trace]
        self.setStyleSheet("#BrandTile { background: transparent; }")

    def setColors(self, colors) -> None:
        cs = [QColor(c) for c in colors if QColor(c).isValid()]
        if cs:
            self._colors = cs
            self.update()

    def _side(self) -> int:
        if self._explicit:
            return self._explicit
        return max(16, round(self.fontMetrics().height() * 1.4))

    def sizeHint(self) -> QSize:
        s = self._side()
        return QSize(s, s)

    minimumSizeHint = sizeHint

    def paintEvent(self, _event) -> None:  # noqa: N802
        c = theme.Colors
        s = min(self.width(), self.height())
        ox = (self.width() - s) / 2.0
        oy = (self.height() - s) / 2.0
        rect = QRectF(ox, oy, s, s)

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        # rounded backplate
        p.setPen(QColor(c.border))
        p.setBrush(QColor(c.panel_elevated))
        radius = s * (theme.Radii.xs / 12.0) + s * 0.12
        p.drawRoundedRect(rect, radius, radius)

        # four quadrant dots
        pad = s * 0.18
        gap = s * 0.10
        cell = (s - 2 * pad - gap) / 2.0
        d = cell * 0.92
        positions = [
            (ox + pad, oy + pad),
            (ox + pad + cell + gap, oy + pad),
            (ox + pad, oy + pad + cell + gap),
            (ox + pad + cell + gap, oy + pad + cell + gap),
        ]
        p.setPen(Qt.NoPen)
        for i, (qx, qy) in enumerate(positions):
            p.setBrush(self._colors[i % len(self._colors)])
            cx = qx + (cell - d) / 2.0
            cy = qy + (cell - d) / 2.0
            p.drawEllipse(QRectF(cx, cy, d, d))
        p.end()


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget as _QW

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    root = _QW()
    root.setWindowTitle("BrandTile — demo")
    pad = theme.Spacing.lg
    lay = QVBoxLayout(root)
    lay.setContentsMargins(pad, pad, pad, pad)
    lay.setSpacing(theme.Spacing.md)

    title = QLabel("BrandTile")
    title.setObjectName("Title")
    lay.addWidget(title)

    row = QHBoxLayout()
    row.setSpacing(theme.Spacing.lg)
    for sz in (18, 24, 36, 56):
        row.addWidget(BrandTile(side=sz))
    # titlebar-style row
    tile = BrandTile()
    word = QLabel("All-Well")
    word.setObjectName("Title")
    row.addSpacing(theme.Spacing.lg)
    row.addWidget(tile)
    row.addWidget(word)
    row.addStretch(1)
    lay.addLayout(row)
    lay.addStretch(1)

    root.resize(420, 180)
    root.show()
    _sys.exit(app.exec())
