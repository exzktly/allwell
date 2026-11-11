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
    from PySide6.QtTest import QTest

    for _ in range(n):
        qapp.processEvents()
    QTest.qWait(wait_ms)
    for _ in range(n):
        qapp.processEvents()


def place(window, rect: QRect, qapp) -> None:
    """Put *window* exactly on *rect*, un-maximized: the offscreen screen is not the fake one, so
    maximizing would size the window to 800×800."""
    window.showNormal()
    window.setGeometry(rect)
    pump(qapp, 30, 300)
