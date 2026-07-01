"""ColorPickerPopover (+ SvSquare, HueStrip) — a free-form colour picker in a
popover.

The "Custom" escape hatch behind `ColorSwatchRow`: a saturation/value
square + a hue strip + a `#RRGGBB` hex field + an alpha field + a "recents"
row (≤8 swatches, reusing `ColorSwatchRow`), all hosted in a `Popover`.

API
---
* ``ColorPickerPopover(parent=None, *, color="#6B8AFD", recents=None)``
* ``setColor(QColor|str)`` / ``color() -> QColor`` (carries alpha)
* ``setRecents(list)`` / ``recents() -> list[QColor]``
* ``popup(anchor, side="bottom", align="start")`` (inherited from `Popover`)
* ``colorPicked(QColor)`` — emitted live as the user drags the SV square / hue
  strip / edits a field.
* ``colorCommitted(QColor)`` — emitted on Return in the hex field, on clicking a
  recent swatch, and when the popover is dismissed (only if the colour changed
  since the last ``setColor``). The picked colour is prepended to ``recents``
  on commit.

Note: the hex/alpha line edits rely on `Qt.Popup` forwarding key events to its
focus widget (it does for `QLineEdit`); if a platform misbehaves, switch the
`Popover` base to `Qt.Tool` + manual outside-click dismiss.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import QRectF, QSize, Qt, Signal  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor, QIntValidator, QLinearGradient, QPainter, QPen,
    QRegularExpressionValidator,
)
from PySide6.QtCore import QRegularExpression  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QVBoxLayout, QWidget,
)

import theme  # noqa: E402
from widgets.color_swatch_row import ColorSwatchRow  # noqa: E402
from widgets.popover import Popover  # noqa: E402


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


class SvSquare(QWidget):
    """Saturation (x) × value (y) plane for a fixed hue; drag to pick."""

    svChanged = Signal(float, float)   # (s, v) — both 0..1

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SvSquare")
        self.setMouseTracking(False)
        self.setCursor(Qt.CrossCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._hue = 0.62      # 0..1
        self._s = 1.0
        self._v = 1.0

    def setHue(self, h: float) -> None:
        h = h % 1.0 if h >= 0 else self._hue
        if h != self._hue:
            self._hue = h
            self.update()

    def hue(self) -> float:
        return self._hue

    def setSv(self, s: float, v: float) -> None:
        s, v = _clamp01(s), _clamp01(v)
        if (s, v) != (self._s, self._v):
            self._s, self._v = s, v
            self.update()

    def sv(self) -> tuple[float, float]:
        return self._s, self._v

    def sizeHint(self) -> QSize:
        n = max(120, round(self.fontMetrics().height() * 9))
        return QSize(n, n)

    minimumSizeHint = lambda self: QSize(80, 80)  # noqa: E731

    def _pick_at(self, x: float, y: float) -> None:
        w, h = max(1, self.width()), max(1, self.height())
        self.setSv(x / w, 1.0 - y / h)
        self.svChanged.emit(self._s, self._v)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._pick_at(event.position().x(), event.position().y())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.LeftButton:
            self._pick_at(event.position().x(), event.position().y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect())
        # white → hue across x
        hg = QLinearGradient(rect.left(), 0.0, rect.right(), 0.0)
        hg.setColorAt(0.0, QColor(theme.Colors.ink_light))
        hg.setColorAt(1.0, QColor.fromHsvF(self._hue, 1.0, 1.0))
        p.fillRect(rect, hg)
        # transparent → black down y
        vg = QLinearGradient(0.0, rect.top(), 0.0, rect.bottom())
        vg.setColorAt(0.0, QColor(0, 0, 0, 0))
        vg.setColorAt(1.0, QColor(0, 0, 0, 255))
        p.fillRect(rect, vg)
        # 1px border
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(theme.Colors.border), 1.0))
        p.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
        # picker ring
        cx = self._s * rect.width()
        cy = (1.0 - self._v) * rect.height()
        rr = max(4.0, rect.width() * 0.04)
        ink = QColor(theme.Colors.ink_light) if self._v < 0.55 else QColor(theme.Colors.ink_dark)
        p.setPen(QPen(ink, 1.6))
        p.drawEllipse(QRectF(cx - rr, cy - rr, 2 * rr, 2 * rr))
        p.end()


class HueStrip(QWidget):
    """Vertical hue ramp 0(top)→1(bottom); drag to pick."""

    hueChanged = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("HueStrip")
        self.setCursor(Qt.SizeVerCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._hue = 0.62

    def setHue(self, h: float) -> None:
        h = h % 1.0 if h >= 0 else self._hue
        if h != self._hue:
            self._hue = h
            self.update()

    def hue(self) -> float:
        return self._hue

    def sizeHint(self) -> QSize:
        return QSize(max(14, round(self.fontMetrics().height() * 1.1)),
                     max(120, round(self.fontMetrics().height() * 9)))

    minimumSizeHint = lambda self: QSize(12, 80)  # noqa: E731

    def _pick_at(self, y: float) -> None:
        h = max(1, self.height())
        self._hue = _clamp01(y / h)
        self.update()
        self.hueChanged.emit(self._hue)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._pick_at(event.position().y())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.LeftButton:
            self._pick_at(event.position().y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect())
        g = QLinearGradient(0.0, rect.top(), 0.0, rect.bottom())
        n = 12
        for i in range(n + 1):
            g.setColorAt(i / n, QColor.fromHsvF(i / n if i < n else 0.999, 1.0, 1.0))
        p.fillRect(rect, g)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(theme.Colors.border), 1.0))
        p.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
        y = self._hue * rect.height()
        p.setPen(QPen(QColor(theme.Colors.ink_light), 2.0))
        p.drawLine(rect.left(), y, rect.right(), y)
        p.setPen(QPen(QColor(theme.Colors.ink_dark), 1.0))
        p.drawLine(rect.left(), y - 1, rect.right(), y - 1)
        p.drawLine(rect.left(), y + 1, rect.right(), y + 1)
        p.end()


class ColorPickerPopover(Popover):
    colorPicked = Signal(QColor)       # live
    colorCommitted = Signal(QColor)    # on Return / recent-click / dismiss

    def __init__(self, parent: QWidget | None = None, *,
                 color="#6B8AFD", recents=None) -> None:
        super().__init__(parent)
        self._color = QColor(color) if QColor(color).isValid() else QColor("#6B8AFD")
        self._committed = QColor(self._color)
        self._recents: list[QColor] = [QColor(c) for c in (recents or []) if QColor(c).isValid()][:8]
        self._updating = False

        content = QWidget()
        v = QVBoxLayout(content)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(theme.Spacing.sm)

        row = QHBoxLayout()
        row.setSpacing(theme.Spacing.sm)
        # fontMetrics-derived minimums: roughly 18 chars wide × 10 lines tall
        # for the SV square, ~1 char wide for the hue strip. Scales at hi-dpi.
        _fm = self.fontMetrics()
        self._sv = SvSquare()
        self._sv.setMinimumSize(
            max(120, _fm.averageCharWidth() * 22),
            max(110, _fm.height() * 10),
        )
        self._hue = HueStrip()
        self._hue.setMinimumWidth(max(12, _fm.averageCharWidth() * 2))
        row.addWidget(self._sv, 1)
        row.addWidget(self._hue, 0)
        v.addLayout(row, 1)

        form = QFormLayout()
        form.setSpacing(theme.Spacing.xs)
        self._hex = QLineEdit()
        self._hex.setValidator(QRegularExpressionValidator(
            QRegularExpression(r"#?([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})"), self._hex))
        self._hex.setPlaceholderText("#RRGGBB")
        self._alpha = QLineEdit()
        self._alpha.setValidator(QIntValidator(0, 255, self._alpha))
        self._alpha.setPlaceholderText("0–255")
        form.addRow("Hex", self._hex)
        form.addRow("Alpha", self._alpha)
        v.addLayout(form)

        self._recents_row = ColorSwatchRow(self._recents or [self._color])
        self._recents_row.colorPicked.connect(self._on_recent_picked)
        rl = QVBoxLayout()
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(2)
        cap = QLabel("RECENTS")
        cap.setObjectName("Caption")
        rl.addWidget(cap)
        rl.addWidget(self._recents_row)
        v.addLayout(rl)

        self.setContentWidget(content)

        self._sv.svChanged.connect(self._on_sv_changed)
        self._hue.hueChanged.connect(self._on_hue_changed)
        self._hex.returnPressed.connect(self._commit_hex)
        self._hex.editingFinished.connect(self._on_hex_edited)
        self._alpha.editingFinished.connect(self._on_alpha_edited)
        self._alpha.returnPressed.connect(self._on_alpha_edited)

        self._sync_from_color()

    # ── API ──────────────────────────────────────────────────────────────
    def setColor(self, c) -> None:
        c = QColor(c)
        if not c.isValid():
            return
        self._color = c
        self._committed = QColor(c)
        self._sync_from_color()

    def color(self) -> QColor:
        return QColor(self._color)

    def setRecents(self, recents) -> None:
        self._recents = [QColor(x) for x in (recents or []) if QColor(x).isValid()][:8]
        self._recents_row.setColors(self._recents or [self._color])

    def recents(self) -> list[QColor]:
        return [QColor(x) for x in self._recents]

    # ── sync ─────────────────────────────────────────────────────────────
    def _sync_from_color(self) -> None:
        self._updating = True
        try:
            h, s, val, a = self._color.getHsvF()
            if h < 0:
                h = self._hue.hue()  # achromatic — keep the slider where it was
            self._hue.setHue(h)
            self._sv.setHue(h)
            self._sv.setSv(s, val)
            rgb = QColor(self._color)
            self._hex.setText(f"#{rgb.red():02X}{rgb.green():02X}{rgb.blue():02X}")
            self._alpha.setText(str(rgb.alpha()))
        finally:
            self._updating = False

    def _recompute_from_hsv(self, *, emit_live: bool = True) -> None:
        h = self._hue.hue()
        s, val = self._sv.sv()
        a = self._color.alpha()
        c = QColor.fromHsvF(min(0.999, max(0.0, h)), s, val)
        c.setAlpha(a)
        self._color = c
        if not self._updating:
            self._hex.blockSignals(True)
            self._hex.setText(f"#{c.red():02X}{c.green():02X}{c.blue():02X}")
            self._hex.blockSignals(False)
        if emit_live:
            self.colorPicked.emit(QColor(self._color))

    # ── handlers ─────────────────────────────────────────────────────────
    def _on_sv_changed(self, _s, _v) -> None:
        if self._updating:
            return
        self._recompute_from_hsv()

    def _on_hue_changed(self, h) -> None:
        if self._updating:
            return
        self._sv.setHue(h)
        self._recompute_from_hsv()

    def _parse_hex(self) -> QColor | None:
        t = self._hex.text().strip()
        if not t:
            return None
        if not t.startswith("#"):
            t = "#" + t
        c = QColor(t)
        return c if c.isValid() else None

    def _on_hex_edited(self) -> None:
        if self._updating:
            return
        c = self._parse_hex()
        if c is None:
            return
        c.setAlpha(self._color.alpha())
        self._color = c
        self._sync_from_color_keep_committed()
        self.colorPicked.emit(QColor(self._color))

    def _on_alpha_edited(self) -> None:
        if self._updating:
            return
        text = self._alpha.text().strip()
        if not text:
            # Treat blank as "no change yet" — silently snapping to 255
            # (the old behaviour) re-emits colorPicked for what was a
            # typo. Re-sync from the current colour so the field shows
            # the live value on next focus.
            self._sync_from_color_keep_committed()
            return
        try:
            a = max(0, min(255, int(text)))
        except ValueError:
            return
        if a != self._color.alpha():
            self._color.setAlpha(a)
            self.colorPicked.emit(QColor(self._color))

    def _sync_from_color_keep_committed(self) -> None:
        # like _sync_from_color but doesn't touch self._committed
        self._updating = True
        try:
            h, s, val, _a = self._color.getHsvF()
            if h < 0:
                h = self._hue.hue()
            self._hue.setHue(h)
            self._sv.setHue(h)
            self._sv.setSv(s, val)
            self._alpha.blockSignals(True)
            self._alpha.setText(str(self._color.alpha()))
            self._alpha.blockSignals(False)
        finally:
            self._updating = False

    def _commit_hex(self) -> None:
        c = self._parse_hex()
        if c is not None:
            c.setAlpha(self._color.alpha())
            self._color = c
            self._sync_from_color_keep_committed()
        self._commit()

    def _on_recent_picked(self, c: QColor) -> None:
        self.setColor(c)
        self.colorPicked.emit(QColor(self._color))
        self._commit()

    def _commit(self) -> None:
        if self._color == self._committed:
            return
        self._committed = QColor(self._color)
        # prepend to recents (dedup, cap 8)
        self._recents = [QColor(self._color)] + [
            x for x in self._recents if x.rgba() != self._color.rgba()
        ]
        self._recents = self._recents[:8]
        self._recents_row.setColors(self._recents)
        self.colorCommitted.emit(QColor(self._color))

    def hideEvent(self, event) -> None:  # noqa: N802
        # dismissing the popover commits the current colour (if changed)
        self._commit()
        super().hideEvent(event)


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import (
        QApplication, QLabel, QPushButton, QVBoxLayout, QWidget as _QW,
    )

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    root = _QW()
    root.setWindowTitle("ColorPickerPopover — demo")
    pad = theme.Spacing.lg
    lay = QVBoxLayout(root)
    lay.setContentsMargins(pad, pad, pad, pad)
    lay.setSpacing(theme.Spacing.md)
    title = QLabel("ColorPickerPopover")
    title.setObjectName("Title")
    lay.addWidget(title)

    swatch = QLabel("        ")
    swatch.setFixedSize(120, 28)
    def _show(c):
        swatch.setStyleSheet(f"background-color: {c.name(QColor.HexArgb)}; "
                             f"border: 1px solid {theme.Colors.border}; border-radius: 4px;")
    _show(QColor("#6B8AFD"))
    lay.addWidget(swatch)

    btn = QPushButton("Open colour picker")
    btn.setObjectName("Primary")
    btn.setCursor(Qt.PointingHandCursor)
    lay.addWidget(btn, 0, Qt.AlignLeft)
    picker = ColorPickerPopover(root, color="#6B8AFD",
                                recents=["#5B9BF8", "#F26B6B", "#4ADE80", "#F5A524"])
    picker.colorPicked.connect(_show)
    echo = QLabel("(open the picker)")
    echo.setObjectName("Secondary")
    picker.colorCommitted.connect(lambda c: echo.setText(f"committed → {c.name(QColor.HexArgb)}"))
    lay.addWidget(echo)
    btn.clicked.connect(lambda: picker.popup(btn, side="bottom"))
    lay.addStretch(1)

    root.resize(420, 220)
    root.show()
    _sys.exit(app.exec())
