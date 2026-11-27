"""The one way to pick several of a list.

A list of timepoints, fields of view, gates, channels or series with a tick
beside each was built eight separate ways — checkboxes in a menu, checkable
actions, a dropdown of its own — some applying as a box was ticked and some
only when the menu closed. :class:`MultiSelect` is the one component: a column
of check rows under an **All** / **None** pair, shown as a page (of an
inspector, of a window), which

* **applies as a box is ticked.** :attr:`changed` carries the checked keys, in
  the list's own order, on every tick — and once, not once per row, for All
  and for None, so a redraw behind it runs once.
* **says what is chosen in three words.** :meth:`summary` is ``"All"``,
  ``"None"`` or ``"3 of 8"``, which is what the row that opens the page shows.

An item is ``(key, label)``, or a bare string that is both. Keys are whatever
the caller's state holds — strings, numbers, tuples — and are compared with
``==``.

Qt-only, no All-Well knowledge. A caller whose update is expensive debounces
:attr:`changed`; there is no "apply when closed", because a page has no close
that every route out of it passes through.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ui import theme


def summarise(checked: int, total: int) -> str:
    """``"All"``, ``"None"`` or ``"3 of 8"`` — and ``"None"`` for an empty list."""
    if total <= 0 or checked <= 0:
        return "None"
    if checked >= total:
        return "All"
    return f"{checked} of {total}"


class MultiSelect(QWidget):
    """Check rows under All / None; :attr:`changed` on every tick."""

    #: The checked keys, in the order the items were given.
    changed = Signal(list)

    def __init__(self, parent: QWidget | None = None, *, empty_text: str = "Nothing to choose from.") -> None:
        super().__init__(parent)
        self.setObjectName("MultiSelect")
        self._keys: list = []
        self._boxes: list[QCheckBox] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.Spacing.sm)
        bulk = QHBoxLayout()
        bulk.setContentsMargins(0, 0, 0, 0)
        bulk.setSpacing(theme.Spacing.sm)
        self.all_button = QPushButton("All", self)
        self.none_button = QPushButton("None", self)
        for button, value in ((self.all_button, True), (self.none_button, False)):
            button.setProperty("variant", "secondary")
            button.clicked.connect(lambda _=False, v=value: self.setAll(v))
            bulk.addWidget(button, 1)
        outer.addLayout(bulk)
        self._empty = QLabel(empty_text, self)
        self._empty.setObjectName("Muted")
        self._empty.setWordWrap(True)
        outer.addWidget(self._empty)
        self._rows = QVBoxLayout()
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(theme.Spacing.xs)
        outer.addLayout(self._rows)
        self._sync_bulk()

    # ── the list ─────────────────────────────────────────────────────────
    def setItems(self, items: Iterable, checked: Iterable = ()) -> None:
        """Replace the list. *items* are ``(key, label)`` pairs or strings;
        *checked* the keys ticked to begin with. Emits nothing: filling a
        page is not the user choosing."""
        for box in self._boxes:
            self._rows.removeWidget(box)
            box.deleteLater()
        self._keys, self._boxes = [], []
        ticked = list(checked)
        for item in items:
            key, label = item if isinstance(item, tuple) else (item, str(item))
            box = QCheckBox(str(label), self)
            box.setChecked(any(key == k for k in ticked))
            box.toggled.connect(self._on_toggled)
            self._rows.addWidget(box)
            self._keys.append(key)
            self._boxes.append(box)
        self._sync_bulk()

    def keys(self) -> list:
        return list(self._keys)

    def checked(self) -> list:
        return [k for k, box in zip(self._keys, self._boxes) if box.isChecked()]

    def setChecked(self, keys: Iterable) -> None:
        """Tick exactly *keys*, emitting nothing (the caller's state changed
        and the page follows it)."""
        wanted = list(keys)
        self._set_boxes(lambda key: any(key == k for k in wanted))

    def setAll(self, value: bool) -> None:
        """Tick every row, or none — one :attr:`changed`, however many rows."""
        before = self.checked()
        self._set_boxes(lambda _key: bool(value))
        if self.checked() != before:
            self.changed.emit(self.checked())

    def summary(self) -> str:
        return summarise(len(self.checked()), len(self._keys))

    def boxes(self) -> list[QCheckBox]:
        return list(self._boxes)

    # ── internals ────────────────────────────────────────────────────────
    def _set_boxes(self, want) -> None:
        for key, box in zip(self._keys, self._boxes):
            was = box.blockSignals(True)
            try:
                box.setChecked(bool(want(key)))
            finally:
                box.blockSignals(was)

    def _on_toggled(self, _checked: bool) -> None:
        self.changed.emit(self.checked())

    def _sync_bulk(self) -> None:
        any_rows = bool(self._keys)
        self._empty.setVisible(not any_rows)
        self.all_button.setEnabled(any_rows)
        self.none_button.setEnabled(any_rows)

    def keyPressEvent(self, event) -> None:  # Qt naming
        # Ctrl+A ticks everything: a list's oldest shortcut.
        if event.key() == Qt.Key_A and event.modifiers() & Qt.ControlModifier:
            self.setAll(True)
            return
        super().keyPressEvent(event)
