"""Analyze reports the form section the user is working in.

The Analyze pane is one tall scrolling form, not a stack of pages, so the help
drawer cannot ask it "which page are you on". ``current_help_section`` answers
from keyboard focus first, then from scroll position — among sections with any
part on screen, the one whose header is nearest the viewport top.

Both fallbacks in that rule are easy to get subtly wrong, and both wrong
versions look fine in a test that only asks for *a* plausible answer:

* "the last header above the viewport top" names a section several screens out
  of sight once the form is scrolled to its end, because the scrollbar's travel
  is the form's height minus one viewport and every remaining header is then
  *below* the top;
* "the first section with any part on screen" names a section by its last few
  pixels — a tall Channel Tokens still clips the top of the viewport long after
  the user has scrolled past everything it says.

So the assertions here name the section they expect, and the reachability test
demands the *whole* map, not a subset of it.

Driven against a real ``AnalyzeTab``: the mapping from a title to a manual
heading is pure and tested in ``test_help_target_routing.py``; what needs a
live widget is the geometry, which is exactly the part that cannot be reasoned
about from the source.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

import help_topics

VIEWPORT_H = 700


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    import theme
    app.setStyleSheet(theme.qss())
    return app


@pytest.fixture(scope="module")
def analyze.tab(qapp):
    from analyze.tab import AnalyzeTab

    tab = AnalyzeTab()
    tab.resize(400, VIEWPORT_H)
    tab.show()
    _settle(qapp)
    return tab


def _settle(app, n: int = 10) -> None:
    for _ in range(n):
        app.processEvents()


def _expand_every_section(qapp, tab) -> None:
    """Open the collapsed sections — a hidden widget cannot take focus, so the
    focus branch is untestable while its section's body is folded."""
    carets = [w for w in tab.findChildren(QLabel)
              if w.objectName() == "SectionTitle" and w.text() == "▸"]
    for caret in carets:
        QTest.mouseClick(caret.parentWidget(), Qt.LeftButton)
    _settle(qapp)


def _focusable_in(section: QWidget) -> QWidget | None:
    for child in section.findChildren(QWidget):
        if child.isVisible() and child.focusPolicy() != Qt.NoFocus:
            return child
    return None


def _clear_focus(qapp, tab) -> None:
    focus = QApplication.focusWidget()
    if focus is not None:
        focus.clearFocus()
    _settle(qapp, 2)


def _scroll(qapp, tab, value: int) -> int:
    bar = tab._left_sidebar.verticalScrollBar()
    bar.setValue(value)
    _settle(qapp, 3)
    return bar.value()


# ── the record the lookup walks ──────────────────────────────────────────────

def test_every_section_is_recorded(analyze.tab):
    assert set(analyze.tab._section_widgets) == set(help_topics.ANALYZE_SECTION_HELP)


def test_sections_are_recorded_in_build_order(analyze.tab):
    titles = list(analyze.tab._section_widgets)
    assert titles[0] == "Filename Schema"
    # The form builds Folders before Channel tokens, and the manual numbers its
    # Analyze headings to match. This assertion had the pair the other way
    # round, which is the order neither the form nor the manual uses.
    assert titles.index("Channel Tokens") > titles.index("Folders")
    assert titles[-1] == "Scratch Disk"


# ── focus wins, and reaches every section ────────────────────────────────────

def test_focus_reaches_every_single_section(qapp, analyze.tab):
    """The whole map, not a subset. Scroll position alone can never name the
    short sections packed into the form's last screenful — they are all on
    screen at once there — so if focus did not reach them, the four manual
    subsections this feature exists to open would be unreachable and a
    subset-style assertion would have called that a pass."""
    _expand_every_section(qapp, analyze.tab)
    reached = {}
    for title, section in analyze.tab._section_widgets.items():
        target = _focusable_in(section)
        assert target is not None, f"{title} has no focusable control to test with"
        target.setFocus()
        _settle(qapp, 2)
        reached[title] = analyze.tab.current_help_section()
    assert reached == {t: t for t in help_topics.ANALYZE_SECTION_HELP}


