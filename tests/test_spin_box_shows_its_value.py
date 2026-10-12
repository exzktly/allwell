"""A spin box has to have room for the number in it.

Qt sizes a ``QSpinBox``'s text area to the widest value its range can hold plus
a margin that does not grow with the range — the same few pixels for 1–9 as
for 0–65535. At the UI size that is **5 or 6 px**: 6 for every range in DejaVu
Sans, 6 for 1–999 and 5 for 0–65535 in Inter, so the exact figure depends on how
the font's digit widths round to whole pixels. Either way it is under half a
digit, so any platform whose text renders a little wider than the
metric predicted (a different font fallback, a hinting difference, a
fractional-DPI scale) clips the value, and on a two-digit box there is nothing
left to read. Groups' *Replicates per group* (range 1–12) is the one that was
reported.

**Measured on the text area, not the widget.** A test that measured the widget
would have passed on a change that grew the box and not the field: Qt lays the
up/down buttons out *inside* the box's right padding, so ``padding-right``
widens the widget and leaves the text area exactly as it was. Only ``min-width``
grows the part the number is drawn in.

That property of ``padding-right`` is also the second half of the fix, used
deliberately. With the app's ordinary 8 px of right padding the 14 px stepper
has 6 px more than the padding allows for, and whether the excess comes out of
the text is the platform style's business — which is the shape of a control
that clips its value on Windows and not on Linux. Reserving the buttons' own
width there means there is nothing left to decide.
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QFontMetricsF
from PySide6.QtWidgets import QHBoxLayout, QSpinBox, QWidget

from ui import theme

pytestmark = pytest.mark.qt

#: The narrow ranges a spin box in this app really carries — a one-digit count,
#: the plate's twelve columns, its ninety-six wells. This is the vulnerable
#: class: a value of one or two digits is short enough that Qt's few pixels of
#: margin are the difference between reading it and not.
_NARROW = ((1, 9), (1, 12), (1, 96))

#: Ranges wide enough that the floor cannot reach them. Kept here deliberately:
#: ``min-width`` is a floor and cannot scale with content, so these keep Qt's
#: own margin and this file says so rather than leaving a reader to assume the
#: guarantee is universal. A few pixels beside a five-digit value is a different
#: proposition from the same beside two — the error that clips text scales with
#: the text, and so does the room Qt gives it.
_WIDE = ((1, 999), (0, 65535))

#: How much clear space a value needs beyond its own width. Qt's own margin is
#: 5–6 px depending on the font, and that is what was not enough; half a digit at the UI size is about 4,
#: so this asks for roughly one whole digit of headroom.
_MIN_SLACK = 9


def _slack(qtbot, qapp, low: int, high: int) -> tuple[int, int]:
    """``(text area width, width the widest value needs)`` for this range."""
    qapp.setStyleSheet(theme.qss())
    host = QWidget()
    qtbot.addWidget(host)
    QHBoxLayout(host)
    box = QSpinBox(host)
    box.setRange(low, high)
    box.setValue(high)
    host.layout().addWidget(box)
    host.show()
    edit = box.lineEdit()
    return edit.width(), edit.fontMetrics().horizontalAdvance(str(high))


@pytest.mark.parametrize(("low", "high"), _NARROW)
def test_a_short_range_has_room_for_its_widest_value(qtbot, qapp, low, high):
    width, needed = _slack(qtbot, qapp, low, high)
    assert width - needed >= _MIN_SLACK, (
        f"a {low}–{high} spin box gives its value {width}px for {needed}px of "
        f"text — {width - needed}px of slack, under the {_MIN_SLACK}px floor. "
        f"Qt's own margin is 5–6px whatever the range, so this is the state a "
        f"slightly wider font clips.")


def test_the_reported_control_is_covered_by_that(qtbot, qapp):
    """Groups' *Replicates per group*, driven through its own builder.

    The range is read off the real control rather than restated, so a change to
    the plate's width cannot leave this test measuring a range nothing uses.
    """
    from types import SimpleNamespace

    from review.core.selection_state import SelectionState
    from review.features.home.groups.tab_view import (
        build_replicate_groups_centre,
    )

    class _Registry:
        def __init__(self):
            self._d = {}

        def register(self, name, widget):
            self._d[name] = widget
            return widget

        def get_or_none(self, name):
            return self._d.get(name)

        def get(self, name):
            return self._d[name]

    qapp.setStyleSheet(theme.qss())
    ctx = SimpleNamespace(
        widgets=_Registry(), window=None,
        state=SimpleNamespace(selection=SelectionState(),
                              dataset=SimpleNamespace(well_paths={})),
        persistence=SimpleNamespace(schedule_save=lambda *_: None),
        bus=SimpleNamespace(emit=lambda *a, **k: None),
    )
    frame = QWidget()
    qtbot.addWidget(frame)
    frame.resize(900, 260)
    build_replicate_groups_centre(ctx, frame)
    box = ctx.widgets.get_or_none("_rep_quick_size_var")
    assert box is not None, "the Quick Replicates size control is not registered"
    box.setValue(box.maximum())
    frame.show()

    edit = box.lineEdit()
    needed = edit.fontMetrics().horizontalAdvance(str(box.maximum()))
    assert edit.width() - needed >= _MIN_SLACK, (
        f"Replicates per group shows {box.maximum()} in {edit.width()}px for "
        f"{needed}px of text")


#: ``QLineEditPrivate::horizontalMargin``: the gap a ``QLineEdit`` keeps between
#: its contents rect and the text on each side. Private to Qt and fixed at 2 px
#: since Qt 4, so it is restated here rather than read.
_LINE_EDIT_SIDE_MARGIN = 2


@pytest.mark.parametrize(("low", "high"), _WIDE)
def test_a_wide_range_at_least_never_loses_room(qtbot, qapp, low, high):
    """The floor does not reach these, and they must not go backwards either.

    Stated as the limit it is: a box whose own range asks for more than the
    floor keeps Qt's own sizing, so this asserts that baseline rather than the
    stronger one above. A change that bought the narrow boxes their headroom by
    taking it from the wide ones fails here.

    **Two halves, both independent of the font.** This used to assert 6 px of
    slack, which is Qt's margin in DejaVu Sans; in Inter at the same size Qt
    gives a 0–65535 box 5 px, and the assertion failed on every machine whose
    ``sans-serif`` is Inter while nothing was clipped. So instead:

    * **the box gets the whole width Qt asked for** (its size hint). This is
      the half that bites. Measured against ``ui/theme.py`` with a rule
      appended to the spin-box block: ``max-width: 60px`` takes 1 px from a
      0–65535 box in DejaVu and fails here while the value still fits (in Inter
      the box needs no more than 60 px of content and loses nothing, so it
      correctly passes), and ``max-width: 40px`` fails both ranges in both
      fonts;
    * **the widest value, at its exact (fractional) advance, fits in the part
      of the field the line edit draws into.** This is the user-visible claim,
      kept as the statement of it, and recorded as not having been seen to fail
      on its own: a 40 px stepper and a 12 px ``margin-left`` on the inner
      ``QLineEdit`` both leave this file green, because under the app
      stylesheet Qt grows the box rather than squeezing the field, and every
      change that did take room was caught by the size-hint half first.
    """
    qapp.setStyleSheet(theme.qss())
    host = QWidget()
    qtbot.addWidget(host)
    QHBoxLayout(host)
    box = QSpinBox(host)
    box.setRange(low, high)
    box.setValue(high)
    host.layout().addWidget(box)
    host.show()

    assert box.width() >= box.sizeHint().width(), (
        f"a {low}–{high} spin box is {box.width()}px wide against the "
        f"{box.sizeHint().width()}px Qt sized it for — something is taking the "
        f"room Qt gives its widest value")

    edit = box.lineEdit()
    margins = edit.textMargins()
    drawable = (edit.contentsRect().width() - margins.left() - margins.right()
                - 2 * _LINE_EDIT_SIDE_MARGIN)
    needed = QFontMetricsF(edit.font()).horizontalAdvance(str(high))
    assert drawable >= needed, (
        f"a {low}–{high} spin box draws its value in {drawable}px, and "
        f"{high} needs {needed:.2f}px — the number is cut off")


@pytest.mark.parametrize("style", ["Fusion", "Windows"])
@pytest.mark.parametrize(("low", "high"), _NARROW + _WIDE)
def test_the_buttons_are_laid_out_clear_of_the_number(qtbot, qapp, style, low,
                                                      high):
    """The stepper gets space of its own, so it cannot be drawn over the value.

    Driven under both style engines this Qt ships, because the reported symptom
    is one platform clipping where another does not, and the sub-control
    geometry is the style's to decide.

    **This one does not bite here, and that is recorded rather than hidden.**
    Removing the reservation leaves it green, because neither style available on
    this platform ever laid the buttons over the text — the overlap is what was
    reported on Windows and cannot be reproduced on Linux. It is kept as the
    invariant a Windows runner would catch, and the assertion that actually
    holds the fix in place is ``test_the_stylesheet_reserves_the_buttons_width``
    below, which is decidable anywhere.
    """
    from PySide6.QtWidgets import QStyle, QStyleOptionSpinBox

    previous = qapp.style().objectName()
    qapp.setStyle(style)
    try:
        qapp.setStyleSheet(theme.qss())
        host = QWidget()
        qtbot.addWidget(host)
        QHBoxLayout(host)
        box = QSpinBox(host)
        box.setRange(low, high)
        box.setValue(high)
        host.layout().addWidget(box)
        host.show()

        option = QStyleOptionSpinBox()
        option.initFrom(box)
        up = box.style().subControlRect(QStyle.CC_SpinBox, option,
                                        QStyle.SC_SpinBoxUp, box)
        edit = box.lineEdit()
        gap = up.left() - (edit.x() + edit.width())
        assert gap >= 0, (
            f"under {style}, a {low}–{high} spin box lays its buttons {-gap}px "
            f"over the area the number is drawn in")
    finally:
        qapp.setStyle(previous)


def test_the_stylesheet_reserves_the_buttons_width(qapp):
    """The right padding is at least as wide as the stepper drawn inside it.

    Decidable from the stylesheet, so unlike the geometry check above this one
    fails the moment the reservation is dropped — on any platform. Qt lays a
    spin box's buttons inside its right padding, so a padding narrower than the
    buttons leaves the excess for the platform style to take out of somewhere,
    and "somewhere" was the number on Windows.

    Read out of the generated QSS rather than restated, so the rule and the test
    cannot drift apart.
    """
    import re

    sheet = theme.qss()
    # Anchored at the start of a line: the generic input rule's selector list
    # also *ends* ``…, QSpinBox, QDoubleSpinBox {``, and an unanchored search
    # finds that one instead and reads its padding.
    block = re.search(r"^QSpinBox,\s*QDoubleSpinBox\s*\{([^}]*)\}",
                      sheet, re.M)
    assert block, "the QSpinBox rule is gone from the stylesheet"
    reserved = re.search(r"padding-right:\s*(\d+)px", block.group(1))
    assert reserved, (
        "QSpinBox no longer reserves right padding for its own buttons; Qt "
        "lays them inside that padding, so without it the platform style "
        "decides whether they come out of the number")
    buttons = re.search(r"QSpinBox::up-button[^{]*\{[^}]*width:\s*(\d+)px",
                        sheet, re.S)
    assert buttons, "the button width is gone from the stylesheet"
    assert int(reserved.group(1)) >= int(buttons.group(1)), (
        f"the right padding reserves {reserved.group(1)}px for a "
        f"{buttons.group(1)}px stepper")


def test_the_floor_is_a_floor_and_not_a_fixed_width(qtbot, qapp):
    """A box whose range asks for more than the floor keeps its own size.

    Pinning every spin box to one width would make a 0–65535 entry as narrow as
    a 1–9 counter, which is the obvious wrong way to satisfy the tests above.
    """
    narrow, _ = _slack(qtbot, qapp, 1, 9)
    wide, _ = _slack(qtbot, qapp, 0, 65535)
    assert wide > narrow
