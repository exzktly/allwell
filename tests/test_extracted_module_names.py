"""Guard: T4-extracted modules must define every module-level name they use.

When a big module is split, a moved function can keep referencing a *module-level*
name (a constant, a ``_logger``, a helper) that was left behind in the original —
ruff's undefined-name check doesn't reliably catch it, so it only surfaces at
runtime (e.g. the ``_logger is not defined`` crash when toggling image-table
binary mode, where ``review_build_threshold_map`` had moved to ``collect.py`` but
``_logger`` / ``_FLATFIELD_PREVIEW_MAX_SAMPLES`` had not).

This walks each extracted module's AST and asserts every *runtime* global name a
top-level function loads is resolvable (module-level binding or builtin).
Annotation positions are skipped — ``from __future__ import annotations`` makes
them lazy strings, so ``Dict`` / ``Tuple`` in a signature need no import.
"""
from __future__ import annotations

import ast
import builtins
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The modules split out during the T4 decomposition (plus the form mixin).
EXTRACTED = [
    "well_viewer/features/review_image/collect.py",
    "well_viewer/features/barplot/collect.py",
    "well_viewer/features/scatter/collect.py",
    "analyze_tab_form.py",
]

_BUILTINS = set(dir(builtins)) | {"__file__", "__name__"}


def _module_bound(tree: ast.Module) -> set[str]:
    bound: set[str] = set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(n.name)
        elif isinstance(n, ast.Import):
            for a in n.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                bound.add(a.asname or a.name)
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            tgts = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in tgts:
                for x in ast.walk(t):
                    if isinstance(x, ast.Name):
                        bound.add(x.id)
    return bound


def _local_bound(fn: ast.AST) -> set[str]:
    local: set[str] = set()
    for a in ast.walk(fn):
        if isinstance(a, ast.arg):
            local.add(a.arg)
        elif isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            local.add(a.name)
        elif isinstance(a, ast.Name) and isinstance(a.ctx, ast.Store):
            local.add(a.id)
        elif isinstance(a, ast.ExceptHandler) and a.name:
            local.add(a.name)
        elif isinstance(a, ast.Import):
            for nm in a.names:
                local.add((nm.asname or nm.name).split(".")[0])
        elif isinstance(a, ast.ImportFrom):
            for nm in a.names:
                local.add(nm.asname or nm.name)
        elif isinstance(a, ast.Global):
            local.update(a.names)
    return local


def _runtime_loads(fn: ast.AST):
    """Yield (lineno, name) for Load-context names, skipping annotation subtrees."""
    def walk(node):
        for field, value in ast.iter_fields(node):
            # Skip annotation positions — lazy strings under future-annotations.
            if field in ("annotation", "returns"):
                continue
            items = value if isinstance(value, list) else [value]
            for item in items:
                if isinstance(item, ast.AST):
                    if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load):
                        yield item.lineno, item.id
                    yield from walk(item)
    yield from walk(fn)


def test_extracted_modules_have_no_undefined_globals():
    offenders: list[str] = []
    for rel in EXTRACTED:
        path = REPO / rel
        tree = ast.parse(path.read_text(), filename=str(path))
        module_bound = _module_bound(tree)
        for fn in [n for n in tree.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            local = _local_bound(fn)
            for lineno, name in _runtime_loads(fn):
                if name in local or name in module_bound or name in _BUILTINS:
                    continue
                offenders.append(f"{rel}:{lineno} {fn.name} -> undefined '{name}'")
    assert not offenders, (
        "Extracted module references a name that wasn't moved/imported with it:\n  "
        + "\n  ".join(sorted(set(offenders)))
    )