def test_focus_outranks_scroll_position(qapp, analyze.tab):
    _expand_every_section(qapp, analyze.tab)
    _scroll(qapp, analyze.tab, 0)
    target = _focusable_in(analyze.tab._section_widgets["Scratch Disk"])
    target.setFocus()
    _settle(qapp, 2)
    assert analyze.tab.current_help_section() == "Scratch Disk"


# ── scroll position, when nothing has focus ──────────────────────────────────

def test_top_of_the_form_reports_the_first_section(qapp, analyze.tab):
    _scroll(qapp, analyze.tab, 0)
    _clear_focus(qapp, analyze.tab)
    assert analyze.tab.current_help_section() == "Filename Schema"


def test_a_section_scrolled_past_is_not_reported(qapp, analyze.tab):
    """The defect this replaced: at this scroll Filename Schema still clips the
    top of the viewport by a few dozen pixels, and "the first section with any
    part on screen" named it while Channel Tokens filled the screen."""
    _clear_focus(qapp, analyze.tab)
    top = analyze.tab._section_widgets["Channel Tokens"]
    form = analyze.tab._left_sidebar.widget()
    wanted = top.mapTo(form, top.rect().topLeft()).y()
    # Land part-way into Channel Tokens: its header is above the viewport top,
    # the previous section's tail is still on screen.
    _scroll(qapp, analyze.tab, wanted + 60)
    assert analyze.tab.current_help_section() == "Channel Tokens"


def test_bottom_of_the_form_reports_the_section_at_the_top_of_the_screen(qapp, analyze.tab):
    """The other defect: at the end of the form every remaining header sits
    below the viewport top, so "the last header above the top" pointed several
    screens back. The answer has to be a section whose header is on screen, or
    whose body covers the top of it — and the nearest such."""
    _clear_focus(qapp, analyze.tab)
    bar = analyze.tab._left_sidebar.verticalScrollBar()
    _scroll(qapp, analyze.tab, bar.maximum())
    title = analyze.tab.current_help_section()
    assert title, "the bottom of the form reported no section at all"

    viewport = analyze.tab._left_sidebar.viewport()

    def _top(name: str) -> int:
        c = analyze.tab._section_widgets[name]
        return c.mapTo(viewport, c.rect().topLeft()).y()

    chosen = analyze.tab._section_widgets[title]
    assert _top(title) + chosen.height() > 0, f"{title} is entirely above the viewport"
    assert _top(title) < viewport.height(), f"{title} is entirely below the viewport"
    on_screen = [
        name for name, c in analyze.tab._section_widgets.items()
        if c.isVisible()
        and _top(name) + c.height() > 0
        and _top(name) < viewport.height()
    ]
    assert title == min(on_screen, key=lambda n: abs(_top(n))), (
        f"{title} is on screen but not the nearest to its top; "
        f"visible: {[(n, _top(n)) for n in on_screen]}"
    )


def test_every_title_the_scroll_rule_can_report_is_mapped(qapp, analyze.tab):
    """Walk the whole scrollbar and check nothing it reports falls off the map.
    The set it can reach is deliberately *not* asserted to be the whole map —
    the form's last screenful shows several sections at once, and geometry
    cannot separate them. That is what the focus branch above is for."""
    _clear_focus(qapp, analyze.tab)
    bar = analyze.tab._left_sidebar.verticalScrollBar()
    seen = set()
    for value in range(bar.minimum(), bar.maximum() + 1, 40):
        _scroll(qapp, analyze.tab, value)
        title = analyze.tab.current_help_section()
        if title:
            seen.add(title)
    assert seen, "scrolling the form reported no section at all"
    unmapped = sorted(seen - set(help_topics.ANALYZE_SECTION_HELP))
    assert not unmapped, f"reported titles with no help topic: {unmapped}"


def test_a_collapsed_sidebar_reports_nothing(qapp, analyze.tab):
    """Ctrl+B hides the whole form; the drawer should fall back to the Analyze
    overview rather than report whatever was last on screen."""
    _clear_focus(qapp, analyze.tab)
    analyze.tab._left_sidebar.hide()
    _settle(qapp, 2)
    try:
        assert analyze.tab.current_help_section() == ""
    finally:
        analyze.tab._left_sidebar.show()
        _settle(qapp, 2)
