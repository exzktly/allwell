"""Architectural-contract guardrails — the static, machine-checkable rules from
ARCHITECTURE.md, encoded as AST tests.

Companion to the existing guards (`test_guardrails`, `test_layering`,
`test_no_silent_except`, `test_remote_manifest`, `test_monolith_budgets`). Each
test here maps to a documented rule; the docstring names the §. These catch
contract drift that ruff can't express.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WELL_VIEWER = REPO_ROOT / "well_viewer"

QT_ROOTS = {"PySide6", "PyQt5", "PyQt6", "shiboken6"}


def _all_imports(tree: ast.Module):
    """Yield (node, top_module) for every Import/ImportFrom at any nesting."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node, alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                yield node, node.module.split(".")[0]


def _parse(path: Path) -> ast.Module:
    return parse_source(path)


def _walk_py(root: Path):
    return [p for p in sorted(root.rglob("*.py")) if "__pycache__" not in p.parts]


# ── §6.2 — the pipeline is self-sufficient: no well_viewer/widgets/Qt ─────────

# Whole directories, not a hand-written file list. The list this replaced named
# twelve files and had fallen five `pipeline/` modules and nine `util/` modules
# behind the tree — every one of them covered by the same §3 Qt-free claim as
# the three `util/` modules that were listed. A directory walk cannot drift the
# same way, which is why `test_services_have_no_qt_imports` (below) never did.
PIPELINE_ROOTS = ["pipeline", "util"]
# Entry points that live in no package of their own.
PIPELINE_FILES = [
    "process_microscopy.py",
]


def _pipeline_paths():
    for root in PIPELINE_ROOTS:
        yield from _walk_py(REPO_ROOT / root)
    for name in PIPELINE_FILES:
        path = REPO_ROOT / name
        if path.exists():
            yield path


def test_pipeline_is_self_sufficient():
    """§6.2: the pipeline (`process_microscopy` + the Qt-free `pipeline/` and
    `util/` packages) must run on a headless worker with no GUI installed — so it
    must never import `well_viewer`, `widgets`, or Qt, at ANY level (module *or*
    function-local, since a function-local import still crashes when the path
    runs on the worker)."""
    forbidden = {"well_viewer", "widgets"} | QT_ROOTS
    offenders = []
    for path in _pipeline_paths():
        name = path.relative_to(REPO_ROOT).as_posix()
        for node, top in _all_imports(_parse(path)):
            if top in forbidden:
                offenders.append(f"{name}:{node.lineno} imports {top}")
    assert not offenders, (
        "Pipeline self-sufficiency (ARCHITECTURE §6.2) violated — these files "
        "must not import well_viewer / widgets / Qt at any level:\n  "
        + "\n  ".join(offenders)
    )


# ── services/ deliberately have no Qt imports (CLI-reusable) ──────────────────

def test_services_have_no_qt_imports():
    """ARCHITECTURE §13: `services/` modules deliberately have no Qt imports, so
    they stay reusable for a headless CLI driver."""
    offenders = []
    for path in _walk_py(REPO_ROOT / "services"):
        for node, top in _all_imports(_parse(path)):
            if top in QT_ROOTS:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} imports {top}")
    assert not offenders, (
        "services/ must stay Qt-free (CLI-reusable):\n  " + "\n  ".join(offenders)
    )


def test_services_do_not_import_well_viewer():
    """ARCHITECTURE §13: `services/` stays Qt-free specifically so it can be
    reused headless — but nothing stopped a service from reaching into the
    Review pane's package for a helper and dragging its whole (Qt-heavy)
    import graph in transitively. `services/run_spec.py` and
    `services/job_queue.py` are the modules this protects: a job queue that
    imports `well_viewer` for a convenience helper stops being usable from a
    headless worker even though it imports no Qt module directly itself."""
    offenders = []
    for path in _walk_py(REPO_ROOT / "services"):
        for node, top in _all_imports(_parse(path)):
            if top == "well_viewer":
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} imports well_viewer")
    assert not offenders, (
        "services/ must not import well_viewer (would pull Qt in transitively "
        "through a package the Qt-free contract can't see):\n  " + "\n  ".join(offenders)
    )


