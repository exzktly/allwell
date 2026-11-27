"""Dataset tools: the five commands about the open dataset, in one named place.

At the desk they are the last group of the dataset menu; in phone layout a
full-screen list behind ⋯. Each test triggers the real entry and asserts the
command's handler ran — or, for a reset in the phone's list, that it did
**not** until the alert was confirmed.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox

from ui import dataset_tools
from ui.dataset_tools import DatasetToolsList, Tool

LABELS = ["Copy analysis…", "Reveal folder", "Storage…", "Reset view…", "Reset analysis…"]


def _tools(ran: list[str]) -> list[Tool]:
    return [Tool(label, f"About {label}", lambda name=label: ran.append(name),
                 destructive=label.startswith("Reset"))
            for label in LABELS]


def test_the_window_lists_exactly_the_five_commands_with_the_resets_marked_destructive():
    """Read off the window's own list, without building the window: the
    labels, their order, and which two ask first."""
    import ast

    from tests._repo_scan import REPO_ROOT, parse_source
    tree = parse_source(REPO_ROOT / "allwell.py")
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_dataset_tools")
    calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "Tool"]
    listed = [(c.args[0].value, any(k.arg == "destructive" and k.value.value is True for k in c.keywords))
              for c in calls]
    assert listed == [("Copy analysis…", False), ("Reveal folder", False), ("Storage…", False),
                      ("Reset view…", True), ("Reset analysis…", True)]


def test_the_desk_s_dataset_menu_ends_in_the_tools_and_each_reaches_its_command(qtbot):
    from review.core import app_shell_view
    from tests._minimal_shell import minimal_shell
    ctx, window = minimal_shell(qtbot)
    window._ctx = ctx
    window._browse = lambda: None
    ran: list[str] = []
    ctx.widgets.register(app_shell_view.DATASET_TOOLS_KEY, lambda: _tools(ran))
    menu = QMenu(window)
    app_shell_view._fill_dataset_menu(window, menu)
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert texts[0] == "Open…"
    assert texts[-6:] == [dataset_tools.TITLE, *LABELS]
    caption = next(a for a in menu.actions() if a.text() == dataset_tools.TITLE)
    assert not caption.isEnabled(), "the caption is a heading, not a command"
    for label in LABELS:
        next(a for a in menu.actions() if a.text() == label).trigger()
    assert ran == LABELS
    assert all(a.menu() is None for a in menu.actions()), "a menu holds commands, one level deep"


def test_with_no_tools_registered_the_menu_has_no_empty_group(qtbot):
    from review.core import app_shell_view
    from tests._minimal_shell import minimal_shell
    ctx, window = minimal_shell(qtbot)
    window._ctx = ctx
    window._browse = lambda: None
    menu = QMenu(window)
    app_shell_view._fill_dataset_menu(window, menu)
    assert dataset_tools.TITLE not in [a.text() for a in menu.actions()]


@pytest.fixture
def listing(qtbot):
    ran: list[str] = []
    dialog = DatasetToolsList(None, _tools(ran))
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog, ran


def test_a_plain_tool_in_the_phone_s_list_runs_after_the_list_has_closed(listing):
    dialog, ran = listing
    dialog.buttons["Reveal folder"].click()
    assert not dialog.isVisible(), "the tool's own window would open from this one"
    QApplication.processEvents()
    assert ran == ["Reveal folder"]


@pytest.mark.parametrize("label", ["Reset view…", "Reset analysis…"])
def test_a_reset_in_the_phone_s_list_does_nothing_until_it_is_confirmed(listing, monkeypatch, label):
    dialog, ran = listing
    asked: list[str] = []

    def _answer(reply):
        def question(_parent, title, text, *_a, **_k):
            asked.append(text)
            return reply
        return staticmethod(question)

    monkeypatch.setattr(QMessageBox, "question", _answer(QMessageBox.StandardButton.Cancel))
    dialog.buttons[label].click()
    QApplication.processEvents()
    assert ran == [] and dialog.isVisible(), "a reset ran, or the list closed, without a yes"
    assert asked and asked[0].startswith(f"About {label}")
    monkeypatch.setattr(QMessageBox, "question", _answer(QMessageBox.StandardButton.Yes))
    dialog.buttons[label].click()
    QApplication.processEvents()
    assert ran == [label] and not dialog.isVisible()


def test_the_window_registers_its_tools_under_the_key_the_menu_reads():
    """Two literals in two files: the window registers under a string, the
    menu reads a constant. Held together here, since nothing else would
    notice them drift apart — the menu would simply lose its last group."""
    from review.core import app_shell_view
    from tests._repo_scan import REPO_ROOT
    source = (REPO_ROOT / "allwell.py").read_text()
    assert f'widgets.register("{app_shell_view.DATASET_TOOLS_KEY}", self._dataset_tools)' in source
