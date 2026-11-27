"""The window in style W1, "Panels" (design language §3.1, row D20).

The header, the mode band, the sidebar and the status bar sit directly on the window's own ground — ``frame``,
darker than the canvas — with no dividing lines between them. The page is a panel in the canvas colour with
rounded corners, set off from the sidebar and from the window's right and bottom edges by an 8-pixel gap.

:func:`qss` styles the regions by the names they are given where they are built (``allwell`` names the header,
``review.core.app_shell_view`` the band, the sidebar, the splitter and the page stack). :func:`install` gives
Review's splitter its gap and clips the page stack to its rounded shape. In phone layout the screen has no width
to give to gaps, so the gaps and the rounding go and come back with it.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRectF
from PySide6.QtGui import QPainterPath, QRegion
from PySide6.QtWidgets import QSplitter, QWidget

from ui import theme

#: The page panel's corner radius and the gap round it (§3.1: 12 and 8 pixels).
PANEL_RADIUS = 12
PANEL_GAP = theme.Spacing.sm


def _rounded(widget: QWidget, radius: int) -> QRegion:
    path = QPainterPath()
    path.addRoundedRect(QRectF(widget.rect()), radius, radius)
    return QRegion(path.toFillPolygon().toPolygon())


class _RoundedMask(QObject):
    """Keeps a widget clipped to a rounded rectangle as it resizes; off while ``enabled`` is False."""

    def __init__(self, widget: QWidget) -> None:
        super().__init__(widget)
        self._widget = widget
        self.enabled = True
        widget.installEventFilter(self)

    def apply(self) -> None:
        w = self._widget
        if self.enabled and w.width() > 0 and w.height() > 0:
            w.setMask(_rounded(w, PANEL_RADIUS))
        else:
            w.clearMask()

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 — Qt naming
        if obj is self._widget and ev.type() == QEvent.Resize:
            self.apply()
        return False


def install(review) -> None:
    """Give *review* (``ReviewApp``) its W1 gaps and its rounded page."""
    from core import phone_layout

    split: QSplitter | None = getattr(review, "_h_pane", None)
    page = getattr(review, "_central_pane_stack", None)
    if split is None or page is None:
        return
    mask = _RoundedMask(page)

    def _arrange(phone: bool) -> None:
        gap = 0 if phone else PANEL_GAP
        split.setHandleWidth(gap if gap else 1)
        split.setContentsMargins(0, 0, gap, gap)
        mask.enabled = not phone
        mask.apply()

    _arrange(phone_layout.is_on())
    phone_layout.follow(page, _arrange)


def qss(c) -> str:
    """The shell's rules: its regions on the frame, their plain containers transparent so the frame shows, and
    the page in the canvas colour."""
    return f"""
QWidget#AppRoot, QWidget#ShellHeader, QWidget#ShellBand, QWidget#ShellRail, QSplitter#ShellSplit,
QWidget#StatusBar {{ background-color: {c.frame}; border: none; }}
QWidget#ShellHeader .QWidget, QWidget#ShellBand .QWidget, QWidget#ShellRail .QWidget,
QWidget#ShellRail QScrollArea, QWidget#StatusBar .QWidget {{ background-color: transparent; }}
QSplitter#ShellSplit::handle {{ background-color: {c.frame}; }}
QStackedWidget#ShellPage {{ background-color: {c.canvas}; }}
"""
