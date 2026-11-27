"""The one way to set a range: a minimum, a maximum, Auto and Reset.

A display range — the brightness window an image is drawn in — was set three
ways: two ``LUT ▾`` panels with their own pair of fields, and a floating
popover whose button appeared in four places. :class:`RangePage` is the one
component, shown as a page:

* **min** and **max** fields. A range applies when either is edited and both
  are numbers with the maximum above the minimum; anything else is left as
  typed and applies nothing, so a half-typed number never moves the picture.
* **Auto** clears both: the caller's own scale takes over. Both fields empty
  is Auto too.
* **Reset** puts back the range the page was opened with — the way out of an
  edit that went wrong, without having to remember the numbers.

:attr:`changed` carries ``(low, high)``, or ``(None, None)`` for Auto.

Qt-only, no All-Well knowledge.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from ui import theme


def parse_number(text: str) -> float | None:
    """The number *text* reads as, or ``None`` when it is not one."""
    try:
        return float(str(text).strip())
    except ValueError:
        return None


def format_number(value) -> str:
    return "" if value is None else f"{float(value):g}"


class RangePage(QWidget):
    """min · max · Auto · Reset."""

    #: ``(low, high)`` when a valid range was entered, ``(None, None)`` for Auto.
    changed = Signal(object, object)

    def __init__(self, parent: QWidget | None = None, *, auto_tip: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("RangePage")
        self._opened_with: tuple[float | None, float | None] = (None, None)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.Spacing.sm)
        self.caption = QLabel("", self)
        self.caption.setObjectName("Muted")
        self.caption.setWordWrap(True)
        self.caption.hide()
        outer.addWidget(self.caption)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.low_edit = QLineEdit(self)
        self.high_edit = QLineEdit(self)
        for edit in (self.low_edit, self.high_edit):
            edit.setPlaceholderText("auto")
            edit.editingFinished.connect(self._commit)
        form.addRow("Minimum", self.low_edit)
        form.addRow("Maximum", self.high_edit)
        outer.addLayout(form)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.Spacing.sm)
        self.auto_button = QPushButton("Auto", self)
        self.reset_button = QPushButton("Reset", self)
        if auto_tip:
            self.auto_button.setToolTip(auto_tip)
        self.reset_button.setToolTip("Back to the range this page opened with.")
        for button in (self.auto_button, self.reset_button):
            button.setProperty("variant", "default")
            row.addWidget(button, 1)
        self.auto_button.clicked.connect(lambda _=False: self.setAuto())
        self.reset_button.clicked.connect(lambda _=False: self.reset())
        outer.addLayout(row)

    # ── API ──────────────────────────────────────────────────────────────
    def setCaption(self, text: str) -> None:
        """A line above the fields: which channel, in what units."""
        self.caption.setText(str(text or ""))
        self.caption.setVisible(bool(text))

    def setRange(self, low, high) -> None:
        """Show *low* and *high* (``None`` for Auto) and remember them as what
        Reset returns to. Emits nothing: filling a page is not an edit."""
        self._opened_with = (low, high)
        self._show(low, high)

    def range(self) -> tuple[float | None, float | None]:
        """The range the fields hold, or ``(None, None)`` when they hold none."""
        low, high = parse_number(self.low_edit.text()), parse_number(self.high_edit.text())
        if low is not None and high is not None and high > low:
            return low, high
        return None, None

    def summary(self) -> str:
        """``"auto"`` or ``"120–3400"``, for the row that opens the page."""
        low, high = self.range()
        return "auto" if low is None else f"{format_number(low)}–{format_number(high)}"

    def setAuto(self) -> None:
        self._show(None, None)
        self.changed.emit(None, None)

    def reset(self) -> None:
        low, high = self._opened_with
        self._show(low, high)
        self.changed.emit(low, high)

    # ── internals ────────────────────────────────────────────────────────
    def _show(self, low, high) -> None:
        self.low_edit.setText(format_number(low))
        self.high_edit.setText(format_number(high))

    def _commit(self) -> None:
        if not self.low_edit.text().strip() and not self.high_edit.text().strip():
            self.changed.emit(None, None)
            return
        low, high = self.range()
        if low is not None:
            self.changed.emit(low, high)
