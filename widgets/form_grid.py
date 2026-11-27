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

Gaps are the four-point grid's: 12 between rows, 16 between the columns. In phone layout a row's label goes above
its controls, the one arrangement a phone's width holds; the grid says so where it is built
(``widgets.form_registry.register``), so phone layout recognises nothing about it. It knows nothing of the app beyond the
two text-role property values the application stylesheet styles (``ui/text_roles.py``).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QWidget

from ui import theme


class _ColumnLabel(QLabel):
    """A row's label: it asks for the whole label column, so the column is one width wherever there is room, and can
    still wrap narrower where there is not."""

    def __init__(self, text: str, parent: QWidget, width: int) -> None:
        super().__init__(text, parent)
        self._column = width

    def sizeHint(self):  # noqa: N802 — Qt naming
        hint = super().sizeHint()
        hint.setWidth(min(self._column, self.maximumWidth()))
        return hint


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
        # The label column asks for *label_width* (each label's size hint) and is not pinned there: on a page with
        # room every row's controls start at one edge, and on a narrow one the labels wrap rather than holding the
        # grid wider than the page.
        self._grid.setColumnStretch(1, 1)
        self._label_width = label_width
        self._rows = 0
        # What was added, in order, so the grid can be laid out again in its other form: ("row", label, field,
        # help note or None) or ("wide", widget).
        self._entries: list[tuple] = []
        self._phone = False
        from widgets import form_registry
        form_registry.register(self, self._arrange)

    def add_row(self, label: str, *controls: QWidget, help: str = "") -> QLabel:  # noqa: A002 — the word a reader uses
        """A labelled row; returns the label, so a page that renames it can."""
        if not controls:
            raise ValueError("add_row needs at least one control")
        cap = _ColumnLabel(label, self, self._label_width)
        cap.setProperty("textRole", "label")
        cap.setWordWrap(True)
        cap.setMaximumWidth(self._label_width)
        cap.setBuddy(controls[0])
        if len(controls) == 1:
            field = controls[0]
        else:
            host = QWidget(self)
            lay = QHBoxLayout(host)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(theme.Spacing.sm)
            for i, c in enumerate(controls):
                lay.addWidget(c, 1 if i == 0 else 0)
            field = host
        note = None
        if help:
            note = QLabel(help, self)
            note.setProperty("textRole", "help")
            note.setWordWrap(True)
        self._entries.append(("row", cap, field, note))
        self._place(self._entries[-1])
        return cap

    def add_wide(self, control: QWidget) -> None:
        """A control that labels itself, across both columns."""
        self._entries.append(("wide", control))
        self._place(self._entries[-1])

    def add_eyebrow(self, text: str) -> QLabel:
        """A group's name, in capitals, above its rows."""
        cap = QLabel(text.upper(), self)
        cap.setProperty("textRole", "eyebrow")
        self._entries.append(("wide", cap))
        self._place(self._entries[-1])
        return cap

    def row_count(self) -> int:
        return self._rows

    def _place(self, entry: tuple) -> None:
        """Lay *entry* out after the rows already placed: beside its label at the desk, under it on a phone."""
        if entry[0] == "wide":
            self._grid.addWidget(entry[1], self._rows, 0, 1, 2)
            self._rows += 1
            return
        _kind, cap, field, note = entry
        if self._phone:
            cap.setMaximumWidth(16_777_215)          # Qt's QWIDGETSIZE_MAX: the label takes the row
            self._grid.addWidget(cap, self._rows, 0, 1, 2)
            self._grid.addWidget(field, self._rows + 1, 0, 1, 2)
            self._rows += 2
        else:
            cap.setMaximumWidth(self._label_width)
            self._grid.addWidget(cap, self._rows, 0, Qt.AlignLeft | Qt.AlignVCenter)
            self._grid.addWidget(field, self._rows, 1)
            self._rows += 1
        if note is not None:
            self._grid.addWidget(note, self._rows, 0 if self._phone else 1, 1, 2 if self._phone else 1)
            self._rows += 1

    def _arrange(self, phone: bool) -> None:
        """Phone layout's form (``form_registry``): every row laid out again, label above or beside."""
        if bool(phone) == self._phone:
            return
        self._phone = bool(phone)
        for entry in self._entries:
            for w in entry[1:]:
                if w is not None:
                    self._grid.removeWidget(w)
        self._rows = 0
        for entry in self._entries:
            hidden = [w.isHidden() for w in entry[1:] if w is not None]
            self._place(entry)
            for w, h in zip([w for w in entry[1:] if w is not None], hidden):
                w.setHidden(h)
