"""A menu built for one opening is gone once it closes.

Every context and drop-down menu in the app is built when it is opened,
parented to a widget that lives as long as its window — a row, a button, a
dialog. Qt frees a child only with its parent, so each opening left another
menu and its actions alive: five openings of a job-queue row's menu left ten
``QMenu`` objects (each with its chain submenu). ``widgets.menu_exec.
exec_transient_menu`` opens the menu and then deletes it.

``tests/test_menu_exec_seam.py`` requires every menu built in the function that
opens it to go through that call; these tests check the call does what it says,
through the real widgets a user clicks.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QMenu, QWidget  # noqa: E402

from widgets import menu_exec  # noqa: E402


def _flush_deletes() -> None:
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.fixture
def opened(monkeypatch):
    """Stand in for the blocking ``QMenu.exec``: record each menu, choose nothing."""
    menus: list[QMenu] = []

    def _exec(menu, _pos):
        menus.append(menu)
        return None

    monkeypatch.setattr(menu_exec, "exec_menu", _exec)
    return menus


def test_a_job_row_s_menu_does_not_pile_up(qapp, opened):
    from datasets.job_queue.row import JobRow
    from services.job_queue import JobSpec, JobState

    row = JobRow(JobSpec(id="j-1", kind="subset", label="Subset", spec={},
                         state=JobState.FAILED))
    for _ in range(5):
        row._open_menu()
    _flush_deletes()
    assert len(opened) == 5
    assert row.findChildren(QMenu) == []


def test_a_checkable_dropdown_s_menu_does_not_pile_up(qapp, opened):
    from widgets.checkable_dropdown import CheckableDropdown

    dropdown = CheckableDropdown()
    for _ in range(5):
        dropdown._open_menu()
    _flush_deletes()
    assert len(opened) == 5
    assert dropdown.findChildren(QMenu) == []


def test_the_chosen_action_is_still_readable_when_the_call_returns(qapp, monkeypatch):
    owner = QWidget()
    menu = QMenu(owner)
    action = menu.addAction("Pick me")
    monkeypatch.setattr(menu_exec, "exec_menu", lambda m, _pos: action)

    chosen = menu_exec.exec_transient_menu(menu, None)
    assert chosen.text() == "Pick me"
    _flush_deletes()
    assert owner.findChildren(QMenu) == []


def test_a_menu_whose_parent_went_with_the_choice_is_left_alone(qapp, monkeypatch):
    """Choosing "Remove" can delete the chip the menu hangs off, and the menu
    with it; disposing of it afterwards must not raise."""
    import shiboken6

    owner = QWidget()
    menu = QMenu(owner)

    def _choose_and_delete_parent(m, _pos):
        shiboken6.delete(owner)
        return None

    monkeypatch.setattr(menu_exec, "exec_menu", _choose_and_delete_parent)
    menu_exec.exec_transient_menu(menu, None)  # must not raise
    assert not shiboken6.isValid(menu)
