"""Small flat plate-map button used in the batch-export group editor."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from ui.theme import get_color


class _WellGridButton(QPushButton):
    """Small flat plate-map button used inside batch-export group editor."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setFixedSize(28, 22)
        self.setFlat(True)
        self.setCursor(Qt.PointingHandCursor)
        self._base_color = ""
        self._active = False

    def set_colors(self, bg: str, fg: str, active: bool = False, enabled: bool = True) -> None:
        self.setEnabled(enabled)
        self._base_color = bg
        self._active = active
        accent = get_color("ACCENT")
        border_color = get_color("BORDER")
        border = f"2px solid {accent}" if active else f"1px solid {border_color}"
        self.setStyleSheet(
            f"QPushButton {{ background: {bg}; color: {fg}; border: {border}; padding: 0px; }}"
        )
