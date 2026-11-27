"""Phone layout on an unfolded foldable: the page's ⚙ Controls docked on the left.

On a phone the Controls sheet is an overlay — a bottom sheet upright, a
right-hand panel sideways — opened with **⚙ Controls** and closed again,
because a phone has no width to keep a column of controls beside a plot. An
unfolded iPhone Duo's inner screen is 626 points across upright and 890
sideways (``core.phone_layout.DUO_INNER_PT``), which is room for both: the
page's controls in a ``DOCKED_SHEET_PT`` column on the left, the plot beside
them, and every change visible as it is made.

:class:`DockedSheet` is that column. It answers the part of the
``widgets.drawer.Drawer`` interface the shell's Controls sheet uses —
``open`` / ``close`` / ``closeNow`` / ``isOpen`` / ``refit``, the ``opened`` /
``closed`` signals, ``setContentWidget`` — so the shell lends a page's control
band to it exactly as it lends one to the overlay (``PhoneShell.open_controls``)
and takes it back on ``closed`` (``_give_band_back``). What differs is where it
sits: not over the page but *in* the layout, as the first pane of the splitter
that holds Review's pages (the splitter the rail sits in at the desk, emptied by
phone layout's rail drawer), so the page is laid out beside it rather than under
it. Opened, it is inserted there at its width; closed, it leaves the splitter, so
the splitter goes back to holding the page alone and the desk's rail returns to
the index it left.

Qt-only and pane-free, like ``ui/phone_window.py``: the shell finds the splitter
through ``PhoneHandles.centre_split``.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget



class DockedSheet(QWidget):
    """A column docked at the left of *splitter*, *width_hint()* pixels wide."""

    opened = Signal()
    closed = Signal()

    def __init__(self, splitter: QSplitter, width_hint: Callable[[], int]) -> None:
        super().__init__()
        self.setObjectName("DockedSheet")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setProperty("phoneSheet", True)
        self._splitter = splitter
        self._width_hint = width_hint
        self._open = False
        self._content: QWidget | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.hide()

    def setContentWidget(self, widget: QWidget) -> None:
        self._content = widget
        self.layout().addWidget(widget)

    def contentWidget(self) -> QWidget | None:
        return self._content

    def isOpen(self) -> bool:
        return self._open

    def open(self) -> None:
        if self._open:
            return
        self._open = True
        self._splitter.insertWidget(0, self)
        self._splitter.setCollapsible(0, False)
        self.refit()
        self.show()
        self.opened.emit()

    def refit(self) -> None:
        """Take the width the hint gives now — the text size or the screen
        changed what a phone point is."""
        if self._open:
            self.setFixedWidth(int(self._width_hint()))     # the shell's hint is never below 1

    def close(self) -> None:
        if not self._open:
            return
        self._open = False
        # Told first, while the content is still inside: the shell gives the
        # page its controls back on ``closed``.
        self.closed.emit()
        self.hide()
        self.setParent(None)

    def closeNow(self) -> None:
        self.close()
