"""An unset value reads "not set", in the disabled ink, through ``ui.text_roles.NOT_SET``.

The same absence read "(not set)", "none", "auto" and "—" on different pages, so a reader could not tell an empty
field from a value. A site is the literal "(not set)" or "not set" written anywhere but ``ui/text_roles.py``, "—" as
a whole string, or "none" / "(none)" handed to ``setText``, ``setPlaceholderText`` or ``addItem``. The roster
(``tests/_design_rosters.UNSET``) holds today's sites per file and only shrinks; row D13 empties it.
"""

from __future__ import annotations

import ast

from tests._design_rosters import UNSET as ROSTER
from tests._design_scan import call_name, count_sites, over_roster, stale_roster, strings_in
from tests._repo_scan import python_sources, rel

HOMES = {"ui/text_roles.py"}
_ANYWHERE = {"(not set)", "not set", "—"}
_AS_VALUE = {"none", "(none)", "None"}
_TAKERS = {"setText", "setPlaceholderText", "addItem"}


def find(_path: str, tree: ast.Module):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.strip() in _ANYWHERE:
            yield node
        elif isinstance(node, ast.Call) and call_name(node) in _TAKERS and node.args:
            yield from (s for s in strings_in(node.args[0]) if s.value.strip() in _AS_VALUE)


def _seen():
    return count_sites(find, python_sources(), rel, skip=lambda r: r in HOMES)


def test_an_unset_value_reads_not_set_through_one_constant():
    bad = over_roster(_seen(), ROSTER)
    assert not bad, ("an unset value written by hand — show it with ui.text_roles.show_value / NOT_SET:\n  "
                     + "\n  ".join(bad))


def test_the_roster_only_describes_sites_that_remain():
    stale = stale_roster(_seen(), ROSTER)
    assert not stale, "lower these roster entries:\n  " + "\n  ".join(stale)
