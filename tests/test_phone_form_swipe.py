"""Phone layout: everything that scrolls, scrolls by a drag.

Over VNC a touch-drag arrives as a left-button mouse drag, which a scroll area
ignores. It was first fixed root by root — the side rail, the Controls sheet,
the logs, Datasets' and Analyze's form pages — and each root left out was a
surface a thumb could not scroll: reported for the Properties panel, measured
for Home (1,600 px of page) and every dialog's list. So phone layout now gives
the drag to every scroll area in the process (``grab_every_scroll_area``, and
the sweep for ones built later), except where a drag already means something
(``drag_scroll_eligible``).

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

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QScrollPrepareEvent
from PySide6.QtWidgets import (
    QApplication, QLabel, QListWidget, QScrollArea, QScroller, QSlider, QVBoxLayout, QWidget,
)

from ui.phone_shell import set_drag_scroll
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


class _DragsCells(QListWidget):
    """An item view that reads mouse moves itself, the way Heat Map's layout
    table drags a cell out — without Qt's own drag-and-drop switched on."""

    def mouseMoveEvent(self, e):  # noqa: N802 — Qt naming
        super().mouseMoveEvent(e)


_KEEP: list = []


def _surfaces(qtbot):
    """One of each kind of scroll area phone layout meets, and which of them
    must scroll by a drag."""
    from PySide6.QtWidgets import QComboBox, QGraphicsView, QTextEdit
    page = QScrollArea()                       # a page, the Properties panel
    log = QTextEdit()
    log.setReadOnly(True)                      # a log
    notes = QTextEdit()                        # an editable box: drag selects
    plain_list = QListWidget()                 # a list that only selects
    reorder = QListWidget()
    reorder.setDragDropMode(QListWidget.InternalMove)   # drag reorders
    drags_cells = _DragsCells()
    scene = QGraphicsView()                    # drag modes of its own
    window = QWidget()                         # another window's list
    in_window = QListWidget(window)
    combo = QComboBox()
    combo.addItems([str(i) for i in range(40)])
    for w in (page, log, notes, plain_list, reorder, drags_cells, scene, window, combo):
        qtbot.addWidget(w)
    scrolls = [page, log, plain_list, in_window, combo.view()]
    keeps_its_drag = [notes, reorder, drags_cells, scene]
    # qtbot holds its widgets weakly: the window and the combo must outlive
    # this function, or their lists go with them.
    _KEEP[:] = [window, combo]
    return scrolls, keeps_its_drag


def test_every_scroll_area_scrolls_by_a_drag_unless_its_drag_means_something(
        qtbot, scroller_calls):
    """The wrong implementation this rejects is a roster of roots — every
    surface outside it, a dialog's list or a combo box's, stays unscrollable —
    and equally one that grabs everything, which takes an editable box's text
    selection and a list's reordering away."""
    from ui.phone_shell import drag_scroll_eligible, grab_every_scroll_area
    scrolls, keeps_its_drag = _surfaces(qtbot)
    assert [drag_scroll_eligible(a) for a in scrolls] == [True] * len(scrolls)
    assert [drag_scroll_eligible(a) for a in keeps_its_drag] == [False] * len(keeps_its_drag)

    grab_every_scroll_area(True)
    grabbed = {id(t) for kind, t in scroller_calls if kind == "grab"}
    assert {id(a.viewport()) for a in scrolls} <= grabbed
    assert not grabbed & {id(a.viewport()) for a in keeps_its_drag}
    assert all(a.viewport().property("_awDragScroll") for a in scrolls)

    grab_every_scroll_area(False)
    ungrabbed = {id(t) for kind, t in scroller_calls if kind == "ungrab"}
    assert ungrabbed == grabbed, "leaving phone layout must give back every grab"
    assert not any(a.viewport().property("_awDragScroll") for a in scrolls)


def test_a_drag_on_a_widget_that_reads_its_own_drags_is_left_to_it(qtbot):
    """A colour picker's square, an Image Table chip: classes that read mouse
    moves themselves keep a drag that starts on them, as a plate does."""
    from widgets.color_picker_popover import SvSquare
    area, label, _plate, _slider = _form(qtbot)
    square = SvSquare()
    area.widget().layout().insertWidget(0, square)
    QApplication.processEvents()
    set_drag_scroll(area.viewport(), True)
    assert not _starts_a_scroll(area, square, QPoint(5, 5)), \
        "a drag on the colour square would scroll the panel instead of picking"
    assert _starts_a_scroll(area, label, QPoint(5, 5))


