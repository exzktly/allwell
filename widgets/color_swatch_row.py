"""ColorSwatchRow — a curated row of tappable colour swatches (+ recents + a
"Custom" tile that opens the full colour picker).

The mockup never opens a free-form colour dialog by default; you pick from a
small constrained set. The selected swatch gets a 2-px accent ring drawn
outside its fill. When ``allow_custom`` is on, the row also shows:

* a small group of *recent* custom colours (≤ ``max_recents``), and
* a conic-gradient **"Custom"** tile at the very end — clicking it opens a
  :class:`widgets.color_picker_popover.ColorPickerPopover` anchored at the
  tile; the committed colour becomes the current selection and is prepended to
  the recents list.

``colorPicked(QColor)`` fires whether the colour came from a curated swatch, a
recent, or the picker (live + on commit).

API
---
* ``ColorSwatchRow(colors=None, parent=None, *, allow_custom=False, recents=None, max_recents=8)``
* ``setColors(iterable)`` / ``colors()``
* ``currentColor()`` / ``setCurrentColor(c)`` / ``currentIndex()`` / ``setCurrentIndex(i)``
* ``setAllowCustom(bool)`` / ``allowCustom()``
* ``setRecents(iterable)`` / ``recents()`` / ``addRecent(c)``
* ``colorPicked(QColor)``
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor, QConicalGradient, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import QSizePolicy, QWidget  # noqa: E402

import theme  # noqa: E402


class ColorSwatchRow(QWidget):
    colorPicked = Signal(QColor)

    def __init__(self, colors=None, parent=None, *, allow_custom: bool = False,
                 recents=None, max_recents: int = 8) -> None:
        super().__init__(parent)
        self.setObjectName("ColorSwatchRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._colors = [QColor(c) for c in (colors or theme.Colors.trace) if QColor(c).isValid()]
        self._max_recents = max(1, int(max_recents))
        self._recents: list[QColor] = [QColor(c) for c in (recents or []) if QColor(c).isValid()][:self._max_recents]
        self._allow_custom = bool(allow_custom)
        self._current = QColor(self._colors[0]) if self._colors else QColor()
        self._hover = -1            # index into self._tiles()
        self._picker = None         # lazily created ColorPickerPopover
        self._picker_anchor = None  # lazily created 1×1 anchor widget
        self.setStyleSheet("#ColorSwatchRow { background: transparent; }")

    # ── API ──────────────────────────────────────────────────────────────
    def setColors(self, colors) -> None:
        self._colors = [QColor(c) for c in colors if QColor(c).isValid()]
        if not self._color_present(self._current) and self._colors:
            self._current = QColor(self._colors[0])
        self.updateGeometry()
        self.update()

    def colors(self) -> list[QColor]:
        return list(self._colors)

    def setAllowCustom(self, on: bool) -> None:
        on = bool(on)
        if on != self._allow_custom:
            self._allow_custom = on
            self.updateGeometry()
            self.update()

    def allowCustom(self) -> bool:
        return self._allow_custom

    def setRecents(self, recents) -> None:
        self._recents = [QColor(c) for c in recents if QColor(c).isValid()][:self._max_recents]
        self.updateGeometry()
        self.update()

    def recents(self) -> list[QColor]:
        return list(self._recents)

    def addRecent(self, color) -> None:
        c = QColor(color)
        if not c.isValid():
            return
        self._recents = [c] + [r for r in self._recents if r.rgb() != c.rgb()]
        self._recents = self._recents[:self._max_recents]
        self.updateGeometry()
        self.update()

    def currentColor(self) -> QColor:
        return QColor(self._current)

    def setCurrentColor(self, color) -> None:
        c = QColor(color)
        if c.isValid() and c.rgb() != self._current.rgb():
            self._current = c
            self.update()

    def currentIndex(self) -> int:
        for i, c in enumerate(self._colors):
            if c.rgb() == self._current.rgb():
                return i
        return -1

    def setCurrentIndex(self, index: int) -> None:
        if 0 <= index < len(self._colors):
            self.setCurrentColor(self._colors[index])

    # ── tile model ───────────────────────────────────────────────────────
    def _tiles(self):
        """(kind, payload) list — kind in {"curated", "recent", "custom"}."""
        tiles = [("curated", c) for c in self._colors]
        if self._allow_custom:
            tiles += [("recent", c) for c in self._recents]
            tiles.append(("custom", None))
        return tiles

    def _color_present(self, c: QColor) -> bool:
        return any(t[1] is not None and t[1].rgb() == c.rgb() for t in self._tiles())

    # ── geometry ─────────────────────────────────────────────────────────
    def _swatch(self) -> int:
        return max(16, round(self.fontMetrics().height() * 1.25))

    def _gap(self) -> int:
        return theme.Spacing.sm

    def _ring(self) -> int:
        return max(2, round(self._swatch() * 0.12))

    def _tile_rects(self) -> list[QRectF]:
        s, g, r = self._swatch(), self._gap(), self._ring()
        rects: list[QRectF] = []
        x = float(r)
        y = (self.height() - s) / 2.0
        prev_kind = None
        for kind, _payload in self._tiles():
            if prev_kind is not None:
                x += g
                if kind != prev_kind:
                    x += g          # extra gap between groups
            rects.append(QRectF(x, y, s, s))
            x += s
            prev_kind = kind
        return rects

    def sizeHint(self) -> QSize:
        s, r = self._swatch(), self._ring()
        rects = self._tile_rects()
        w = (rects[-1].right() + r) if rects else (2 * r)
        return QSize(round(w), s + 2 * r)

    minimumSizeHint = sizeHint

    # ── input ────────────────────────────────────────────────────────────
    def _index_at(self, x: float, y: float) -> int:
        g = self._gap()
        for i, rect in enumerate(self._tile_rects()):
            if rect.adjusted(-g / 2, 0, g / 2, 0).contains(x, y):
                return i
        return -1

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return
        i = self._index_at(event.position().x(), event.position().y())
        if i < 0:
            return
        kind, payload = self._tiles()[i]
        if kind == "custom":
            self._open_picker(self._tile_rects()[i])
            return
        c = QColor(payload)
        changed = c.rgb() != self._current.rgb()
        self._current = c
        self.update()
        self.colorPicked.emit(QColor(c))
        _ = changed

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        i = self._index_at(event.position().x(), event.position().y())
        if i != self._hover:
            self._hover = i
            self.update()

    def leaveEvent(self, _event) -> None:  # noqa: N802
        if self._hover != -1:
            self._hover = -1
            self.update()

    # ── custom-colour picker ─────────────────────────────────────────────
    def _open_picker(self, tile_rect: QRectF) -> None:
        from widgets.color_picker_popover import ColorPickerPopover  # local: heavy
        if self._picker is None:
            self._picker = ColorPickerPopover(self)
            self._picker.colorPicked.connect(self._on_picker_live)
            self._picker.colorCommitted.connect(self._on_picker_committed)
        self._picker.setRecents(self._recents)
        self._picker.setColor(self._current if self._current.isValid() else QColor("#6B8AFD"))
        # Anchor: a 1×1 invisible child at the tile centre — popup() maps it to global.
        if self._picker_anchor is None:
            self._picker_anchor = QWidget(self)
            self._picker_anchor.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        c = tile_rect.center()
        self._picker_anchor.setGeometry(int(c.x()), int(c.y()), 1, 1)
        self._picker_anchor.show()
        self._picker.popup(self._picker_anchor, side="bottom", align="start")

    def _on_picker_live(self, color: QColor) -> None:
        self._current = QColor(color)
        self.update()
        self.colorPicked.emit(QColor(color))

    def _on_picker_committed(self, color: QColor) -> None:
        self.addRecent(color)
        self._current = QColor(color)
        self.update()
        self.colorPicked.emit(QColor(color))

    # ── painting ─────────────────────────────────────────────────────────
    def _paint_custom_tile(self, p: QPainter, rect: QRectF) -> None:
        c = theme.Colors
        radius = theme.Radii.xs
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.save()
        p.setClipPath(path)
        grad = QConicalGradient(rect.center(), 90.0)
        for i in range(7):
            t = i / 6.0
            grad.setColorAt(min(0.999, t), QColor.fromHsvF((i % 6) / 6.0, 0.75, 0.95))
        p.fillRect(rect, grad)
        p.restore()
        p.setPen(QPen(QColor(c.border), 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect, radius, radius)
        # a small white "+" on a dark disc in the centre
        d = rect.width() * 0.46
        disc = QRectF(rect.center().x() - d / 2, rect.center().y() - d / 2, d, d)
        p.setBrush(QColor(0, 0, 0, 130))
        p.setPen(Qt.NoPen)
        p.drawEllipse(disc)
        p.setPen(QPen(QColor(theme.Colors.ink_light), max(1.0, d * 0.12)))
        cx, cy, arm = rect.center().x(), rect.center().y(), d * 0.26
        p.drawLine(QPointF(cx - arm, cy), QPointF(cx + arm, cy))
        p.drawLine(QPointF(cx, cy - arm), QPointF(cx, cy + arm))

    def paintEvent(self, _event) -> None:  # noqa: N802
        c = theme.Colors
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        radius = theme.Radii.xs
        rects = self._tile_rects()
        tiles = self._tiles()
        for i, ((kind, payload), rect) in enumerate(zip(tiles, rects)):
            if kind == "custom":
                self._paint_custom_tile(p, rect)
            else:
                p.setPen(QPen(QColor(c.border), 1.0))
                p.setBrush(QColor(payload))
                p.drawRoundedRect(rect, radius, radius)
            is_current = payload is not None and payload.rgb() == self._current.rgb()
            if is_current:
                p.setBrush(Qt.NoBrush)
                ring = self._ring()
                p.setPen(QPen(QColor(c.accent), ring))
                p.drawRoundedRect(rect.adjusted(-ring, -ring, ring, ring),
                                  radius + ring, radius + ring)
            elif i == self._hover:
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(c.border_strong), max(1.0, self._ring() * 0.7)))
                p.drawRoundedRect(rect.adjusted(-1.5, -1.5, 1.5, 1.5),
                                  radius + 1.5, radius + 1.5)
        p.end()


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication, QFormLayout, QLabel, QVBoxLayout, QWidget as _QW

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    root = _QW()
    root.setWindowTitle("ColorSwatchRow — demo")
    pad = theme.Spacing.lg
    lay = QVBoxLayout(root)
    lay.setContentsMargins(pad, pad, pad, pad)
    lay.setSpacing(theme.Spacing.md)

    title = QLabel("ColorSwatchRow")
    title.setObjectName("Title")
    lay.addWidget(title)

    form = QFormLayout()
    form.setSpacing(theme.Spacing.md)
    traces = ColorSwatchRow()
    custom = ColorSwatchRow(allow_custom=True,
                            recents=["#9B59B6", "#1ABC9C", "#E67E22"])
    custom.setCurrentColor("#9B59B6")
    form.addRow("Trace colour:", traces)
    form.addRow("Trace + custom:", custom)
    lay.addLayout(form)

    echo = QLabel("(pick a swatch)")
    echo.setObjectName("Secondary")
    lay.addWidget(echo)
    traces.colorPicked.connect(lambda c: echo.setText(f"trace → {c.name()}"))
    custom.colorPicked.connect(lambda c: echo.setText(f"custom → {c.name()}"))
    lay.addStretch(1)

    root.resize(420, 220)
    root.show()
    _sys.exit(app.exec())
