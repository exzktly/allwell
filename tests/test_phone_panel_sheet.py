"""In phone layout a plot's Properties panel opens as a full-width sheet.

Reported: in phone layout, tapping Properties from the ⚙ Controls sheet above
the bar plot opened the panel *under* that sheet, and the panel was not usable
on a phone — it is ``Metrics.dock_w`` (360 px) wide, so on a 390 px window it
squeezed the figure to a sliver and ran off the edge.

``review.core.phone_panel_sheet.present`` now closes the Controls sheet, lends
the plot's dock to a Drawer covering the window's width with the panel's fixed
width lifted, and gives the dock back — at its index, with its widths — when the
panel's own ✕ hides it, or when phone layout ends. At the desk it does nothing.
The whole path through the real window is checked in
``tests/test_phone_layout_app.py``; this file holds the mechanism in the fast lane.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from core import phone_layout
from review.core import phone_panel_sheet
from widgets.drawer import Drawer


@pytest.fixture
def phone():
    phone_layout.set_on(True)
    yield
    phone_layout.set_on(False)


def _window(qtbot):
    """A window shaped like a plot tab: the figure, then a dock holding a
    360 px fixed-width panel, plus an open ⚙ Controls sheet over it."""
    win = QWidget()
    qtbot.addWidget(win)
    win.resize(390, 780)
    lay = QHBoxLayout(win)
    plot = QWidget(win)
    lay.addWidget(plot, 1)
    dock = QWidget(win)
    QVBoxLayout(dock)
    panel = QWidget(dock)
    panel.setFixedWidth(360)
    dock.layout().addWidget(panel)
    dock.setFixedWidth(360)
    lay.addWidget(dock, 0)
    controls = Drawer(win, edge="bottom", scrim=0.0)
    controls.setProperty("phoneSheet", True)
    win.show()
    controls.open()
    return win, lay, dock, panel, controls


def test_at_the_desk_the_dock_stays_beside_the_plot(qtbot):
    win, lay, dock, panel, controls = _window(qtbot)
    assert phone_panel_sheet.present(dock) is False
    assert dock.parentWidget() is win and controls.isOpen()


def test_the_panel_opens_full_width_over_the_controls_and_comes_back(qtbot, phone):
    win, lay, dock, panel, controls = _window(qtbot)
    index = lay.indexOf(dock)

    assert phone_panel_sheet.present(dock) is True
    sheet = win._aw_phone_panel_sheet
    assert not controls.isOpen(), "the Controls sheet must close, not cover the panel"
    assert sheet.isOpen() and dock.parentWidget() is sheet.contentWidget()
    assert dock.maximumWidth() > 360 and panel.maximumWidth() > 360
    qtbot.waitUntil(lambda: sheet.width() == win.width(), timeout=2000)

    # The panel's own ✕ hides the dock: the sheet closes and the dock goes home.
    dock.setVisible(False)
    assert not sheet.isOpen()
    assert dock.parentWidget() is win and lay.indexOf(dock) == index
    assert (dock.minimumWidth(), dock.maximumWidth()) == (360, 360)
    assert (panel.minimumWidth(), panel.maximumWidth()) == (360, 360)


def test_leaving_phone_layout_gives_the_dock_back(qtbot, phone):
    win, lay, dock, panel, controls = _window(qtbot)
    phone_panel_sheet.present(dock)
    phone_layout.set_on(False)
    assert not win._aw_phone_panel_sheet.isOpen()
    assert dock.parentWidget() is win and dock.maximumWidth() == 360
    assert not dock.isVisible(), "the sheet closing is the panel closing"
