"""Analyze in phone layout: the form is the page, the run a strip above it.

Reported: in phone layout the Analyze pane was "basically unusable" — it opened
on an idle dashboard and an empty log, its whole form was behind ☰, and Run sat
at the foot of a ~3,500 px column inside that drawer. ``analyze/phone_view.py``
rearranges the same widgets into two views (Settings, Progress) and moves the
run controls to a bar at the bottom of the pane; leaving phone layout puts
everything back.

The real window's wiring (the page button, the rail collapse leaving the form
alone) is pinned in ``test_phone_layout_app.py``; this file pins the view
itself on a bare ``AnalyzeTab``. So is the plate being laid out afresh when
the strip leaves its compact form (``PlateProgress.setCompact``): a bare pane
could not reproduce the stale grid the real window drew, so a test here would
pass with the fix deleted.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from analyze.phone_view import PROGRESS, SETTINGS
from core import phone_layout
from core.panes import PANEL_IS_PAGE


@pytest.fixture
def tab(qtbot):
    from analyze.tab import AnalyzeTab
    t = AnalyzeTab()
    qtbot.addWidget(t)
    t.resize(420, 900)
    t.show()
    qtbot.waitExposed(t)
    yield t
    phone_layout.set_on(False)


def _pump() -> None:
    for _ in range(5):
        QApplication.processEvents()


def test_phone_layout_makes_the_form_the_page_with_run_at_the_foot(tab):
    form = tab._left_sidebar
    splitter = form.parentWidget()
    run_row = tab._run_btn.parentWidget()
    home = run_row.parentWidget()
    view = tab._phone_view

    phone_layout.set_on(True)
    _pump()
    assert view.current() == SETTINGS
    assert splitter.orientation() == Qt.Vertical
    assert splitter.indexOf(form) == 1, "the run strip is not above the form"
    assert form.isVisible() and form.property(PANEL_IS_PAGE)
    assert form.width() == splitter.width(), "the form kept its desk rail width"
    assert tab._dashboard.isCompact() and not tab._log.isVisible()
    lay = tab.layout()
    assert lay.indexOf(run_row) == lay.count() - 1, "Run is not at the foot of the pane"
    assert run_row.objectName() == "PhoneRunBar"

    phone_layout.set_on(False)
    _pump()
    assert splitter.orientation() == Qt.Horizontal
    assert splitter.indexOf(form) == 0
    assert run_row.parentWidget() is home and run_row.objectName() == ""
    assert not tab._dashboard.isCompact() and not form.property(PANEL_IS_PAGE)
    assert form.isVisible() and tab._log.isVisible()


def test_progress_shows_the_run_and_hides_the_form(tab):
    view = tab._phone_view
    phone_layout.set_on(True)
    _pump()
    view.show_view(PROGRESS)
    _pump()
    assert view.current() == PROGRESS
    assert not tab._left_sidebar.isVisible()
    assert not tab._dashboard.isCompact() and tab._log.isVisible()
    view.show_view(SETTINGS)
    _pump()
    assert tab._left_sidebar.isVisible() and tab._dashboard.isCompact()


def test_a_run_starting_shows_progress(tab):
    """The Stop button turning enabled is what every way of starting a run
    shares; it, not the Run button, is what the view watches."""
    view = tab._phone_view
    phone_layout.set_on(True)
    _pump()
    tab._stop_btn.setEnabled(False)
    assert view.current() == SETTINGS
    tab._stop_btn.setEnabled(True)
    assert view.current() == PROGRESS
    assert not tab._left_sidebar.isVisible()


def test_a_run_starting_at_the_desk_changes_nothing(tab):
    view = tab._phone_view
    tab._stop_btn.setEnabled(False)
    tab._stop_btn.setEnabled(True)
    assert view.current() == SETTINGS
    assert tab._left_sidebar.isVisible() and not tab._dashboard.isCompact()



def _y_in(tab, w) -> tuple[int, int]:
    top = w.mapTo(tab, w.rect().topLeft()).y()
    return top, top + w.height()


def test_progress_on_a_short_screen_scrolls_rather_than_covering_the_plate(tab):
    """Reported: on a phone, Analyze's plate was cut off by the log and could
    not be viewed. At full phone width the plate alone needs ~250 px here (540
    in the real window), so on a short page the dashboard and log do not fit;
    the column was squeezed below its minimum, the dashboard cut short and the
    log drawn over the plate. Progress now scrolls: the dashboard keeps its
    full height and the log starts below the plate."""
    from PySide6.QtWidgets import QScrollArea, QWidget
    view = tab._phone_view
    dash = tab._dashboard
    # A child placed by geometry, as in the real window: a top-level pane
    # grows to its own minimum height and the page is never short.
    host = QWidget()
    try:
        host.resize(390, 360)
        tab.setParent(host)
        tab.setGeometry(0, 0, 390, 360)
        host.show()
        phone_layout.set_on(True)
        dash.setStacked(True)   # the shell's half of phone layout
        _pump()
        assert tab.height() == 360
        view.show_view(PROGRESS)
        _pump()

        _, plate_bottom = _y_in(tab, dash._plate)
        _, dash_bottom = _y_in(tab, dash)
        log_top, _ = _y_in(tab, tab._log)
        assert dash.height() >= dash.minimumSizeHint().height(), \
            f"the dashboard was squeezed to {dash.height()} of {dash.minimumSizeHint().height()} px"
        assert plate_bottom <= dash_bottom, "the plate runs out of the dashboard"
        assert log_top >= plate_bottom, \
            f"the log (from y={log_top}) is drawn over the plate (to y={plate_bottom})"
        scroll = view._scroll
        assert isinstance(scroll, QScrollArea) and scroll.isVisible()
        assert dash.isVisibleTo(scroll) and scroll.widget().isAncestorOf(dash)
        bar = scroll.verticalScrollBar()
        assert bar.maximum() > 0, "the column does not fit, so it must scroll"
        bar.setValue(bar.maximum())
        _pump()
        _, log_bottom = _y_in(tab, tab._log)
        assert log_bottom <= scroll.mapTo(tab, scroll.rect().bottomLeft()).y() + 1, \
            "scrolled to the end, the log is still out of reach"
    finally:
        # The pane is the fixture's to tear down, not the host's to delete.
        tab.setParent(None)
        host.deleteLater()


def test_settings_keeps_the_strip_unscrolled_and_desk_gets_the_column_back(tab):
    view = tab._phone_view
    form = tab._left_sidebar
    splitter = form.parentWidget()
    phone_layout.set_on(True)
    _pump()
    view.show_view(PROGRESS)
    _pump()
    view.show_view(SETTINGS)
    _pump()
    right = view._parts()[2]
    assert splitter.widget(0) is right, "the Settings strip is not straight in the splitter"
    assert not view._scroll.isVisible()
    view.show_view(PROGRESS)
    _pump()
    phone_layout.set_on(False)
    _pump()
    assert [splitter.widget(i) for i in range(splitter.count())] == [form, right]
    assert right.isVisible() and tab._dashboard.isVisible() and tab._log.isVisible()
    assert view._scroll.widget() is None and not view._scroll.isVisible()


def test_a_drag_on_a_status_plate_scrolls_the_page(qtbot):
    """The run dashboard's plate fills most of the Progress page; if it kept
    every drag that started on it, there would be almost nowhere left to
    scroll from. A plate that selects wells still keeps its drags."""
    from ui.phone_shell import _plate_owns_drag
    from widgets.plate_geometry import apply_plate_geometry
    from widgets.well_plate_selector import WellPlateSelector
    status, picker = WellPlateSelector(), WellPlateSelector()
    qtbot.addWidget(status)
    qtbot.addWidget(picker)
    apply_plate_geometry(status, role="status")
    apply_plate_geometry(picker, role="picker")
    assert not _plate_owns_drag(status) and not _plate_owns_drag(status._grid)
    assert _plate_owns_drag(picker) and _plate_owns_drag(picker._grid)
