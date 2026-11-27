"""Gaps and margins come from the spacing scale (``ui.theme.Spacing``: 4, 8, 12, 16, 24), never a number.

About 180 literal values in twelve sizes stood beside 177 uses of the scale, so two pages with the same structure
spaced it differently. A site is a ``setSpacing`` / ``setHorizontalSpacing`` / ``setVerticalSpacing`` /
``setContentsMargins`` call holding an integer literal other than 0 anywhere in its arguments (``Spacing.sm + 2``
included). The roster (``tests/_design_rosters.SPACING``) holds today's sites per file and only shrinks; row D12
empties it.
"""

from __future__ import annotations

import ast

from tests._design_rosters import SPACING as ROSTER
from tests._design_scan import call_name, count_sites, over_roster, stale_roster
from tests._repo_scan import python_sources, rel

SETTERS = {"setSpacing", "setHorizontalSpacing", "setVerticalSpacing", "setContentsMargins"}


def find(_path: str, tree: ast.Module):
    for call in ast.walk(tree):
        if isinstance(call, ast.Call) and call_name(call) in SETTERS:
            if any(isinstance(n, ast.Constant) and type(n.value) is int and n.value != 0
                   for arg in call.args for n in ast.walk(arg)):
                yield call


def _seen():
    return count_sites(find, python_sources(), rel)


def test_spacing_comes_from_the_scale():
    bad = over_roster(_seen(), ROSTER)
    assert not bad, "a gap or margin written as a number — use ui.theme.Spacing:\n  " + "\n  ".join(bad)


def test_the_roster_only_describes_sites_that_remain():
    stale = stale_roster(_seen(), ROSTER)
    assert not stale, "lower these roster entries:\n  " + "\n  ".join(stale)
