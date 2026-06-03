"""CheckableDropdown — a compact multi-select rendered as a button + checkbox menu.

The space-saving cousin of a multi-select :class:`~widgets.chip_group.ChipGroup`:
instead of a row of pill chips that grows as wide as the option list, this shows
a single summary button ("3 of 9 selected") that drops a :class:`QMenu` of
checkboxes (plus All / None shortcuts) — the same interaction the scatter tab's
timepoint picker uses. Use it when the option list is long enough that inline
chips would overflow a narrow sidebar.

API (a subset-compatible drop-in for ``ChipGroup(exclusive=False)``)
-------------------------------------------------------------------
* ``addItem(text, data=None) -> int``
* ``checkedData() -> list``           — ``data`` of every checked item, in order
* ``setCheckedData(values)``          — check exactly the items whose data is in *values*
* ``checkedLabels() -> list[str]``
* ``changed`` signal                  — emitted on any selection change

The checked state lives on the widget (not in the transient menu), so the
selection can be read / set headlessly without ever opening the menu.
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt, Signal  # noqa: E402
from PySide6.QtGui import QAction  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QCheckBox, QMenu, QPushButton, QSizePolicy, QVBoxLayout, QWidgetAction, QWidget,
)


class CheckableDropdown(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None, *,
                 noun: str = "selected", empty_text: str = "None selected") -> None:
        super().__init__(parent)
        self.setObjectName("CheckableDropdown")
        self._noun = noun
        self._empty_text = empty_text
        self._labels: list[str] = []
        self._data: list[object] = []
        self._checked: list[bool] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._button = QPushButton(self)
        self._button.setObjectName("CheckableDropdownButton")
        self._button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._button.setCursor(Qt.PointingHandCursor)
        # A click pops the checkbox menu just below the button.
        self._button.clicked.connect(self._open_menu)
        lay.addWidget(self._button)
        self._refresh_summary()

    # ── API ──────────────────────────────────────────────────────────────
    def addItem(self, text: str, data: object | None = None) -> int:
        idx = len(self._labels)
        self._labels.append(str(text))
        self._data.append(data if data is not None else text)
        self._checked.append(False)
        self._refresh_summary()
        return idx

    def count(self) -> int:
        return len(self._labels)

    def checkedIndices(self) -> list[int]:
        return [i for i, on in enumerate(self._checked) if on]

    def checkedData(self) -> list:
        return [self._data[i] for i in self.checkedIndices()]

    def checkedLabels(self) -> list[str]:
        return [self._labels[i] for i in self.checkedIndices()]

    def setCheckedData(self, values) -> None:
        vals = list(values or [])
        changed = False
        for i, d in enumerate(self._data):
            want = d in vals
            if self._checked[i] != want:
                self._checked[i] = want
                changed = True
        self._refresh_summary()
        if changed:
            self.changed.emit()

    # ── internals ────────────────────────────────────────────────────────
    def _set_checked(self, index: int, on: bool) -> None:
        if 0 <= index < len(self._checked) and self._checked[index] != bool(on):
            self._checked[index] = bool(on)
            self._refresh_summary()
            self.changed.emit()

    def _set_all(self, value: bool) -> None:
        changed = False
        for i in range(len(self._checked)):
            if self._checked[i] != value:
                self._checked[i] = value
                changed = True
        self._refresh_summary()
        if changed:
            self.changed.emit()

    def _refresh_summary(self) -> None:
        total = len(self._labels)
        n = sum(self._checked)
        if n == 0:
            text = self._empty_text
        elif n == total:
            text = f"All {total} {self._noun}"
        else:
            text = f"{n} of {total} {self._noun}"
        self._button.setText(f"{text}  ▾")  # ▾ menu-affordance caret
        if self.checkedLabels():
            self._button.setToolTip(", ".join(self.checkedLabels()))
        else:
            self._button.setToolTip("")

    def _open_menu(self) -> None:
        menu = QMenu(self._button)
        for i, label in enumerate(self._labels):
            cb = QCheckBox(label, menu)
            cb.setChecked(self._checked[i])
            cb.toggled.connect(lambda checked, idx=i: self._set_checked(idx, checked))
            wa = QWidgetAction(menu)
            wa.setDefaultWidget(cb)
            menu.addAction(wa)
        menu.addSeparator()
        all_action = QAction("All", menu)
        all_action.triggered.connect(lambda _=False: self._set_all(True))
        menu.addAction(all_action)
        none_action = QAction("None", menu)
        none_action.triggered.connect(lambda _=False: self._set_all(False))
        menu.addAction(none_action)
        btn = self._button
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
