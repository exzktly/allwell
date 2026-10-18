"""The plot hold's reasons are compared through ``core/plot_hold``'s constants.

``review/core/plot_hold.py`` owns ``PlotState.held_for`` and names its reasons
(``HOLD_LOADING``, ``HOLD_GATING``). A reader that compares the field to a bare
string — ``held_for == "gating"`` — keeps working only while the constant's
value happens to match it: rename the constant and Home's "markers pending"
legend, or the plot pages' gating placeholder, silently never shows, with
nothing raising. Two such readers had grown up beside the ones that use the
constant (``home/controller.py``, ``core/ui_helpers.py``).

The rule, decidable from the syntax tree: outside ``core/plot_hold.py`` no
comparison sets a ``held_for`` read (the attribute, ``getattr(…, "held_for",
…)`` or a ``held_for(ctx)`` call) against a non-empty string literal. Comparing
against ``""`` (not held) is allowed; that is the field's empty default, not a
reason.
"""

from __future__ import annotations

import ast

from tests._repo_scan import parse_source, python_sources, rel

OWNER = "review/core/plot_hold.py"


def _reads_held_for(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute) and node.attr == "held_for":
        return True
    if isinstance(node, ast.Call):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name == "held_for":
            return True
        if name == "getattr" and len(node.args) >= 2:
            key = node.args[1]
            return isinstance(key, ast.Constant) and key.value == "held_for"
    return False


def _is_reason_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value != ""


def _sites(path, tree) -> list[str]:
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        if any(_reads_held_for(o) for o in operands) and any(_is_reason_literal(o) for o in operands):
            out.append(f"{rel(path)}:{node.lineno}")
    return out


def test_no_reader_compares_the_hold_to_a_bare_reason_string():
    offenders = []
    for path in python_sources():
        if rel(path) == OWNER:
            continue
        offenders.extend(_sites(path, parse_source(path)))
    assert not offenders, (
        "Compare PlotState.held_for through core/plot_hold's constants "
        "(plot_hold.held_for(ctx) == plot_hold.HOLD_GATING), not a bare string:\n  "
        + "\n  ".join(offenders)
    )
