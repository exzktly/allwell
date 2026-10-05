"""A menu or toolbar action whose signal is connected is created on the Python side.

``owner.addAction(text)`` creates the action in C++; once a Python callable is
connected to one of its signals, PySide keeps the wrapper alive after the menu
and the action are deleted — one wrapper per action, every time the menu is
rebuilt. Row Q4 of the remediation plan (deleted when its last row closed; ``git log --
_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md`` recovers it) found
it as the last thing a closed window left behind (seven wrappers per window,
all from the dataset menu), and ``widgets/owned_action.py`` has the
measurement. ``owned_action(owner, text)`` is the shape that is released with
its owner.

Flagged: the result of an ``addAction(...)`` call whose signal is connected,
either chained (``m.addAction("x").triggered.connect(...)``) or through a name
bound to it in the same function. ``addAction(text, callable)`` is not flagged
unless its result is connected too: that overload was measured and leaves
nothing behind.
"""

from __future__ import annotations

import ast

from tests._repo_scan import parse_source, python_sources, rel


def _is_add_action(node) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "addAction")


def _connected_signal_owner(call: ast.Call):
    """For ``<x>.<signal>.connect(...)``, return ``<x>``; else ``None``."""
    if (isinstance(call.func, ast.Attribute) and call.func.attr == "connect"
            and isinstance(call.func.value, ast.Attribute)):
        return call.func.value.value
    return None


def connected_unowned_actions(tree: ast.AST) -> list[int]:
    """Line numbers of ``addAction(...)`` results whose signals are connected."""
    lines: list[int] = []
    scopes = [tree, *(n for n in ast.walk(tree)
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))]
    for scope in scopes:
        # Every binding, not the last one: a function that reuses ``act`` for
        # two menus' actions connects both, and keeping one line per name
        # reported them one at a time, fix by fix.
        bound: dict[str, list[int]] = {}
        for node in ast.walk(scope):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and _is_add_action(node.value)):
                bound.setdefault(node.targets[0].id, []).append(node.value.lineno)
        for node in ast.walk(scope):
            if not isinstance(node, ast.Call):
                continue
            owner = _connected_signal_owner(node)
            if owner is None:
                continue
            if _is_add_action(owner):
                lines.append(owner.lineno)
            elif isinstance(owner, ast.Name) and owner.id in bound:
                lines.extend(bound[owner.id])
    return sorted(set(lines))


def test_no_connected_action_is_created_by_add_action():
    sites = []
    for path in python_sources():
        for line in connected_unowned_actions(parse_source(path)):
            sites.append(f"{rel(path)}:{line}")
    assert not sites, (
        "These actions are created by addAction(text) and then connected; PySide "
        "keeps each wrapper after the menu is gone. Build them with "
        "widgets.owned_action.owned_action(owner, text) instead:\n  "
        + "\n  ".join(sites))
