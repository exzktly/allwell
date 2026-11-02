"""Clicks on the sidebar plate redraw the plots once, after the clicks pause.

Every click used to commit the selection and redraw every plot at once, so
picking six wells one click at a time drew the plots six times, each draw
holding up the next click. The plate now repaints at each click and the plots
wait :data:`selection_controller.PLATE_CLICK_DEBOUNCE_MS` after the last one; a
drag is one gesture and commits once at its end in the same way.

Full-app (``loaded_app``): the clicks go through the real sidebar plate, its
signals and the real handlers, and a stub would show none of that.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from review.core import selection_controller as sc


def _pt(grid, r, c) -> QPoint:
    _lab, cell, ox, oy = grid._metrics()
    return QPoint(int(ox + c * cell + cell / 2), int(oy + r * cell + cell / 2))


def _click(grid, r, c) -> None:
    QTest.mousePress(grid, Qt.LeftButton, Qt.NoModifier, _pt(grid, r, c))
    QApplication.processEvents()
    QTest.mouseRelease(grid, Qt.LeftButton, Qt.NoModifier, _pt(grid, r, c))
    QApplication.processEvents()


def _counting(loaded_app, monkeypatch):
    commits = []
    real = sc.on_plate_sel_change
    monkeypatch.setattr(sc, "on_plate_sel_change", lambda c: (commits.append(1), real(c)))
    loaded_app.show()
    QApplication.processEvents()
    return commits, loaded_app._ctx.widgets.get("_sidebar_plate")._grid


def test_a_run_of_clicks_redraws_once_after_they_pause(loaded_app, qtbot, monkeypatch):
    commits, grid = _counting(loaded_app, monkeypatch)
    ctx = loaded_app._ctx
    assert not sc._plate_passive_in_group_mode(ctx)
    before = set(loaded_app._app_state.selection.selected_wells)

    for r, c in [(0, 0), (0, 1), (1, 0)]:          # A01, A02, B01
        _click(grid, r, c)
    assert commits == [], "a click redrew the plots before the clicks had paused"
    # The plate and the selection follow every click at once.
    assert set(loaded_app._app_state.selection.selected_wells) == before ^ {"A01", "A02", "B01"}

    qtbot.waitUntil(lambda: len(commits) == 1, timeout=sc.PLATE_CLICK_DEBOUNCE_MS * 8)
    qtbot.wait(sc.PLATE_CLICK_DEBOUNCE_MS * 2)
    assert len(commits) == 1


def test_a_drag_commits_once_after_it_ends(loaded_app, qtbot, monkeypatch):
    commits, grid = _counting(loaded_app, monkeypatch)
    QTest.mousePress(grid, Qt.LeftButton, Qt.NoModifier, _pt(grid, 0, 0))
    QApplication.processEvents()
    for c in range(1, 6):
        p = _pt(grid, 0, c)
        # A held-button move, one event-loop pass apart, as a real mouse delivers them.
        QApplication.sendEvent(grid, QMouseEvent(QEvent.MouseMove, QPointF(p), QPointF(grid.mapToGlobal(p)),
                                                 Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
        QApplication.processEvents()
    assert loaded_app._ctx.widgets.get("_sidebar_plate").isSelecting()
    assert commits == []
    QTest.mouseRelease(grid, Qt.LeftButton, Qt.NoModifier, _pt(grid, 0, 5))
    qtbot.waitUntil(lambda: len(commits) == 1, timeout=sc.PLATE_CLICK_DEBOUNCE_MS * 8)
    qtbot.wait(sc.PLATE_CLICK_DEBOUNCE_MS * 2)
    assert len(commits) == 1
