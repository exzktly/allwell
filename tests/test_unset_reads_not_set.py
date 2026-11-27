"""An unset value reads "not set", in the disabled ink, through ``ui.text_roles.NOT_SET``.

The same absence read "(not set)", "none" and "—" on different pages, so a reader could not tell an empty field
from a value. A site is the literal "(not set)" or "not set" written anywhere but ``ui/text_roles.py``, or "—",
"none" or "(none)" as the whole of a string handed to a widget's text (``setText``, ``setPlaceholderText``,
``addItem``, a label, a table, list or tree item). A "—" elsewhere — a figure's table cell, a CSV, a log line, a
separator — is not a control's unset value and is not this rule's; nor is "auto", which names a setting. The roster
(``tests/_design_rosters.UNSET``) holds today's sites per file and only shrinks; row D13 empties it.
"""

from __future__ import annotations

import ast

from tests._design_rosters import UNSET as ROSTER
from tests._design_scan import call_name, count_sites, over_roster, stale_roster, strings_in
from tests._repo_scan import python_sources, rel

HOMES = {"ui/text_roles.py"}
_AS_VALUE = {"(not set)", "not set", "—", "none", "(none)", "None"}
#: A combo's empty-list sentinel, which is not an unset value (above).
_COMBO = {"addItem", "addItems"}
_TAKERS = {"setText", "setPlaceholderText", "addItem", "addItems", "setItemText", "QLabel", "QTableWidgetItem",
           "QListWidgetItem", "QTreeWidgetItem"}


def find(_path: str, tree: ast.Module):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and call_name(node) in _TAKERS:
            values = _AS_VALUE - {"—"} if call_name(node) in _COMBO else _AS_VALUE
            yield from (s for a in node.args for s in strings_in(a) if s.value.strip() in values)


def _seen():
    return count_sites(find, python_sources(), rel, skip=lambda r: r in HOMES)


def test_an_unset_value_reads_not_set_through_one_constant():
    bad = over_roster(_seen(), ROSTER)
    assert not bad, ("an unset value written by hand — show it with ui.text_roles.show_value / NOT_SET:\n  "
                     + "\n  ".join(bad))


def test_the_roster_only_describes_sites_that_remain():
    stale = stale_roster(_seen(), ROSTER)
    assert not stale, "lower these roster entries:\n  " + "\n  ".join(stale)
