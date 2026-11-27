"""A button that opens a menu draws exactly one arrow.

Qt paints a menu indicator on any ``QToolButton`` / ``QPushButton`` that has a menu, unless a style sheet switches it
off. A label ending in "▾" on such a button therefore draws two arrows side by side, a large one in the text and a
small one in the corner — the defect that kept coming back, one button at a time (the queued-output button on
Analyze, the phone layout's page buttons, the Nanowells page's *Excluded (n)*). Each fix read one of the places the
indicator can be decided — the class, the popup mode, the theme's ``hasMenu`` rule, a ``::menu-indicator`` rule in
the button's own sheet — and the next button was decided somewhere else.

So the measurement is made in pixels: :func:`tests._layout_checks.draws_menu_indicator` renders the button, switches
its indicator off, renders it again and compares. ``tests/test_every_page_renders.py`` runs the same measurement
(``double_carets``) over every visible button on every page; this file covers the shared menu-button factories,
which a page tour only reaches where a page happens to show one, and proves the measurement can tell the two cases
apart in both directions.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QMenu, QPushButton, QToolButton, QVBoxLayout, QWidget

from tests._layout_checks import double_carets, draws_menu_indicator, draws_two_carets
from ui import theme


@pytest.fixture
def host(qapp):
    """A widget carrying the application's style sheet, so a child button is styled as it is in the app."""
    widget = QWidget()
    widget.setStyleSheet(theme.qss())
    QVBoxLayout(widget)
    yield widget
    widget.deleteLater()


def _menu_tool_button(host, text, *, has_menu=False, hide_indicator=False):
    button = QToolButton(host)
    button.setText(text)
    button.setPopupMode(QToolButton.InstantPopup)
    button.setMenu(QMenu(button))
    if has_menu:
        button.setProperty("hasMenu", True)
    if hide_indicator:
        button.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")
    host.layout().addWidget(button)
    return button


def _arrows(button) -> int:
    """How many arrows *button* draws: the caret in its text, plus the menu indicator if one is painted."""
    return ("▾" in button.text()) + draws_menu_indicator(button)


# ── the measurement itself, in both directions ────────────────────────────────


def test_a_caret_in_the_text_beside_qt_s_indicator_is_two_arrows(host):
    """The Excluded (n) shape as it shipped: "▾" in the text, an InstantPopup tool button, nothing hiding the arrow."""
    button = _menu_tool_button(host, "Excluded (2) ▾")
    assert _arrows(button) == 2 and draws_two_carets(button)


def test_a_caret_beside_the_theme_s_chevron_is_two_arrows(host):
    """``hasMenu`` gives the theme's chevron, which is an indicator too — a caret in the text still doubles it."""
    button = _menu_tool_button(host, "Excluded (2) ▾", has_menu=True)
    assert _arrows(button) == 2 and draws_two_carets(button)


def test_a_push_button_with_a_menu_draws_qt_s_indicator_too(host):
    button = QPushButton("Save ▾", host)
    button.setMenu(QMenu(button))
    host.layout().addWidget(button)
    assert draws_two_carets(button)


def test_the_theme_s_chevron_alone_is_one_arrow(host):
    button = _menu_tool_button(host, "Excluded (2)", has_menu=True)
    assert _arrows(button) == 1 and not draws_two_carets(button)


def test_a_caret_with_the_indicator_switched_off_is_one_arrow(host):
    button = _menu_tool_button(host, "Export ▾", hide_indicator=True)
    assert _arrows(button) == 1 and not draws_two_carets(button)


def test_a_caret_on_a_button_with_no_menu_is_one_arrow(host):
    """A disclosure toggle ("Details ▾") opens no menu, so Qt draws it no indicator."""
    button = QToolButton(host)
    button.setText("Details ▾")
    host.layout().addWidget(button)
    assert _arrows(button) == 1 and not draws_two_carets(button)


def test_the_page_check_names_a_double_arrow_button_and_only_that_one(host):
    _menu_tool_button(host, "Excluded (2) ▾").setObjectName("Doubled")
    _menu_tool_button(host, "Excluded (2)", has_menu=True).setObjectName("Single")
    host.show()
    found = double_carets(host)
    assert [(f.check, f.widget) for f in found] == [("caret", "QToolButton#Doubled")]


# ── the shared factories every page builds its menu buttons with ──────────────


def test_make_action_menu_button_draws_one_arrow(host):
    from review.core.ui_helpers import make_action_menu_button

    for text in ("Save", "Save ▾"):
        button, _menu = make_action_menu_button(text, host)
        assert _arrows(button) == 1, text


def test_a_button_that_opens_an_inspector_page_draws_no_menu_arrow(host):
    """A panel's button opens a page of the inspector, not a menu: a value
    button ends in "›", a tab's root reads its name, and neither carries a
    "▾" or Qt's indicator — an arrow that says "menu" over something that is
    not one is the third convention this work removes.

    Wrong implementations: the caret left in the label, a ``QMenu`` still set
    on the button (Qt then draws its indicator), ``InstantPopup`` left on."""
    from review.core.inspector_panels import make_popover_button

    value, _c, _l = make_popover_button("Fold change ▾", host, tab="data", summary=lambda: "vs DMSO")
    bare, _c, _l = make_popover_button("Fit", host, tab="data")
    root, _c, _l = make_popover_button("Display ▾", host, root=True)
    assert value.text() == "Fold change: vs DMSO ›"
    assert bare.text() == "Fit ›"
    assert root.text() == "Display"
    for button in (value, bare, root):
        assert button.menu() is None
        assert "▾" not in button.text()
        assert not draws_menu_indicator(button), button.text()


def test_the_nanowells_excluded_button_draws_one_arrow(host):
    """*Excluded (n)* as the page builds and fills it: the theme's chevron, and no "▾" in the text."""
    from types import SimpleNamespace

    from review.features.nanowell.exclude_view import build_exclude_controls, fill_excluded

    registered = {}
    ctx = SimpleNamespace(widgets=SimpleNamespace(register=lambda key, w: registered.__setitem__(key, w)))
    well_cb = SimpleNamespace(currentData=lambda: "A01")
    controls = build_exclude_controls(ctx, well_cb)
    button = controls.excluded
    button.setParent(host)
    host.layout().addWidget(button)
    fill_excluded(button, ["A01", "A02"], ctx)
    assert button.text() == "Excluded (2)"
    assert _arrows(button) == 1 and draws_menu_indicator(button)
    controls.exclude.deleteLater()
