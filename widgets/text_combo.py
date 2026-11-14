"""A text field that also offers a list of suggestions: an editable combo box with a line edit's text API.

For a field that takes a name the person may type, when most of the time the name is one the form already knows. The
list is only a list of suggestions — any text can be typed, and is kept exactly as typed (there is no inline completion
that would turn a prefix of a suggestion into the suggestion) — and picking one is the same as typing it. It answers
``text()``, ``setText()``, ``clear()``, ``textChanged`` and ``textEdited`` like the ``QLineEdit`` it stands in for, so
the code reading the field does not change.

No All-Well coupling.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox


class TextCombo(QComboBox):
    textChanged = Signal(str)
    #: The text changed by a person: typed, or picked from the list (not ``setText``).
    textEdited = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        # No completer: an editable combo's default one completes inline and case-insensitively, so typing a prefix of
        # a suggestion ("DAPI" beside "DAPI_405") fills in the rest, and leaving the field keeps it. The field takes
        # names the list does not hold, so what was typed is what the field says; the list is picked from, not guessed.
        self.setCompleter(None)
        edit = self.lineEdit()
        edit.textChanged.connect(self.textChanged)
        edit.textEdited.connect(self.textEdited)
        self.activated.connect(lambda _i: self.textEdited.emit(self.text()))

    def text(self) -> str:
        return self.lineEdit().text()

    def setText(self, text: str) -> None:  # noqa: N802 — QLineEdit's name
        self.setEditText(text)

    def clear(self) -> None:
        """Empty the text, as a ``QLineEdit`` does; the suggestions stay (:meth:`set_options` replaces them)."""
        self.setEditText("")

    def setPlaceholderText(self, text: str) -> None:  # noqa: N802
        self.lineEdit().setPlaceholderText(text)

    def set_options(self, options: Iterable[str]) -> None:
        """Replace the suggestions, keeping the text as it is. Silent: the text ends as it began, and a signal on the way
        (emptied, then restored) would reach a listener that answers by calling this again."""
        text = self.text()
        blocked = self.blockSignals(True)
        try:
            super().clear()
            self.addItems([o for o in options if o])
            self.setEditText(text)
        finally:
            self.blockSignals(blocked)
