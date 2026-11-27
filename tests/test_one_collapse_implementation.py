"""The application folds a section one way: ``widgets.collapsible_section.CollapsibleSection`` (row D23).

Analyze built its own — a caret label whose text was flipped by hand between ▸ and ▾, and a body shown and hidden
from a monkey-patched ``mousePressEvent`` — beside the widget every other pane used, so the two looked and behaved
differently and a fix to one never reached the other. A site is a call that sets a caret's text to one of the two
fold glyphs on a condition (``setText("▾" if open else "▸")``): the shape of a fold built by hand. A ▾ that only
marks a menu, a stepper's dropdown or a disclosure toggle set once is not one.

:data:`PENDING` holds today's sites, each with the row that removes it, and only shrinks.
"""

from __future__ import annotations

import ast

from tests._design_scan import call_name, count_sites, over_roster, stale_roster
from tests._repo_scan import python_sources, rel

HOME = "widgets/collapsible_section.py"
_FOLD = {"▸", "▾"}

PENDING: dict[str, int] = {}


def find(_path: str, tree: ast.Module):
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and call_name(node) == "setText" and node.args
                and isinstance(node.args[0], ast.IfExp)):
            arms = [node.args[0].body, node.args[0].orelse]
            if all(isinstance(a, ast.Constant) and str(a.value).strip() in _FOLD for a in arms):
                yield node


def _seen():
    return count_sites(find, python_sources(), rel, skip=lambda r: r == HOME)


def test_a_section_folds_through_collapsible_section():
    bad = over_roster(_seen(), PENDING)
    assert not bad, ("a fold built by hand — use widgets.collapsible_section.CollapsibleSection:\n  "
                     + "\n  ".join(bad))


def test_the_pending_roster_only_describes_sites_that_remain():
    stale = stale_roster(_seen(), PENDING)
    assert not stale, "lower these roster entries:\n  " + "\n  ".join(stale)


def test_analyze_s_sections_are_collapsible_section_cards(qapp):
    """Through the real form builder: every Analyze section is the shared widget, the folding ones fold from
    their header and the others are titled cards that do not."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from analyze.tab import AnalyzeTab
    from widgets.collapsible_section import CollapsibleSection

    tab = AnalyzeTab()
    try:
        sections = tab._section_widgets
        assert sections and all(isinstance(s, CollapsibleSection) for s in sections.values()), {
            t: type(s).__name__ for t, s in sections.items()}
        folding = tab._section_cards
        assert folding and set(folding) < set(sections)
        title, card = next((t, c) for t, c in folding.items() if not c.isExpanded())
        body = tab._collapsible_sections[title][0]
        QTest.mouseClick(card.header(), Qt.LeftButton)
        assert card.isExpanded() and body.isVisibleTo(tab), f"{title} did not open from its header"
        fixed = next(s for t, s in sections.items() if t not in folding)
        QTest.mouseClick(fixed.header(), Qt.LeftButton)
        assert fixed.isExpanded(), "a section that does not fold folded"
    finally:
        tab.deleteLater()
