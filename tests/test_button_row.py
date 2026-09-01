"""A row of buttons wraps rather than forcing its host wider.

A ``QHBoxLayout`` full of buttons reports the sum of their widths as its
minimum, and that minimum propagates: on the Statistics page the three
group actions made the column they sit in about 370px at its narrowest, which
pushed the well map out of the row beside it at a pane width where the two still
fitted. The whole value of ``widgets/button_row.ButtonRow`` is the number it
reports when asked how narrow it can be, so that is what these measure.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _buttons(parent, *widths):
    from PySide6.QtWidgets import QPushButton
    out = []
    for w in widths:
        b = QPushButton("", parent)
        b.setFixedWidth(w)
        out.append(b)
    return out


def test_its_minimum_is_its_widest_button_not_their_sum(qapp):
    """The whole point, stated as the number.

    Not "it is narrower than a QHBoxLayout would be", which is satisfied by any
    saving at all including one too small to change a layout decision.
    """
    from PySide6.QtWidgets import QHBoxLayout, QWidget
    from widgets.button_row import ButtonRow

    row = ButtonRow(None, *_buttons(None, 110, 71, 180))
    assert row.minimumSizeHint().width() == 180, (
        f"the row's minimum is {row.minimumSizeHint().width()}px; wrapping "
        f"means it is the widest single button")

    boxed = QWidget()
    layout = QHBoxLayout(boxed)
    for b in _buttons(boxed, 110, 71, 180):
        layout.addWidget(b)
    assert boxed.minimumSizeHint().width() > row.minimumSizeHint().width(), (
        "a box layout of the same buttons is not wider, so this fixture is not "
        "the case ButtonRow exists for")


def test_it_takes_a_second_line_rather_than_clipping(qapp):
    """Narrow, the buttons go below one another and every one keeps its width —
    the alternative to wrapping is not a shorter row, it is a cut button."""
    from widgets.button_row import ButtonRow

    buttons = _buttons(None, 110, 71, 180)
    row = ButtonRow(None, *buttons)
    # Shown, because ``FlowLayout._do_layout`` skips items whose widget is not
    # visible — an unshown row measures as empty and every assertion below would
    # pass against a layout holding nothing at all.
    row.show()
    row.resize(200, 400)
    qapp.processEvents()
    assert all(not b.isHidden() for b in buttons)

    layout = row.layout()
    assert layout.heightForWidth(200) > layout.heightForWidth(1000), (
        "the row is no taller at 200px than at 1000px, so it did not wrap")
    # And the host reserves that height, which a widget in a stacked page does
    # not do from ``heightForWidth`` alone — the wrapped line would be drawn
    # behind whatever sits below it.
    assert row.minimumHeight() >= layout.heightForWidth(200), (
        f"the row reserves {row.minimumHeight()}px for rows needing "
        f"{layout.heightForWidth(200)}px, so the second line is clipped")
    for button in buttons:
        assert button.width() == button.minimumWidth(), (
            f"a button was squeezed to {button.width()}px")


def test_buttons_can_be_added_after_construction(qapp):
    """``add`` is how a caller with conditional actions builds one, so the
    minimum has to follow rather than being fixed at construction."""
    from widgets.button_row import ButtonRow

    row = ButtonRow(None, *_buttons(None, 110))
    assert row.minimumSizeHint().width() == 110
    row.add(_buttons(None, 180)[0])
    assert row.minimumSizeHint().width() == 180
