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


class _HeaderLender:
    """Puts the mode switch and the ‹ › history arrows in the header at the desk, and back in Review's band in
    phone layout.

    Window style W1 has one top bar: the application's name, the mode switch beside it, the commands at the
    right; the page tabs are the page panel's first row. Phone layout has no header — it hides it — and builds its
    own bar out of the band (☰, the arrows, the mode switch, the page menu, Controls), so there the band is shown
    and the borrowed widgets go home. Each leaves a hidden placeholder in the band, so it goes back to exactly
    the place phone layout's own insertions were counted from.
    """

    def __init__(self, header_layout, at: int, band: QWidget, widgets: list) -> None:
        self._header_layout, self._at, self._band = header_layout, at, band
        self._widgets = [w for w in widgets if w is not None]
        self._holders: list[tuple[QWidget, QWidget]] = []

    def arrange(self, phone: bool) -> None:
        band_layout = self._band.layout()
        if phone:
            for w, holder in self._holders:
                index = band_layout.indexOf(holder)
                band_layout.insertWidget(index, w)
                band_layout.removeWidget(holder)
                holder.deleteLater()
                w.show()
            self._holders = []
            self._band.show()
            return
        if not self._holders:
            for i, w in enumerate(self._widgets):
                holder = QWidget(self._band)
                holder.hide()
                index = band_layout.indexOf(w)
                band_layout.insertWidget(index, holder)
                self._header_layout.insertWidget(self._at + i, w)
                self._holders.append((w, holder))
        self._band.hide()


def lend_to_header(header_layout, at: int, band: QWidget, widgets: list) -> None:
    """At the desk, move *widgets* (the mode switch, the history arrows) from *band* into *header_layout* at
    index *at*, and hide the band; in phone layout put them back and show it. Follows phone layout for as long
    as *band* lives."""
    from core import phone_layout

    lender = _HeaderLender(header_layout, at, band, widgets)
    lender.arrange(phone_layout.is_on())
    phone_layout.follow(band, lender.arrange)


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
