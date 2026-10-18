"""A ``state.py`` / ``*_state.py`` module is a state container, not machinery.

ARCHITECTURE §3's suffix table: "``state.py`` / ``_state.py`` — Dataclass state
container; no logic, only fields + defaults." ``test_architecture_contracts::
test_feature_module_naming`` checks file *names*, and only under
``review/features/``, so a state module elsewhere could grow into something
else with nothing to notice. ``review/core/stats_cache_state.py`` did: it came
to hold a thread-safe, byte-bounded LRU with an ``RLock``, eviction counters and
an environment-variable budget parser beside its dataclass, until that store
moved to ``review/data/population_store.py``.

"No logic" is not decidable from the syntax tree — a dataclass may carry a
small method, and several Qt-free ``*_state.py`` modules in ``review/domain/``
hold pure helpers beside their dataclasses — but the shape that store had is:

* every top-level class is a ``@dataclass`` or an ``Enum``, never a hand-written
  class with its own ``__init__``;
* nothing imports concurrency, process or environment machinery (``threading``,
  ``queue``, ``multiprocessing``, ``concurrent``, ``subprocess``, ``os``).

The scan covers every package root (``tests/_repo_scan.PACKAGE_ROOTS``). An
exemption names one class or one import in one file, with its reason, and the
liveness test fails when an entry stops describing a real violation.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._repo_scan import parse_source, python_sources, rel

#: Modules a state module may not import (the top-level package is matched).
FORBIDDEN_IMPORTS = frozenset({
    "threading", "queue", "multiprocessing", "concurrent", "subprocess", "os",
})

#: ``(path, name)`` → why that class or import is allowed in that state module.
EXEMPT = {
    ("widgets/empty_state.py", "EmptyState"):
        "a Qt widget that draws an empty-state placeholder; the name says what it "
        "shows, not that it holds state",
    ("widgets/empty_state.py", "os"):
        "the same widget module: it puts the repository root on sys.path so its "
        "demo runs as a script",
    ("review/features/saved_plots/state.py", "SavedPlotsStore"):
        "the Saved Plots store with its disk paths, which predates this guard; "
        "moving it out of state.py is a change of its own",
}


def _is_state_module(path: Path) -> bool:
    return path.name == "state.py" or path.name.endswith("_state.py")


def _allowed_class(node: ast.ClassDef) -> bool:
    for deco in node.decorator_list:
        target = deco.func if isinstance(deco, ast.Call) else deco
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if name == "dataclass":
            return True
    for base in node.bases:
        name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
        if name in {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"}:
            return True
    return False


def _violations(path: Path, tree: ast.Module) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and not _allowed_class(node):
            out.append((node.name, f"class {node.name} is neither a @dataclass nor an Enum"))
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = [node.module]
        for name in names:
            top = name.split(".")[0]
            if top in FORBIDDEN_IMPORTS:
                out.append((top, f"imports {name}"))
    return out


def _all_violations() -> list[tuple[str, str, str]]:
    found = []
    for path in python_sources():
        if not _is_state_module(path):
            continue
        for name, why in _violations(path, parse_source(path)):
            found.append((rel(path), name, why))
    return found


def test_state_modules_hold_only_dataclasses_and_no_machinery():
    offenders = [f"{p}: {why}" for p, name, why in _all_violations() if (p, name) not in EXEMPT]
    assert not offenders, (
        "A state module holds machinery (ARCHITECTURE §3: a state.py / _state.py "
        "module is a dataclass container, fields + defaults only). Move the class "
        "or the machinery to a module of its own, as PopulationStore moved to "
        "review/data/population_store.py:\n  " + "\n  ".join(offenders)
    )


def test_every_exemption_still_describes_a_violation():
    live = {(p, name) for p, name, _why in _all_violations()}
    stale = sorted(f"{p}::{name}" for p, name in EXEMPT if (p, name) not in live)
    assert not stale, (
        "These EXEMPT entries no longer describe a violation; delete them:\n  "
        + "\n  ".join(stale)
    )
