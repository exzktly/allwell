"""A function that can return ``None`` says so in its signature.

The shape: one path returns a value, another returns bare ``None``, and the
signature carries no return annotation at all. The caller then cannot tell
"there is no value" from "the value is zero" without reading the body — and
that conflation is the source of most of the silently-wrong numbers this
codebase has produced. A spread with one replicate, a mask that could not be
resolved, a scan that came up empty: each is *undefined*, and each has at some
point been drawn as ``0``.

93 functions across 52 files were in this state when the rule landed, against
186 that already declared it. They are all fixed; there is no allowlist, and a
new one fails here.

Deliberately narrow. It fires only when **both** an explicit valued ``return``
and an explicit bare ``return`` / ``return None`` appear in the same function,
which needs no flow analysis and cannot be argued with. A function that returns
a value on one path and falls off the end on another is the same defect and is
not caught here: telling a real fall-off from an early-out that cannot be
reached takes flow analysis, and a guard with false positives gets deleted.
"""
from __future__ import annotations

import ast

from tests._repo_scan import python_sources, rel


def _own_returns(fn: ast.AST) -> list[ast.Return]:
    """``return`` statements belonging to *fn*, not to a nested def or lambda."""
    out: list[ast.Return] = []
    stack = list(getattr(fn, "body", []))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Return):
            out.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return out


def _is_bare(node: ast.Return) -> bool:
    return node.value is None or (
        isinstance(node.value, ast.Constant) and node.value.value is None
    )


def offenders_in(source: str, path: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - the tree parses
        return []
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.returns is not None:
            continue
        returns = _own_returns(fn)
        if not any(_is_bare(r) for r in returns):
            continue
        if not any(not _is_bare(r) for r in returns):
            continue
        out.append(f"{path}:{fn.lineno} {fn.name}()")
    return out


def offenders() -> list[str]:
    out: list[str] = []
    for path in python_sources():
        out += offenders_in(path.read_text(encoding="utf-8"), rel(path))
    return out


def test_a_function_that_can_return_none_declares_it():
    found = offenders()
    assert not found, (
        f"{len(found)} function(s) return a value on one path and None on "
        "another with no return annotation. Declare the type "
        "(`-> X | None`), so a caller can tell 'no value' from 'zero':\n  "
        + "\n  ".join(found)
    )
