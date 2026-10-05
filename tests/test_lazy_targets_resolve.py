"""Every ``lazy("module", "name")`` target names something the module defines.

``review/core/lazy.py`` defers an import until the proxy is first called, which
is how ``runtime_app`` and a few panels reach feature code without importing it
at module load. The cost is that the target is a pair of strings: nothing
resolves it until the call, so a name that has moved away fails only when a user
reaches the code path, as an ``AttributeError`` from inside the proxy.

Row A3 of the remediation plan (deleted when its last row closed; ``git log --
_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md`` recovers it) removed the
re-exports ``barplot/controller`` carried for other modules. The four-form
facade scan saw every import, attribute reach and patch of them — and not four
names ``runtime_app`` resolved through ``_lazy_names`` strings, which would have
broken the bar tab's drag-to-reorder and its well labels at first use. The
feature registry's hooks (``_import_attr``) are the same shape and are read too:
one of them is the only thing that reaches ``export.style``'s clipboard name.
This resolves every literal target at test time instead.
"""

from __future__ import annotations

import ast
import importlib

from tests._repo_scan import parse_source, python_sources, rel

#: ``lazy`` / ``lazy_names`` from ``review/core/lazy.py``, and the feature
#: registry's ``_import_attr`` and ``_lazy_projector``, which resolve their
#: hooks and cache projectors the same way.
_LAZY_CALLS = {"lazy", "_lazy", "lazy_names", "_lazy_names", "_import_attr",
               "_lazy_projector"}


def lazy_targets(tree: ast.AST) -> list[tuple[int, str, str]]:
    """``(line, module, name)`` for every literal ``lazy``/``lazy_names`` call."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else "")
        if name not in _LAZY_CALLS or not node.args:
            continue
        if not all(isinstance(a, ast.Constant) and isinstance(a.value, str)
                   for a in node.args):
            continue
        module, *names = [a.value for a in node.args]
        for attr in names:
            found.append((node.lineno, module, attr))
    return found


def test_every_lazy_target_resolves():
    missing = []
    for path in python_sources():
        if rel(path) == "review/core/lazy.py":
            continue
        for line, module, attr in lazy_targets(parse_source(path)):
            mod = importlib.import_module(module)
            if not hasattr(mod, attr):
                missing.append(f"{rel(path)}:{line}: {module}.{attr}")
    assert not missing, (
        "These lazy targets name nothing in their module; point each at the "
        "module that defines it:\n  " + "\n  ".join(missing))
