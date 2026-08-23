"""The segmented control paints the four tones its own stylesheet declares.

``_Segment.paintEvent`` suppresses the style's text rendering so it can centre
the icon and label itself. That makes the ``color:`` declarations in
``SegmentedControl._build_qss`` unreachable for the drawn text: a QSS
pseudo-state colour is resolved into the painter Qt would have used, not into
the widget palette, so the previous code — which read
``QPalette.ButtonText`` off the widget and off the style option — got one
near-identical tone back for every state. A checked segment drew in
``#969ca9`` and an unchecked one in ``#98a2b3``, a difference of two units per
channel, where the stylesheet asks for ``text_primary`` against
``text_secondary``.

The painter now asks :func:`widgets.segmented_control.segment_text_color`,
which is also the single place the four tones are named. These tests hold the
two halves together: the resolver must return a genuinely different tone for
each state, and the stylesheet must declare the same tone the painter uses.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

import theme  # noqa: E402
from widgets.segmented_control import SegmentedControl, segment_text_color  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _qss_colors(qss: str) -> dict[str, str]:
    """Map segment selector suffix (``""``/``"hover"``/…) → declared colour."""
    out: dict[str, str] = {}
    pattern = re.compile(
        r"#SegmentedControl QToolButton#Segment(?::(\w+))?\s*\{([^}]*)\}"
    )
    for state, body in pattern.findall(qss):
        m = re.search(r"(?<!-)\bcolor:\s*([^;]+);", body)
        if m:
            out[state] = m.group(1).strip()
    return out


def test_the_stylesheet_declares_a_tone_for_each_state(qapp):
    ctrl = SegmentedControl()
    colors = _qss_colors(ctrl._build_qss())
    assert set(colors) == {"", "hover", "checked", "disabled"}, colors


def test_the_painter_uses_the_tone_the_stylesheet_declares(qapp):
    ctrl = SegmentedControl()
    ctrl.addSegment("A")
    ctrl.addSegment("B")
    declared = _qss_colors(ctrl._build_qss())

    seg = ctrl._buttons[0]
    other = ctrl._buttons[1]

    other.setChecked(True)
    assert seg.isEnabled() and not seg.isChecked()
    assert segment_text_color(seg) == declared[""]

    seg.setChecked(True)
    assert segment_text_color(seg) == declared["checked"]

    seg.setChecked(False)
    seg.setEnabled(False)
    assert segment_text_color(seg) == declared["disabled"]


class _StubSegment:
    """The three predicates :func:`segment_text_color` reads, set explicitly.

    ``underMouse()`` cannot be driven reliably under the offscreen QPA — it
    reports True for a shown widget whose rect contains the platform's default
    cursor position — so the hover tone is checked against a stub rather than a
    real widget. The real-widget tests below keep their segments hidden, where
    ``underMouse()`` is deterministically False.
    """

    def __init__(self, *, enabled=True, checked=False, hover=False):
        self._enabled, self._checked, self._hover = enabled, checked, hover

    def isEnabled(self):
        return self._enabled

    def isChecked(self):
        return self._checked

    def underMouse(self):
        return self._hover


def test_hover_brightens_an_unchecked_segment(qapp):
    ctrl = SegmentedControl()
    declared = _qss_colors(ctrl._build_qss())
    assert segment_text_color(_StubSegment(hover=True)) == declared["hover"]
    assert segment_text_color(_StubSegment()) == declared[""]


def test_disabled_wins_over_hover_and_checked(qapp):
    """QSS orders ``:disabled`` last, so it must not be overridden by hover."""
    ctrl = SegmentedControl()
    declared = _qss_colors(ctrl._build_qss())
    stub = _StubSegment(enabled=False, checked=True, hover=True)
    assert segment_text_color(stub) == declared["disabled"]


def test_checked_and_unchecked_are_visibly_different(qapp):
    """The defect this file exists for: two states, one indistinguishable tone."""
    ctrl = SegmentedControl()
    ctrl.addSegment("A")
    ctrl.addSegment("B")
    seg, other = ctrl._buttons

    other.setChecked(True)
    unchecked = segment_text_color(seg)
    seg.setChecked(True)
    checked = segment_text_color(seg)

    assert checked != unchecked
    assert checked == theme.Colors.text_primary
    assert unchecked == theme.Colors.text_secondary
