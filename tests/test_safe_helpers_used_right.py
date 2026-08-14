"""``swallow`` and ``swallowed`` are used the way each is built to be used.

Two helpers with near-identical names and opposite call shapes
(``well_viewer/core/safe.py``, ``util/aw_safe.py``): ``swallow(note)`` is a
context manager, ``swallowed(note)`` is a call for inside an ``except`` block.
Swap them and Python does not complain until the line runs —
``with swallowed():`` raises ``AttributeError: __enter__``, and it raises
*before* the body, so the guarded work never happens at all.

That shipped. ``fold_change_controls_view._open_editor`` wrapped
``open_pairing_editor`` in ``with swallowed():``, so the per-sample control
editor could not be opened by the one control that opens it. A lane of ~4,950
tests stayed green because the callback is reached only by clicking a link in a
built popover, and the tests that exercise the editor call
``open_pairing_editor`` directly.

The mirror-image error is in here too: ``swallowed()`` used as a bare statement
outside any ``except`` block logs "swallowed" with no exception and suppresses
nothing, which reads in a log as a failure that never occurred.

Both are decidable from the syntax tree, which is why this is cheap enough to
be worth having: no call site can be reached only at runtime.
"""
from __future__ import annotations

import ast

from tests._repo_scan import python_sources, rel

#: The context manager, and the call-inside-except. Both modules define both
#: names, so a file importing either one is scanned against the same rule.
_CM_NAMES = {"swallow"}
_CALL_NAMES = {"swallowed"}


def _callee(node: ast.AST) -> str:
    """The bare function name a Call node invokes, or ``""``."""
    fn = getattr(node, "func", None)
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return ""


def offenders_in(source: str, path: str) -> list[str]:
    """Every misuse of the two helpers in *source*."""
    try:
        tree = ast.parse(source, path)
    except SyntaxError:  # pragma: no cover - a file we cannot parse
        return []
    out: list[str] = []

    # 1. `with swallowed():` — the shape that raises before its body runs.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        for item in node.items:
            if not isinstance(item.context_expr, ast.Call):
                continue
            name = _callee(item.context_expr)
            if name in _CALL_NAMES:
                out.append(
                    f"{path}:{item.context_expr.lineno} `with {name}()` raises "
                    f"AttributeError: __enter__ — use `with swallow(note)`"
                )

    # 2. `swallow(...)` called for its value rather than entered — the same
    #    mistake pointing the other way. A context manager invoked and dropped
    #    suppresses nothing; the body it was meant to guard runs unprotected.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        if _callee(node.value) in _CM_NAMES:
            out.append(
                f"{path}:{node.lineno} `{_callee(node.value)}(...)` as a bare "
                f"statement guards nothing — use `with swallow(note):`"
            )

    # 3. `swallowed()` as a statement outside any `except` handler. Inside one
    #    it is correct and is the common case, so the walk is scoped: descend
    #    from each handler body and mark what is legitimately covered first.
    covered: set = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        for inner in ast.walk(node):
            covered.add(id(inner))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        if _callee(node.value) in _CALL_NAMES and id(node) not in covered:
            out.append(
                f"{path}:{node.lineno} `{_callee(node.value)}()` outside an "
                f"`except` block logs a failure that did not happen"
            )
    return out


def _offenders() -> list[str]:
    out: list[str] = []
    for path in python_sources():
        out.extend(offenders_in(path.read_text(), rel(path)))
    return out


def test_the_two_suppression_helpers_are_not_swapped():
    offenders = _offenders()
    assert not offenders, (
        "`swallow` is a context manager and `swallowed` is a call for inside "
        "an except block; swapping them fails only when the line runs, and the "
        "`with swallowed()` form raises before the body it was meant to "
        "guard:\n" + "\n".join(f"  {o}" for o in offenders)
    )
