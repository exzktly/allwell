"""A wheel over a dropdown or number box scrolls the form, not the field, until the field has focus.

Reported on the Analyze tab: the form is long and has to be scrolled, and every dropdown the pointer crossed changed
its value. Driven as the wheel reaches a widget: a ``QWheelEvent`` sent to the field.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QScrollArea, QSpinBox, QVBoxLayout, QWidget  # noqa: E402

from core import wheel_guard  # noqa: E402


def _wheel(widget, delta: int = -120) -> None:
    here = QPointF(widget.rect().center())
    QApplication.sendEvent(widget, QWheelEvent(here, QPointF(widget.mapToGlobal(widget.rect().center())), QPoint(0, 0),
                                               QPoint(0, delta), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False))


@pytest.fixture
def form(qtbot):
    wheel_guard.install(QApplication.instance())
    page = QWidget()
    lay = QVBoxLayout(page)
    first = QLineEdit()                           # the window's first field takes the focus when it opens
    lay.addWidget(first)
    combo = QComboBox()
    combo.addItems(["one", "two", "three"])
    spin = QSpinBox()
    spin.setRange(0, 100)
    spin.setValue(50)
    lay.addWidget(combo)
    lay.addWidget(spin)
    tail = QWidget()
    tail.setMinimumHeight(2000)                   # a long form: the area has room to scroll
    lay.addWidget(tail)
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(page)
    area.resize(300, 300)
    qtbot.addWidget(area)
    area.show()
    qtbot.waitExposed(area)
    first.setFocus()
    return area, combo, spin


def test_the_wheel_over_an_unfocused_dropdown_scrolls_the_form_and_leaves_the_value(form):
    area, combo, _spin = form
    assert not combo.hasFocus() and area.verticalScrollBar().value() == 0
    _wheel(combo)
    assert combo.currentIndex() == 0, "the dropdown took the wheel"
    assert area.verticalScrollBar().value() > 0, "the form did not scroll"


def test_the_wheel_over_an_unfocused_number_box_scrolls_the_form_and_leaves_the_value(form):
    area, _combo, spin = form
    _wheel(spin, 120)
    assert spin.value() == 50, "the number box took the wheel"
    _wheel(spin, -120)
    assert spin.value() == 50 and area.verticalScrollBar().value() > 0


def test_a_dropdown_that_has_the_focus_still_leaves_the_wheel_to_the_form(form, qtbot):
    """It keeps the focus after a choice is made in it, so focus cannot be what lets the wheel through: the next scroll
    over a dropdown the person had used changed its value again, which is the behaviour reported as persisting."""
    area, combo, _spin = form
    combo.setFocus(Qt.MouseFocusReason)
    area.activateWindow()
    qtbot.waitUntil(combo.hasFocus, timeout=2000)
    _wheel(combo, -120)
    assert combo.currentIndex() == 0, "a focused dropdown took the wheel"
    assert area.verticalScrollBar().value() > 0


def test_a_focused_number_box_takes_the_wheel(form, qtbot):
    area, _combo, spin = form
    spin.setFocus(Qt.MouseFocusReason)
    area.activateWindow()
    qtbot.waitUntil(spin.hasFocus, timeout=2000)
    _wheel(spin, 120)
    assert spin.value() == 51, "a number box the person clicked into ignored the wheel"


def test_a_guarded_field_takes_no_focus_from_the_wheel(form):
    """Qt focuses a widget on a *real* wheel event before any filter runs, when its policy includes the wheel — which a
    dropdown's and a number box's do by default. That focus then let the first tick of a trackpad scroll change the value
    the moment a dropdown came under the pointer. A sent event is not a real one and cannot show it, so the policy that
    causes it is what is asserted."""
    _area, combo, spin = form
    assert combo.focusPolicy() == Qt.StrongFocus and spin.focusPolicy() == Qt.StrongFocus   # click and tab, no wheel


def test_a_field_built_later_gets_the_same_policy(form, qtbot):
    area, _combo, _spin = form
    late = QComboBox(area.widget())
    assert late.focusPolicy() == Qt.WheelFocus           # Qt's default, until it is shown
    late.show()
    qtbot.waitExposed(late)
    assert late.focusPolicy() == Qt.StrongFocus


def test_the_app_installs_the_guard_at_startup():
    """A guard nothing installs guards nothing: ``allwell.main`` is where the one application-wide filter is put on."""
    from tests._repo_scan import REPO_ROOT

    main_source = (REPO_ROOT / "allwell.py").read_text(encoding="utf-8").split("def main()", 1)[1]
    assert "wheel_guard.install(app)" in main_source


@pytest.mark.parametrize(("value", "wanted"), [(None, True), ("", True), ("1", True), ("0", False), (" 0 ", False)])
def test_the_guard_can_be_left_out_for_a_diagnosis(value, wanted):
    """``ALLWELL_WHEEL_GUARD=0`` starts the app without the guard, to learn whether a reported crash inside its filter
    needs it; anything else, or nothing, installs it."""
    env = {} if value is None else {wheel_guard.SWITCH_ENV: value}
    assert wheel_guard.wanted(env) is wanted


def test_the_app_records_whether_the_guard_is_on_in_the_boot_log():
    """The switch is a diagnosis, so the crash log it is read beside must say which way the session ran."""
    from tests._repo_scan import REPO_ROOT

    main_source = (REPO_ROOT / "allwell.py").read_text(encoding="utf-8").split("def main()", 1)[1]
    assert "if wheel_guard.wanted():" in main_source
    assert '_boot_diag.boot("wheel guard: on")' in main_source
    assert '_boot_diag.boot(f"wheel guard: off ({wheel_guard.SWITCH_ENV}=0)")' in main_source
