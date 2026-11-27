"""A page's head and foot (``ui/page_chrome.py``).

The foot row's one promise is the reason beside a disabled primary action: shown while the action is disabled,
gone when it is enabled — following the button's own enabled state, which is what a page changes, rather than a
second call the page has to remember to make. Pressed through the real button, the way a page enables it.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from ui import buttons
from ui.page_chrome import ActionRow, PageHeader


def _settle():
    for _ in range(3):
        QApplication.processEvents()


def test_the_reason_shows_while_the_action_is_disabled_and_goes_when_it_is_not(qtbot):
    run = buttons.primary("Run pipeline")
    row = ActionRow(run, others=(buttons.default("Add to queue"),))
    qtbot.addWidget(row)
    row.show()
    run.setEnabled(False)
    row.setDisabledReason("Choose an input folder to continue.")
    _settle()
    assert row.reason() == "Choose an input folder to continue."
    run.setEnabled(True)
    _settle()
    assert row.reason() == ""
    run.setEnabled(False)
    _settle()
    assert row.reason() == "Choose an input folder to continue."


def test_an_enabled_action_shows_no_reason_whatever_was_set(qtbot):
    run = buttons.primary("Export")
    row = ActionRow(run)
    qtbot.addWidget(row)
    row.show()
    row.setDisabledReason("Add a group first.")
    _settle()
    assert row.reason() == ""


def test_the_primary_action_is_last_and_its_neighbours_before_it(qtbot):
    run = buttons.primary("Create subset")
    queue = buttons.default("Add to queue")
    row = ActionRow(run, others=(queue,))
    qtbot.addWidget(row)
    row.resize(600, 40)
    row.show()
    _settle()
    assert queue.geometry().right() < run.geometry().left()
    assert run.geometry().right() >= row.width() - 1


def test_the_header_carries_a_page_title_a_detail_and_its_commands(qtbot):
    reload = buttons.quiet("Reload")
    head = PageHeader("plate_0412", detail="/data/plates/plate_0412", commands=(reload,))
    qtbot.addWidget(head)
    head.resize(600, 60)
    head.show()
    _settle()
    assert head._title.property("textRole") == "pageTitle"
    assert head._detail.property("textRole") == "number" and head._detail.isVisible()
    assert reload.geometry().left() > head._title.geometry().right()
    head.setDetail("")
    assert not head._detail.isVisibleTo(head)
