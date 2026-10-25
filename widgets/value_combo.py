"""ValueCombo — a QComboBox whose items are (value, label) pairs.

The label is what a person reads ("Robust local mesh"); the value is what the
program means by it (``robust_mesh``), carried as the item's userData. Code
that decides or records something reads :meth:`value` and writes
:meth:`setValue`, and never the label: a label is copy, which is rewritten for
readers, while a value is an argument the pipeline parses and a sidecar stores.

``currentText()`` still returns the label, exactly as on a plain combo. That is
the trap this class exists to make visible: a caller that used to compare
``currentText()`` against an id now reads a sentence, and the comparison quietly
answers ``False``. So the value has its own accessor with its own name, and a
caller that wants the id says so.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox


class ValueCombo(QComboBox):
    """A combo of ``(value, label)`` items, read and written by value."""

    def __init__(self, options=(), parent=None) -> None:
        super().__init__(parent)
        for value, label in options:
            self.addOption(value, label)

    def addOption(self, value: str, label: str) -> None:
        """Append an item showing *label* and meaning *value*."""
        self.addItem(label, userData=value)

    def value(self) -> str:
        """The current item's value, or ``""`` when the combo is empty."""
        data = self.currentData()
        return "" if data is None else str(data)

    def setValue(self, value: str) -> bool:
        """Select the item meaning *value*; ``False`` (and no change) when no
        item means it, so a caller restoring an unknown value can tell."""
        index = self.findData(value)
        if index < 0:
            return False
        self.setCurrentIndex(index)
        return True

    def values(self) -> list[str]:
        """Every item's value, in order."""
        return [str(self.itemData(i)) for i in range(self.count())]
