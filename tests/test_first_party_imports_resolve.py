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

The same fact has a second spelling, and it is the one that got away. A module
imported as an *object* — `from review.domain import selections_model as _sel`,
then `_sel.from_block(...)` — binds a name the first test resolves perfectly
well, because the module does still exist; what moved is the attribute read off
it. Splitting that module left six such attribute reads reaching for functions
it no longer defined, across four files, and every one of them passed `ruff`,
the guard family, and the first test in this file. (Six reads, four files — the
count this guard produces, not the four the first draft of this sentence said.) `test_every_module_attribute_resolves`
is the half that reads the attribute.

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


def _module_object_aliases(tree: ast.Module) -> dict[str, str]:
    """``alias -> dotted module`` for every ``from pkg import submodule`` here.

    **A name is dropped when its bindings disagree**, not merely when there are
    several of them. The scan walks the whole tree with no notion of scope, so a
    name meaning two things in two functions means nothing it can check:
    `features/stats/controller.py` binds `_st` to `scipy.stats` inside one
    function and to `review.domain.stats_tests` inside another, and reading the
    second module's definitions against the first one's call sites accused four
    scipy functions of not existing.

    Dropping every *multi*-bound alias was the first version of that rule and it
    was far too blunt: per-function lazy importing is this repository's dominant
    idiom, so the same module is routinely imported under the same alias in six
    or nine different functions of one file — `services/remote_runner.py` binds
    `run_journal` six times and reads eight attributes off it. Measured, the
    blunt rule left 8 aliases and 39 attribute reads unchecked, every one of them
    perfectly consistent, and it would have walked past the very defect this
    guard was written for had `_sel` been imported in two functions rather than
    one.

    So the bindings are gathered per name: a name bound only by `from pkg import
    mod` / `import pkg.mod` statements that all name **one** module is that
    module; a name bound any other way, or to two different modules, is dropped.
    Every binding form counts as a disagreeing one — an assignment, a parameter,
    a `def`, a `class`, a `global`, a `with` or comprehension target, an
    `except E as X`, a `case X:` — because none of them says which module the
    name holds. The last two are handled explicitly: both bind a bare identifier
    rather than an `ast.Name`, so the Store branch cannot see them, and this
    sentence claimed `except` was covered before either was.
    """
    #: name -> the set of modules it is bound to; ``None`` in the set means a
    #: binding that says nothing about which module (an assignment, a parameter).
    bound: dict[str, set] = {}

    def bind(name: str, dotted=None) -> None:
        bound.setdefault(name, set()).add(dotted)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            # ``import pkg.mod`` binds ``pkg``; ``import pkg.mod as m`` binds ``m``
            # to ``pkg.mod`` — the spelling the first version of this scan could
            # not see at all, worth 26 unchecked reads across 8 first-party sites.
            for a in node.names:
                if a.asname:
                    bind(a.asname, a.name if _module_path(a.name) else None)
                else:
                    bind(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "*":
                    continue
                name = a.asname or a.name
                dotted = (f"{node.module}.{a.name}"
                          if node.module and not node.level else None)
                bind(name, dotted if dotted and _module_path(dotted) else None)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bind(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bind(node.name)
        elif isinstance(node, ast.arg):
            bind(node.arg)
        elif isinstance(node, ast.Global):
            for name in node.names:
                bind(name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            # `except E as X` binds a bare identifier rather than an ast.Name,
            # so it is invisible to the Store branch above. Same for `case X:`.
            bind(node.name)
        elif isinstance(node, ast.MatchAs) and node.name:
            bind(node.name)

    return {
        name: next(iter(mods))
        for name, mods in bound.items()
        if len(mods) == 1 and next(iter(mods)) is not None
        and next(iter(mods)).split(".")[0] in _FIRST_PARTY
    }


def test_every_module_attribute_resolves():
    """``_sel.from_block`` where ``_sel`` is a module this repository defines.

    The private names are checked like any other: ``_sel.block_is_v2`` is as
    broken as ``_sel.from_block`` when the module stops defining it, and an
    underscore is a convention about who *should* call it rather than a claim
    that nobody does.
    """
    offenders: list[str] = []
    cache: dict[str, set[str] | None] = {}

    for path in python_sources():
        tree = parse_source(path)
        aliases = _module_object_aliases(tree)
        if not aliases:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and isinstance(node.ctx, ast.Load)):
                continue
            dotted = aliases.get(node.value.id)
            if dotted is None:
                continue
            if dotted not in cache:
                target = _module_path(dotted)
                cache[dotted] = (
                    _bindings(parse_source(target)) if target is not None else None)
            bound = cache[dotted]
            if bound is None:
                continue                      # dynamic or star-importing: fail closed
            if _module_path(f"{dotted}.{node.attr}") is not None:
                continue                      # a submodule, not an attribute
            if node.attr in bound or node.attr.startswith("__"):
                continue
            offenders.append(
                f"{path.relative_to(REPO_ROOT)}:{node.lineno} reads "
                f"`{node.value.id}.{node.attr}`, but `{dotted}` does not define "
                f"`{node.attr}`")

    assert not offenders, (
        "these attribute reads name something their module does not define — an "
        "AttributeError waiting for whatever first runs the line:\n  "
        + "\n  ".join(sorted(set(offenders))) + "\n\n"
        "A symbol moved and the call site did not follow. This is the form a "
        "name-by-name sweep walks past: the *import* still resolves, because "
        "the module is still there, and only the attribute is gone.")
