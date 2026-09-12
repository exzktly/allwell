"""Every `from <first-party module> import <name>` must name something that module defines.

This is the failure a symbol move leaves behind, and it has now happened twice in
two branches. Lifting `build_hline_separator` out of `review.core.ui_helpers`
repointed thirteen single-line importers and walked straight past two written in
the parenthesised multi-line form, because the sweep searched for a *spelling*
rather than for the fact. Both survived `ruff`, the whole guard family and every
targeted file that was run; the fast lane found them only because two unrelated
test modules happened to import the tab they were in.

That is the general shape: `ruff` does not resolve imports across modules, and a
broken one is invisible until something actually imports the module — which, for
a GUI built lazily, can be no test at all. Deciding it from the syntax tree costs
a couple of seconds and needs nothing imported.

**Every limitation here fails closed**, i.e. skips rather than accuses:

* a module that defines `__getattr__` at module scope can serve any name (the
  `review/__init__.py` lazy-import pattern), so it is not checked;
* a module containing `from x import *` may bind anything, so it is not checked;
* `__init__.py` re-exports are followed only as far as their own bindings —
  a package that re-exports through a star-import lands in the rule above.

The binding scan is deliberately broad: a plain `def`/`class`, an assignment (in
any nesting, since a conditional or `try`/`except ImportError` fallback is a real
top-level definition), an `import ... as`, an annotated assignment, a `for`
target, a walrus, and a `global` declaration all count as defining the name.
Anything it cannot see makes the guard skip, never accuse.
"""
from __future__ import annotations

import ast

from tests._repo_scan import PACKAGE_ROOTS, REPO_ROOT, parse_source, python_sources

#: Root modules are importable as first-party names too.
_ROOT_MODULES = {p.stem for p in REPO_ROOT.glob("*.py")}
_FIRST_PARTY = set(PACKAGE_ROOTS) | _ROOT_MODULES


def _module_path(dotted: str):
    """The file backing a first-party dotted module name, or None."""
    rel = dotted.replace(".", "/")
    for cand in (REPO_ROOT / f"{rel}.py", REPO_ROOT / rel / "__init__.py"):
        if cand.is_file():
            return cand
    return None


def _bindings(tree: ast.Module) -> set[str] | None:
    """Names the module binds, or None when it can bind anything."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            return None  # a star-import may bind anything
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                out.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            out.add(node.id)
        elif isinstance(node, ast.Global):
            out.update(node.names)
    if "__getattr__" in out:
        return None  # PEP 562: the module serves names dynamically
    return out


def test_every_first_party_import_names_something_that_exists():
    offenders: list[str] = []
    cache: dict[str, set[str] | None] = {}

    for path in python_sources():
        tree = parse_source(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
                continue
            if node.module.split(".")[0] not in _FIRST_PARTY:
                continue
            if any(a.name == "*" for a in node.names):
                continue
            if node.module not in cache:
                target = _module_path(node.module)
                cache[node.module] = (
                    _bindings(parse_source(target)) if target is not None else None)
            bound = cache[node.module]
            if bound is None:
                continue  # module absent, dynamic, or star-importing: fail closed
            for alias in node.names:
                # A package may expose a submodule without binding its name.
                if _module_path(f"{node.module}.{alias.name}") is not None:
                    continue
                if alias.name not in bound:
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno} imports "
                        f"`{alias.name}` from `{node.module}`, which does not define it")

    assert not offenders, (
        "these imports name something their target module does not define — an "
        "ImportError waiting for whatever first imports the file:\n  "
        + "\n  ".join(sorted(offenders)) + "\n\n"
        "Usually a symbol moved and this call site did not follow. `ruff` does "
        "not resolve imports across modules, so nothing else reports it until "
        "the module is imported at runtime — which, for a tab that builds "
        "lazily, may be no test at all.")
