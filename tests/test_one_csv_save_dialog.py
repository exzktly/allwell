"""One Save-As dialog for a CSV, so every CSV export offers the same picker.

Four CSV exports each opened their own ``QFileDialog.getSaveFileName``, and the
four had drifted: two filters (``All files (*)`` and the DOS-era ``(*.*)``,
which Qt honours literally on some platforms and which then hides every
extensionless name from a filter whose own label says it shows everything), two
answers to whether the picker starts beside the open dataset, and two answers
to whether it suggests a filename. A user cannot tell which of the four they
are looking at, and the difference is not one anybody chose.

``core/csv_export.ask_save_csv`` is the dialog. Only the *writer* stays with
its caller, because a dict-writer's schema is its field names.

The shape this guard exists for is the one that shipped: the helper was
extracted, one caller was converted, and the eight-site original was left in
place beside it — §7.5 #5, where half-swept is worse than unswept because one
package then holds two definitions of the same dialog and neither looks wrong.
"""

from __future__ import annotations

import ast

from tests._repo_scan import parse_source, python_sources

#: The module that owns the dialog. Its own call is the one this rule is about.
_OWNER = "csv_export.py"


def _module_strings(tree: ast.AST) -> dict[str, str]:
    """``{NAME: value}`` for the module's own string constants.

    The site that shipped held its filter in a module-level ``_CSV_FILTER``,
    so a guard reading only the literals *inside* the call walked straight past
    it — it found the two sites that inlined their filter and reported the
    sweep complete. §7.5 #2: a hardened guard that passes on the first run has
    probably not been hardened.
    """
    out: dict[str, str] = {}
    for node in getattr(tree, "body", []):
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, ast.AnnAssign)
                   else [])
        value = getattr(node, "value", None)
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                out[target.id] = value.value
    return out


def _mentions_csv(node: ast.AST, constants: dict[str, str]) -> bool:
    """A string in this expression — or named by it — naming a ``.csv`` filter."""
    for inner in ast.walk(node):
        if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
            if ".csv" in inner.value.lower():
                return True
        if isinstance(inner, ast.Name) and ".csv" in constants.get(
                inner.id, "").lower():
            return True
    return False


def test_only_csv_export_opens_a_csv_save_dialog():
    offenders = []
    for path in python_sources():
        if path.name == _OWNER:
            continue
        tree = parse_source(path)
        constants = _module_strings(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "attr", None) != "getSaveFileName":
                continue
            # The filter is the argument that names the extensions; matching on
            # any string in the call rather than on position keeps a keyword
            # argument, or one built from a constant, in scope.
            if any(_mentions_csv(arg, constants) for arg in node.args) or any(
                    _mentions_csv(kw.value, constants) for kw in node.keywords):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        "these open their own CSV Save-As dialog, so the filter, the starting "
        "directory and the suggested filename drift from every other CSV "
        "export in the app:\n  " + "\n  ".join(offenders)
        + "\nGo through review.core.csv_export.ask_save_csv."
    )
