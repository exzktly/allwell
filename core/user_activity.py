"""When did the person at the computer last use the app?

Unattended work sometimes wants to put something in front of the user — a
recipe run opening its finished analysis in Review — and doing it while they
are working in another pane takes the window out from under them.
:class:`UserActivity` notes when the person last did something, so such work
can ask :func:`is_idle` first.

It **samples** rather than intercepts: every few seconds it reads the mouse
position, and a position that moved is activity. An application-wide event
filter would see every key press as well, but a Python ``eventFilter`` on the
``QApplication`` runs for every event of every widget, and installing one
took the whole app down with a native crash under the page-tour test — a
cost and a risk out of all proportion to the question. Keyboard-only work
with a still mouse can therefore read as idle after the threshold; the cost
of that is the window moving to Review once, which is what an idle answer
does anyway.

"Idle" also covers the app not being the active application: someone working
in another program, or at a locked screen, is not using this one.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtGui import QCursor, QGuiApplication

#: How long without activity counts as idle, in seconds.
IDLE_AFTER_SECONDS = 120.0
#: How often the mouse position is sampled, in milliseconds.
SAMPLE_MS = 5000

_INSTANCE: "UserActivity | None" = None


class UserActivity(QObject):
    """Records the monotonic time the mouse was last seen to move."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.last_input = time.monotonic()
        self._last_pos = QCursor.pos()
        self._timer = QTimer(self)
        self._timer.setInterval(SAMPLE_MS)
        self._timer.timeout.connect(self.sample)
        self._timer.start()

    def sample(self) -> None:
        pos = QCursor.pos()
        if pos != self._last_pos:
            self._last_pos = pos
            self.last_input = time.monotonic()

    def idle_for(self) -> float:
        return time.monotonic() - self.last_input


def install(app: QObject | None = None) -> UserActivity | None:
    """Start sampling once, owned by the application; returns the sampler."""
    global _INSTANCE
    app = app or QGuiApplication.instance()
    if app is None:
        return None
    if _INSTANCE is None:
        _INSTANCE = UserActivity(app)
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
    """Whether nobody is using the app: it is not the active application, or
    the mouse has not moved for *threshold* seconds. Before sampling has
    started, only the first is known."""
    if QGuiApplication.instance() is None:
        return False
    if QGuiApplication.applicationState() != Qt.ApplicationActive:
        return True
    return _INSTANCE is not None and _INSTANCE.idle_for() >= threshold
