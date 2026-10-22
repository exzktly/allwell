"""Phone layout's shell for the Datasets pane: its pages fit, its tools are
one tap away.

Reported: the Datasets tools were "basically unusable" on a phone. Two halves
live in ``ui/phone_shell.py``:

- every form page under ``PhoneHandles.fit_roots`` is watched (``_FitOnShow``)
  and fitted to the phone's width (``ui/phone_fit``) when it shows or its width
  changes — a tool page is shown by the pane's own rail, which phone layout does
  not hear — and given back its desk form when phone layout ends;
- in Datasets mode the page button lists the tools (``_fill_tool_menu``), which
  were otherwise reachable only from ☰; in Analyze mode it lists the pane's
  phone views (Settings, Progress).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import (
    QApplication, QBoxLayout, QHBoxLayout, QLineEdit, QMenu, QScrollArea,
    QVBoxLayout, QWidget,
)

from core import phone_layout
from ui.phone_shell import PhoneShell, _FitOnShow


@pytest.fixture
def phone():
    phone_layout.set_on(True)
    yield
    phone_layout.set_on(False)


def _wide_page(qtbot):
    root = QWidget()
    qtbot.addWidget(root)
    QVBoxLayout(root)
    area = QScrollArea(root)
    area.setWidgetResizable(True)
    root.layout().addWidget(area)
    content = QWidget()
    QVBoxLayout(content)
    row = QWidget()
    h = QHBoxLayout(row)
    for _ in range(3):
        e = QLineEdit()
        e.setMinimumWidth(160)
        h.addWidget(e)
    content.layout().addWidget(row)
    area.setWidget(content)
    root.resize(320, 500)
    root.show()
    qtbot.waitExposed(root)
    return root, area, row


def test_a_page_under_the_fit_roots_is_fitted_and_given_back(qtbot, phone):
    root, area, row = _wide_page(qtbot)
    shell = SimpleNamespace(_h=SimpleNamespace(fit_roots=lambda: [root, None]),
                            _refresh_tool_label=lambda: None)
    PhoneShell._fit_pages(shell)
    QApplication.processEvents()
    assert area.findChild(_FitOnShow) is not None, "the page is not watched"
    assert row.layout().direction() == QBoxLayout.TopToBottom, "the page was not fitted"

    phone_layout.set_on(False)
    PhoneShell._fit_pages(shell)
    assert row.layout().direction() == QBoxLayout.LeftToRight, \
        "leaving phone layout did not give the page back its desk form"


def test_a_page_shown_later_is_fitted_when_it_shows(qtbot, phone):
    """A Datasets tool page is shown by the pane's own rail; the watcher, not
    a refresh, is what fits it."""
    root, area, row = _wide_page(qtbot)
    root.hide()
    shell = SimpleNamespace(_h=SimpleNamespace(fit_roots=lambda: [root]),
                            _refresh_tool_label=lambda: None)
    PhoneShell._fit_pages(shell)
    assert row.layout().direction() == QBoxLayout.LeftToRight, \
        "a hidden page has no width to fit to yet"
    root.show()
    qtbot.waitExposed(root)
    QApplication.processEvents()
    assert row.layout().direction() == QBoxLayout.TopToBottom


def _bind(shell, *names):
    for name in names:
        setattr(shell, name, getattr(PhoneShell, name).__get__(shell))


def test_the_page_button_lists_the_tools_in_datasets_mode(qtbot):
    shown = []
    tools = ["Concatenate Datasets", "Convert CZI", "Job Queue"]
    host = QWidget()
    qtbot.addWidget(host)
    shell = SimpleNamespace(
        _h=SimpleNamespace(mode=lambda: "datasets", tool_names=lambda: tools,
                           current_tool=lambda: "Convert CZI", show_tool=shown.append),
        _page_menu=QMenu(host),
        refresh=lambda: None,
    )
    _bind(shell, "_fill_tool_menu", "_fill_named_menu", "_pick_tool")
    PhoneShell._fill_page_menu(shell)
    actions = shell._page_menu.actions()
    assert [a.text() for a in actions] == tools
    assert [a.text() for a in actions if a.isChecked()] == ["Convert CZI"]
    actions[2].trigger()
    assert shown == ["Job Queue"]


def test_the_page_button_lists_analyze_s_views_in_analyze_mode(qtbot):
    """Analyze's phone form has two views (``analyze/phone_view.py``); the
    page button names the one on screen and switches between them."""
    shown = []
    host = QWidget()
    qtbot.addWidget(host)
    shell = SimpleNamespace(
        _h=SimpleNamespace(mode=lambda: "analyze",
                           page_views=lambda: ["Settings", "Progress"],
                           page_view=lambda: "Settings",
                           show_page_view=shown.append),
        _page_menu=QMenu(host),
        refresh=lambda: None,
    )
    _bind(shell, "_fill_named_menu", "_pick_page_view")
    PhoneShell._fill_page_menu(shell)
    actions = shell._page_menu.actions()
    assert [a.text() for a in actions] == ["Settings", "Progress"]
    assert [a.text() for a in actions if a.isChecked()] == ["Settings"]
    actions[1].trigger()
    assert shown == ["Progress"]
