"""A wheel over a dropdown or number box scrolls the form, not the field, until the field has focus.

Reported on the Analyze tab: the form is long and has to be scrolled, and every dropdown the pointer crossed changed
its value. Driven as the wheel reaches a widget: a ``QWheelEvent`` sent to the field.

The guard is on the form's own fields (``keep_guarding``), never on the application: an application filter in Python
crashed a Windows session twice, inside the filter, just after Review loaded a dataset, and not once with it off.
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
    wheel_guard.keep_guarding(page)
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


def test_a_field_added_to_the_form_later_is_guarded_too(form, qtbot):
    """Fields the form builds after it was guarded — a row added inside a nested section — are walked in when the
    form's layout changes."""
    area, _combo, _spin = form
    page = area.widget()
    section = QWidget()
    inner = QVBoxLayout(section)
    page.layout().insertWidget(1, section)
    qtbot.wait(50)
    late = QComboBox()
    late.addItems(["one", "two"])
    inner.addWidget(late)
    qtbot.waitUntil(lambda: late.focusPolicy() == Qt.StrongFocus, timeout=2000)
    _wheel(late)
    assert late.currentIndex() == 0, "a field added later took the wheel"


def test_a_dropdown_outside_a_guarded_form_is_left_alone(qtbot):
    """The guard is the form's, not the application's: a dropdown elsewhere (a Review page) keeps Qt's own wheel, and
    nothing of the guard runs for it."""
    page = QWidget()
    lay = QVBoxLayout(page)
    combo = QComboBox()
    combo.addItems(["one", "two", "three"])
    lay.addWidget(combo)
    qtbot.addWidget(page)
    page.show()
    qtbot.waitExposed(page)
    assert combo.focusPolicy() == Qt.WheelFocus and not combo.property("_aw_wheel_guarded")
    _wheel(combo)
    assert combo.currentIndex() == 1


def test_no_filter_is_put_on_the_application():
    """The crash this rules out was inside a filter on the whole application: nothing may install one again."""
    from tests._repo_scan import REPO_ROOT

    assert not hasattr(wheel_guard, "install")
    main_source = (REPO_ROOT / "allwell.py").read_text(encoding="utf-8").split("def main()", 1)[1]
    assert "wheel_guard" not in main_source


def test_the_analyze_form_is_guarded(qtbot):
    """The form the behaviour was asked for: the real Analyze tab guards its dropdowns and number boxes."""
    from analyze.tab import AnalyzeTab

    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    fields = [*tab._left_sidebar.widget().findChildren(QComboBox), *tab._left_sidebar.widget().findChildren(QSpinBox)]
    assert len(fields) > 5
    assert all(f.property("_aw_wheel_guarded") for f in fields), [f.objectName() for f in fields
                                                                   if not f.property("_aw_wheel_guarded")]
