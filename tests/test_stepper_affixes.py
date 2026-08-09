"""A Stepper with a suffix still refuses text that is not a number.

``Stepper`` gained a prefix/suffix so the ON-cutoff quick-edit could show its
unit inside the box the user types into (" spots"), which is what stops a bare
``-1`` reading as a sentinel rather than "no cut". The suffix is drawn as part
of the line-edit's text, and the ``QDoubleValidator`` installed on that edit
judges the *whole* string — so from the moment a suffix was set, every value the
box displayed was ``Invalid``.

That is worse than a mislabelled state. ``QLineEdit`` only reverts an edit that
takes the field from valid to invalid; a field that is already invalid accepts
whatever is typed into it. The suffix therefore converted "input refused" into
"input accepted, then silently discarded when the edit commits" — the old number
reappeared and nothing told the user their edit had gone.

So this pins both halves: the affixed text validates, and rubbish still does not.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QApplication

from widgets.stepper import Stepper


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def spots(qapp):
    """The quick-edit's count configuration: negative floor, " spots" suffix."""
    s = Stepper(value=-1.0, minimum=-1.0, maximum=1e9, decimals=0)
    s.setSuffix(" spots")
    s.setValue(-1.0)
    return s


def _state(stepper, text: str):
    state, _t, _p = stepper._edit.validator().validate(text, len(text))
    return state


def test_the_displayed_value_is_valid(spots):
    assert spots._edit.text() == "-1 spots"
    assert _state(spots, "-1 spots") == QValidator.Acceptable
    assert _state(spots, "20 spots") == QValidator.Acceptable


def test_rubbish_is_still_refused(spots):
    assert _state(spots, "abc") == QValidator.Invalid
    assert _state(spots, "20 spotsabc") == QValidator.Invalid
    assert _state(spots, "1 2 spots") == QValidator.Invalid


def test_a_half_typed_number_is_intermediate(spots):
    """An empty box and a lone sign have to be reachable mid-edit."""
    for text in ("", " spots", "- spots", ". spots"):
        assert _state(spots, text) == QValidator.Intermediate, text


def test_typing_rubbish_does_not_reach_the_field(spots):
    """The behaviour the user actually meets, asserted through the widget.

    ``insert`` runs the same validation path as a keystroke, so an unchanged
    text here is the refusal that went missing while every value was invalid.
    """
    spots._edit.setCursorPosition(0)
    spots._edit.insert("abc")
    assert spots._edit.text() == "-1 spots"


def test_a_typed_number_commits(spots):
    spots._edit.setText("20 spots")
    spots._commit_text()
    assert spots.value() == 20.0
    assert spots._edit.text() == "20 spots"


def test_an_unaffixed_stepper_is_unaffected(qapp):
    """The intensity configuration, which never had a suffix to strip."""
    s = Stepper(value=50.0, minimum=0.0, maximum=1e9, decimals=1)
    assert _state(s, "50.0") == QValidator.Acceptable
    assert _state(s, "abc") == QValidator.Invalid


def test_clearing_the_digits_to_retype_is_allowed(spots):
    """Deleting the number leaves the bare suffix behind, and that must pass.

    ``" spots"`` is the state the box is in for one keystroke every time a user
    selects the value and types over it. Refusing it makes the field
    uneditable by the most ordinary route there is.
    """
    spots._edit.setText("20 spots")
    spots._edit.setSelection(0, 2)
    spots._edit.insert("")
    assert spots._edit.text() == " spots"
    spots._edit.setCursorPosition(0)
    spots._edit.insert("5")
    spots._commit_text()
    assert spots.value() == 5.0
