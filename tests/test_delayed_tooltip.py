"""``widgets.delayed_tooltip.DelayedToolTip``: a card shows only once the pointer has rested on one thing.

Asked for: a one-second delay before the nanowell hover card. The tests drive the real timer at its real second and
spy on ``QToolTip``, the boundary the card crosses to reach the screen.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QToolTip, QWidget  # noqa: E402

from widgets.delayed_tooltip import DELAY_MS, DelayedToolTip  # noqa: E402


@pytest.fixture
def spy(monkeypatch):
    calls = []
    monkeypatch.setattr(QToolTip, "showText", lambda _pos, text, *_a: calls.append(("show", text)))
    monkeypatch.setattr(QToolTip, "hideText", lambda: calls.append(("hide", None)))
    return calls


def _shown(calls):
    return [text for kind, text in calls if kind == "show"]


def test_the_card_waits_a_second_of_rest_before_it_shows(qtbot, spy):
    widget = QWidget()
    qtbot.addWidget(widget)
    tip = DelayedToolTip(widget)
    assert DELAY_MS == 1000
    tip.point("a", "card a")
    qtbot.wait(600)
    assert _shown(spy) == [] and tip.pending()                     # not yet: the pointer has rested 0.6 s
    tip.point("a", "card a")                                       # moving within the same thing: the clock runs on
    # Due at 1.0 s from the first point; a wait restarted by the second point would show it at 1.6 s.
    qtbot.waitUntil(lambda: _shown(spy) == ["card a"], timeout=700)


def test_moving_onto_another_thing_starts_the_wait_over_and_leaving_hides_at_once(qtbot, spy):
    widget = QWidget()
    qtbot.addWidget(widget)
    tip = DelayedToolTip(widget, delay_ms=300)
    tip.point("a", "card a")
    qtbot.wait(200)
    tip.point("b", "card b")                                       # another nanowell before a's card showed
    qtbot.wait(200)
    assert _shown(spy) == []                                       # b has rested 0.2 s of its 0.3
    qtbot.waitUntil(lambda: _shown(spy) == ["card b"], timeout=1000)
    spy.clear()
    tip.clear()
    assert spy == [("hide", None)] and not tip.pending()
    tip.point("c", "card c")
    tip.clear()                                                    # left before the card was due
    qtbot.wait(400)
    assert _shown(spy) == []
