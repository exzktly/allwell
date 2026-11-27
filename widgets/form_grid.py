"""A form laid out as two columns: labels in one column of one width, controls in the other.

Form pages and inspector pages hold many labelled controls one under another. Laid out one row at a time, each
label takes its own width and the controls start wherever their label ends, so there is no edge to scan down.
:class:`FormGrid` gives every row the same label column, so the controls line up.

- :meth:`add_row` — a label and one or more controls (several sit side by side: a field and its Browse…), with an
  optional line of help under the controls. The label is in the label role (``textRole="label"``) and takes no
  colon.
- :meth:`add_wide` — a control that labels itself (a checkbox, a segmented control's own caption) across both
  columns.
- :meth:`add_eyebrow` — a group's name in capitals above the rows that belong to it.

Gaps are the four-point grid's: 12 between rows, 16 between the columns. It knows nothing of the app beyond the
two text-role property values the application stylesheet styles (``ui/text_roles.py``).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QWidget

from ui import theme


class FormGrid(QWidget):
    """Rows of (label, controls), the labels in a column *label_width* wide (``Metrics.form_label_w``)."""

    def __init__(self, parent: QWidget | None = None, *, label_width: int | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FormGrid")
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(theme.Spacing.lg)
        self._grid.setVerticalSpacing(theme.Spacing.md)
        if label_width is None:
            label_width = theme.Metrics.form_label_w
        self._grid.setColumnMinimumWidth(0, label_width)
        self._grid.setColumnStretch(1, 1)
        self._label_width = label_width
        self._rows = 0

    def add_row(self, label: str, *controls: QWidget, help: str = "") -> QLabel:  # noqa: A002 — the word a reader uses
        """A labelled row; returns the label, so a page that renames it can."""
        if not controls:
            raise ValueError("add_row needs at least one control")
        cap = QLabel(label, self)
        cap.setProperty("textRole", "label")
        cap.setWordWrap(True)
        cap.setMaximumWidth(self._label_width)
        cap.setBuddy(controls[0])
        self._grid.addWidget(cap, self._rows, 0, Qt.AlignLeft | Qt.AlignVCenter)
        if len(controls) == 1:
            self._grid.addWidget(controls[0], self._rows, 1)
        else:
            host = QWidget(self)
            lay = QHBoxLayout(host)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(theme.Spacing.sm)
            for i, c in enumerate(controls):
                lay.addWidget(c, 1 if i == 0 else 0)
            self._grid.addWidget(host, self._rows, 1)
        self._rows += 1
        if help:
            note = QLabel(help, self)
            note.setProperty("textRole", "help")
            note.setWordWrap(True)
            self._grid.addWidget(note, self._rows, 1)
            self._rows += 1
        return cap

    def add_wide(self, control: QWidget) -> None:
        """A control that labels itself, across both columns."""
        self._grid.addWidget(control, self._rows, 0, 1, 2)
        self._rows += 1

    def add_eyebrow(self, text: str) -> QLabel:
        """A group's name, in capitals, above its rows."""
        cap = QLabel(text.upper(), self)
        cap.setProperty("textRole", "eyebrow")
        self._grid.addWidget(cap, self._rows, 0, 1, 2)
        self._rows += 1
        return cap

    def row_count(self) -> int:
        return self._rows
