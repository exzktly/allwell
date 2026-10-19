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
    sheet = phone_panel_sheet.sheet_of(win)
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
    assert not phone_panel_sheet.sheet_of(win).isOpen()
    assert dock.parentWidget() is win and dock.maximumWidth() == 360
    assert not dock.isVisible(), "the sheet closing is the panel closing"


def test_the_window_holds_one_sheet_and_no_loose_attribute(qtbot, phone):
    """The window keeps no ``_aw_*`` attribute for its sheet (CLAUDE.md §5: state
    lives on typed objects, not loose attributes on the main window); the sheet
    is the window's one child Drawer marked ``phonePanelSheet``, reused by every
    later panel."""
    win, lay, dock, panel, controls = _window(qtbot)
    phone_panel_sheet.present(dock)
    dock.setVisible(False)
    phone_panel_sheet.present(dock)
    loose = [name for name in vars(win) if name.startswith("_aw_")]
    assert loose == [], f"the window carries loose attributes: {loose}"
    sheets = [d for d in win.findChildren(Drawer) if d.property("phonePanelSheet")]
    assert len(sheets) == 1, f"{len(sheets)} panel sheets on one window"
    assert sheets[0].isOpen() and dock.parentWidget() is sheets[0].contentWidget()


def test_mounting_a_panel_in_phone_layout_never_squeezes_the_plot(qtbot, phone):
    """Reported: opening Properties in phone layout crushed the bar plots for
    good. ``mount_widget_in_plot_dock`` showed the dock *beside* the figure and
    only then lent it to the sheet, so for that instant the 360 px panel took
    its width out of the plot — and the style pass that runs as Properties opens
    fitted the titles and margins to the squeezed figure, where they stayed.

    Driven through the real helpers a plot tab is built from: the dock must
    never be shown while it sits in the plot, and the plot must never get
    narrower than the window."""
    from PySide6.QtCore import QEvent, QObject

    from review.core.ui_helpers import make_plot_with_right_dock, mount_widget_in_plot_dock

    win = QWidget()
    qtbot.addWidget(win)
    win.resize(390, 780)
    plot_area, _plot_layout, dock = make_plot_with_right_dock(win)
    host = dock.parentWidget()
    win.show()
    qtbot.waitUntil(lambda: plot_area.width() == win.width(), timeout=2000)

    class _Spy(QObject):
        def __init__(self):
            super().__init__()
            self.shown_in_plot = 0
            self.plot_widths = []

        def eventFilter(self, obj, ev):
            if obj is dock and ev.type() == QEvent.Show and dock.parentWidget() is host:
                self.shown_in_plot += 1
            if obj is plot_area and ev.type() == QEvent.Resize:
                self.plot_widths.append(ev.size().width())
            return False

    spy = _Spy()
    dock.installEventFilter(spy)
    plot_area.installEventFilter(spy)

    panel = QWidget()
    panel.setFixedWidth(360)
    mount_widget_in_plot_dock(dock, panel, 360)
    sheet = phone_panel_sheet.sheet_of(win)
    assert sheet is not None and sheet.isOpen()
    assert dock.parentWidget() is sheet.contentWidget() and dock.isVisible()
    qtbot.wait(50)

    assert spy.shown_in_plot == 0, "the dock was shown beside the plot before the sheet took it"
    assert all(w == win.width() for w in spy.plot_widths), \
        f"the plot was squeezed while the panel opened: widths {spy.plot_widths}"
    assert plot_area.width() == win.width()

    # And at the desk the dock still opens beside the plot.
    dock.setVisible(False)
    phone_layout.set_on(False)
    mount_widget_in_plot_dock(dock, panel, 360)
    assert dock.parentWidget() is host and dock.isVisible()
