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


def test_a_focused_dropdown_takes_the_wheel(form, qtbot):
    area, combo, _spin = form
    combo.setFocus(Qt.MouseFocusReason)
    area.activateWindow()
    qtbot.waitUntil(combo.hasFocus, timeout=2000)
    _wheel(combo, -120)
    assert combo.currentIndex() == 1, "a field the person clicked into ignored the wheel"


def test_the_app_installs_the_guard_at_startup():
    """A guard nothing installs guards nothing: ``allwell.main`` is where the one application-wide filter is put on."""
    from tests._repo_scan import REPO_ROOT

    main_source = (REPO_ROOT / "allwell.py").read_text(encoding="utf-8").split("def main()", 1)[1]
    assert "wheel_guard.install(app)" in main_source
