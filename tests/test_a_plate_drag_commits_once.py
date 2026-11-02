"""A drag across the sidebar plate commits the selection once, when it ends.

The plate fires ``selectionChanged`` for every well a drag enters, each from
its own mouse event. The handler's end-of-tick deferral merged nothing across
those events, so every well entered redrew every plot, and the release then
committed once more. Measured on the mini dataset before the fix: two commits
during a drag through two wells and a third on release.

Full-app (``loaded_app``): the commit is wired through the real sidebar plate,
its signals and the real handlers, which is what a stub would not show.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from review.core import selection_controller as sc


def _drag(grid, cells) -> None:
    _lab, cell, ox, oy = grid._metrics()

    def pt(r, c):
        return QPoint(int(ox + c * cell + cell / 2), int(oy + r * cell + cell / 2))

    QTest.mousePress(grid, Qt.LeftButton, Qt.NoModifier, pt(*cells[0]))
    QApplication.processEvents()
    for r, c in cells[1:]:
        # A held-button move, one event-loop pass apart, as a real mouse delivers them.
        ev = QMouseEvent(QEvent.MouseMove, QPointF(pt(r, c)), QPointF(grid.mapToGlobal(pt(r, c))),
                         Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(grid, ev)
        QApplication.processEvents()
    return pt(*cells[-1])


def test_a_drag_commits_once_when_it_ends(loaded_app, monkeypatch):
    ctx = loaded_app._ctx
    commits = []
    real = sc.on_plate_sel_change
    monkeypatch.setattr(sc, "on_plate_sel_change", lambda c: (commits.append(1), real(c)))
    grid = ctx.widgets.get("_sidebar_plate")._grid
    loaded_app.show()
    QApplication.processEvents()
    assert not sc._plate_passive_in_group_mode(ctx)

    # Row A, columns 1-6: the mini dataset holds A01 and A02 of them.
    end = _drag(grid, [(0, c) for c in range(0, 6)])
    assert ctx.widgets.get("_sidebar_plate").isSelecting()
    assert commits == [], "the selection was committed while the drag was still going"
    assert {"A01", "A02"} <= set(loaded_app._app_state.selection.selected_wells)

    QTest.mouseRelease(grid, Qt.LeftButton, Qt.NoModifier, end)
    QApplication.processEvents()
    assert len(commits) == 1


def test_a_click_commits_once(loaded_app, monkeypatch):
    ctx = loaded_app._ctx
    commits = []
    real = sc.on_plate_sel_change
    monkeypatch.setattr(sc, "on_plate_sel_change", lambda c: (commits.append(1), real(c)))
    grid = ctx.widgets.get("_sidebar_plate")._grid
    loaded_app.show()
    QApplication.processEvents()
    end = _drag(grid, [(1, 1)])          # B02: press, a pass of the event loop, release
    QTest.mouseRelease(grid, Qt.LeftButton, Qt.NoModifier, end)
    QApplication.processEvents()
    assert len(commits) == 1
