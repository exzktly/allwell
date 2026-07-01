"""GradientStrip — a custom-painted horizontal colour-ramp swatch.

A thin widget that paints a `QLinearGradient` left→right from a list of stops
(or sampled from a ``t∈[0,1] → colour`` callable), reversible. Used by
`LutSelector` for the trigger button's preview and for every row in its picker
list, but it's a standalone primitive — anything that needs to show a colour
ramp can use it.

API
---
* ``GradientStrip(stops=None, parent=None, *, reversed=False)`` — *stops* is one
  of: a list of ``(pos∈[0,1], colour)`` pairs; a flat list of colours (placed at
  evenly spaced positions); a callable ``f(t∈[0,1]) → colour``; or ``None``
  (renders an empty/disabled ramp).
* ``setStops(stops)`` / ``stops() -> list[(float, QColor)]``
* ``setSamples(fn, n=24)`` — convenience: ``setStops`` from sampling *fn* at *n*
  evenly spaced points.
* ``setReversed(on)`` / ``isReversed()`` — flips the ramp left↔right at paint
  time (the stored stops are unchanged).
* ``colorAt(t)`` — the (un-reversed) colour at ``t∈[0,1]`` by linear interpolation.

Size derives from the widget font (DPI-aware); colours/borders from ``theme``.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import QRectF, QSize, Qt  # noqa: E402
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import QSizePolicy, QWidget  # noqa: E402

import theme  # noqa: E402


def _normalize_stops(stops) -> list[tuple[float, QColor]]:
    if stops is None:
        return []
    if callable(stops):
        n = 24
        return [(i / (n - 1), QColor(stops(i / (n - 1)))) for i in range(n)]
    items = list(stops)
    if not items:
        return []
    # list of (pos, colour) pairs?
    if all(isinstance(it, (tuple, list)) and len(it) == 2 and isinstance(it[0], (int, float))
           for it in items):
        out = [(max(0.0, min(1.0, float(p))), QColor(c)) for p, c in items]
    else:
        # flat list of colours → evenly spaced
        n = len(items)
        if n == 1:
            out = [(0.0, QColor(items[0])), (1.0, QColor(items[0]))]
        else:
            out = [(i / (n - 1), QColor(c)) for i, c in enumerate(items)]
    out = [(p, c) for p, c in out if c.isValid()]
    out.sort(key=lambda pc: pc[0])
    return out


class GradientStrip(QWidget):
    def __init__(self, stops=None, parent: QWidget | None = None,
                 *, reversed: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("GradientStrip")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._stops: list[tuple[float, QColor]] = _normalize_stops(stops)
        self._reversed = bool(reversed)
        self.setStyleSheet("#GradientStrip { background: transparent; }")

    # ── API ──────────────────────────────────────────────────────────────
    def setStops(self, stops) -> None:
        self._stops = _normalize_stops(stops)
        self.update()

    def stops(self) -> list[tuple[float, QColor]]:
        return [(p, QColor(c)) for p, c in self._stops]

    def setSamples(self, fn, n: int = 24) -> None:
        n = max(2, int(n))
        self.setStops([(i / (n - 1), fn(i / (n - 1))) for i in range(n)])

    def setReversed(self, on: bool) -> None:
        on = bool(on)
        if on != self._reversed:
            self._reversed = on
            self.update()

    def isReversed(self) -> bool:
        return self._reversed

    def colorAt(self, t: float) -> QColor:
        """Linear-interpolated colour at ``t∈[0,1]`` (ignores ``reversed``)."""
        if not self._stops:
            return QColor()
        t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
        if t <= self._stops[0][0]:
            return QColor(self._stops[0][1])
        if t >= self._stops[-1][0]:
            return QColor(self._stops[-1][1])
        for (p0, c0), (p1, c1) in zip(self._stops, self._stops[1:]):
            if p0 <= t <= p1:
                f = 0.0 if p1 == p0 else (t - p0) / (p1 - p0)
                return QColor(
                    round(c0.red()   + (c1.red()   - c0.red())   * f),
                    round(c0.green() + (c1.green() - c0.green()) * f),
                    round(c0.blue()  + (c1.blue()  - c0.blue())  * f),
                )
        return QColor(self._stops[-1][1])

    # ── geometry ─────────────────────────────────────────────────────────
    def sizeHint(self) -> QSize:
        h = max(10, round(self.fontMetrics().height() * 0.85))
        return QSize(max(96, round(self.fontMetrics().horizontalAdvance("0") * 12)), h)

    def minimumSizeHint(self) -> QSize:
        return QSize(24, max(8, round(self.fontMetrics().height() * 0.6)))

    # ── painting ─────────────────────────────────────────────────────────
    def paintEvent(self, _event) -> None:  # noqa: N802
        c = theme.Colors
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = theme.Radii.xs

        if not self._stops:
            p.setBrush(QColor(c.panel_elevated))
            p.setPen(QPen(QColor(c.border_subtle), 1.0))
            p.drawRoundedRect(rect, radius, radius)
            return

        grad = QLinearGradient(rect.left(), 0.0, rect.right(), 0.0)
        for pos, col in self._stops:
            grad.setColorAt(1.0 - pos if self._reversed else pos, col)
        p.setBrush(grad)
        p.setPen(QPen(QColor(c.border), 1.0))
        p.drawRoundedRect(rect, radius, radius)
        p.end()


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    import math
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QFormLayout, QLabel, QVBoxLayout, QWidget as _QW,
    )

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    root = _QW()
    root.setWindowTitle("GradientStrip — demo")
    pad = theme.Spacing.lg
    outer = QVBoxLayout(root)
    outer.setContentsMargins(pad, pad, pad, pad)
    outer.setSpacing(theme.Spacing.md)
    title = QLabel("GradientStrip")
    title.setObjectName("Title")
    outer.addWidget(title)

    form = QFormLayout()
    form.setSpacing(theme.Spacing.md)

    g_pairs = GradientStrip([(0.0, "#0B0F17"), (0.5, "#6B8AFD"), (1.0, "#F0F4FF")])
    g_pairs.setMinimumHeight(18)
    g_flat = GradientStrip(["#1F4FB0", "#2E8C50", "#B5781A", "#B02C2C"])  # evenly spaced
    g_flat.setMinimumHeight(18)
    g_func = GradientStrip(reversed=False)
    g_func.setMinimumHeight(18)
    # a viridis-ish ramp via a callable (no matplotlib needed for the demo)
    g_func.setSamples(lambda t: QColor.fromHsvF(0.75 - 0.75 * t, 0.55 + 0.25 * math.sin(t * math.pi),
                                                0.35 + 0.55 * t))
    g_empty = GradientStrip(None)
    g_empty.setMinimumHeight(18)

    form.addRow("(pos, colour) stops:", g_pairs)
    form.addRow("flat colour list:", g_flat)
    form.addRow("sampled from a callable:", g_func)
    form.addRow("empty:", g_empty)
    outer.addLayout(form)

    rev = QCheckBox("reversed")
    rev.toggled.connect(g_pairs.setReversed)
    rev.toggled.connect(g_flat.setReversed)
    rev.toggled.connect(g_func.setReversed)
    outer.addWidget(rev)

    echo = QLabel("colorAt(0.5) of the first strip: " + g_pairs.colorAt(0.5).name())
    echo.setObjectName("Secondary")
    outer.addWidget(echo)
    outer.addStretch(1)

    root.resize(420, 240)
    root.show()
    _sys.exit(app.exec())
