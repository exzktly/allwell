"""A button does not carry its state in its label, and no push button is a toggle.

"Pooled: Off" (14 sites) and a button reading "SEM" that looks like a command both hide a setting inside a
command's look. The design language (§2.5) gives an on/off setting a checkbox when a button elsewhere applies it
and a switch when it acts at once, and a choice among a few a segmented control. A site is a string literal
reading ``…: On`` / ``…: Off``, or ``setCheckable(True)`` on a push button the same module built. The roster
(``tests/_design_rosters.STATE_IN_LABEL``) holds today's sites per file and only shrinks; row D13 empties it.
"""

from __future__ import annotations

import ast
import re

from tests._design_rosters import STATE_IN_LABEL as ROSTER
from tests._design_scan import call_name, count_sites, over_roster, stale_roster
from tests._repo_scan import python_sources, rel

_STATE = re.compile(r":\s*(On|Off)\b")
_BUILDERS = {"QPushButton", "btn_primary", "btn_secondary", "btn_card", "btn_danger", "_btn", "button", "primary",
             "default", "quiet", "destructive"}


def _target(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def find(_path: str, tree: ast.Module):
    pushed = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and call_name(node.value) in _BUILDERS:
            pushed.update(t for t in map(_target, node.targets) if t)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and _STATE.search(node.value):
            yield node
        elif (isinstance(node, ast.Call) and call_name(node) == "setCheckable" and node.args
              and isinstance(node.args[0], ast.Constant) and node.args[0].value is True
              and isinstance(node.func, ast.Attribute) and _target(node.func.value) in pushed):
            yield node


def _seen():
    return count_sites(find, python_sources(), rel)


def test_no_button_carries_its_state():
    bad = over_roster(_seen(), ROSTER)
    assert not bad, ("a button whose label carries its state, or a checkable push button — use a checkbox, a "
                     "switch (widgets/toggle_switch.py) or a segmented control:\n  " + "\n  ".join(bad))


def test_the_roster_only_describes_sites_that_remain():
    stale = stale_roster(_seen(), ROSTER)
    assert not stale, "lower these roster entries:\n  " + "\n  ".join(stale)
