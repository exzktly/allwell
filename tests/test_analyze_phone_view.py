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

