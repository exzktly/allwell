"""Phone layout: Datasets' and Analyze's form pages scroll by a drag.

Asked for: drag-to-scroll on the Datasets and Analyze form views. Over VNC a
touch-drag arrives as a left-button mouse drag, which a scroll area ignores.
Measured in the real window: Analyze's form (in the ☰ drawer) already took the
drag, Datasets' nine tool pages did not. ``PhoneShell._grab_page_scrolling``
grabs every ``QScrollArea`` under ``PhoneHandles.swipe_roots`` (Datasets,
Analyze) on the switch and on every ``refresh``.

Those forms hold plates — Datasets' subset, concatenate and OME-import tools,
Analyze's background map — and a drag on a plate selects wells. A viewport that
scrolls by a drag refuses to start scrolling when the drag starts on a plate or
a slider (``_DragOwnerGuard``), so the press reaches the plate as if no gesture
were there.

The guard is checked with the event Qt sends before it scrolls
(``QScrollPrepareEvent``) rather than a simulated swipe: QTest stamps its mouse
events from a clock of its own, from which QScroller reads a drag's speed, so a
simulated swipe scrolls or not by timing (``test_phone_plot_swipe.py``).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QScrollPrepareEvent
from PySide6.QtWidgets import (
    QApplication, QLabel, QScrollArea, QScroller, QSlider, QVBoxLayout, QWidget,
)

from ui.phone_shell import PhoneShell, set_drag_scroll
from widgets.well_plate_selector import WellPlateSelector


def _form(qtbot):
    area = QScrollArea()
    qtbot.addWidget(area)
    area.resize(360, 400)
    area.setWidgetResizable(True)
    inner = QWidget()
    lay = QVBoxLayout(inner)
    label = QLabel("Input folder")
    plate = WellPlateSelector()
    slider = QSlider(Qt.Horizontal)
    tail = QLabel("…")
    tail.setFixedHeight(1200)
    for w in (label, plate, slider, tail):
        lay.addWidget(w)
    area.setWidget(inner)
    area.show()
    qtbot.waitExposed(area)
    return area, label, plate, slider


def _starts_a_scroll(area, w: QWidget, at: QPoint) -> bool:
    vp = area.viewport()
    ev = QScrollPrepareEvent(w.mapTo(vp, at).toPointF())
    ev.ignore()
    QApplication.sendEvent(vp, ev)
    return ev.isAccepted()


def test_a_drag_on_the_form_may_scroll_it_but_not_one_on_a_plate_or_slider(qtbot):
    area, label, plate, slider = _form(qtbot)
    set_drag_scroll(area.viewport(), True)
    assert _starts_a_scroll(area, label, QPoint(5, 5))
    grid_point = QPoint(plate.width() // 2, plate.height() // 2)
    assert not _starts_a_scroll(area, plate, grid_point), \
        "a drag on the plate would scroll the form instead of selecting wells"
    assert not _starts_a_scroll(area, slider, QPoint(10, slider.height() // 2))


def test_turning_it_off_removes_the_guard_and_turning_it_on_twice_adds_one(qtbot):
    area, *_ = _form(qtbot)
    vp = area.viewport()
    set_drag_scroll(vp, True)
    set_drag_scroll(vp, True)
    from ui.phone_shell import _DragOwnerGuard
    assert len(vp.findChildren(_DragOwnerGuard, options=Qt.FindDirectChildrenOnly)) == 1
    set_drag_scroll(vp, False)
    QApplication.processEvents()
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert vp.findChildren(_DragOwnerGuard, options=Qt.FindDirectChildrenOnly) == []


@pytest.fixture
def scroller_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(QScroller, "grabGesture",
                        lambda target, kind: calls.append(("grab", target)))
    monkeypatch.setattr(QScroller, "ungrabGesture",
                        lambda target: calls.append(("ungrab", target)))
    return calls


def test_every_form_page_under_the_swipe_roots_is_grabbed(qtbot, scroller_calls):
    """Datasets builds one scroll area per tool page; all of them, and
    Analyze's form, take the drag — and nothing outside the roots does."""
    datasets = QWidget()
    qtbot.addWidget(datasets)
    tools = [QScrollArea(datasets) for _ in range(3)]
    analyze = QWidget()
    qtbot.addWidget(analyze)
    form = QScrollArea(analyze)
    elsewhere = QScrollArea()
    qtbot.addWidget(elsewhere)
    shell = SimpleNamespace(_h=SimpleNamespace(swipe_roots=lambda: [datasets, None, analyze]))

    PhoneShell._grab_page_scrolling(shell, True)
    grabbed = {id(t) for kind, t in scroller_calls if kind == "grab"}
    assert grabbed == {id(a.viewport()) for a in (*tools, form)}

    PhoneShell._grab_page_scrolling(shell, False)
    ungrabbed = {id(t) for kind, t in scroller_calls if kind == "ungrab"}
    assert ungrabbed == grabbed
