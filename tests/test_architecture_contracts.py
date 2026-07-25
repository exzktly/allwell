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
    return ast.parse(path.read_text(), filename=str(path))


def _walk_py(root: Path):
    return [p for p in sorted(root.rglob("*.py")) if "__pycache__" not in p.parts]


# ── §6.2 — the pipeline is self-sufficient: no well_viewer/widgets/Qt ─────────

PIPELINE_FILES = [
    "process_microscopy.py",
    "pipeline/auto_threshold.py",
    "pipeline/background.py",
    "pipeline/cellpose.py",
    "pipeline/flatfield.py",
    "pipeline/io.py",
    "pipeline/shape_features.py",
    "pipeline/stardist.py",
    "util/well_token.py",
    "util/timepoint.py",
    "util/aw_safe.py",
]


def test_pipeline_is_self_sufficient():
    """§6.2: the pipeline (`process_microscopy` + its Qt-free repo-root modules)
    must run on a headless worker with no GUI installed — so it must never import
    `well_viewer`, `widgets`, or Qt, at ANY level (module *or* function-local,
    since a function-local import still crashes when the path runs on the
    worker)."""
    forbidden = {"well_viewer", "widgets"} | QT_ROOTS
    offenders = []
    for name in PIPELINE_FILES:
        path = REPO_ROOT / name
        if not path.exists():
            continue
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


# ── widgets/ have no All-Well app coupling ────────────────────────────────────

def test_widgets_do_not_import_the_app():
    """ARCHITECTURE §13 (widgets): reusable widgets carry no All-Well-specific
    knowledge — they must not import `well_viewer`, `analyze_tab`, or `allwell`.
    (They may compose other `widgets.*` primitives.)"""
    forbidden = {"well_viewer", "analyze_tab", "allwell"}
    offenders = []
    for path in _walk_py(REPO_ROOT / "widgets"):
        for node, top in _all_imports(_parse(path)):
            if top in forbidden:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} imports {top}")
    assert not offenders, (
        "widgets/ must not couple to the app (well_viewer / analyze_tab / "
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


# ── no module-scope import of a removable feature, anywhere (removability) ────

# Support libs under features/ that are always present (not removable rail tabs):
# importing THEM at module scope is fine. Everything else under features/ is a
# removable feature whose package must be importable-on-demand only.
_SUPPORT_LIBS = {"export", "preview", "crop", "qc"}


def _feature_pkg_of(path: Path):
    """The ``features/<pkg>`` package a file belongs to, or None if not under
    features/."""
    parts = path.relative_to(WELL_VIEWER).parts
    if len(parts) >= 2 and parts[0] == "features":
        return parts[1]
    return None


def _removable_feature_imports(tree: ast.Module, importer_pkg):
    """``(lineno, target_pkg)`` for every module-scope ``from
    well_viewer.features.<F>`` where F is a *removable* feature and the import is
    not intra-feature (importer already lives in F). Function-local imports and
    ``if TYPE_CHECKING:`` blocks are allowed (not module-body children)."""
    out = []
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        mod = node.module or ""
        if not mod.startswith("well_viewer.features."):
            continue
        target = mod.split(".")[2]
        if target == "registry" or target in _SUPPORT_LIBS:
            continue
        if target == importer_pkg:  # intra-feature import — allowed
            continue
        out.append((node.lineno, target))
    return out


def test_no_module_scope_imports_of_removable_features():
    """Every removable feature (every ``features/<X>`` except the support libs
    ``export`` / ``preview`` / ``crop`` / ``qc`` and the assembly module
    ``registry.py``) must be importable on demand only: nothing may
    ``from well_viewer.features.<X> import …`` at module scope, except inside
    ``features/registry.py`` (the sanctioned lazy-wrapper assembly point) and
    intra-feature imports. This is what makes a feature removable by commenting
    its descriptor out — AND deletable from disk — without breaking startup."""
    offenders = []
    for path in _walk_py(WELL_VIEWER):
        if path.name == "__init__.py" and _feature_pkg_of(path) is None:
            pass  # well_viewer/__init__.py — still scanned below
        # features/registry.py is the one allowed module-scope importer.
        if path == WELL_VIEWER / "features" / "registry.py":
            continue
        importer_pkg = _feature_pkg_of(path)
        for lineno, target in _removable_feature_imports(_parse(path), importer_pkg):
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} -> well_viewer.features.{target}")
    assert not offenders, (
        "Module-scope import of a removable feature — make it function-local "
        "(lazy), use the existing runtime_app `_lazy(...)` proxy, or move shared "
        "code to a lower layer (core/data). This import would break the feature's "
        "removal/deletion:\n  " + "\n  ".join(sorted(offenders))
    )


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
    """ARCHITECTURE §9 (EventBus): a small, capped catalogue of exactly five
    cross-cutting signals. Adding one is a real architectural decision — update
    the docstring + this guard deliberately, don't let it creep."""
    expected = {
        "channel_changed", "selection_changed", "dataset_loaded",
        "plot_options_changed", "redraw_requested",
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
        f"{sorted(expected)}. The bus is intentionally capped at 5 — if this is "
        "deliberate, update ARCHITECTURE.md §9 and this guard together."
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
    "box_select.py", "well_grid_button.py",
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
