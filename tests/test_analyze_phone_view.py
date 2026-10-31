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


@pytest.fixture
def hosted(tab):
    """The pane as a child placed by geometry, as in the real window: a
    top-level pane grows to its own minimum height and the page is never
    short. Yields ``place(w, h)``, which sizes the page."""
    from PySide6.QtWidgets import QWidget
    host = QWidget()
    host.resize(390, 360)
    tab.setParent(host)
    host.show()
    phone_layout.set_on(True)
    tab._dashboard.setStacked(True)   # the shell's half of phone layout
    _pump()

    def place(w: int, h: int) -> None:
        # Placed once phone layout is on, as the shell lays the pane into the
        # page. Placed before, the geometry is clamped to the *desktop*
        # layout's minimum, which under the app's stylesheet is 361 px.
        host.resize(w, h)
        tab.setGeometry(0, 0, w, h)
        _pump()
        assert tab.height() == h

    try:
        yield place
    finally:
        # The pane is the fixture's to tear down, not the host's to delete.
        tab.setParent(None)
        host.deleteLater()


def _bottom_of(tab, w) -> int:
    return _y_in(tab, w)[1]


def test_progress_on_a_short_screen_fits_the_page_without_scrolling(tab, hosted):
    """Reported: in phone layout the log box and the page's scroll bar were
    clipped by the run controls, and the outer scroll bar read as part of the
    log. Progress used to put the dashboard and log in a scroll area; now the
    page fits: the plate shrinks, at its own aspect, so the dashboard, a
    readable log and the run bar are all on screen at once."""
    from PySide6.QtWidgets import QScrollArea
    from analyze.phone_view import LOG_MIN_LINES
    view = tab._phone_view
    dash, log, plate = tab._dashboard, tab._log, tab._dashboard._plate
    run_bar = tab._run_btn.parentWidget()
    # Short enough that the full-width plate (258 px here, ~540 in the real
    # window) does not fit, tall enough that the plate's floor does.
    hosted(390, 480)
    view.show_view(PROGRESS)
    _pump()

    column = view._parts()[2]
    assert not any(isinstance(a, QScrollArea) and a.isAncestorOf(dash)
                   for a in tab.findChildren(QScrollArea)), \
        "the Progress page is inside a scroll area again"
    plate_top, plate_bottom = _y_in(tab, plate)
    log_top, log_bottom = _y_in(tab, log)
    bar_top = _y_in(tab, run_bar)[0]
    assert plate_bottom <= _bottom_of(tab, dash) <= log_top, \
        f"the plate (to y={plate_bottom}) runs into the log (from y={log_top})"
    assert log_bottom <= bar_top == _bottom_of(tab, column), \
        f"the log (to y={log_bottom}) runs under the run bar (from y={bar_top})"
    assert _bottom_of(tab, run_bar) == tab.height()
    lines = (log.height() - 2 * log.frameWidth()) // log.fontMetrics().lineSpacing()
    assert lines >= LOG_MIN_LINES, f"the log keeps {lines} lines, not {LOG_MIN_LINES}"
    # Shrunk by width and height together: a height cap alone at full width
    # squeezes the wells flat and runs the plate's labels together.
    assert plate.height() == plate.heightForWidth(plate.width())
    natural = plate.heightForWidth(dash.plate_room_width())
    assert plate.maximumHeight() < natural, "the plate did not give way"


def test_progress_on_a_tall_screen_keeps_the_full_width_plate(tab, hosted):
    """The fit only takes what the page lacks: with room to spare the plate is
    as wide as the column, as it was before the fit existed."""
    view = tab._phone_view
    plate = tab._dashboard._plate
    hosted(390, 1400)
    view.show_view(PROGRESS)
    _pump()
    natural = plate.heightForWidth(tab._dashboard.plate_room_width())
    assert plate.maximumHeight() == natural


def test_the_fit_follows_the_page_when_it_grows_again(tab, hosted):
    """A rotation or a resize refits: the plate shrunk for a short page grows
    back on a tall one rather than keeping the cap it was given."""
    view = tab._phone_view
    plate = tab._dashboard._plate
    hosted(390, 480)
    view.show_view(PROGRESS)
    _pump()
    natural = plate.heightForWidth(tab._dashboard.plate_room_width())
    assert plate.maximumHeight() < natural
    hosted(390, 1400)
    assert plate.maximumHeight() == natural


def test_leaving_phone_layout_hands_back_the_log_and_plate_sizes(tab, hosted):
    from widgets.plate_geometry import apply_plate_geometry
    view = tab._phone_view
    dash, log, plate = tab._dashboard, tab._log, tab._dashboard._plate
    log_min = log.minimumHeight()
    hosted(390, 360)
    view.show_view(PROGRESS)
    _pump()
    assert log.minimumHeight() > log_min
    dash.setStacked(False)
    phone_layout.set_on(False)
    _pump()
    assert log.minimumHeight() == log_min
    expected = (plate.maximumWidth(), plate.maximumHeight())
    apply_plate_geometry(plate, role="status")
    assert (plate.maximumWidth(), plate.maximumHeight()) == expected, \
        "the plate kept the phone fit's caps at the desk"


def test_settings_keeps_the_strip_and_desk_gets_the_column_back(tab):
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
    assert splitter.widget(0) is right
    assert right.maximumHeight() < 1000, "the Settings strip is not capped at its height"
    view.show_view(PROGRESS)
    _pump()
    phone_layout.set_on(False)
    _pump()
    assert [splitter.widget(i) for i in range(splitter.count())] == [form, right]
    assert right.isVisible() and tab._dashboard.isVisible() and tab._log.isVisible()


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


def test_sideways_the_stage_rail_sits_beside_the_plate_without_its_bars(qtbot):
    """Sideways the page is ~264 pt tall; with the stage rail above it the
    plate had under a hundred points left. Beside it, the rail takes its
    minimum width — its per-stage bars go, the dot and count saying the same —
    and the plate the rest of the row. Upright, all of it comes back."""
    from PySide6.QtWidgets import QBoxLayout
    from widgets.run_dashboard import RunDashboard
    dash = RunDashboard()
    qtbot.addWidget(dash)
    dash.resize(440, 300)
    dash.show()
    dash.setStacked(True)
    bars = [row.bar for row in dash._rail.values()]
    assert bars and all(b.isVisibleTo(dash) for b in bars)
    assert dash._body_lay.direction() == QBoxLayout.TopToBottom
    upright_room = dash.plate_room_width()

    dash.setRailBeside(True)
    _pump()
    assert dash._body_lay.direction() == QBoxLayout.LeftToRight
    assert not any(b.isVisibleTo(dash) for b in bars)
    rail = dash._rail_host
    assert rail.maximumWidth() == rail.minimumSizeHint().width()
    assert dash.plate_room_width() == (dash._body.width() - rail.width()
                                       - dash._body_lay.spacing())
    assert dash.plate_room_width() < upright_room

    dash.setRailBeside(False)
    _pump()
    assert dash._body_lay.direction() == QBoxLayout.TopToBottom
    assert all(b.isVisibleTo(dash) for b in bars)
    assert dash.plate_room_width() == upright_room