# ── widgets/ have no All-Well app coupling ────────────────────────────────────

def test_widgets_do_not_import_the_app():
    """ARCHITECTURE §13 (widgets): reusable widgets carry no All-Well-specific
    knowledge — they must not import `well_viewer`, `analyze`, or `allwell`.
    (They may compose other `widgets.*` primitives.)"""
    forbidden = {"well_viewer", "analyze", "allwell"}
    offenders = []
    for path in _walk_py(REPO_ROOT / "widgets"):
        for node, top in _all_imports(_parse(path)):
            if top in forbidden:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} imports {top}")
    assert not offenders, (
        "widgets/ must not couple to the app (well_viewer / analyze / "
        "allwell):\n  " + "\n  ".join(offenders)
    )


# ── controllers/state never import WellViewerApp at runtime (circular) ────────

def _type_checking_line_ranges(tree: ast.Module):
    """Line ranges of `if TYPE_CHECKING:` bodies (imports there are type-only)."""
    ranges = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            t = node.test
            is_tc = (isinstance(t, ast.Name) and t.id == "TYPE_CHECKING") or (
                isinstance(t, ast.Attribute) and t.attr == "TYPE_CHECKING")
            if is_tc:
                for stmt in node.body:
                    ranges.append((stmt.lineno, getattr(stmt, "end_lineno", stmt.lineno)))
    return ranges


