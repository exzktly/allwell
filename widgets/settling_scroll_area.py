"""A resizable scroll area whose content may be height-for-width without the two sizing each other for ever.

``QScrollArea`` filters its content widget's resize events and answers each by re-sizing the content and
deciding whether the vertical scroll bar is needed. When the content is **height-for-width** — a well plate
locks its height to its width — the two decisions feed each other: a taller content brings the bar in, the bar
narrows the viewport, the narrower content is shorter, the bar goes away, the wider content is taller again.
Qt makes both moves synchronously, inside the resize event that caused them, so at a size where neither state
is stable the recursion never returns and the process dies of a stack overflow. Review's rail did this on the
way to the Plotting page once the plate's labels moved to 12 px.

:class:`SettlingScrollArea` answers a content resize once: a resize that arrives while it is still answering
one is the consequence of its own answer, and is left alone. The content then keeps the size of the first
answer — at worst a few pixels taller than the narrower viewport needs, so the bar stays — and the next real
resize is answered afresh. It knows nothing of All-Well; ``tests/test_settling_scroll_area.py`` holds it.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QScrollArea


class SettlingScrollArea(QScrollArea):
    """A ``QScrollArea`` that answers each content resize once, never from inside its own answer."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._answering = False

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:  # noqa: N802 — Qt naming
        if ev.type() != QEvent.Resize or obj is not self.widget():
            return super().eventFilter(obj, ev)
        if self._answering:
            return False
        self._answering = True
        try:
            return super().eventFilter(obj, ev)
        finally:
            self._answering = False
