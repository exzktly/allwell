"""The plotting and remote facades re-export nothing beyond what they declare.

Row A3 of the remediation plan (deleted when its last row closed; ``git log --
_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md`` recovers it) froze the
surfaces the earlier rows left behind. ``barplot/controller.py`` and
``export/style.py`` had grown into pass-through modules: names moved out to
``barplot/collect.py``, ``review/core/plot_style.py`` and
``widgets/fig_clipboard.py`` and were imported back so existing callers kept
working, which kept every caller pointed at the wrong module and hid which
module owned what. Every caller now imports from the owner; this guard holds
the end state.

For each frozen facade:

* a name the facade *imports* may be **read** through it (``from F import X``,
  ``F.X``, a ``lazy("F", "X")`` / registry ``_import_attr`` target) only if it
  is a declared re-export with a written reason in :data:`FACADES`;
* a name it imports may be **patched** through it only if the facade itself
  calls that name: then the patch is a binding seam, and patching the facade is
  the correct target (patching the owner would not reach the facade's binding);
* it holds no pure re-export (an import nothing in the module reads) that is
  not declared;
* each declared re-export is live (someone reaches it through the facade) and
  is the owner's own object.

``services/remote_runner.py`` keeps its own end-state guard
(``tests/test_remote_runner_facade.py``); the ownership test below adds that
each name in its ``__all__`` resolves to the object its owner defines.

Do not add a re-export to make a caller's import work: import from the owner.
"""

from __future__ import annotations

import ast
import functools
import importlib
from collections.abc import Iterable
from pathlib import Path

from tests._facade_scan import (
    ATTRIBUTE,
    FROM_IMPORT,
    PATCH_FORMS,
    consumer_sources,
    consumers_of,
    module_level_names,
    names_loaded_in,
    reexports,
)
from tests._repo_scan import REPO_ROOT, parse_source, rel
from tests.test_lazy_targets_resolve import lazy_targets

#: Frozen facade (repository path) -> {declared re-export: why it stays}.
FACADES: dict[str, dict[str, str]] = {
    "review/core/plot_style.py": {
        "follows_theme": (
            "a renderer takes its ink and its 'this is ink' marker from one "
            "import; the marker lives in widgets.plot_card because the repaint "
            "that honours it is PlotCard.setPlotTheme's"),
    },
    "review/features/barplot/controller.py": {},
    "review/features/barplot/collect.py": {},
    "review/features/export/style.py": {},
    "review/features/export/sidebar_view.py": {},
}

RUNNER = "services/remote_runner.py"

_READ_FORMS = frozenset({FROM_IMPORT, ATTRIBUTE, "lazy-target"})


def _module_name(path: str) -> str:
    return path[:-3].replace("/", ".")


def _reaches(module: str, sources: Iterable[Path], exclude: Path):
    """``consumers_of`` plus string-resolved ``lazy`` / ``_import_attr`` reads."""
    sources = list(sources)
    users = consumers_of(module, sources, exclude=[exclude])
    for path in sources:
        if Path(path).resolve() == exclude.resolve():
            continue
        for _line, target, name in lazy_targets(parse_source(path)):
            if target == module:
                shown = (rel(path) if Path(path).is_relative_to(REPO_ROOT)
                         else Path(path).as_posix())
                users.setdefault(name, set()).add((shown, "lazy-target"))
    return users


def undeclared_reaches(facade: Path, declared: Iterable[str],
                       reaches: dict[str, set[tuple[str, str]]]) -> list[str]:
    """``"<name> via <file> (<form>)"`` for each of *reaches* the facade may
    not serve (*reaches* as :func:`_reaches` returns them)."""
    bound = module_level_names(facade)
    loaded = names_loaded_in(facade)
    declared = set(declared)
    found = []
    for name, users in reaches.items():
        if bound.get(name) != "import" or name in declared:
            continue
        for shown, form in sorted(users):
            if form in PATCH_FORMS and name in loaded:
                continue  # a binding seam: the facade calls it, so patch it here
            if form in _READ_FORMS or form in PATCH_FORMS:
                found.append(f"{name} via {shown} ({form})")
    return found


@functools.cache
def _sources(module: str) -> tuple[Path, ...]:
    """The files that could reach *module* at all.

    Every form the scan sees spells the module's own name somewhere in the
    file, and its package's name too unless the file sits inside that package
    — a dotted import, ``from <parent> import <leaf>``, a string target, or a
    relative ``from .<leaf> import`` from a sibling. A file that fails both
    cannot reach it. Scanning only those keeps five facades from costing five
    walks of the whole tree.
    """
    parent, leaf = module.split(".")[-2:]
    package = (REPO_ROOT / Path(*module.split(".")[:-1])).resolve()
    return tuple(p for p in consumer_sources()
                 if leaf in (text := p.read_text(encoding="utf-8"))
                 and (parent in text or package in p.resolve().parents))


@functools.cache
def _reaches_of(path: str):
    return _reaches(_module_name(path), _sources(_module_name(path)),
                    REPO_ROOT / path)


def test_no_facade_serves_a_name_it_does_not_own():
    found = []
    for path, declared in FACADES.items():
        for hit in undeclared_reaches(REPO_ROOT / path, declared,
                                      _reaches_of(path)):
            found.append(f"{path}: {hit}")
    assert not found, (
        "These reach a name through a facade that only imports it; import it "
        "from the module that defines it:\n  " + "\n  ".join(found))


def test_no_facade_holds_an_undeclared_reexport():
    found = []
    for path, declared in FACADES.items():
        extra = reexports(REPO_ROOT / path) - set(declared)
        found += [f"{path}: {name}" for name in sorted(extra)]
    assert not found, (
        "These imports are read by nothing in their module and are not declared "
        "re-exports; delete them:\n  " + "\n  ".join(found))


def _import_owner(path: Path, name: str) -> str | None:
    """The module a facade's ``from X import name`` names, if it has one."""
    for node in parse_source(path).body:
        if isinstance(node, ast.ImportFrom) and node.module:
            if any((a.asname or a.name) == name for a in node.names):
                return node.module
    return None


def test_declared_reexports_are_live_and_are_the_owners_object():
    problems = []
    for path, declared in FACADES.items():
        facade_path = REPO_ROOT / path
        module = _module_name(path)
        users = _reaches_of(path)
        facade = importlib.import_module(module)
        for name in declared:
            owner = _import_owner(facade_path, name)
            if owner is None:
                problems.append(f"{path}: {name} is declared but not imported")
                continue
            if not any(form in _READ_FORMS for _f, form in users.get(name, ())):
                problems.append(f"{path}: {name} is declared but nothing reads it")
            if getattr(facade, name) is not getattr(
                    importlib.import_module(owner), name):
                problems.append(f"{path}: {name} is not {owner}.{name}")
    assert not problems, "\n".join(problems)


def test_the_runner_facade_resolves_to_its_owners():
    runner_path = REPO_ROOT / RUNNER
    runner = importlib.import_module(_module_name(RUNNER))
    bound = module_level_names(runner_path)
    problems = []
    for name in runner.__all__:
        if not hasattr(runner, name):
            problems.append(f"{name} does not resolve")
            continue
        # Any import of the name decides ownership, even one a later
        # assignment rebinds: a wrapper over the owner's object is not it.
        owner = _import_owner(runner_path, name)
        if owner is not None:
            if getattr(runner, name) is not getattr(
                    importlib.import_module(owner), name):
                problems.append(f"{name} is not its owner's object ({owner})")
        elif bound.get(name) != "defined":
            problems.append(f"{name} is neither defined nor imported at module scope")
    assert not problems, "\n".join(problems)
