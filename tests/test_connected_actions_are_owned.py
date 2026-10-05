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
either chained (``m.addAction("x").triggered.connect(...)``), through a name or
``self.`` attribute bound to it, through an indexed signal, or by handing it to
a module function that connects that parameter. ``addAction(text, callable)`` is not flagged
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
    """For ``<x>.<signal>.connect(...)`` or ``<x>.<signal>[T].connect(...)``,
    return ``<x>``; else ``None``."""
    if not (isinstance(call.func, ast.Attribute) and call.func.attr == "connect"):
        return None
    signal = call.func.value
    if isinstance(signal, ast.Subscript):          # triggered[bool]
        signal = signal.value
    if isinstance(signal, ast.Attribute):
        return signal.value
    return None


def _key(node) -> str | None:
    """A binding a later read can be matched against: ``a`` or ``self._a``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return ast.unparse(node)
    return None


def _bindings(scope) -> dict[str, list[int]]:
    """Every ``addAction(...)`` result bound to a key in *scope*: plain,
    annotated and walrus assignment."""
    bound: dict[str, list[int]] = {}
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
            targets, value = [node.target], node.value
        else:
            continue
        if value is not None and _is_add_action(value):
            for target in targets:
                key = _key(target)
                if key:
                    bound.setdefault(key, []).append(value.lineno)
    return bound


def _connecting_helpers(tree) -> dict[str, set[int]]:
    """Module functions that connect a signal of one of their parameters:
    ``name -> {positional index}``. Handing an action to one connects it."""
    out: dict[str, set[int]] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = [a.arg for a in fn.args.args]
        for call in ast.walk(fn):
            if isinstance(call, ast.Call):
                owner = _connected_signal_owner(call)
                if isinstance(owner, ast.Name) and owner.id in params:
                    idx = params.index(owner.id)
                    if params[0] in ("self", "cls"):
                        idx -= 1  # the caller passes everything after it
                    if idx >= 0:  # a method connecting a signal of self
                        out.setdefault(fn.name, set()).add(idx)
    return out


def connected_unowned_actions(tree: ast.AST) -> list[int]:
    """Line numbers of ``addAction(...)`` results whose signals are connected.

    Seen: a chained connect; a name or ``self.`` attribute bound to the result
    (plain, annotated or walrus) and connected anywhere it is visible — a name
    within its function, an attribute anywhere in the module, since an action
    built in one method is routinely wired in another; an indexed signal
    (``triggered[bool]``); and the result, or a name bound to it, handed to a
    module function that connects that parameter.
    """
    lines: list[int] = []
    helpers = _connecting_helpers(tree)
    module_attrs = {k: v for k, v in _bindings(tree).items() if "." in k}
    scopes = [tree, *(n for n in ast.walk(tree)
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))]
    for scope in scopes:
        # Every binding, not the last one: a function that reuses ``act`` for
        # two menus' actions connects both, and keeping one line per name
        # reported them one at a time, fix by fix.
        bound = {k: v for k, v in _bindings(scope).items() if "." not in k}
        bound.update(module_attrs)
        for node in ast.walk(scope):
            if not isinstance(node, ast.Call):
                continue
            owner = _connected_signal_owner(node)
            if owner is not None:
                if _is_add_action(owner):
                    lines.append(owner.lineno)
                elif _key(owner) in bound:
                    lines.extend(bound[_key(owner)])
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            for idx in helpers.get(name, ()):
                if idx < len(node.args):
                    arg = node.args[idx]
                    if _is_add_action(arg):
                        lines.append(arg.lineno)
                    elif _key(arg) in bound:
                        lines.extend(bound[_key(arg)])
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
