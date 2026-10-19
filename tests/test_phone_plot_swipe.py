"""Phone layout: a swipe on a plot scrolls the page it is on.

Reported: on a phone over VNC the only way to scroll a plot page was its scroll
bar, a few pixels wide once the phone has shrunk the remote desktop. A
touch-drag reaches the app as a left-button mouse drag, which a scroll area
ignores. ``PhoneShell._swipe_plot_area`` gives the scroll area holding a plot
Qt's drag-to-scroll — on the main pages only (``band_roots``), and off again at
the desk.

The assertions are on the calls to ``QScroller``, not on a simulated swipe:
QTest stamps its synthetic mouse events from a clock of its own, QScroller reads
a drag's speed from those stamps, and a simulated swipe therefore scrolls or
not by timing. And ``QScroller.grabbedGesture`` cannot tell the two states
apart — it reads the same after ``ungrabGesture`` as before.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import QScrollArea, QScroller, QVBoxLayout, QWidget

from ui.phone_shell import PhoneShell


@pytest.fixture
def scroller_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(QScroller, "grabGesture",
                        lambda target, kind: calls.append(("grab", target, kind)))
    monkeypatch.setattr(QScroller, "ungrabGesture",
                        lambda target: calls.append(("ungrab", target)))
    return calls


def _plot_page(qtbot):
    """A main page (the band root) holding a scroll area holding a plot, the
    way a plot tab nests its canvas."""
    root = QWidget()
    qtbot.addWidget(root)
    QVBoxLayout(root)
    area = QScrollArea(root)
    root.layout().addWidget(area)
    inner = QWidget()
    QVBoxLayout(inner)
    canvas = FigureCanvasQTAgg(Figure())
    inner.layout().addWidget(canvas)
    area.setWidget(inner)
    return root, area, canvas


def _shell(roots):
    """Only what ``_swipe_plot_area`` and ``size_canvas`` read off the shell."""
    shell = SimpleNamespace(_h=SimpleNamespace(band_roots=lambda: roots),
                            pt=lambda n: int(n))
    shell._band_managed = lambda w: PhoneShell._band_managed(shell, w)
    shell._swipe_plot_area = lambda c, on: PhoneShell._swipe_plot_area(shell, c, on)
    return shell


def test_a_plot_on_a_main_page_scrolls_its_page_by_a_drag(qtbot, scroller_calls):
    root, area, canvas = _plot_page(qtbot)
    shell = _shell([root])
    PhoneShell._swipe_plot_area(shell, canvas, True)
    assert scroller_calls == [("grab", area.viewport(), QScroller.LeftMouseButtonGesture)]
    PhoneShell._swipe_plot_area(shell, canvas, False)
    assert scroller_calls[-1] == ("ungrab", area.viewport())


def test_sizing_a_canvas_for_the_phone_is_what_turns_it_on(qtbot, scroller_calls):
    """``size_canvas`` runs for every plot on the switch, on Show and from the
    sweep — so a plot built after the switch gets the swipe too. This drives
    the real entry point rather than the helper."""
    root, area, canvas = _plot_page(qtbot)
    shell = _shell([root])
    PhoneShell.size_canvas(shell, canvas, True)
    assert ("grab", area.viewport(), QScroller.LeftMouseButtonGesture) in scroller_calls
    PhoneShell.size_canvas(shell, canvas, False)
    assert scroller_calls[-1] == ("ungrab", area.viewport())


def test_a_plot_outside_the_main_pages_is_left_alone(qtbot, scroller_calls):
    """A plot in a dialog or a side panel is not a page the phone scrolls."""
    _root, _area, canvas = _plot_page(qtbot)
    elsewhere = QWidget()
    qtbot.addWidget(elsewhere)
    PhoneShell._swipe_plot_area(_shell([elsewhere]), canvas, True)
    assert scroller_calls == []


def test_a_plot_with_no_scroll_area_around_it_is_left_alone(qtbot, scroller_calls):
    root = QWidget()
    qtbot.addWidget(root)
    QVBoxLayout(root)
    canvas = FigureCanvasQTAgg(Figure())
    root.layout().addWidget(canvas)
    PhoneShell._swipe_plot_area(_shell([root]), canvas, True)
    assert scroller_calls == []
