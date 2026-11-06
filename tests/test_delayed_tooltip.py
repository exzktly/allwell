"""``widgets.delayed_tooltip.DelayedToolTip``: a card shows only once the pointer has rested on one thing.

Asked for: a one-second delay before the nanowell hover card. The tests drive the real timer at its real second and
spy on ``QToolTip``, the boundary the card crosses to reach the screen. Every claim is about *when* a card showed,
read from timestamps rather than from how long a ``qtbot.wait`` slept — on a busy runner a wait overruns, and a test
that assumes it did not reports the timer wrong when it was right.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QToolTip, QWidget  # noqa: E402

from widgets.delayed_tooltip import DELAY_MS, DelayedToolTip  # noqa: E402


@pytest.fixture
def spy(monkeypatch):
    calls = []
    monkeypatch.setattr(QToolTip, "showText", lambda _pos, text, *_a: calls.append(("show", text, time.monotonic())))
    monkeypatch.setattr(QToolTip, "hideText", lambda: calls.append(("hide", None, time.monotonic())))
    return calls


def _shown(calls):
    return [(text, at) for kind, text, at in calls if kind == "show"]


def test_the_card_waits_a_second_of_rest_and_a_move_within_the_same_thing_does_not_restart_it(qtbot, spy):
    widget = QWidget()
    qtbot.addWidget(widget)
    tip = DelayedToolTip(widget)
    assert DELAY_MS == 1000
    first = time.monotonic()
    tip.point("a", "card a")
    assert _shown(spy) == []
    qtbot.wait(400)
    again = time.monotonic()
    tip.point("a", "card a")                                       # moving within the same thing
    qtbot.waitUntil(lambda: bool(_shown(spy)), timeout=5000)
    (text, at), = _shown(spy)
    assert text == "card a"
    # Qt's default (coarse) timers may fire up to 5% early, so "a second" is at least 0.95 s less a hair.
    assert at - first >= 0.9, at - first                           # it waited the second
    if again < first + 0.85:                                       # the second move came before the card was due:
        assert at - again < 0.9, (at - first, again - first)       # the clock ran on rather than starting over


def test_moving_onto_another_thing_starts_the_wait_over_and_leaving_hides_at_once(qtbot, spy):
    widget = QWidget()
    qtbot.addWidget(widget)
    tip = DelayedToolTip(widget, delay_ms=300)
    tip.point("a", "card a")
    qtbot.wait(100)
    moved = time.monotonic()
    tip.point("b", "card b")                                       # another nanowell
    qtbot.waitUntil(lambda: any(text == "card b" for text, _at in _shown(spy)), timeout=5000)
    at_b = next(at for text, at in _shown(spy) if text == "card b")
    assert at_b - moved >= 0.27, at_b - moved                      # b waited its own full delay (less 5%)
    spy.clear()
    tip.clear()
    assert [kind for kind, *_ in spy] == ["hide"] and not tip.pending()
    tip.point("c", "card c")
    tip.clear()                                                    # left before the card was due
    qtbot.wait(400)
    assert _shown(spy) == []
