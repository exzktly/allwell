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
``==``. A :class:`Heading` among the items is a caption over the rows that
follow it (a family of columns), not a row: it has no tick and no key.

Qt-only, no All-Well knowledge. A caller whose update is expensive debounces
:attr:`changed`; there is no "apply when closed", because a page has no close
that every route out of it passes through.

:class:`MultiSelectButton` is the same component for a form: a button that
reads the choice (``2 of 8 timepoints ›``) and opens the list as a page. Where
the window has an inspector the caller hands it an opener that pushes the page
there; elsewhere (the Analyze form, a Datasets tool) the page is a small window
of its own.
"""

from __future__ import annotations

from collections.abc import Iterable

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from ui import theme


def summarise(checked: int, total: int) -> str:
    """``"All"``, ``"None"`` or ``"3 of 8"`` — and ``"None"`` for an empty list."""
    if total <= 0 or checked <= 0:
        return "None"
    if checked >= total:
        return "All"
    return f"{checked} of {total}"


class Heading(str):
    """A caption among a :class:`MultiSelect`'s items, over the rows after it."""


class MultiSelect(QWidget):
    """Check rows under All / None; :attr:`changed` on every tick."""

    #: The checked keys, in the order the items were given.
    changed = Signal(list)

    def __init__(self, parent: QWidget | None = None, *, empty_text: str = "Nothing to choose from.") -> None:
        super().__init__(parent)
        self.setObjectName("MultiSelect")
        self._keys: list = []
        self._boxes: list[QCheckBox] = []
        self._headings: list[QLabel] = []
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
        for row in (*self._boxes, *self._headings):
            self._rows.removeWidget(row)
            row.deleteLater()
        self._keys, self._boxes, self._headings = [], [], []
        ticked = list(checked)
        for item in items:
            if isinstance(item, Heading):
                caption = QLabel(str(item), self)
                caption.setObjectName("Secondary")
                self._rows.addWidget(caption)
                self._headings.append(caption)
                continue
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
        """Tick every row, or none — one :attr:`changed`, however many rows.
        A disabled row (an option that is always on) keeps its tick."""
        before = self.checked()
        fixed = {id(b): b.isChecked() for b in self._boxes if not b.isEnabled()}
        self._set_boxes(lambda _key: bool(value))
        for box in self._boxes:
            if id(box) in fixed:
                was = box.blockSignals(True)
                box.setChecked(fixed[id(box)])
                box.blockSignals(was)
        if self.checked() != before:
            self.changed.emit(self.checked())

    def summary(self) -> str:
        return summarise(len(self.checked()), len(self._keys))

    def boxes(self) -> list[QCheckBox]:
        return list(self._boxes)

    def headings(self) -> list[str]:
        """The captions among the rows, in order."""
        return [h.text() for h in self._headings]

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


#: What ends a button that opens a page (not a menu arrow: it has no menu).
CHEVRON = "›"


