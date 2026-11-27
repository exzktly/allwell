"""A panel docked in a splitter beside the content it serves.

An overlay — a drawer, a sheet — covers what it adjusts. Where there is room,
a panel that sits *in* the layout is better: the content is laid out beside it
rather than under it, and every change shows as it is made.
:class:`DockedPanel` is that panel. Opened, it joins a ``QSplitter`` at its
``side`` (``"left"``: the first pane; ``"right"``: the last); closed, it leaves
the splitter, which goes back to holding what it held, at the indices it held
them.

Two hosts use it. Phone layout docks a page's Controls at the left of an
unfolded foldable, where the screen is wide enough for a column beside the
plot. The desk docks the inspector at the right of Review's pages.

It answers the part of the ``widgets.drawer.Drawer`` interface a host of
either kind uses — ``open`` / ``close`` / ``closeNow`` / ``isOpen`` /
``refit``, the ``opened`` / ``closed`` signals, ``setContentWidget`` — so a
shell lends widgets to it exactly as it lends them to an overlay, and takes
them back on ``closed``, which is emitted while the content is still inside.

The width is ``width_hint()`` pixels. Fixed by default; with
``resizable=True`` the splitter's handle moves it between ``min_width`` and
``max_width`` and ``widthChanged`` reports where the user left it, so a host
can remember it.

Qt-only, no All-Well knowledge.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from ui import theme

_QWIDGETSIZE_MAX = 16_777_215


class DockedPanel(QWidget):
    """A column docked at one *side* of *splitter*, *width_hint()* pixels wide."""

    opened = Signal()
    closed = Signal()
    #: The user dragged the splitter's handle: the panel's new width.
    widthChanged = Signal(int)

    def __init__(self, splitter: QSplitter, width_hint: Callable[[], int], *,
                 side: str = "left", resizable: bool = False,
                 min_width: int = 240, max_width: int = 640) -> None:
        super().__init__()
        self.setObjectName("DockedPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._splitter = splitter
        self._width_hint = width_hint
        self._side = side if side in ("left", "right") else "left"
        self._resizable = bool(resizable)
        self._limits = (int(min_width), int(max_width))
        self._open = False
        self._content: QWidget | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        c = theme.Colors
        edge = "right" if self._side == "left" else "left"
        self.setStyleSheet(f"#DockedPanel {{ background-color: {c.rail};"
                           f" border-{edge}: 1px solid {c.border_subtle}; }}")
        splitter.splitterMoved.connect(self._on_splitter_moved)
        self.hide()

    def side(self) -> str:
        return self._side

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
        index = 0 if self._side == "left" else self._splitter.count()
        self._splitter.insertWidget(index, self)
        at = self._splitter.indexOf(self)
        self._splitter.setCollapsible(at, False)
        self._splitter.setStretchFactor(at, 0)
        self.refit()
        self.show()
        self.opened.emit()

    def _width(self) -> int:
        want = int(self._width_hint())                     # a host's hint is never below 1
        if self._resizable:
            lo, hi = self._limits
            want = max(lo, min(hi, want))
        return want

    def refit(self) -> None:
        """Take the width the hint gives now — the text size or the screen
        changed what it should be."""
        if not self._open:
            return
        want = self._width()
        if not self._resizable:
            self.setFixedWidth(want)
            return
        lo, hi = self._limits
        self.setMinimumWidth(lo)
        self.setMaximumWidth(hi)
        sizes = self._splitter.sizes()
        at = self._splitter.indexOf(self)
        if not (0 <= at < len(sizes)):
            return
        # The width comes out of the widest other pane (the page), never the
        # rail: a splitter asked only to grow one pane shrinks its neighbours
        # in proportion.
        others = [i for i in range(len(sizes)) if i != at]
        if others:
            widest = max(others, key=lambda i: sizes[i])
            sizes[widest] = max(1, sizes[widest] - (want - sizes[at]))
        sizes[at] = want
        self._splitter.setSizes(sizes)

    def _on_splitter_moved(self, *_a) -> None:
        if self._open and self._resizable:
            self.widthChanged.emit(self.width())

    def close(self) -> None:
        if not self._open:
            return
        self._open = False
        # Told first, while the content is still inside: a host gives back
        # what it lent on ``closed``.
        self.closed.emit()
        self.hide()
        self.setParent(None)

    def closeNow(self) -> None:
        self.close()
