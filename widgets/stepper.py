"""Stepper — a numeric input with a stacked ▴/▾ column and an accent focus ring.

A token-styled alternative to ``QSpinBox`` / ``QDoubleSpinBox`` (whose
sub-controls render natively on some platforms). Built from a ``QLineEdit`` plus
two stacked ``QToolButton``s.

API (spin-box-like)
-------------------
* ``value()`` / ``setValue(v)`` — ``valueChanged(float)`` signal.
* ``setRange(lo, hi)`` / ``setMinimum`` / ``setMaximum``
* ``setSingleStep(step)`` / ``setDecimals(n)`` (``0`` ⇒ integer)
* ``setSuffix(text)`` / ``setPrefix(text)``

Sizes derive from the font (DPI-aware).
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QDoubleValidator, QFontMetrics
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLineEdit, QSizePolicy, QToolButton, QVBoxLayout,
)

import theme


class Stepper(QFrame):
    valueChanged = Signal(float)

    def __init__(self, parent=None, *, value: float = 0.0,
                 minimum: float = -1e9, maximum: float = 1e9,
                 single_step: float = 1.0, decimals: int = 0) -> None:
        super().__init__(parent)
        self.setObjectName("Stepper")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self._min = float(minimum)
        self._max = float(maximum)
        self._step = float(single_step)
        self._decimals = max(0, int(decimals))
        self._value = 0.0
        self._suffix = ""
        self._prefix = ""

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._edit = QLineEdit(self)
        self._edit.setObjectName("StepperEdit")
        self._edit.setFrame(False)
        self._edit.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._validator = QDoubleValidator(self._min, self._max, self._decimals, self)
        self._validator.setNotation(QDoubleValidator.StandardNotation)
        self._edit.setValidator(self._validator)
        self._edit.editingFinished.connect(self._commit_text)

        btn_col = QVBoxLayout()
        btn_col.setContentsMargins(0, 0, 0, 0)
        btn_col.setSpacing(0)
        self._up = QToolButton(self)
        self._up.setObjectName("StepperUp")
        self._up.setText("▴")
        self._down = QToolButton(self)
        self._down.setObjectName("StepperDown")
        self._down.setText("▾")
        for b in (self._up, self._down):
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.setAutoRepeat(True)
            b.setAutoRepeatInterval(60)
            b.setAutoRepeatDelay(300)
            btn_col.addWidget(b)
        self._up.clicked.connect(lambda: self.stepBy(+1))
        self._down.clicked.connect(lambda: self.stepBy(-1))

        lay.addWidget(self._edit, 1)
        lay.addLayout(btn_col)

        self._edit.installEventFilter(self)
        self.setStyleSheet(self._build_qss())
        self.setValue(value)
        self._update_size()

    # ── value API ────────────────────────────────────────────────────────
    def value(self) -> float:
        return self._value

    def setValue(self, v: float) -> None:
        v = self._clamp(self._round(float(v)))
        changed = (v != self._value)
        self._value = v
        self._edit.setText(self._format(v))
        if changed:
            self.valueChanged.emit(v)

    def stepBy(self, steps: int) -> None:
        self.setValue(self._value + steps * self._step)

    def setRange(self, lo: float, hi: float) -> None:
        self._min, self._max = float(lo), float(hi)
        self._validator.setRange(self._min, self._max, self._decimals)
        self.setValue(self._value)
        self._update_size()

    def setMinimum(self, lo: float) -> None:
        self.setRange(lo, self._max)

    def setMaximum(self, hi: float) -> None:
        self.setRange(self._min, hi)

    def setSingleStep(self, step: float) -> None:
        self._step = float(step)

    def setDecimals(self, n: int) -> None:
        self._decimals = max(0, int(n))
        self._validator.setDecimals(self._decimals)
        self._edit.setText(self._format(self._value))
        self._update_size()

    def setSuffix(self, text: str) -> None:
        self._suffix = text or ""
        self._edit.setText(self._format(self._value))
        self._update_size()

    def setPrefix(self, text: str) -> None:
        self._prefix = text or ""
        self._edit.setText(self._format(self._value))
        self._update_size()

    def bindingAdapter(self):
        """``(getter, setter, change_signal)`` for binding-driven panels."""
        return (self.value, self.setValue, self.valueChanged)

    # ── internals ────────────────────────────────────────────────────────
    def _update_size(self) -> None:
        """Size the widget to the widest value its range can display (plus the
        ▴/▾ column), so it stays compact and its arrows are never clipped.

        The bare ``QLineEdit`` sizeHint is ~17 characters wide, which made every
        Stepper demand ~186px — arrows floated far from the value in wide rows and
        got clipped at the edge of narrow docks. Bounding the width to the actual
        value length fixes both. Clamped to a sane [4, 11]-digit range so a huge
        ``maximum`` (e.g. 1e9) doesn't blow it back up, and a tiny one still shows
        the arrows. Recomputed whenever the range / decimals / affixes change."""
        fm: QFontMetrics = self._edit.fontMetrics()
        samples = [self._format(self._min), self._format(self._max),
                   self._format(self._value)]
        text_w = max((fm.horizontalAdvance(s) for s in samples), default=0)
        text_w = max(fm.horizontalAdvance("0000"),
                     min(text_w, fm.horizontalAdvance("0" * 11)))
        # edit padding (8+8) + the arrow column + frame.
        full = int(text_w) + 16 + 26
        self.setMaximumWidth(full)
        self.setMinimumWidth(min(full, 58))

    def _clamp(self, v: float) -> float:
        return max(self._min, min(self._max, v))

    def _round(self, v: float) -> float:
        if self._decimals == 0:
            return float(round(v))
        return round(v, self._decimals)

    def _format(self, v: float) -> str:
        body = f"{int(round(v))}" if self._decimals == 0 else f"{v:.{self._decimals}f}"
        return f"{self._prefix}{body}{self._suffix}"

    def _strip(self, text: str) -> str:
        text = text.strip()
        if self._prefix and text.startswith(self._prefix):
            text = text[len(self._prefix):]
        if self._suffix and text.endswith(self._suffix):
            text = text[: -len(self._suffix)]
        return text.strip()

    def _commit_text(self) -> None:
        raw = self._strip(self._edit.text())
        try:
            v = float(raw) if raw not in ("", "-", "+", ".") else self._value
        except ValueError:
            v = self._value
        self.setValue(v)

    def eventFilter(self, obj, event) -> bool:
        # PySide6 occasionally dispatches non-QEvent arguments through
        # eventFilter (observed: a QStandardItem from a completer popup
        # being routed in place of the QEvent). Bail before any
        # ``event.type()`` access so the stepper survives those edge
        # cases instead of TypeError-ing the whole widget tree.
        if not isinstance(event, QEvent):
            return False
        if obj is self._edit:
            if event.type() in (QEvent.FocusIn, QEvent.FocusOut):
                self.setProperty("focused", event.type() == QEvent.FocusIn)
                self.style().unpolish(self)
                self.style().polish(self)
            elif event.type() == QEvent.Wheel:
                self.stepBy(1 if event.angleDelta().y() > 0 else -1)
                return True
            elif event.type() == QEvent.KeyPress:
                if event.key() == Qt.Key_Up:
                    self.stepBy(+1)
                    return True
                if event.key() == Qt.Key_Down:
                    self.stepBy(-1)
                    return True
        return super().eventFilter(obj, event)

    def _build_qss(self) -> str:
        c, t, r = theme.Colors, theme.Typography, theme.Radii
        return f"""
        #Stepper {{
            background-color: {c.panel_elevated};
            border: 1px solid {c.border};
            border-radius: {r.sm}px;
        }}
        #Stepper:hover {{ border-color: {c.border_strong}; }}
        #Stepper[focused="true"] {{ border-color: {c.accent}; }}
        #Stepper QLineEdit#StepperEdit {{
            background: transparent;
            border: none;
            color: {c.text_primary};
            padding: 4px 8px;
            font-size: {t.body_size}px;
            selection-background-color: {c.accent};
            selection-color: {c.accent_fg};
        }}
        #Stepper QToolButton#StepperUp, #Stepper QToolButton#StepperDown {{
            background-color: transparent;
            border: none;
            border-left: 1px solid {c.border_subtle};
            color: {c.text_secondary};
            padding: 0 6px;
            font-size: {t.caption_size}px;
        }}
        #Stepper QToolButton#StepperUp {{ border-bottom: 1px solid {c.border_subtle}; }}
        #Stepper QToolButton#StepperUp:hover, #Stepper QToolButton#StepperDown:hover {{
            background-color: {c.hover};
            color: {c.text_primary};
        }}
        """


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import (
        QApplication, QFormLayout, QLabel, QVBoxLayout, QWidget as _QW,
    )

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    root = _QW()
    root.setWindowTitle("Stepper — demo")
    pad = theme.Spacing.lg
    outer = QVBoxLayout(root)
    outer.setContentsMargins(pad, pad, pad, pad)
    outer.setSpacing(theme.Spacing.md)

    title = QLabel("Stepper")
    title.setObjectName("Title")
    outer.addWidget(title)

    form = QFormLayout()
    form.setSpacing(theme.Spacing.md)
    s_int = Stepper(value=8, minimum=1, maximum=96, single_step=1, decimals=0)
    s_int.setSuffix(" px")
    s_frac = Stepper(value=0.5, minimum=0.0, maximum=1.0, single_step=0.05, decimals=2)
    s_dpi = Stepper(value=300, minimum=72, maximum=1200, single_step=50, decimals=0)
    s_dpi.setSuffix(" dpi")
    form.addRow("Line width:", s_int)
    form.addRow("Threshold:", s_frac)
    form.addRow("Export DPI:", s_dpi)
    outer.addLayout(form)

    echo = QLabel("(interact above)")
    echo.setObjectName("Secondary")
    outer.addWidget(echo)
    s_int.valueChanged.connect(lambda v: echo.setText(f"line width → {v}"))
    s_frac.valueChanged.connect(lambda v: echo.setText(f"threshold → {v}"))
    s_dpi.valueChanged.connect(lambda v: echo.setText(f"dpi → {v}"))
    outer.addStretch(1)

    root.resize(360, 280)
    root.show()
    _sys.exit(app.exec())
