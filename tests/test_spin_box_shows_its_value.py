"""A spin box has to have room for the number in it.

Qt sizes a ``QSpinBox``'s text area to the widest value its range can hold plus
a fixed **6 px** — the same 6 px whether the range is 1–9 or 0–65535. That is
under half a digit, so any platform whose text renders a little wider than the
metric predicted (a different font fallback, a hinting difference, a
fractional-DPI scale) clips the value, and on a two-digit box there is nothing
left to read. Groups' *Replicates per group* (range 1–12) is the one that was
reported.

**Measured on the text area, not the widget.** Padding does not fix this and a
test that measured the widget would have said it did: Qt lays the up/down
buttons out *inside* the box's right padding, so ``padding-right`` widens the
widget and leaves the text area exactly as it was. Only ``min-width`` grows the
part the number is drawn in.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QHBoxLayout, QSpinBox, QWidget

import theme

pytestmark = pytest.mark.qt

#: The narrow ranges a spin box in this app really carries — a one-digit count,
#: the plate's twelve columns, its ninety-six wells. This is the vulnerable
#: class: a value of one or two digits is short enough that Qt's fixed 6 px is
#: the difference between reading it and not.
_NARROW = ((1, 9), (1, 12), (1, 96))

#: Ranges wide enough that the floor cannot reach them. Kept here deliberately:
#: ``min-width`` is a floor and cannot scale with content, so these keep Qt's
#: own 6 px and this file says so rather than leaving a reader to assume the
#: guarantee is universal. Six pixels beside a five-digit value is a different
#: proposition from six beside two — the error that clips text scales with the
#: text, and so does the room Qt gives it.
_WIDE = ((1, 999), (0, 65535))

#: How much clear space a value needs beyond its own width. Qt's own margin is
#: 6 px and 6 px is what was not enough; half a digit at the UI size is about 4,
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
        f"Qt's own margin is 6px whatever the range, so this is the state a "
        f"slightly wider font clips.")


def test_the_reported_control_is_covered_by_that(qtbot, qapp):
    """Groups' *Replicates per group*, driven through its own builder.

    The range is read off the real control rather than restated, so a change to
    the plate's width cannot leave this test measuring a range nothing uses.
    """
    from types import SimpleNamespace

    from well_viewer.core.selection_state import SelectionState
    from well_viewer.features.home.groups.tab_view import (
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


@pytest.mark.parametrize(("low", "high"), _WIDE)
def test_a_wide_range_at_least_never_loses_room(qtbot, qapp, low, high):
    """The floor does not reach these, and they must not go backwards either.

    Stated as the limit it is: a box whose own range asks for more than the
    floor keeps Qt's 6 px, so this asserts that baseline rather than the
    stronger one above. A change that bought the narrow boxes their headroom by
    taking it from the wide ones fails here.
    """
    width, needed = _slack(qtbot, qapp, low, high)
    assert width >= needed + 6, (
        f"a {low}–{high} spin box gives its value {width}px for {needed}px of "
        f"text, below even Qt's own margin")


def test_the_floor_is_a_floor_and_not_a_fixed_width(qtbot, qapp):
    """A box whose range asks for more than the floor keeps its own size.

    Pinning every spin box to one width would make a 0–65535 entry as narrow as
    a 1–9 counter, which is the obvious wrong way to satisfy the tests above.
    """
    narrow, _ = _slack(qtbot, qapp, 1, 9)
    wide, _ = _slack(qtbot, qapp, 0, 65535)
    assert wide > narrow
