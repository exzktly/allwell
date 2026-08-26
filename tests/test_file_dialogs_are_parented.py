"""Every ``QFileDialog`` static call must be given a parent.

An unparented ``QFileDialog`` on macOS — the ship target — opens as a detached
window instead of an application sheet. It can fall behind the main window with
no way back to it, which reads to the user as the app having hung. The rule was
already written down at ``widgets/path_field.py``, where the picker closure
takes and forwards a *parent* rather than hardcoding ``None`` precisely for this
reason; it just was not enforced, and three sites drifted off it.

This is a syntax-decidable rule, so it is checked as one: the first positional
argument of ``QFileDialog.getSaveFileName`` / ``getOpenFileName`` /
``getOpenFileNames`` / ``getExistingDirectory`` must not be the literal
``None``, and a local name whose every assignment in the enclosing function is
the literal ``None`` counts as that same literal. The second half is not
pedantry: the third site this guard was written for spelled it
``parent = None`` on the line above the call, which a literal-only check reads
as compliant. An attribute or parameter (``ctx.window``, ``self``) passes —
whether it holds ``None`` at runtime is not decidable here.
"""

from __future__ import annotations

import ast

from tests._repo_scan import python_sources, rel
from tests._repo_scan import parse_source

_STATIC_PICKERS = {
    "getSaveFileName",
    "getOpenFileName",
    "getOpenFileNames",
    "getExistingDirectory",
}


def _none_only_locals(scope: ast.AST) -> set[str]:
    """Names whose every assignment inside *scope* is the literal ``None``."""
    assigned: dict[str, list[ast.expr]] = {}
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    assigned.setdefault(tgt.id, []).append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                assigned.setdefault(node.target.id, []).append(node.value)
    return {
        name for name, values in assigned.items()
        if values and all(
            isinstance(v, ast.Constant) and v.value is None for v in values
        )
    }


def _offenders(tree: ast.AST, path: str) -> list[str]:
    out = []
    scopes = [tree] + [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    for scope in scopes:
        nones = _none_only_locals(scope)
        for node in ast.walk(scope):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not isinstance(fn, ast.Attribute) or fn.attr not in _STATIC_PICKERS:
                continue
            if not node.args:
                continue
            first = node.args[0]
            literal = isinstance(first, ast.Constant) and first.value is None
            via_name = isinstance(first, ast.Name) and first.id in nones
            if literal or via_name:
                shown = "None" if literal else f"{first.id} (= None)"
                entry = f"  {path}:{node.lineno}: {fn.attr}({shown}, ...)"
                if entry not in out:
                    out.append(entry)
    return out


def test_no_unparented_file_dialog():
    offenders: list[str] = []
    for path in python_sources():
        try:
            tree = parse_source(path)
        except SyntaxError:
            continue
        offenders.extend(_offenders(tree, rel(path)))
    assert not offenders, (
        "QFileDialog opened with no parent — on macOS this is a detached "
        "window that can hide behind the main one:\n" + "\n".join(offenders)
    )
