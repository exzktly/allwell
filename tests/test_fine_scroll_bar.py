"""FineScrollBar: dragging the handle cannot outrun the content.

Reported: the Application Log drawer "scrolls too fast by using the scroll
bar". A plain bar maps the whole log onto the drawer's short track, so one
pixel of handle drag moved many lines. The drawer's bar now moves at most one
line per pixel of drag.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSlider, QTextEdit

from widgets.fine_scroll_bar import FineScrollBar


def _pump() -> None:
    for _ in range(5):
        QApplication.processEvents()


def _view(qtbot, lines: int) -> tuple[QTextEdit, FineScrollBar]:
    view = QTextEdit()
    qtbot.addWidget(view)
    bar = FineScrollBar(Qt.Vertical, view, max_rate=lambda: view.fontMetrics().lineSpacing())
    view.setVerticalScrollBar(bar)
    view.setReadOnly(True)
    view.setPlainText("\n".join(f"line {i}" for i in range(lines)))
    view.resize(400, 200)
    view.show()
    qtbot.waitExposed(view)
    _pump()
    return view, bar


def _handle_centre(bar) -> QPoint:
    opt = QStyleOptionSlider()
    bar.initStyleOption(opt)
    return bar.style().subControlRect(QStyle.CC_ScrollBar, opt, QStyle.SC_ScrollBarSlider, bar).center()


def _drag_handle(bar, dy: int) -> None:
    start = _handle_centre(bar)
    QTest.mousePress(bar, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(bar, start + QPoint(0, dy))
    QTest.mouseRelease(bar, Qt.LeftButton, Qt.NoModifier, start + QPoint(0, dy))


def test_a_handle_drag_on_a_long_log_moves_one_line_per_pixel(qtbot):
    view, bar = _view(qtbot, 3000)
    line = view.fontMetrics().lineSpacing()
    # The defect: a plain bar moves this many units per pixel — many lines.
    assert bar.proportional_rate() > 5 * line
    _drag_handle(bar, 40)
    assert bar.value() == 40 * line, (
        f"a 40 px drag moved {bar.value()} units, not 40 lines of {line}")
    assert not bar.isSliderDown()


def test_a_short_document_drags_exactly_as_a_plain_bar(qtbot):
    view, bar = _view(qtbot, 40)
    line = view.fontMetrics().lineSpacing()
    natural = bar.proportional_rate()
    assert 0 < natural < line                     # slower than the cap: not capped
    assert bar.drag_rate() == natural
    _drag_handle(bar, 30)
    assert bar.value() == round(30 * natural)


def test_a_click_on_the_groove_still_pages(qtbot):
    _view_, bar = _view(qtbot, 3000)
    below = _handle_centre(bar) + QPoint(0, 120)
    QTest.mouseClick(bar, Qt.LeftButton, Qt.NoModifier, below)
    _pump()
    assert bar.value() == bar.pageStep()