def test_a_drag_on_a_plot_scrolls_only_a_plot_page(qtbot):
    """On a plot page a swipe on the plot is how the page scrolls
    (``_swipe_plot_area`` marks its viewport); anywhere else — a dialog's
    plot — the drag is the plot's."""
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
    area, _label, _plate, _slider = _form(qtbot)
    canvas = FigureCanvasQTAgg(Figure())
    canvas.setMinimumHeight(200)
    area.widget().layout().insertWidget(0, canvas)
    QApplication.processEvents()
    set_drag_scroll(area.viewport(), True)
    centre = QPoint(canvas.width() // 2, canvas.height() // 2)
    assert not _starts_a_scroll(area, canvas, centre)
    area.viewport().setProperty("_awSwipePlots", True)
    assert _starts_a_scroll(area, canvas, centre)


def _page_with_log(qtbot, lines: int):
    """A drag-scrolled page holding a log that scrolls by a drag of its own —
    Analyze's Progress page and every Datasets tool page are this shape."""
    from PySide6.QtWidgets import QTextEdit
    area = QScrollArea()
    qtbot.addWidget(area)
    area.resize(360, 400)
    area.setWidgetResizable(True)
    inner = QWidget()
    lay = QVBoxLayout(inner)
    head = QLabel("Run")
    log = QTextEdit()
    log.setReadOnly(True)
    log.setFixedHeight(150)
    log.setPlainText("\n".join(f"line {i}" for i in range(lines)))
    tail = QLabel("…")
    tail.setFixedHeight(1200)
    for w in (head, log, tail):
        lay.addWidget(w)
    area.setWidget(inner)
    area.show()
    qtbot.waitExposed(area)
    set_drag_scroll(area.viewport(), True)
    set_drag_scroll(log.viewport(), True)
    QApplication.processEvents()
    return area, head, log


def test_a_drag_on_a_log_that_scrolls_is_the_log_s_not_the_page_s(qtbot):
    """Reported: in phone layout Analyze's log could not be scrolled. Measured
    in the real window, a swipe on it moved the Progress page (688 -> 568) and
    the log 6 px: both viewports took the gesture and the page's won."""
    area, head, log = _page_with_log(qtbot, lines=400)
    assert log.verticalScrollBar().maximum() > 0
    centre = log.viewport().rect().center()
    assert not _starts_a_scroll(area, log.viewport(), centre), \
        "a drag on the log would scroll the page around it"
    assert _starts_a_scroll(area, head, QPoint(5, 5)), \
        "a drag beside the log must still scroll the page"


def test_a_log_with_nothing_to_scroll_leaves_the_drag_to_the_page(qtbot):
    area, _head, log = _page_with_log(qtbot, lines=2)
    assert log.verticalScrollBar().maximum() == 0
    assert _starts_a_scroll(area, log.viewport(), log.viewport().rect().center()), \
        "an empty log would be a dead spot on the page"


def test_a_log_that_does_not_scroll_by_a_drag_leaves_it_to_the_page(qtbot):
    area, _head, log = _page_with_log(qtbot, lines=400)
    set_drag_scroll(log.viewport(), False)
    QApplication.processEvents()
    assert _starts_a_scroll(area, log.viewport(), log.viewport().rect().center())


def test_an_unwrapped_log_with_only_sideways_range_leaves_the_drag_to_the_page(qtbot):
    """A page drag is vertical: a few long unwrapped lines give the log horizontal range only, and that must not
    make it a dead spot to swipe the page from."""
    from PySide6.QtWidgets import QTextEdit
    area, _head, log = _page_with_log(qtbot, lines=2)
    log.setLineWrapMode(QTextEdit.NoWrap)
    log.setPlainText("x" * 2000 + "\nshort")
    QApplication.processEvents()
    assert log.verticalScrollBar().maximum() == 0 and log.horizontalScrollBar().maximum() > 0
    assert _starts_a_scroll(area, log.viewport(), log.viewport().rect().center())