def test_nothing_imports_wellviewerapp_at_runtime():
    """ARCHITECTURE §9: controllers reach the app via the `app` argument and must
    NOT import `WellViewerApp` (that's circular). Allowed: `runtime_app.py` (home),
    `well_viewer/__init__.py` (lazy PEP-562 getattr), and `TYPE_CHECKING`-only
    imports for type hints."""
    offenders = []
    for path in _walk_py(WELL_VIEWER):
        if path.name == "runtime_app.py" or path == WELL_VIEWER / "__init__.py":
            continue
        tree = _parse(path)
        tc_ranges = _type_checking_line_ranges(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(
                a.name == "WellViewerApp" for a in node.names
            ):
                if any(lo <= node.lineno <= hi for lo, hi in tc_ranges):
                    continue  # type-only import
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not offenders, (
        "Runtime import of WellViewerApp (circular) — reach the app via the "
        "`app` arg, or guard the import under `if TYPE_CHECKING:`:\n  "
        + "\n  ".join(offenders)
    )


# ── removable features must stay importable-on-demand (removability) ─────────
#
# Two rules, one machinery. Both ask "who imports a package the profile may
# delete", and they differ only in *when* the import counts:
#
#   * A top-level removable feature (``features/stats``) is dropped by commenting
#     its descriptor out or deleting its directory. A function-local import is the
#     sanctioned way to reach one, because the call site is guarded by the feature
#     being present. Only import-time imports are violations.
#   * A nested removable sub-package (``features/home/ratios``) is a directory
#     ``derive_profile`` deletes on its own, and the shape that reaches for one is
#     usually a plain helper call with no guard in front of it. Deferring that
#     import defers the crash rather than avoiding it, so **function-local imports
#     count too** — with one narrow, listed exemption for a container dispatching
#     to its own sub-tabs by tab name.
#
# The extractor below is shared so the two cannot drift apart in what they can
# see, which is how `playground/worker.py` reached into `image_table` unnoticed:
# `ast.Import`, relative imports and imports nested in a module-level `try:`/`if:`
# were invisible to both.

from tests._repo_scan import python_sources  # noqa: E402
from tests._repo_scan import parse_source

# Support libs under features/ that are always present (not removable rail tabs):
# importing THEM at module scope is fine. Everything else under features/ is a
# removable feature whose package must be importable-on-demand only.
_SUPPORT_LIBS = {"export", "preview", "crop", "qc"}


def _feature_pkg_of(path: Path):
    """The ``features/<pkg>`` package a file belongs to, or None if not under
    features/."""
    try:
        parts = path.relative_to(WELL_VIEWER).parts
    except ValueError:
        return None
    if len(parts) >= 2 and parts[0] == "features":
        return parts[1]
    return None


def _module_of(path: Path) -> str:
    """Dotted module name for a repo file, e.g. ``well_viewer.features.home.controller``."""
    return ".".join(path.relative_to(REPO_ROOT).with_suffix("").parts)


def _deferred_node_ids(tree: ast.Module) -> set:
    """Ids of every node inside a function body — i.e. not run at import time.

    A class body *is* run at import, and so is anything inside a module-level
    ``try:`` or ``if:``; only a function defers execution.
    """
    out: set = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                if child is not node:
                    out.add(id(child))
    return out


def _imported_modules(node: ast.AST, here: str):
    """Every module string an import node names, absolute.

    Handles ``from x import y`` (``ImportFrom``), ``import x.y`` (``Import``,
    which the first version of both guards never looked at) and relative
    ``from .y import z``, resolved against *here*'s own package.
    """
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    if not node.level:
        return [node.module or ""]
    parts = here.split(".")[:-1]                 # the file's own package
    base = parts[:len(parts) - (node.level - 1)] if node.level > 1 else parts
    return [".".join([*base, node.module]) if node.module else ".".join(base)]


def _import_nodes(tree: ast.Module):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node


def test_no_module_scope_imports_of_removable_features():
    """Every removable feature (every ``features/<X>`` except the support libs
    ``export`` / ``preview`` / ``crop`` / ``qc`` and the assembly module
    ``registry.py``) must be importable on demand only: nothing may import
    ``well_viewer.features.<X>`` at import time, except inside
    ``features/registry.py`` (the sanctioned lazy-wrapper assembly point) and
    intra-feature imports. This is what makes a feature removable by commenting
    its descriptor out — AND deletable from disk — without breaking startup."""
    offenders = []
    for path in python_sources():
        if path == WELL_VIEWER / "features" / "registry.py":
            continue
        tree = _parse(path)
        here = _module_of(path)
        importer_pkg = _feature_pkg_of(path)
        deferred = _deferred_node_ids(tree)
        tc_ranges = _type_checking_line_ranges(tree)
        for node in _import_nodes(tree):
            if id(node) in deferred:
                continue                          # lazy — the sanctioned shape
            if any(lo <= node.lineno <= hi for lo, hi in tc_ranges):
                continue                          # type-only
            for mod in _imported_modules(node, here):
                if not mod.startswith("well_viewer.features."):
                    continue
                target = mod.split(".")[2]
                if target == "registry" or target in _SUPPORT_LIBS:
                    continue
                if target == importer_pkg:        # intra-feature — allowed
                    continue
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno} -> {mod}")
    assert not offenders, (
        "Import-time import of a removable feature — make it function-local "
        "(lazy), use the existing runtime_app `_lazy(...)` proxy, or move shared "
        "code to a lower layer (core/data). This import would break the feature's "
        "removal/deletion:\n  " + "\n  ".join(sorted(offenders)))


