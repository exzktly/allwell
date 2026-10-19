"""Tapping a stepper's number in phone layout keeps the stepper its height.

Reported: in phone layout, trying to edit a number in a plot's Properties panel
shrank the text box vertically until it almost disappeared. The field is a
``Stepper``; on focus it re-polishes itself to colour its border, and the
re-polish fired the ``StyleChange`` that re-measures the row height. Measured
from *inside* that polish, the probe combo ``control_row_height`` builds came
back at the desk's height without the touch floors, so a 110 px touch stepper
was pinned to 21 px the moment its field took focus.

The stepper is built in a shown window under the application stylesheet with
the touch floors on, as phone layout builds it, and focus is moved into the
field the way a tap moves it.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget

from ui import theme
from widgets.stepper import Stepper


@pytest.fixture
def touch_sheet(qapp):
    """The application stylesheet with phone layout's touch floors on."""
    before = qapp.styleSheet()
    theme.set_touch_unit(2.0)
    qapp.setStyleSheet(theme.qss())
    yield
    theme.set_touch_unit(None)
    qapp.setStyleSheet(before)


def _touch_stepper(qtbot) -> tuple[QWidget, Stepper]:
    win = QWidget()
    qtbot.addWidget(win)
    QVBoxLayout(win)
    # Takes the window's first focus, so the stepper's field is focused only
    # when the test moves focus there.
    win.layout().addWidget(QLineEdit())
    st = Stepper(value=18.0, minimum=1.0, maximum=72.0, decimals=0)
    win.layout().addWidget(st)
    win.show()
    st.setTouchMode(True)
    QApplication.processEvents()
    return win, st


def test_focusing_the_field_keeps_the_touch_height(qtbot, touch_sheet):
    win, st = _touch_stepper(qtbot)
    touch_h = st.height()
    # Larger than the desk row, or the test measures nothing.
    assert touch_h >= 2 * 44 - 2, f"the touch floor did not apply ({touch_h}px)"

    win.activateWindow()
    st._edit.setFocus(Qt.MouseFocusReason)
    qtbot.waitUntil(lambda: st.property("focused") is True, timeout=2000)
    QApplication.processEvents()
    assert st.height() == touch_h, (
        f"focusing the field changed the stepper from {touch_h}px to {st.height()}px")
    assert st._edit.height() == touch_h - 2

    st._edit.clearFocus()
    qtbot.waitUntil(lambda: st.property("focused") is False, timeout=2000)
    QApplication.processEvents()
    assert st.height() == touch_h
