"""The screens the full-app tests render on.

Qt's offscreen platform reports one 800×800 screen, which is neither a desktop nor
a phone, so a full-app test run on it says nothing about how a page fits a real
screen. :class:`FakeScreen` makes every ``QScreen`` report the available geometry
of a chosen screen — the three a person has — and :func:`place` puts the window
there. The phone shell reads ``QScreen.availableGeometry`` / ``geometry`` for its
phone point, its orientation and its window size, so patching those two is the
same thing the fast-lane phone tests do (``tests/test_phone_windows.py``).
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QScreen

#: The desktop the app's own code assumes when it has no screen (``phone_shell`` falls
#: back to this), a 1640×980 monitor.
DESK = QRect(0, 0, 1640, 980)
#: A phone held upright after RealVNC resizes the Windows desktop to its shape, and
#: sideways (``tests/test_phone_windows.py`` uses the same two).
PHONE_UPRIGHT = QRect(0, 0, 432, 936)
PHONE_SIDEWAYS = QRect(0, 0, 936, 432)

SCREENS: dict[str, QRect] = {"desk": DESK, "phone-upright": PHONE_UPRIGHT, "phone-sideways": PHONE_SIDEWAYS}


def is_phone(screen: str) -> bool:
    return screen.startswith("phone")


class FakeScreen:
    """``with FakeScreen(PHONE_UPRIGHT) as screen:`` — every ``QScreen`` reports that
    rectangle until the block ends; ``screen.turn(rect)`` changes it (the phone turned).

    Python's view only: Qt's C++ still sees the offscreen 800×800 screen, so what Qt places by
    itself (a ``QMenu`` kept on screen, a combo box's list) is placed against that and cannot be
    measured here. For those, start the process with a real screen of the size —
    ``QT_QPA_PLATFORM="offscreen:configfile=<json>"``, ARCHITECTURE.md §10.4."""

    def __init__(self, rect: QRect) -> None:
        self.rect = QRect(rect)
        self._patch = pytest.MonkeyPatch()

    def __enter__(self) -> FakeScreen:
        self._patch.setattr(QScreen, "availableGeometry", lambda _self: QRect(self.rect))
        self._patch.setattr(QScreen, "geometry", lambda _self: QRect(self.rect))
        return self

    def __exit__(self, *_exc) -> None:
        self._patch.undo()

    def turn(self, rect: QRect) -> None:
        self.rect = QRect(rect)


def pump(qapp, n: int = 20, wait_ms: int = 120) -> None:
    """Run the event loop: queued calls, polish, layout, and a short real wait for timers."""
    for _ in range(n):
        qapp.processEvents()
    wait_events(qapp, wait_ms)
    for _ in range(n):
        qapp.processEvents()


#: The longest a :func:`wait_events` slice sleeps, in seconds, before it runs the event loop again.
_WAIT_SLICE_S = 0.005


def wait_events(qapp, wait_ms: int) -> None:
    """Wait *wait_ms* while running the event loop — timers, queued calls and deferred deletes — as
    ``QTest.qWait`` does, but sleeping in Python between slices so the GIL is free while it waits.

    ``QTest.qWait`` keeps the GIL for the whole wait: a worker thread (the Nanowells page's loads, its
    Rare responders analysis) got about 0.3% of its time beside it, and an analysis that takes 1.9 s took
    69–88 s, so a page measured after a wait was measured before its data could arrive. The app's own
    event loop (``QApplication.exec``) releases the GIL and has no such cost."""
    import time

    from PySide6.QtCore import QCoreApplication, QEvent

    deadline = time.monotonic() + wait_ms / 1000
    while True:
        qapp.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(_WAIT_SLICE_S, remaining))


def place(window, rect: QRect, qapp) -> None:
    """Put *window* exactly on *rect*, un-maximized: the offscreen screen is not the fake one, so
    maximizing would size the window to 800×800."""
    window.showNormal()
    window.setGeometry(rect)
    pump(qapp, 30, 300)