def _nested_removable_packages() -> dict[str, str]:
    """``{"well_viewer.features.home.ratios": "Ratios", …}`` — every removable
    unit in ``profiles/full.toml`` whose package sits *inside* another feature.

    The catalog is the authority on what gets physically deleted, so it is also
    the authority on which sub-packages a container may not bind.
    """
    import tomllib

    catalog = tomllib.loads((REPO_ROOT / "profiles" / "full.toml")
                            .read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for entry in catalog.get("feature", []):
        for rel in entry.get("paths") or []:
            parts = Path(rel).parts
            # well_viewer / features / <container> / <sub-package>
            if len(parts) == 4 and parts[:2] == ("well_viewer", "features"):
                out[".".join(parts)] = str(entry.get("name"))
    return out


#: The one sanctioned shape for reaching a nested removable sub-package from
#: outside it: a container's own sub-tab dispatch, which selects a builder by the
#: tab's *name* (``sub.tabText(idx)``). In a stripped build the tab does not
#: exist, so the branch holding the import is unreachable — which is exactly what
#: is NOT true of an ordinary helper call, and why this list is per
#: ``(importer, target package)`` rather than per file.
#:
#: `features/registry.py` is exempt wholesale (it is the assembly point) and is
#: skipped before this is consulted.
_DISPATCH_EXEMPT = {
    ("well_viewer/features/image_browser/controller.py",
     "well_viewer.features.image_browser.image_table"),
    ("well_viewer/features/image_browser/controller.py",
     "well_viewer.features.image_browser.cell_browser"),
    ("well_viewer/features/image_browser/controller.py",
     "well_viewer.features.image_browser.playground"),
}


def _nested_offenders():
    """``(rel, pkg, lineno, mod, name, deferred)`` for every reach into a nested
    removable package from outside it — at import time *or* inside a function."""
    nested = _nested_removable_packages()
    found = []
    for path in python_sources():
        if path == WELL_VIEWER / "features" / "registry.py":
            continue
        rel = str(path.relative_to(REPO_ROOT))
        here = _module_of(path)
        tree = _parse(path)
        deferred = _deferred_node_ids(tree)
        tc_ranges = _type_checking_line_ranges(tree)
        for node in _import_nodes(tree):
            if any(lo <= node.lineno <= hi for lo, hi in tc_ranges):
                continue
            for mod in _imported_modules(node, here):
                for pkg, name in nested.items():
                    if mod != pkg and not mod.startswith(pkg + "."):
                        continue
                    if here == pkg or here.startswith(pkg + "."):
                        continue      # inside the sub-package itself — allowed
                    found.append((rel, pkg, node.lineno, mod, name,
                                  id(node) in deferred))
    return found


def test_a_container_feature_does_not_import_its_own_removable_sub_packages():
    """§5.3 removability, for the nested case the sibling guard cannot see.

    The rule above compares only the first path segment under ``features/``, so
    it reads an import from ``features/home/ratios`` by
    ``features/home/controller.py`` as intra-feature and waves it through. But
    Ratios is its own removable unit with its own ``[[feature]]`` block:
    ``derive_profile`` deletes that directory, and an import in the container
    would then fail at startup — the whole app, not just the tab. The same
    applies to Image Browser's three sub-tabs.

    **A function-local import is not a fix here**, which is the lesson that cost
    this branch a defect. `playground/worker.py` reached into the Image Table
    from inside a function, so the first version of this guard could not see it,
    and the Playground's reference-field modes died on a daemon thread in a build
    that had dropped the Image Table. A deferred import of a deleted module
    raises just as surely, one frame later. The only exemption is a container
    dispatching to its own sub-tabs by tab name, where the branch cannot be
    reached once the tab is gone — see ``_DISPATCH_EXEMPT``.
    """
    nested = _nested_removable_packages()
    assert nested, "profiles/full.toml lists no nested removable package"
    offenders = [
        f"{rel}:{lineno} -> {mod} (removable unit {name!r}"
        + (", function-local — still fatal once the directory is gone)"
           if deferred else ")")
        for rel, pkg, lineno, mod, name, deferred in _nested_offenders()
        if (rel, pkg) not in _DISPATCH_EXEMPT
    ]
    assert not offenders, (
        "Import of a removable SUB-package from outside it. `derive_profile` "
        "deletes this directory, so the import breaks the app, not just the tab "
        "— and making it function-local only moves the failure to the first call. "
        "Move the shared code down to `data/` where every sibling can reach it, "
        "or reach the sub-tab through its descriptor's build adapter in "
        "features/registry.py:\n  " + "\n  ".join(sorted(offenders)))


def test_the_dispatch_exemption_still_describes_real_imports():
    """An allowlist that outlives what it excuses starts hiding the next one.

    Every entry in ``_DISPATCH_EXEMPT`` must still name an import this guard
    would otherwise reject. When a container stops reaching for a sub-package,
    its entry must go — and this is what says so.
    """
    live = {(rel, pkg) for rel, pkg, *_ in _nested_offenders()}
    stale = _DISPATCH_EXEMPT - live
    assert not stale, (
        "These entries no longer describe a real import — delete them from "
        f"_DISPATCH_EXEMPT: {sorted(stale)}")


def test_disabling_a_leaf_feature_drops_it_everywhere():
    """Commenting a descriptor out of ``FEATURES`` removes the feature's rail
    entry, dispatch, and state slice — with no dangling references. Exercised on
    a leaf feature (Review CSV) by filtering the registry and asserting it
    vanishes from every surface the core consults."""
    from well_viewer.core.app_state import AppState
    from well_viewer.core.feature_registry import FeatureRegistry
    from well_viewer.features.registry import attach_feature_state, build_feature_registry

    full = build_feature_registry()
    assert full.get("Review CSV") is not None  # present by default

    pruned = FeatureRegistry(d for d in full.all() if d.name != "Review CSV")
    # Gone from lookup, group enumeration, and every hook fan-out.
    assert pruned.get("Review CSV") is None
    assert "Review CSV" not in {d.name for d in pruned.in_group("Data")}
    assert "Review CSV" not in {d.name for d in pruned.with_hook("on_activate")}
    # State attach still works and contributes the same non-Review slices.
    state = AppState()
    attach_feature_state(state, pruned)
    assert hasattr(state, "bar") and hasattr(state, "stats")


# ── EventBus signal catalogue is capped ───────────────────────────────────────

def test_event_bus_signal_catalogue_is_capped():
    """ARCHITECTURE §9 (EventBus): a small, capped catalogue of exactly four
    cross-cutting signals. Adding one is a real architectural decision — update
    the docstring + this guard deliberately, don't let it creep.

    Freezing a roster keeps signals from creeping in; it does not keep a dead
    one from staying. ``redraw_requested`` sat here with no emitter and no
    subscriber, held in place by this very assertion, while the docstring
    beside it called the bus load-bearing. The companion test below is the
    other half: every name in this set has to be reachable from somewhere."""
    expected = {
        "channel_changed", "selection_changed", "dataset_loaded",
        "plot_options_changed",
    }
    tree = _parse(WELL_VIEWER / "core" / "event_bus.py")
    signals = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            fn = node.value.func
            if (isinstance(fn, ast.Name) and fn.id == "Signal") or (
                isinstance(fn, ast.Attribute) and fn.attr == "Signal"):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        signals.add(t.id)
    assert signals == expected, (
        f"EventBus signal catalogue changed: {sorted(signals)} != "
        f"{sorted(expected)}. The bus is intentionally capped at 4 — if this is "
        "deliberate, update ARCHITECTURE.md §9 and this guard together."
    )


def test_every_event_bus_signal_is_reachable():
    """A frozen roster stops signals creeping in; it does not stop one dying.

    ``redraw_requested`` lived on the bus with no emitter and no subscriber,
    held in place by the roster above while the module docstring cited the
    catalogue as evidence that the bus is load-bearing. This is the other
    direction: every signal declared on ``EventBus`` must be named somewhere
    outside its own module and outside the tests — an ``.emit(`` or a
    ``.connect(`` on ``bus.<name>``, or a subscriber wiring it up.
    """
    tree = _parse(WELL_VIEWER / "core" / "event_bus.py")
    signals = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            fn = node.value.func
            if (isinstance(fn, ast.Name) and fn.id == "Signal") or (
                isinstance(fn, ast.Attribute) and fn.attr == "Signal"):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        signals.add(t.id)

    from tests._repo_scan import python_sources

    seen: set[str] = set()
    for path in python_sources():
        if path.name == "event_bus.py" or "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in signals:
            if f".{name}." in text or f".{name}\n" in text:
                seen.add(name)

    dead = sorted(signals - seen)
    assert not dead, (
        f"EventBus signals with no emitter and no subscriber anywhere: {dead}. "
        "A signal nobody sends is a claim about the design the code does not "
        "support — remove it, or wire it up."
    )


# ── feature module naming convention ──────────────────────────────────────────

# Documented bare/legacy exceptions (ARCHITECTURE §3 "Known exceptions") + the
# cohesive sub-modules extracted during the P4 split. New feature files should
# match the pattern (controller/renderer/state/worker/callbacks/__init__, or a
# *_view.py / *_panel.py / _private.py) rather than extend this list.
FEATURE_NAME_EXCEPTIONS = {
    # "view.py" was removed (audit A17): features/stats and features/grouping
    # were normalized from bare view.py → tab_view.py, so no feature file uses
    # the bare name anymore.
    "models.py", "params.py", "region.py", "service.py", "style.py",
    "box_select.py",
    "export.py", "grid_layout.py", "image_compose.py",
    # scatter density-coloring + rectangular-gating helpers (point rendering,
    # density math, gate stats, RectangleSelector wiring) — a draw-mode of the
    # scatter canvas, paired with scatter/controller.py.
    "gating.py",
    # image_table right-click per-cell override popover, paired with
    # image_table/controller.py.
    "grid_ops.py",
    # image_table smart-layout builders (flat + nested/compound-axis grid assembly,
    # per-channel LUT re-keying, Build presets), split out of image_table/
    # controller.py to hold its line budget; paired with it and the grid_layout math.
    "build_layout.py",
    # the feature registry: the single assembly point for FeatureDescriptors and
    # the sole sanctioned importer of well_viewer.features.* (the import-inversion
    # seam) — not a per-feature module, so it sits outside the naming scheme.
    "registry.py",
    # image_table's feature-owned view-state (de)serialization — the snapshot /
    # apply pair persistence/view_state.py fans out via the ``view_state_io``
    # FeatureDescriptor hook, so a profiled-out Image Table takes its own
    # persistence with it. A persistence-IO module, not a view/compute one.
    "view_state_io.py",
}


def _feature_name_ok(name: str) -> bool:
    if name in {"controller.py", "renderer.py", "collect.py", "state.py",
                "worker.py", "callbacks.py", "__init__.py"}:
        return True
    # ``_compute.py`` — the documented pure-math suffix (ARCHITECTURE §3;
    # e.g. domain/stats_compute.py, grouping/cell_gating_compute.py from A3).
    if name.endswith("_view.py") or name.endswith("_panel.py") or name.endswith("_compute.py"):
        return True
    if name.startswith("_"):
        return True
    return name in FEATURE_NAME_EXCEPTIONS


def test_feature_module_naming():
    """ARCHITECTURE §3: per-feature files follow a small naming scheme
    (`controller`/`renderer`/`collect`/`state`/`worker`/`callbacks`, `*_view`,
    `*_panel`, `_private`), with a fixed list of documented exceptions. A new
    name outside the scheme should be a deliberate choice — add it here + to the
    doc. ``collect.py`` is the pure data-collection half paired with a
    ``renderer.py`` (e.g. scatter)."""
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in _walk_py(WELL_VIEWER / "features")
        if not _feature_name_ok(p.name)
    ]
    assert not offenders, (
        "Feature module(s) outside the naming scheme (ARCHITECTURE §3). Rename "
        "to the convention, or add to FEATURE_NAME_EXCEPTIONS + document it:\n  "
        + "\n  ".join(offenders)
    )
