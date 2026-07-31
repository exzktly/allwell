"""ModeBar — the app's top-level Review / Analyze / Datasets selector.

These three are not tabs of one screen. They are three different applications
sharing a window, and switching between them replaces the entire interface. The
control has to say so at rest, not only once a mode is active.

That is why **every segment is a filled control at all times**: the active one
accent-filled, the inactive ones raised on ``panel_elevated`` with full-strength
primary text and full-opacity icons. Nothing here is muted. Muting is how the
rail's section list and the centre pane's tab bar express "not currently
selected"; borrowing it here would demote a switch between applications to a
third tab strip, which is exactly how an earlier revision of this control read.

The segments are flush — no gutters, no corner radius — so the bar is one solid
band rather than three floating chips, and it is sized to the height of the
centre pane's tab bar so the two share a bottom edge.

No All-Well coupling — a plain reusable widget.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

import theme
from widgets import icons as _icons

_ICON_PX = 14


class _ModeSegment(QWidget):
    """One mode. Filled whether or not it is the current one."""

    clicked = Signal()

    def __init__(self, text: str, icon: str | None, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self._text = text
        self._icon = icon
        self._active = False

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.Spacing.xs + 2)
        row.addStretch(1)

        self._icon_lbl = QLabel(self)
        self._icon_lbl.setVisible(bool(icon))
        row.addWidget(self._icon_lbl)

        self._text_lbl = QLabel(text, self)
        row.addWidget(self._text_lbl)
        row.addStretch(1)

        self.setActive(False)

    def setActive(self, active: bool) -> None:
        self._active = bool(active)
        c, t = theme.Colors, theme.Typography
        fill = c.accent if self._active else c.panel_elevated
        fg = c.accent_fg if self._active else c.text_primary
        self.setStyleSheet(f"background-color: {fill};")
        # Full weight in both states: an inactive mode is still a destination.
        self._text_lbl.setStyleSheet(
            f"color: {fg}; font-size: {t.caption_size}px; font-weight: {t.semibold};"
        )
        # The glyph is repainted in the segment's own foreground colour rather
        # than handed in as a fixed QIcon. A QIcon's Normal pixmap is drawn in
        # ``text_secondary``, which on the accent-filled active segment is a
        # dark glyph on a saturated blue — the icon all but disappears exactly
        # where the control is meant to read loudest.
        if self._icon:
            self._icon_lbl.setPixmap(
                _icons.make_pixmap(self._icon, fg, _ICON_PX,
                                   self.devicePixelRatioF())
            )

    def isActive(self) -> bool:
        return self._active

    def text(self) -> str:
        return self._text

    def mousePressEvent(self, event) -> None:  # Qt naming
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ModeBar(QWidget):
    """Flush, equal-width segments across the full width of its host."""

    currentChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None, *, height: int | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ModeBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setStyleSheet(
            f"#ModeBar {{ background-color: {theme.Colors.panel_elevated};"
            f" border-bottom: 1px solid {theme.Colors.border}; }}"
        )
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(0)  # flush: one band, not three chips
        self._segments: list[_ModeSegment] = []
        self._data: list[object] = []
        self._current = -1
        if height is not None:
            self.setFixedHeight(height)

    def addSegment(self, text: str, icon: str | None = None,
                   data: object | None = None) -> int:
        """Add a mode. *icon* is a ``widgets.icons`` glyph name, not a QIcon —
        the segment recolours it to match whichever state it is in."""
        idx = len(self._segments)
        seg = _ModeSegment(text, icon, self)
        seg.clicked.connect(lambda i=idx: self.setCurrentIndex(i))
        self._row.addWidget(seg, 1)
        self._segments.append(seg)
        self._data.append(data if data is not None else text)
        if self._current < 0:
            self.setCurrentIndex(0)
        return idx

    def setCurrentIndex(self, index: int) -> None:
        if not (0 <= index < len(self._segments)) or index == self._current:
            return
        self._current = index
        for i, seg in enumerate(self._segments):
            seg.setActive(i == index)
        self.currentChanged.emit(index)

    def currentIndex(self) -> int:
        return self._current

    def currentData(self) -> object | None:
        if 0 <= self._current < len(self._data):
            return self._data[self._current]
        return None

    def setCurrentData(self, value: object) -> None:
        if value in self._data:
            self.setCurrentIndex(self._data.index(value))

    def count(self) -> int:
        return len(self._segments)

    def segmentText(self, index: int) -> str:
        return self._segments[index].text()

    def isSegmentActive(self, index: int) -> bool:
        return self._segments[index].isActive()
