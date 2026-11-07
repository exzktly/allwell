"""DelayedToolTip — a tooltip that waits before it shows, for a pointer that rests rather than passes.

A canvas that shows a card for whatever is under the pointer, on every move, flashes cards across the screen as the
pointer crosses a dense picture. This holds each card until the pointer has rested on the same thing for *delay_ms*
(one second by default): :meth:`point` names what is under the pointer and the card it would show, :meth:`clear`
says nothing is. Moving within the same thing neither restarts the wait nor re-shows the card; moving onto something
else starts the wait over; leaving hides the card at once — :meth:`clear`, or the pointer leaving the widget (or the
widget hiding), which the tooltip watches itself: a canvas whose pointer has left it reports no further move, so a
card still waiting would otherwise show wherever the pointer had gone.

Qt only, no application knowledge.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QToolTip

#: How long the pointer rests on a thing before its card shows, in milliseconds.
DELAY_MS = 1000


class DelayedToolTip(QObject):
    def __init__(self, widget, delay_ms: int = DELAY_MS) -> None:
        super().__init__(widget)
        self._widget = widget
        self._key = None
        self._text = ""
        self._shown = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(int(delay_ms))
        self._timer.timeout.connect(self._show)
        widget.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 — Qt's name
        """The pointer left the widget, or the widget hid: no card for what was under the pointer."""
        if watched is self._widget and event.type() in (QEvent.Leave, QEvent.Hide):
            self.clear()
        return False

    def point(self, key, text: str) -> None:
        """The pointer is over *key*, whose card is *text*: show it once the pointer has rested there."""
        if key == self._key:
            self._text = text                            # the same thing: its card, kept current, on the same clock
            return
        self._key, self._text, self._shown = key, text, False
        QToolTip.hideText()
        self._timer.start()

    def clear(self) -> None:
        """The pointer is over nothing with a card: hide the card and forget the wait."""
        self._timer.stop()
        if self._key is not None or self._shown:
            QToolTip.hideText()
        self._key, self._text, self._shown = None, "", False

    def setDelay(self, delay_ms: int) -> None:  # noqa: N802 — Qt's spelling
        """Wait *delay_ms* before a card shows (a test drives it at 0)."""
        self._timer.setInterval(int(delay_ms))

    def pending(self) -> bool:
        """Whether a card is waiting to show."""
        return self._timer.isActive()

    def _show(self) -> None:
        if self._key is not None and self._text:
            QToolTip.showText(QCursor.pos(), self._text, self._widget)
            self._shown = True
