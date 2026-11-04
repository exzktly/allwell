"""A number a spin box will not take is refused out loud, never silently put back.

Reported twice on the rim focus boxes (the Nanowells page's and the Cell Gating panel's): the threshold "snaps back"
to where it started whenever the user clicks elsewhere. Both boxes take 30 to 95 (``review.domain.
nanowell_rim_focus.MIN_FRACTION`` / ``MAX_FRACTION``), and Qt's answer to any other number on Enter or focus-out is to
put the last value back without a word. ``widgets.spin_box`` now emits ``refused(text)`` exactly then (from Qt's own
``fixup``), and the one-second pause refuses a number no further digit can rescue while leaving a partial one alone.
"""

from __future__ import annotations

import pytest

from widgets.spin_box import TYPING_PAUSE_MS, DoubleSpinBox, SpinBox


@pytest.fixture
def box(qtbot):
    b = SpinBox()
    qtbot.addWidget(b)
    b.setRange(30, 95)
    b.setValue(70)
    refused: list = []
    b.refused.connect(refused.append)
    return b, refused


def _type(qtbot, b, text):
    b.lineEdit().selectAll()
    qtbot.keyClicks(b.lineEdit(), text)


def test_enter_or_leaving_the_box_on_a_number_it_does_not_take_says_so(qtbot, box):
    b, refused = box
    _type(qtbot, b, "20")
    b.interpretText()                    # what Enter and focus-out do
    assert refused == ["20"]
    assert b.value() == 70 and b.lineEdit().text() == "70"


def test_a_number_no_digit_can_rescue_is_refused_after_the_pause(qtbot, box):
    b, refused = box
    _type(qtbot, b, "20")
    qtbot.waitUntil(lambda: refused == ["20"], timeout=TYPING_PAUSE_MS * 3)
    assert b.value() == 70


def test_a_partial_number_is_left_as_typed_through_the_pause(qtbot, box):
    b, refused = box
    seen: list = []
    b.valueChanged.connect(seen.append)
    _type(qtbot, b, "4")                 # on the way to 45
    qtbot.wait(TYPING_PAUSE_MS * 2)
    assert refused == [] and seen == [] and b.lineEdit().text() == "4"
    qtbot.keyClicks(b.lineEdit(), "5")
    qtbot.waitUntil(lambda: seen == [45], timeout=TYPING_PAUSE_MS * 3)
    assert refused == []


def test_a_number_it_takes_and_a_value_set_from_state_refuse_nothing(qtbot, box):
    b, refused = box
    _type(qtbot, b, "55")
    b.interpretText()
    b.setValue(80)
    assert refused == [] and b.value() == 80


def test_the_double_box_says_so_too(qtbot):
    b = DoubleSpinBox()
    qtbot.addWidget(b)
    b.setRange(0.3, 0.95)
    b.setValue(0.7)
    refused: list = []
    b.refused.connect(refused.append)
    _type(qtbot, b, "0.1")
    b.interpretText()
    assert refused == ["0.1"] and b.value() == 0.7
    assert b.range_text() == "0.30–0.95"


@pytest.mark.parametrize("typed", ["96", "100"])
def test_a_number_over_the_maximum_is_kept_as_typed_and_refused(qtbot, box, typed):
    """Qt drops the keystroke that takes a number over the maximum, so 96 became 9 — committed, and never typed."""
    b, refused = box
    seen: list = []
    b.valueChanged.connect(seen.append)
    _type(qtbot, b, typed)
    assert b.lineEdit().text() == typed
    b.interpretText()
    assert refused == [typed] and seen == [] and b.value() == 70


def test_a_lower_rim_focus_is_taken(qtbot):
    """The rule's floor is 1%: a low fraction only flags the worst-blurred rims (``nanowell_rim_focus``)."""
    from review.domain import nanowell_rim_focus as rfc

    assert rfc.clamp_fraction(0.1) == 0.1 and rfc.clamp_fraction(0.01) == 0.01
    assert rfc.clamp_fraction(0.0) is None and rfc.clamp_fraction(0.96) is None
