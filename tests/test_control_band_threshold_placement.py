"""Threshold sits last in the control band, and matches the row's height.

Two complaints about the same row. The scope zone holds the controls that say
*what data is shown* — channel, property, timepoints — and Threshold, which is
the odd one out: it narrows the data rather than choosing which data to show.
It belongs at the end of that group, but ``add_scope`` appends in call order and
tabs build their controls in whatever order their builder runs, so Threshold
landed mid-row with Timepoints after it.

And it was visibly taller than everything beside it: a ``Stepper``'s two stacked
step buttons each want a full text row, which came to 35px against the combos'
25px, so the row read as ragged.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


def _band_with_threshold(qapp, *, threshold_position: int):
    """A band whose Threshold is added at *threshold_position* of four."""
    from PySide6.QtWidgets import QPushButton

    from widgets.control_band import ControlBand
    from widgets.stepper import Stepper

    band = ControlBand()
    threshold = None
    for i in range(4):
        if i == threshold_position:
            threshold = band.add_scope("Threshold", Stepper(value=50.0),
                                       trailing=True)
        else:
            band.add_scope("", QPushButton(f"control {i}"))
    band.resize(900, 60)
    band.show()
    qapp.processEvents()
    return band, threshold


@pytest.mark.parametrize("position", [0, 1, 2, 3])
def test_threshold_is_last_whatever_order_it_was_added_in(qapp, position):
    band, threshold = _band_with_threshold(qapp, threshold_position=position)
    scope = band._scope
    assert scope.itemAt(scope.count() - 1).widget() is threshold


def test_threshold_sits_to_the_right_of_every_other_scope_control(qapp):
    """Last in the layout is not enough — it has to render rightmost too."""
    band, threshold = _band_with_threshold(qapp, threshold_position=1)
    scope = band._scope
    others = [scope.itemAt(i).widget() for i in range(scope.count())
              if scope.itemAt(i).widget() is not threshold]
    assert threshold.x() > max(w.x() for w in others)


def test_an_untagged_control_keeps_its_call_order(qapp):
    """``trailing`` is opt-in; nothing else moves."""
    from PySide6.QtWidgets import QPushButton

    from widgets.control_band import ControlBand

    band = ControlBand()
    first = band.add_scope("", QPushButton("one"))
    second = band.add_scope("", QPushButton("two"))
    scope = band._scope
    assert scope.itemAt(0).widget() is first
    assert scope.itemAt(1).widget() is second


def test_the_stepper_is_the_same_height_as_the_combos_beside_it(qapp):
    """The ragged-row complaint. 35 vs 25 before."""
    from widgets.labeled_combo import LabeledComboBox
    from widgets.stepper import Stepper

    combo = LabeledComboBox("Channel")
    combo.addItems(["GFP"])
    stepper = Stepper(value=50.0)
    assert stepper.sizeHint().height() == combo.sizeHint().height()


def test_the_row_height_is_measured_not_hardcoded(qapp):
    """It depends on the stylesheet and the font, so a literal would go stale.

    Mirrors ``widgets.mode_bar.tab_bar_height``. It must measure the *same*
    control the band actually uses — measuring a bare ``QComboBox`` instead of
    a ``LabeledComboBox`` reproduced the very mismatch this removes, since the
    subclass is 3px shorter.
    """
    from widgets.labeled_combo import LabeledComboBox
    from widgets.stepper import control_row_height

    assert control_row_height() == LabeledComboBox("Ag").sizeHint().height()


def test_moving_an_item_to_the_end_keeps_it_alive(qapp):
    """``removeWidget`` + ``addWidget`` cannot do this — Qt deletes the item
    ``takeAt`` returns, so the re-add would operate on a dead object."""
    from PySide6.QtWidgets import QPushButton, QWidget

    from widgets.flow_layout import FlowLayout

    host = QWidget()
    flow = FlowLayout(host)
    a, b = QPushButton("a", host), QPushButton("b", host)
    flow.addWidget(a)
    flow.addWidget(b)
    assert flow.move_to_end(a) is True
    assert [flow.itemAt(i).widget() for i in range(flow.count())] == [b, a]
    assert flow.move_to_end(QPushButton("absent", host)) is False
