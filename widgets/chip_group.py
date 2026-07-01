"""ChipGroup — a row of compact pill chips, single- or multi-select.

The smaller cousin of ``SegmentedControl``: rounded "pill" chips that sit inline
with text. Use single-select for things like a per-plot channel chip, or
multi-select for trace toggles / filters.

API
---
* ``addChip(text, data=None) -> int``
* single-select: ``setCurrentIndex(i)`` / ``currentIndex()`` / ``currentData()``,
  ``currentChanged(int)`` signal.
* multi-select (construct with ``exclusive=False``): ``setChecked(i, on)`` /
  ``isChecked(i)`` / ``checkedIndices()``, ``chipToggled(int, bool)`` signal.

Token-styled; chip padding/radius are spacing/font relative (DPI-aware).
"""

from __future__ import annotations

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt, Signal  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QButtonGroup, QHBoxLayout, QSizePolicy, QToolButton, QWidget,
)

import theme  # noqa: E402


class ChipGroup(QWidget):
    currentChanged = Signal(int)        # single-select
    chipToggled = Signal(int, bool)     # multi-select

    def __init__(self, parent: QWidget | None = None, *, exclusive: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("ChipGroup")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._exclusive = bool(exclusive)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(theme.Spacing.sm)
        self._layout.addStretch(1)

        self._group = QButtonGroup(self)
        self._group.setExclusive(self._exclusive)
        self._buttons: list[QToolButton] = []
        self._data: list[object] = []
        self._current = -1

        self._group.idToggled.connect(self._on_id_toggled)
        self.setStyleSheet(self._build_qss())

    # ── API ──────────────────────────────────────────────────────────────
    def addChip(self, text: str, data: object | None = None) -> int:
        idx = len(self._buttons)
        btn = QToolButton(self)
        btn.setObjectName("Chip")
        btn.setText(text)
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFocusPolicy(Qt.NoFocus)
        # Insert before the trailing stretch.
        self._layout.insertWidget(self._layout.count() - 1, btn)
        self._group.addButton(btn, idx)
        self._buttons.append(btn)
        self._data.append(data)
        if self._exclusive and self._current < 0:
            self.setCurrentIndex(0)
        return idx

    def count(self) -> int:
        return len(self._buttons)

    # single-select
    def currentIndex(self) -> int:
        return self._current

    def setCurrentIndex(self, index: int) -> None:
        if not self._exclusive or not (0 <= index < len(self._buttons)):
            return
        self._buttons[index].setChecked(True)

    def currentData(self) -> object | None:
        if 0 <= self._current < len(self._data):
            return self._data[self._current]
        return None

    def setCurrentByData(self, value) -> None:
        """(exclusive mode) select the chip whose ``data`` equals *value*."""
        for i, d in enumerate(self._data):
            if d == value:
                self.setCurrentIndex(i)
                return

    # multi-select
    def isChecked(self, index: int) -> bool:
        return 0 <= index < len(self._buttons) and self._buttons[index].isChecked()

    def setChecked(self, index: int, on: bool) -> None:
        if 0 <= index < len(self._buttons):
            self._buttons[index].setChecked(bool(on))

    def checkedIndices(self) -> list[int]:
        return [i for i, b in enumerate(self._buttons) if b.isChecked()]

    def checkedData(self) -> list:
        """(multi-select mode) the ``data`` of every checked chip, in chip order."""
        return [self._data[i] for i in self.checkedIndices()]

    def setCheckedData(self, values) -> None:
        """(multi-select mode) check exactly the chips whose ``data`` is in *values*."""
        vals = list(values or [])
        for i, d in enumerate(self._data):
            self.setChecked(i, d in vals)

    def chipText(self, index: int) -> str:
        return self._buttons[index].text() if 0 <= index < len(self._buttons) else ""

    def bindingAdapter(self):
        """``(getter, setter, change_signal)`` for binding-driven panels —
        exclusive mode binds the current chip ``data``; multi-select binds the
        list of checked ``data``."""
        if self._exclusive:
            return (self.currentData, self.setCurrentByData, self.currentChanged)
        return (self.checkedData, self.setCheckedData, self.chipToggled)

    # ── internals ────────────────────────────────────────────────────────
    def _on_id_toggled(self, idx: int, checked: bool) -> None:
        if self._exclusive:
            if checked and idx != self._current:
                self._current = idx
                self.currentChanged.emit(idx)
        else:
            self.chipToggled.emit(idx, checked)

    def _build_qss(self) -> str:
        c, t, r = theme.Colors, theme.Typography, theme.Radii
        return f"""
        #ChipGroup {{ background: transparent; }}
        #ChipGroup QToolButton#Chip {{
            background-color: {c.panel_elevated};
            border: 1px solid {c.border};
            border-radius: {r.pill}px;
            color: {c.text_secondary};
            padding: 3px 10px;
            font-size: {t.small_size}px;
            font-weight: {t.medium};
        }}
        #ChipGroup QToolButton#Chip:hover {{
            background-color: {c.hover};
            border-color: {c.border_strong};
            color: {c.text_primary};
        }}
        #ChipGroup QToolButton#Chip:checked {{
            background-color: {c.accent_dim};
            border-color: {c.accent};
            color: {c.text_primary};
        }}
        #ChipGroup QToolButton#Chip:disabled {{ color: {c.text_faint}; }}
        """


# ── standalone visual test ──────────────────────────────────────────────────
if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget as _QW

    app = QApplication.instance() or QApplication(_sys.argv)
    app.setStyleSheet(theme.qss())

    root = _QW()
    root.setWindowTitle("ChipGroup — demo")
    pad = theme.Spacing.lg
    lay = QVBoxLayout(root)
    lay.setContentsMargins(pad, pad, pad, pad)
    lay.setSpacing(theme.Spacing.md)

    title = QLabel("ChipGroup")
    title.setObjectName("Title")
    lay.addWidget(title)

    lay.addWidget(QLabel("Single-select (channel):"))
    chan = ChipGroup(exclusive=True)
    for name in ("DAPI", "GFP", "RFP", "Cy5"):
        chan.addChip(name, data=name.lower())
    chan.setCurrentIndex(1)
    lay.addWidget(chan)

    lay.addWidget(QLabel("Multi-select (overlays):"))
    multi = ChipGroup(exclusive=False)
    for name in ("Grid", "Threshold", "Legend", "Error band"):
        multi.addChip(name)
    multi.setChecked(0, True)
    multi.setChecked(1, True)
    lay.addWidget(multi)

    echo = QLabel("(interact above)")
    echo.setObjectName("Secondary")
    lay.addWidget(echo)
    chan.currentChanged.connect(lambda i: echo.setText(f"channel → {chan.chipText(i)} ({chan.currentData()})"))
    multi.chipToggled.connect(lambda i, on: echo.setText(f"overlays → {[multi.chipText(j) for j in multi.checkedIndices()]}"))
    lay.addStretch(1)

    root.resize(420, 280)
    root.show()
    _sys.exit(app.exec())
