"""Analyze reports the form section the user has scrolled to.

The Analyze pane is one tall scrolling form, not a stack of pages, so the help
drawer cannot ask it "which page are you on" — ``current_help_section`` answers
by scroll position instead: the last section whose header has passed the top of
the viewport. Every title it returns is a key in
``help_topics.ANALYZE_SECTION_HELP``, which is what turns it into a manual
heading.

Driven against a real ``AnalyzeTab`` rather than a stub. The mapping from a
title to a heading is pure and tested in ``test_help_target_routing.py``; what
needs a live widget is the geometry — a section's position mapped into the
scroll area's viewport is exactly the thing that cannot be reasoned about from
the source.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

import help_topics


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    import theme
    app.setStyleSheet(theme.qss())
    return app


@pytest.fixture(scope="module")
def analyze_tab(qapp):
    from analyze_tab import AnalyzeTab

    tab = AnalyzeTab()
    tab.resize(400, 700)
    tab.show()
    for _ in range(10):
        qapp.processEvents()
    return tab


def _settle(qapp, n: int = 10) -> None:
    for _ in range(n):
        qapp.processEvents()


def _scroll_to(qapp, tab, title: str) -> int:
    """Put *title*'s section header at the top of the viewport.

    Returns the scroll value actually reached — the bar clamps at its maximum,
    and the sections near the bottom of the form cannot be brought to the top.
    """
    container = tab._section_widgets[title]
    bar = tab._left_sidebar.verticalScrollBar()
    form = tab._left_sidebar.widget()
    bar.setValue(container.mapTo(form, container.rect().topLeft()).y())
    _settle(qapp)
    return bar.value()


def test_every_section_is_recorded(analyze_tab):
    """The record is what the lookup walks; an unrecorded section is invisible
    to it however well the map is filled in."""
    assert set(analyze_tab._section_widgets) == set(help_topics.ANALYZE_SECTION_HELP)


def test_sections_are_recorded_in_build_order(analyze_tab):
    """The scan stops at the first section below the fold, so the record's order
    has to be the form's visual order."""
    titles = list(analyze_tab._section_widgets)
    assert titles[0] == "Filename Schema"
    assert titles.index("Folders") > titles.index("Channel Tokens")
    assert titles[-1] == "Scratch Disk"


@pytest.mark.parametrize("title", ["Channel Tokens", "Folders"])
def test_scrolling_to_a_section_reports_that_section(qapp, analyze_tab, title):
    reached = _scroll_to(qapp, analyze_tab, title)
    container = analyze_tab._section_widgets[title]
    form = analyze_tab._left_sidebar.widget()
    wanted = container.mapTo(form, container.rect().topLeft()).y()
    assert reached == wanted, "the bar clamped — pick a section the form can reach"
    assert analyze_tab.current_help_section() == title


def test_top_of_the_form_reports_the_first_section(qapp, analyze_tab):
    analyze_tab._left_sidebar.verticalScrollBar().setValue(0)
    _settle(qapp)
    assert analyze_tab.current_help_section() == "Filename Schema"


def test_bottom_of_the_form_reports_a_section_that_is_on_screen(qapp, analyze_tab):
    """The bug a scroll-position rule invites: the form is taller than the
    distance the bar travels to reach its end, so at the bottom every remaining
    header is *below* the viewport top. Naming "the last header above the top"
    there points at a section several screens out of sight."""
    bar = analyze_tab._left_sidebar.verticalScrollBar()
    bar.setValue(bar.maximum())
    _settle(qapp)
    title = analyze_tab.current_help_section()
    assert title, "the bottom of the form reported no section at all"
    container = analyze_tab._section_widgets[title]
    viewport = analyze_tab._left_sidebar.viewport()
    top = container.mapTo(viewport, container.rect().topLeft()).y()
    assert top + container.height() > 0, f"{title} is entirely above the viewport"
    assert top < viewport.height(), f"{title} is entirely below the viewport"


def test_every_reported_title_maps_to_a_topic(qapp, analyze_tab):
    """Walk the whole form and check nothing it can report falls off the map."""
    bar = analyze_tab._left_sidebar.verticalScrollBar()
    seen = set()
    for value in range(bar.minimum(), bar.maximum() + 1, 40):
        bar.setValue(value)
        _settle(qapp, 2)
        title = analyze_tab.current_help_section()
        if title:
            seen.add(title)
    assert seen, "scrolling the form reported no section at all"
    unmapped = sorted(seen - set(help_topics.ANALYZE_SECTION_HELP))
    assert not unmapped, f"reported titles with no help topic: {unmapped}"