class MultiSelectButton(QWidget):
    """A form's multi-select: a button reading the choice, opening the list.

    The options and their ticks are held here, so the list can be filled,
    refilled and read whether or not its page is open. :attr:`changed` is
    emitted once per change of the ticks — one tick, All, None, or
    :meth:`setCheckedData` — with no argument; read :meth:`checkedData`.
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None, *,
                 noun: str = "selected", empty_text: str = "None selected",
                 title: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("MultiSelectButton")
        self._noun = noun
        self._empty_text = empty_text
        self._title = title or noun.capitalize()
        self._labels: list[str] = []
        self._data: list[object] = []
        self._checked: list[bool] = []
        #: Said on the page while it has no options, so an empty list reads as
        #: waiting rather than as All and None that do nothing.
        self._empty_page_text = ""
        #: ``opener(self)`` shows the page somewhere else (an inspector);
        #: ``None`` shows it as a window of this button's own.
        self._opener: Callable[[MultiSelectButton], None] | None = None
        self._page: MultiSelect | None = None
        self._window: QDialog | None = None
        #: ``face(checked, total) -> str``: what the button reads, when the
        #: caller words it itself (:meth:`setFaceFormatter`).
        self._face = None
        #: Keys always ticked and not to be unticked (a channel always drawn).
        self._locked: list = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._button = QPushButton(self)
        self._button.setObjectName("MultiSelectButtonFace")
        self._button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._button.setCursor(Qt.PointingHandCursor)
        self._button.clicked.connect(lambda _=False: self.open())
        lay.addWidget(self._button)
        self._refresh_summary()

    # ── the options ──────────────────────────────────────────────────────
    def addItem(self, text: str, data: object | None = None) -> int:
        idx = len(self._labels)
        self._labels.append(str(text))
        self._data.append(data if data is not None else text)
        self._checked.append(False)
        self._refresh_summary()
        return idx

    def count(self) -> int:
        return len(self._labels)

    def clear(self) -> None:
        """Drop every option and its tick. Never emits ``changed`` — the
        option list is not the selection, and a refill that happens to end
        with the same ticks is not an edit."""
        self._labels.clear()
        self._data.clear()
        self._checked.clear()
        self._refresh_summary()

    def setEmptyPageText(self, text: str) -> None:
        """What the page says while it has no options; ``""`` for its default."""
        self._empty_page_text = str(text or "")

    def items(self) -> list[tuple[object, str]]:
        """``(data, label)`` per option, in order — what a page lists."""
        return list(zip(self._data, self._labels))

    def title(self) -> str:
        return self._title

    def text(self) -> str:
        """What the button reads now."""
        return self._button.text()

    def checkedIndices(self) -> list[int]:
        return [i for i, on in enumerate(self._checked) if on]

    def checkedData(self) -> list:
        return [self._data[i] for i in self.checkedIndices()]

    def checkedLabels(self) -> list[str]:
        return [self._labels[i] for i in self.checkedIndices()]

    def setCheckedData(self, values) -> None:
        vals = list(values or [])
        self._apply([d in vals for d in self._data])

    def applyChecked(self, keys) -> None:
        """The page's ticks changed to *keys*: one ``changed`` for the lot."""
        self.setCheckedData(keys)

    def setFaceFormatter(self, face) -> None:
        """Word the button through ``face(checked_data, all_data) -> str``."""
        self._face = face
        self._refresh_summary()

    def setLocked(self, keys) -> None:
        """Keep *keys* ticked and their rows disabled: options that are always on."""
        self._locked = list(keys or [])
        self._sync_page()

    def setOpener(self, opener: Callable[[MultiSelectButton], None] | None) -> None:
        """Show the page through *opener* (an inspector's push) instead of in
        a window of this button's own."""
        self._opener = opener

    # ── internals ────────────────────────────────────────────────────────
    def _apply(self, ticks: list[bool]) -> None:
        changed = ticks != self._checked
        self._checked = list(ticks)
        self._refresh_summary()
        self._sync_page()
        if changed:
            self.changed.emit()

    def _set_checked(self, index: int, on: bool) -> None:
        if 0 <= index < len(self._checked):
            ticks = list(self._checked)
            ticks[index] = bool(on)
            self._apply(ticks)

    def _set_all(self, value: bool) -> None:
        self._apply([bool(value)] * len(self._checked))

    def _set_face(self, text: str) -> None:
        self._button.setText(f"{text} {CHEVRON}")

    def _refresh_summary(self) -> None:
        if self._face is not None:
            self._set_face(self._face(self.checkedData(), list(self._data)))
            self._button.setToolTip(", ".join(self.checkedLabels()))
            return
        total = len(self._labels)
        n = sum(self._checked)
        if n == 0:
            text = self._empty_text
        elif n == total:
            text = f"All {total} {self._noun}"
        else:
            text = f"{n} of {total} {self._noun}"
        self._set_face(text)
        self._button.setToolTip(", ".join(self.checkedLabels()))

    # ── the page ─────────────────────────────────────────────────────────
    def open(self) -> None:
        """Show the list: through the opener when one is set, else as a
        window of this button's own."""
        if self._opener is not None:
            self._opener(self)
            return
        if self._window is None:
            self._window = self._build_window()
        self._sync_page()
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()

    def page(self) -> MultiSelect | None:
        """The list in this button's own window, once it has been opened."""
        return self._page

    def _build_window(self) -> QDialog:
        window = QDialog(self.window())
        window.setObjectName("MultiSelectWindow")
        window.setWindowTitle(self._title)
        outer = QVBoxLayout(window)
        scroll = QScrollArea(window)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        page = MultiSelect(scroll)
        # All and None go through this button, which a subclass may give a
        # meaning of its own (every timepoint of the source, not every row).
        for button, value in ((page.all_button, True), (page.none_button, False)):
            button.clicked.disconnect()
            button.clicked.connect(lambda _=False, v=value: self._set_all(v))
        page.changed.connect(self.applyChecked)
        scroll.setWidget(page)
        outer.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, window)
        buttons.rejected.connect(window.close)
        outer.addWidget(buttons)
        self._page = page
        return window

    def _sync_page(self) -> None:
        """Make the open page show the options and ticks held here."""
        page = self._page
        if page is None:
            return
        if page.keys() != self._data or [b.text() for b in page.boxes()] != self._labels:
            page.setItems(self.items(), self.checkedData())
        else:
            page.setChecked(self.checkedData())
        page._empty.setText(self._empty_page_text or "Nothing to choose from.")
        page._sync_bulk()
        for button in (page.all_button, page.none_button):
            button.setEnabled(self._bulk_enabled())
        for key, box in zip(page.keys(), page.boxes()):
            if key in self._locked:
                box.setEnabled(False)

    def _bulk_enabled(self) -> bool:
        """Whether All and None have anything to act on."""
        return bool(self._labels)
