"""Nothing unpacks the fold-change request as a tuple.

``FoldChangeState`` was a ``(vs_control_on, control_label, vs_t0_on)`` tuple and
briefly emulated one, so a call site could keep writing ``a, b, c = …``. The
emulation was removed deliberately — it broke the ``__eq__``/``__hash__``
contract, and it let a site that had not been taught about a new field drop it
in silence, which for this object means raw values drawn under a fold-change
axis label.

Removing it left two unpack sites behind, and both were live defects that a
green CI run said nothing about, because both sit on paths only the slow lane
reaches: ``barplot/renderer._set_frac_ylim`` (a ``TypeError`` on every bar
redraw) and ``fold_change.is_active``. Ruff does not flag a bad unpack, and a
pure-logic test never draws a figure. So the guard is static.

Read it by name. ``FoldChangeState.active`` answers the question both of those
sites were spelling out by hand.
"""
from __future__ import annotations

import ast

from tests._repo_scan import python_sources, rel

#: Producers of a ``FoldChangeState``. A tuple target on any of them is the bug.
#: ``_fc_state`` is the Batch Export panels' own accessor, and it is here
#: because ``fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0 = self._fc_state()`` is the
#: exact spelling both panels carried before this object existed — a guard
#: that could not see the method call would have been blind to the shape it
#: was written for.
_PRODUCERS = frozenset({"fold_change_state", "coerce_fc_state", "_fc_state"})


def _callee_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
    return ""


def offenders_in(tree: ast.AST, name: str) -> list[str]:
    """Sites in *tree* that bind a producer's result to a tuple target."""
    out: list[str] = []
    for node in ast.walk(tree):
        targets: list = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            targets = [node.target]
        else:
            continue
        value = node.value if isinstance(node, ast.Assign) else node.iter
        if _callee_name(value) not in _PRODUCERS:
            continue
        for target in targets:
            if isinstance(target, (ast.Tuple, ast.List)):
                out.append(f"{name}:{node.lineno}")
    return out


def _offenders() -> list[str]:
    out: list[str] = []
    for path in python_sources():
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - a file we cannot parse
            continue
        out.extend(offenders_in(tree, rel(path)))
    return out


def test_the_fold_change_request_is_read_by_name():
    offenders = _offenders()
    assert not offenders, (
        "FoldChangeState is not a tuple — unpacking it raises, and a site that "
        "unpacks three values drops any field added later. Read it by name "
        "(`fc.active`, `fc.control`, `fc.per_member`):\n"
        + "\n".join(f"  {o}" for o in offenders)
    )
