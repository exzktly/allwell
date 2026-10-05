"""A Python subclass of a Qt class is declared at module scope, not inside a function.

Measured with ``tests/perf/bench_app_retention.py`` (row Q1 of
``_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md``): the Home notes box was a
``QPlainTextEdit`` subclass declared inside its builder, its ``focusOutEvent``
closing over ``ctx``. After the window was closed and deleted, that widget's
Python wrapper survived its C++ object, held by a reference the garbage
collector cannot see, and through the closure it kept every closed
``ReviewApp`` alive — about 24 MB each. Declared at module scope and handed
``ctx`` instead, the wrapper is freed with the window. Isolated reproductions of
a per-call Qt subclass were all freed, so which Shiboken path holds the wrapper
is not pinned down; what is decidable is the shape, and this file rules it out.

The shape costs nothing to avoid: pass what the methods need to ``__init__``
and keep it on the instance, which the collector can see.

An exemption is a row with its reason, never an absence.
"""

from __future__ import annotations

import ast

from tests._repo_scan import parse_source, python_sources, rel

#: ``"path::function.Class"`` -> why the shape is safe there.
EXEMPT: dict[str, str] = {
    "review/core/ui_helpers.py::_themed_nav_toolbar_class._ThemedNavToolbar": (
        "built once per process and cached in the module global "
        "_THEMED_NAV_CLS; its methods close over nothing app-specific — the "
        "function exists only to defer the matplotlib Qt backend import"),
}


def _qt_names(tree: ast.AST) -> set[str]:
    """Names in this file bound to a Qt class, imported anywhere in it."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and (
                node.module.startswith("PySide6")
                or node.module.startswith("matplotlib.backends.backend_qt")):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _base_is_qt(base: ast.AST, qt: set[str]) -> bool:
    text = ast.unparse(base)
    return (text in qt or text.split(".")[-1] in qt
            or text.startswith(("QtWidgets.", "QtCore.", "QtGui.", "PySide6.")))


def _function_local_qt_classes(tree: ast.AST):
    qt = _qt_names(tree)
    found = []

    def visit(node, scope: list[str], in_function: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, scope + [child.name], True)
            elif isinstance(child, ast.ClassDef):
                if in_function and any(_base_is_qt(b, qt) for b in child.bases):
                    found.append((".".join(scope + [child.name]), child.lineno))
                visit(child, scope + [child.name], in_function)
            else:
                visit(child, scope, in_function)

    visit(tree, [], False)
    return found


def _offenders(sources) -> list[str]:
    out = []
    for path in sources:
        for qualname, line in _function_local_qt_classes(parse_source(path)):
            key = f"{rel(path)}::{qualname}"
            if key not in EXEMPT:
                out.append(f"{key} (line {line})")
    return out


def test_no_qt_subclass_is_declared_inside_a_function():
    offenders = _offenders(python_sources())
    assert not offenders, (
        "Qt subclasses declared inside a function — move each to module scope "
        "and pass what its methods need to __init__:\n  " + "\n  ".join(offenders))


def test_every_exemption_still_names_a_function_local_qt_class():
    live = set()
    for path in python_sources():
        for qualname, _line in _function_local_qt_classes(parse_source(path)):
            live.add(f"{rel(path)}::{qualname}")
    stale = sorted(set(EXEMPT) - live)
    assert not stale, f"exemptions that no longer describe a real site: {stale}"
