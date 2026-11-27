"""Style sheets are written by the theme, not set widget by widget.

A ``setStyleSheet`` call outside ``ui/theme.py`` freezes the tokens it reads at the moment it runs, so the widget
cannot follow a palette chosen later, and it styles one widget where a rule in the application sheet would style
every one like it. 169 such calls in 66 files is what made a live palette switch impossible. The roster
(``tests/_design_rosters.STYLE_SHEET``) holds today's sites per file and only shrinks; row D12 empties it, keeping
only entries with a written reason (the menu-indicator rule of CLAUDE.md §7.12 is one).
"""

from __future__ import annotations

import ast

from tests._design_rosters import STYLE_SHEET as ROSTER
from tests._design_scan import call_name, count_sites, over_roster, stale_roster
from tests._repo_scan import python_sources, rel

HOMES = {"ui/theme.py"}


def find(_path: str, tree: ast.Module):
    yield from (c for c in ast.walk(tree) if isinstance(c, ast.Call) and call_name(c) == "setStyleSheet")


def _seen():
    return count_sites(find, python_sources(), rel, skip=lambda r: r in HOMES)


def test_no_style_sheet_is_set_outside_the_theme():
    bad = over_roster(_seen(), ROSTER)
    assert not bad, ("a style sheet set on a widget — write the rule into the application sheet "
                     "(ui/theme.py, or a module's qss() it includes):\n  " + "\n  ".join(bad))


def test_the_roster_only_describes_sites_that_remain():
    stale = stale_roster(_seen(), ROSTER)
    assert not stale, "lower these roster entries:\n  " + "\n  ".join(stale)
