"""When did the person at the computer last use the app?

Unattended work sometimes wants to put something in front of the user — a
queued **Open in Review** step showing a finished analysis — and doing it
while they are typing in another pane takes the window out from under them.
:class:`UserActivity` is an application-wide event filter that notes the time
of the last key press, mouse press or wheel turn in any of the app's windows,
so such work can ask :func:`is_idle` first.

"Idle" also covers the app not being the active application at all: someone
working in another program is not using this one.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication

#: How long without input counts as idle, in seconds.
IDLE_AFTER_SECONDS = 120.0

_INPUT_EVENTS = frozenset({
    QEvent.KeyPress, QEvent.MouseButtonPress, QEvent.Wheel, QEvent.TouchBegin,
})

_INSTANCE: "UserActivity | None" = None


class UserActivity(QObject):
    """Records the monotonic time of the last user input to the application."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.last_input = time.monotonic()

    def eventFilter(self, _obj, event) -> bool:  # Qt naming
        if event.type() in _INPUT_EVENTS:
            self.last_input = time.monotonic()
        return False

    def idle_for(self) -> float:
        return time.monotonic() - self.last_input


def install(app: QApplication | None = None) -> UserActivity | None:
    """Install the filter on the application once; returns it."""
    global _INSTANCE
    app = app or QApplication.instance()
    if app is None:
        return None
    if _INSTANCE is None:
        _INSTANCE = UserActivity(app)
        app.installEventFilter(_INSTANCE)
    return _INSTANCE


def review_hand_off(*, when_idle: bool, idle: bool, on_review: bool) -> str:
    """What a hand-off of a finished result to Review should do.

    ``"switched"`` — load it and move the window to Review: asked by a person
    (*when_idle* false), or nobody is using the app. ``"loaded"`` — someone is
    working in another pane: load it behind them and leave the window. ``"held"``
    — someone is reading Review itself: loading would replace what they are
    looking at, so nothing is touched.
    """
    if not when_idle or idle:
        return "switched"
    return "held" if on_review else "loaded"


def is_idle(threshold: float = IDLE_AFTER_SECONDS) -> bool:
    """Whether nobody is using the app: no input for *threshold* seconds, or
    another application is in front. Without the filter installed, nothing is
    known, and the answer is ``False`` — the cautious one."""
    app = QApplication.instance()
    if app is not None and QApplication.activeWindow() is None:
        return True
    return _INSTANCE is not None and _INSTANCE.idle_for() >= threshold
