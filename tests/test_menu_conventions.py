"""The menu conventions of the surfaces plan (row S26), each through the
control a user presses.

* A menu button draws exactly one arrow (CLAUDE.md §7.12): Quick Setup drew
  none, and Presets drew Qt's native indicator rather than the theme's.
* A menu is at most one level deep: the job row's *New job using this output*
  held a second level of pairings.
* The current entry of a list is marked, not ticked: the phone's page menu and
  a job's input list used checkable actions, which toggle off when pressed
  again.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QMenu, QWidget

from tests._layout_checks import draws_menu_indicator, draws_two_carets
from widgets.menu_marks import is_current


@pytest.fixture
def app_sheet(qapp):
    from ui import theme
    qapp.setStyleSheet(theme.qss())
    yield
    qapp.setStyleSheet("")


def test_presets_is_a_menu_button_with_one_arrow(qtbot, app_sheet):
    from analyze.presets import AnalyzePresetsMixin

    class Host(AnalyzePresetsMixin, QWidget):
        pass

    host = Host()
    qtbot.addWidget(host)
    button = host._build_presets_button(host)
    assert button.menu() is not None and button.property("hasMenu") is True
    assert "▾" not in button.text()
    assert draws_menu_indicator(button), "Presets opens a menu and nothing says so"
    assert not draws_two_carets(button)


def test_quick_setup_shows_an_arrow(qtbot, app_sheet):
    from PySide6.QtWidgets import QToolButton

    from review.features.batch_export import base_panel

    source = open(base_panel.__file__, encoding="utf-8").read()
    assert 'qs_btn.setProperty("hasMenu", True)' in source
    assert "menu-indicator { image: none" not in source
    # And the theme draws that property's chevron on a real button.
    button = QToolButton()
    qtbot.addWidget(button)
    button.setText("Quick Setup")
    button.setPopupMode(QToolButton.InstantPopup)
    button.setProperty("hasMenu", True)
    button.setMenu(QMenu(button))
    assert draws_menu_indicator(button) and not draws_two_carets(button)


def _depth(menu: QMenu) -> int:
    subs = [a.menu() for a in menu.actions() if a.menu() is not None]
    return 1 + max((_depth(m) for m in subs), default=0)


def test_the_job_row_menu_is_never_two_levels_deep(qapp):
    from datasets.job_queue.row import NEXT_STEP_MENU, JobRow
    from services.job_queue import JobSpec, JobState

    row = JobRow(JobSpec(id="j1", kind="convert_czi", label="day1", spec={}, state=JobState.PENDING))
    qapp.processEvents()
    row._others = [("j2", "Convert CZI — day2")]
    menu = row.build_menu()
    assert _depth(menu) <= 2, "a submenu holds a submenu"
    nxt = next(a.menu() for a in menu.actions() if a.text() == NEXT_STEP_MENU)
    texts = [a.text() for a in nxt.actions() if a.text()]
    assert any(t.endswith("with a folder you pick on the page…") for t in texts)
    assert any(" with " in t and "day2" in t for t in texts), texts


def test_a_job_s_input_list_marks_its_source_rather_than_ticking_it(qapp):
    from datasets.job_queue.row import JobRow
    from services.job_queue import JobSpec, JobState

    row = JobRow(JobSpec(id="j2", kind="subset", label="run", spec={}, state=JobState.PENDING))
    sub = QMenu()
    chosen = []
    row._add_sources(sub, [("j1", "Convert CZI — day1"), ("j3", "Convert CZI — day3")], [],
                     lambda pred: pred == "j3", chosen.append)
    entries = [a for a in sub.actions() if a.text()]
    assert not any(a.isCheckable() for a in entries)
    assert [is_current(a) for a in entries] == [False, True]
    assert entries[1].font().bold() and not entries[0].font().bold()
    entries[1].trigger()
    entries[1].trigger()
    assert chosen == ["j3", "j3"], "pressing the current source again un-chose it"
