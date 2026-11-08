"""FocusChip — a status-bar pill naming one item, with a way back to it and a ✕.

``●  B03 · F001 · 24h · #187  ✕``. The text is a flat button (``activated``);
the ✕ clears (``cleared``). Hidden while it names nothing. Pure display widget:
it knows nothing about cells or the app — the caller supplies the text and
decides what the two signals do.

API
---
* ``FocusChip(parent=None, *, tooltip="")``
* ``setItem(text | None)`` — show *text*, or hide for ``None``
* ``text()``
* signals ``activated()``, ``cleared()``
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from ui import theme


class FocusChip(QFrame):
    activated = Signal()
    cleared = Signal()

    def __init__(self, parent: QWidget | None = None, *, tooltip: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("FocusChip")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.Spacing.sm, 0, theme.Spacing.xs, 0)
        lay.setSpacing(theme.Spacing.xs)
        self._dot = QLabel("●", self)
        self._dot.setObjectName("FocusChipDot")
        lay.addWidget(self._dot)
        self._button = QPushButton("", self)
        self._button.setObjectName("FocusChipText")
        self._button.setFlat(True)
        self._button.setCursor(Qt.PointingHandCursor)
        if tooltip:
            self._button.setToolTip(tooltip)
        self._button.clicked.connect(lambda _=False: self.activated.emit())
        lay.addWidget(self._button)
        self._close = QPushButton("✕", self)
        self._close.setObjectName("FocusChipClear")
        self._close.setFlat(True)
        self._close.setToolTip("Stop focusing this cell")
        self._close.clicked.connect(lambda _=False: self.cleared.emit())
        lay.addWidget(self._close)
        c = theme.Colors
        self.setStyleSheet(
            f"#FocusChip {{ background: {c.panel_elevated}; border: 1px solid {c.border_subtle}; "
            f"border-radius: {theme.Radii.pill}px; }}"
            f"#FocusChipDot {{ color: {c.focused_cell}; font-size: {theme.Typography.caption_size}px; }}"
            f"#FocusChipText, #FocusChipClear {{ color: {c.text_primary}; border: none; "
            f"background: transparent; padding: 0 2px; font-size: {theme.Typography.caption_size}px; }}"
            f"#FocusChipText:hover {{ text-decoration: underline; }}"
            f"#FocusChipClear {{ color: {c.text_muted}; }}")
        self.setVisible(False)

    def setItem(self, text: str | None) -> None:
        self._button.setText(text or "")
        self.setVisible(bool(text))

    def text(self) -> str:
        return self._button.text()
