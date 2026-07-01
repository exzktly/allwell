"""ToggleSwitch — an animated on/off switch that is a drop-in for QCheckBox.

Subclasses ``QCheckBox`` (so ``isChecked()`` / ``setChecked()`` /
``toggled(bool)`` / ``stateChanged(int)`` / keyboard handling all behave exactly
like a checkbox) but suppresses the native indicator and paints a sliding switch
— plus the optional text label — itself. Clicking anywhere on the widget
toggles it.

Sizes derive from the widget font, so it scales with DPI / font scaling.
Colours come from ``theme`` tokens.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import (  # noqa: E402
    Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt,
)
from PySide6.QtGui import QColor, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import QCheckBox, QSizePolicy, QWidget  # noqa: E402

import theme  # noqa: E402
from widgets._support import lerp_color, with_alpha  # noqa: E402


class ToggleSwitch(QCheckBox):
    """A sliding boolean toggle. ``toggled(bool)`` semantics match ``QCheckBox``."""

    def __init__(self, text: str = "", parent: QWidget | None = None,
                 *, checked: bool = False) -> None:
        super().__init__(text, parent)
        self.setObjectName("ToggleSwitch")
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        # Neutralise the stock indicator + spacing; everything is custom-painted.
        self.setStyleSheet(
            "QCheckBox#ToggleSwitch { spacing: 0px; background: transparent; }"
            "QCheckBox#ToggleSwitch::indicator { width: 0px; height: 0px; }"
        )
        self.setChecked(bool(checked))
        self._pos = 1.0 if self.isChecked() else 0.0

        self._anim = QPropertyAnimation(self, b"knobPosition", self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.InOutCubic)
        self.toggled.connect(self._on_toggled)

    # ── animated property ────────────────────────────────────────────────
    def _get_knob_position(self) -> float:
        return self._pos

    def _set_knob_position(self, value: float) -> None:
        self._pos = float(value)
        self.update()

    knobPosition = Property(float, _get_knob_position, _set_knob_position)

    def _on_toggled(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def bindingAdapter(self):
        """``(getter, setter, change_signal)`` for binding-driven panels — the
        bound value is the boolean checked state."""
        return (self.isChecked, lambda v: self.setChecked(bool(v)), self.toggled)

    # ── geometry (font-relative → DPI aware) ─────────────────────────────
    def _track_h(self) -> int:
        return max(16, self.fontMetrics().height())

    def _track_w(self) -> int:
        return round(self._track_h() * 1.85)

    def _text_gap(self) -> int:
        return theme.Spacing.sm if self.text() else 0

    def _pad(self) -> int:
        # leave room so the focus ring isn't clipped
        return max(2, round(self._track_h() * 0.16))

    def sizeHint(self) -> QSize:
        th = self._track_h()
        pad = self._pad()
        w = pad + self._track_w() + self._text_gap()
        if self.text():
            w += self.fontMetrics().horizontalAdvance(self.text())
        w += pad
        h = max(th, self.fontMetrics().height()) + 2 * pad
        return QSize(w, h)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    # ── input ────────────────────────────────────────────────────────────
    def hitButton(self, pos) -> bool:  # noqa: N802
        # Toggle when clicking anywhere on the widget, not just the (zero-size)
        # indicator / label rect.
        return self.rect().contains(pos)

    # ── painting ─────────────────────────────────────────────────────────
    def paintEvent(self, _event) -> None:  # noqa: N802
        c = theme.Colors
        tw, th = self._track_w(), self._track_h()
        x0 = float(self._pad())
        y0 = (self.height() - th) / 2.0
        radius = th / 2.0

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        enabled = self.isEnabled()
        off_fill = QColor(c.panel if not enabled else c.panel_elevated)
        on_fill = QColor(c.accent_dim if not enabled else c.accent)
        track_fill = lerp_color(off_fill, on_fill, self._pos)
        track_border = lerp_color(
            QColor(c.border), QColor(c.accent if enabled else c.border_strong), self._pos
        )

        track_rect = QRectF(x0, y0, tw, th)
        p.setPen(QPen(track_border, 1.0))
        p.setBrush(track_fill)
        p.drawRoundedRect(track_rect, radius, radius)

        margin = max(1.5, th * 0.14)
        knob_d = th - 2.0 * margin
        knob_x = x0 + margin + (tw - 2.0 * margin - knob_d) * self._pos
        knob_y = y0 + margin
        if not enabled:
            knob_color = QColor(c.text_muted)
        else:
            knob_color = lerp_color(QColor(c.text_secondary), QColor(c.ink_light), self._pos)
        p.setPen(Qt.NoPen)
        p.setBrush(knob_color)
        p.drawEllipse(QRectF(knob_x, knob_y, knob_d, knob_d))

        if enabled and self.hasFocus():
            grow = max(1.0, th * 0.13)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(with_alpha(c.accent, 0.5), max(1.0, grow * 0.8)))
            p.drawRoundedRect(
                track_rect.adjusted(-grow, -grow, grow, grow),
                radius + grow, radius + grow,
            )

        if self.text():
            p.setPen(QColor(c.text_primary if enabled else c.text_muted))
            p.setFont(self.font())
            tx = x0 + tw + self._text_gap()
            p.drawText(QRectF(tx, 0.0, max(0.0, self.width() - tx), float(self.height())),
                       int(Qt.AlignVCenter | Qt.AlignLeft), self.text())
        p.end()


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import (
        QApplication, QFormLayout, QLabel, QVBoxLayout, QWidget as _QW,
    )

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    root = _QW()
    root.setWindowTitle("ToggleSwitch — demo")
    outer = QVBoxLayout(root)
    outer.setContentsMargins(theme.Spacing.lg, theme.Spacing.lg,
                             theme.Spacing.lg, theme.Spacing.lg)
    title = QLabel("ToggleSwitch")
    title.setObjectName("Title")
    outer.addWidget(title)

    form = QFormLayout()
    form.setSpacing(theme.Spacing.md)
    on = ToggleSwitch(checked=True)
    off = ToggleSwitch()
    labelled = ToggleSwitch("Show grid", checked=True)
    disabled_on = ToggleSwitch(checked=True)
    disabled_on.setEnabled(False)
    disabled_off = ToggleSwitch()
    disabled_off.setEnabled(False)
    form.addRow("On by default:", on)
    form.addRow("Off by default:", off)
    form.addRow("With label:", labelled)
    form.addRow("Disabled (on):", disabled_on)
    form.addRow("Disabled (off):", disabled_off)
    outer.addLayout(form)

    echo = QLabel("toggled → (interact above)")
    echo.setObjectName("Secondary")
    outer.addWidget(echo)
    on.toggled.connect(lambda v: echo.setText(f"'On by default' toggled → {v}"))
    off.toggled.connect(lambda v: echo.setText(f"'Off by default' toggled → {v}"))
    labelled.toggled.connect(lambda v: echo.setText(f"'Show grid' toggled → {v}"))
    outer.addStretch(1)

    root.resize(360, 320)
    root.show()
    _sys.exit(app.exec())
