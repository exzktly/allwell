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
from PySide6.QtGui import QDoubleValidator, QValidator, QFontMetrics
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLineEdit, QSizePolicy, QToolButton, QVBoxLayout,
)

import theme


def control_row_height() -> int:
    """The height a themed combo box takes, measured rather than guessed.

    A stepper sits in the same control row as the channel and property combos,
    so it has to be the same height as them or the row reads as ragged. Left to
    itself it is taller: its two stacked step buttons each want a full text
    row, which came to 35px against the combos' 25px.

    The number is derived from a throwaway combo rather than written down,
    because it depends on the application stylesheet and the current font —
    exactly the two things a literal would go stale against. This mirrors
    ``widgets.mode_bar.tab_bar_height``, which measures a ``QTabBar`` for the
    same reason, and is deliberately not cached for the same reason: a widget
    built during startup would freeze a stylesheet that has not been applied.
    """
    from widgets.labeled_combo import LabeledComboBox

    # A LabeledComboBox, not a bare QComboBox: the band's combos are that
    # subclass and it is 3px shorter, so measuring the plain one silently
    # reproduced the mismatch this function exists to remove.
    probe = LabeledComboBox("Ag")
    probe.addItem("Ag")              # ascender + descender: the tallest case
    probe.ensurePolished()
    height = probe.sizeHint().height()
    probe.deleteLater()
    return height


class _AffixValidator(QValidator):
    """Validate the *number* a Stepper's text carries, not the affixes around it.

    ``QDoubleValidator`` alone judges the whole line, so the moment a prefix or
    suffix is set every displayed value ("-1 spots") is ``Invalid``. That does
    not merely mislabel the text: ``QLineEdit`` only reverts an edit that takes
    the field from valid to invalid, so an already-invalid box accepts every
    keystroke. The suffix turned "input refused" into "input accepted and then
    silently discarded on commit" — the box showed the old number again and the
    user's edit vanished with no feedback.

    Delegating on the stripped text restores the refusal, and keeps the affixes
    the display feature they were meant to be.
    """

    def __init__(self, stepper: "Stepper", inner: QDoubleValidator) -> None:
        super().__init__(stepper)
        self._stepper = stepper
        self._inner = inner

    def validate(self, text: str, pos: int):  # noqa: N802 - Qt override
        stripped = self._stepper._strip(text)
        # An empty box, or a lone sign / decimal point, is Intermediate: the user
        # is mid-edit and must be allowed to get to a real number.
        if stripped in ("", "-", "+", ".", "-.", "+."):
            return (QValidator.Intermediate, text, pos)
        state, _fixed, _p = self._inner.validate(stripped, min(pos, len(stripped)))
        return (state, text, pos)


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
        # Wrapped so a prefix/suffix does not make every displayed value invalid
        # — see _AffixValidator for what that silently costs.
        self._edit.setValidator(_AffixValidator(self, self._validator))
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

        # Match the combos beside us. The two step buttons split the row
        # between them; without the explicit split each still asks for a full
        # text row and the frame is forced taller than it was just pinned to.
        self._apply_row_height()

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
        """The number inside *text*, with prefix and suffix taken off.

        The affixes are matched without their own padding — a suffix of
        " spots" is recognised in "spots" too. Deleting the digits from
        "20 spots" leaves exactly that, and the whole-string match refused to
        see a suffix there, so clearing the box to retype read as rubbish and
        the keystroke was rejected.
        """
        text = text.strip()
        prefix = self._prefix.strip()
        suffix = self._suffix.strip()
        if prefix and text.startswith(prefix):
            text = text[len(prefix):]
        if suffix and text.endswith(suffix):
            text = text[: -len(suffix)]
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

    def _apply_row_height(self) -> None:
        """Pin this stepper to the height of the combos beside it.

        The frame's own 1px border top and bottom is the only thing outside the
        two children, so they get what is left. Without pinning the edit as
        well, its vertical text padding keeps the layout's minimum above the
        height the frame was just given and the frame renders taller than asked.
        """
        row_h = control_row_height()
        self.setFixedHeight(row_h)
        inner = max(1, row_h - 2)
        self._edit.setFixedHeight(inner)
        for b in (self._up, self._down):
            b.setFixedHeight(inner // 2)

    def changeEvent(self, event) -> None:  # Qt naming
        """Re-measure when the stylesheet or font moves under us.

        Measuring once in ``__init__`` is not enough: the reference height
        depends on the application stylesheet, and a stepper built before that
        stylesheet is applied — which is every stepper built during startup —
        would freeze an unstyled number and then sit a pixel or two off the
        combos for the rest of the session. This is the same staleness
        ``mode_bar.tab_bar_height`` refuses to cache for.
        """
        if event.type() in (QEvent.StyleChange, QEvent.FontChange):
            self._apply_row_height()
        super().changeEvent(event)

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
        /* Without these a disabled stepper is indistinguishable from a live
           one: the arrows keep their full-strength secondary tone and the
           value keeps its primary tone, so the only cue that the control is
           inert is that clicking it does nothing. */
        #Stepper QLineEdit#StepperEdit:disabled {{ color: {c.text_faint}; }}
        #Stepper QToolButton#StepperUp:disabled,
        #Stepper QToolButton#StepperDown:disabled {{
            color: {c.text_faint};
            background-color: transparent;
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
