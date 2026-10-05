"""CI guardrails for the ``runtime_app`` teardown.

These ratchet the god-object's decline. Each phase re-measures and tightens the
budgets; a phase that regrows ``runtime_app.py`` past its ceiling, adds a
parallel state implementation, or grows one of the tracked redundancy metrics
(getattr/hasattr probes, persistence wrappers, ``app = ctx.app`` unwraps) trips
a guardrail.

Per the teardown plan the budgets were warn-only through Phases 1-8 and
**hard-fail from Phase 9** — ``_HARD_FAIL`` is now ``True``. A change that
regrows ``runtime_app.py`` past its budgeted ceiling, or adds a parallel state
implementation, now fails the suite. Lower the budgets when a change legitimately
shrinks the file; never raise them.
"""

from __future__ import annotations

import ast
import warnings
from pathlib import Path
from tests._repo_scan import parse_source

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_APP = REPO_ROOT / "review" / "runtime_app.py"
#: The core state modules, named because they are the boundary itself.
_CORE_STATE = ("app_state.py", "event_bus.py", "dataset_state.py",
               "selection_state.py", "plot_state.py", "channel_state.py")


def _state_files() -> list:
    """Every state module, *derived* rather than listed.

    The hand-written half of this roster named three feature paths that moved
    under `features/image_browser/` long ago. The existence check below then
    skipped the whole test — silently, for as long as the paths were stale — so
    the one thing enforcing this boundary enforced nothing. A `rglob` cannot go
    stale on a move, and covers a feature added tomorrow.
    """
    core = [REPO_ROOT / "review" / "core" / n for n in _CORE_STATE]
    feature = sorted((REPO_ROOT / "review" / "features").rglob("state.py"))
    return core + feature


STATE_FILES = _state_files()
FEATURES_DIR = REPO_ROOT / "review" / "features"

# Budgets hard-fail (Phase 9 onward).
_HARD_FAIL = True

# Ratcheting ceilings. Re-measured as the teardown proceeds; lowered as each
# phase removes code. Phase 0 baseline: 7028 lines / 166 attrs.
# Teardown-completion Phase 1 (helper extraction + dead-code deletion): 6628.
# Phase 3a added 3 lines of load-bearing EventBus wiring (RedrawCoordinator).
# Phase 4 (PersistenceRegistry) removed ~17 wrapper methods + 5 debounce flags.
# Phase 8 relocated the _build_* builders into views/: stats group,
# _build_groups_centre, _build_preview_picker, _build_cell_gating_subtab,
# and _build_ui (the window-shell assembler -> views/app_shell_view.py).
# Phase 9 (Bar) relocated _draw_per_cell_bar_mode + helpers into barplot_renderer.py.
# Phase 9 (Scatter) moved _redraw_scatter / _on_scatter_click / _on_scatter_motion /
# _redraw_scatter_agg bodies into scatter_controller, _on_fig_click into lineplot_controller.
# Phase 9 (Review-image) moved 6 _on_review_image_* handler bodies into
# review_image_controller (wheel / press / drag / release / move / hover).
# Phase 9 (Selection) moved 6 _on_sidebar_plate_* handler bodies into
# selection_controller.
# Phase 9 (Stats/RI-channel) moved _stats_on_test_change, _stats_update_tp_menu,
# _stats_refresh_colors, _on_review_image_channel_selected.
# Phase 9b moved _on_tab_change + _on_notebook_current_changed bodies into
# new tab_controller (with the centre-stack dispatch + notebook history).
# Phase 10 deleted the legacy __getattr__ widget forwarder; WidgetRegistry now
# stamps registered widgets directly onto the bound app, so plain attribute
# lookup resolves them and the forwarder is no longer needed.
# The redundancy audit deleted the 86 methods that no call site anywhere in the
# repo — app, tests, or docs — still named. Nearly all were one-line delegation
# shims left behind after the ctx migration rewired their callers to the
# controller functions directly, so the shim survived with nothing pointing at
# it. 199 methods → 113, and the imports they were the sole users of went with
# them.
# The architecture-review sweep deleted three more delegators whose last call
# site had already been rewired (_render_review_image_display,
# _set_review_image_include_mode, _update_tp_selection_display): 876 → 862.
# The A8 teardown finished for the replicate-set helpers: the nine-symbol lazy
# bind into features/grouping went, four of its names being dead, and the three
# ctx-shaped adapters were replaced by direct domain calls. 862 -> 856.
# The simplification audit deleted three delegators that no caller had left —
# _sel_set_composition, _sel_select and _rank_color_wells, whose backing
# functions the Groups views already call through ctx directly: 856 -> 849.
# Freezing the plotting facades pointed the bar lazy binds at the modules that
# own their names, and deleted the nine lazy binds nothing read (five bar
# helpers, two figure-save orchestrators, two review-image handlers): 849 -> 844.
RUNTIME_APP_LINE_BUDGET = 844
# Phase 8 moved widget attrs out with their builders: _build_groups_centre (3),
# _build_cell_gating_subtab (1), _build_ui (14).
# Code-review X13: removed 3 dead never-read attrs (_montage_chan_cb,
# _bar_quick_pair_dir, _bar_quick_iter_order) → 71 - 3 = 68.
# Roadmap A1: relocated 10 widget-handle placeholder None-seeds
# (_plot_chan_cb, _review_image_chan_cb, _bar_tp_cb, _bar_swarm_cb,
# _bar_violin_cb, _violin_bw_edit, _entry_edit, _cdf_xmin_edit, _cdf_xmax_edit,
# _preview_fov_cb) — builders register them via WidgetRegistry. 23 → 13 actual.
RUNTIME_APP_ATTR_BUDGET = 13

# Redundancy-metric ceilings (teardown-completion Phase 2). Each tracks a
# concrete duplication the teardown consumes; like the budgets above they only
# ratchet down. A PR that adds a parallel implementation trips one of these.
RUNTIME_APP_GETATTR_BUDGET = 5          # defensive getattr/hasattr probes
RUNTIME_APP_PERSIST_WRAPPER_BUDGET = 0   # save/load persistence wrappers
TAB_CTX_APP_UNWRAP_BUDGET = 0           # `app = ctx.app` unwraps in tabs/ (Phase 6+7 collapsed)

# ── God-object burn-down ratchets (roadmap WS-A; see
#    _Docs/Markdowns/GOD_OBJECT_REPOSITORY_ROADMAP.md) ─────────────────────────
# Two remaining god-object dimensions the `ctx.app` burn-down never metered.
# Seeded at the measured values on the main baseline (build-time-feature-profiles
# merged via PR #639); they only ratchet down as WS-A migrates the raw-`self`
# delegators (A2/A4) and the `app`-first free functions (A3). The `_not_slack`
# twin forces the budget down once a phase legitimately shrinks the count.
CORE_DIR = REPO_ROOT / "review" / "core"
PERSIST_DIR = REPO_ROOT / "review" / "persistence"
RAW_SELF_DELEGATOR_BUDGET = 0          # `def _m(self,...): return fn(self, ...)` in runtime_app.py — A2/A4 COMPLETE
RAW_SELF_DELEGATOR_SLACK = 0
# B4: every PersistenceRegistry-facing entry point takes `ctx`, not the god-object.
PERSIST_APP_PARAM_BUDGET = 0
APP_FIRST_FUNC_BUDGET = 0             # top-level funcs in core/+features/ whose first param is `app`
APP_FIRST_FUNC_SLACK = 15               # A3: playground 135→120, stats 120→109, grouping 109→107, heatmap-sidebar 107→102, ui_helpers-notify 102→94, plot_orch-save 94→88, tab_controller 88→80, split_view 80→68, selection_controller 68→43, load_controller 43→34, registry hooks 34→26, channel 26→22, ui_helpers 22→19, leaf 19→12, builder-chain 12→5, export/style 5→0 (A3 COMPLETE)
# Terminal condition #3 (vertical-slice purity): core/ must eventually carry ZERO
# feature imports. Module-scope core→feature imports are already banned at 0 by
# ``test_architecture_contracts``; the residual is FUNCTION-LOCAL imports — the
# interleaved post-load / redraw / event dispatch that needs an ordered-hook
# design to invert (roadmap phase B1b, a deferred epic). This ratchet meters that
# residual so it can only shrink. Seeded at today's count; WELL_MIME moved to
# ``core/dnd`` took it 34→33; deleting the dead ``ui_helpers.on_theme_change``
# (which held the last function-local imports of ``features.grouping.tab_view``
# and ``features.stats.controller``) took it 33→31; moving the manual-curation
# projector out of Review Image into ``data/curation.py`` (roadmap B4b) took the
# loader's import of it with it, 31→30.
CORE_FEATURE_IMPORT_BUDGET = 0
CORE_FEATURE_IMPORT_SLACK = 0


def _enforce(ok: bool, message: str) -> None:
    """Hard-fail or warn depending on the phase (see ``_HARD_FAIL``)."""
    if ok:
        return
    if _HARD_FAIL:
        raise AssertionError(message)
    warnings.warn(message, stacklevel=2)


def _distinct_self_attrs(path: Path) -> set[str]:
    """Distinct ``self._*`` names that are assignment targets in *path*."""
    tree = parse_source(path)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        else:
            continue
        for target in targets:
            for sub in ast.walk(target):
                if (isinstance(sub, ast.Attribute)
                        and isinstance(sub.value, ast.Name)
                        and sub.value.id == "self"
                        and sub.attr.startswith("_")):
                    names.add(sub.attr)
    return names


def test_runtime_app_line_budget():
    lines = len(RUNTIME_APP.read_text().splitlines())
    _enforce(
        lines <= RUNTIME_APP_LINE_BUDGET,
        f"runtime_app.py is {lines} lines, over the {RUNTIME_APP_LINE_BUDGET} "
        "budget. The teardown ratchet only moves down — extract, don't append.",
    )


def test_runtime_app_attr_budget():
    count = len(_distinct_self_attrs(RUNTIME_APP))
    _enforce(
        count <= RUNTIME_APP_ATTR_BUDGET,
        f"ReviewApp assigns {count} distinct self._* attributes, over the "
        f"{RUNTIME_APP_ATTR_BUDGET} budget. Move state into typed state objects.",
    )


def test_state_package_import_contract():
    """``review.state.*`` must never import runtime_app / views / tabs.

    That acyclic boundary is what lets controllers depend on the state package
    directly — the key unlock of the teardown. Unlike the budgets this is a
    hard invariant: a breach is a real import cycle, so it always fails.
    """
    missing = [str(p.relative_to(REPO_ROOT)) for p in STATE_FILES if not p.exists()]
    assert not missing, (
        "STATE_FILES names a module that does not exist, so this guard would "
        f"have measured nothing: {missing}")

    forbidden = (
        "review.runtime_app",
    )
    offenders: list[str] = []
    for py in sorted(p for p in STATE_FILES if p.exists()):
        tree = parse_source(py)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                if any(module == f or module.startswith(f + ".")
                       for f in forbidden):
                    offenders.append(
                        f"{py.relative_to(REPO_ROOT)}: imports {module}")

    assert not offenders, (
        "review/state/ broke its import contract:\n  "
        + "\n  ".join(offenders)
    )


# ── Redundancy-metric ratchets ───────────────────────────────────────────────
#
# A co-equal goal of the teardown is consuming redundancy, not relocating it.
# Each counter below tracks a concrete duplication a later phase eliminates;
# the guardrail holds the count from rising in the meantime.


def _getattr_hasattr_calls(path: Path) -> int:
    """Count ``getattr()`` / ``hasattr()`` call sites in *path*.

    These defensive probes exist because the god-object's construction order
    was uncertain. Dependency injection (Phases 6-10) makes presence
    guaranteed, so each migrated domain deletes its probes.
    """
    tree = parse_source(path)
    return sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in ("getattr", "hasattr")
    )


def _persistence_wrapper_methods(path: Path) -> int:
    """Count the save/load persistence wrapper methods in *path*.

    Each merely forwards to a ``review.persistence.<domain>`` function;
    Phase 4 folds all of them into one ``PersistenceRegistry``.
    """
    tree = parse_source(path)
    return sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and (node.name.endswith("_save_to_data_dir")
             or node.name.endswith("_load_from_data_dir"))
    )


def _ctx_app_unwraps(directory: Path) -> int:
    """Count ``app = ctx.app`` unwrap statements across *directory*.

    The redundant verbatim line that opens nearly every tab builder. DI
    narrowing (Phases 6-7) removes one per builder as its controller takes
    ``ctx`` directly.
    """
    if not directory.is_dir():
        return 0
    total = 0
    for py in sorted(directory.rglob("*.py")):
        tree = parse_source(py)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "app"
                    and isinstance(node.value, ast.Attribute)
                    and node.value.attr == "app"
                    and isinstance(node.value.value, ast.Name)
                    and node.value.value.id == "ctx"):
                total += 1
    return total


def test_runtime_app_getattr_probe_budget():
    count = _getattr_hasattr_calls(RUNTIME_APP)
    _enforce(
        count <= RUNTIME_APP_GETATTR_BUDGET,
        f"runtime_app.py has {count} getattr/hasattr probes, over the "
        f"{RUNTIME_APP_GETATTR_BUDGET} budget. These defensive probes exist "
        "only because construction order is uncertain; DI removes the "
        "uncertainty. The ratchet only moves down — delete probes, don't add.",
    )


def test_runtime_app_persistence_wrapper_budget():
    count = _persistence_wrapper_methods(RUNTIME_APP)
    _enforce(
        count <= RUNTIME_APP_PERSIST_WRAPPER_BUDGET,
        f"runtime_app.py has {count} persistence save/load wrapper methods, "
        f"over the {RUNTIME_APP_PERSIST_WRAPPER_BUDGET} budget. Phase 4 folds "
        "these into one PersistenceRegistry — the ratchet only moves down.",
    )


def test_tab_ctx_app_unwrap_budget():
    count = _ctx_app_unwraps(FEATURES_DIR)
    _enforce(
        count <= TAB_CTX_APP_UNWRAP_BUDGET,
        f"review/features/ has {count} 'app = ctx.app' unwraps, over the "
        f"{TAB_CTX_APP_UNWRAP_BUDGET} budget. DI narrowing (Phases 6-7) removes "
        "one per builder — the ratchet only moves down.",
    )


# ── God-object burn-down ratchets (roadmap WS-A) ─────────────────────────────


def _raw_self_delegators(path: Path) -> list[str]:
    """Names of methods in *path* whose sole body statement is
    ``return fn(self, ...)`` — i.e. a one-line delegator that forwards the raw
    god-object (``self``) rather than the narrowed ``self._ctx``. A ``self._ctx``
    forwarder passes an ``ast.Attribute`` first arg and is correctly excluded."""
    tree = parse_source(path)
    out: list[str] = []
    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef):
            continue
        for m in cls.body:
            if not isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = [s for s in m.body
                    if not (isinstance(s, ast.Expr)
                            and isinstance(s.value, ast.Constant))]  # drop docstring
            if len(body) != 1 or not isinstance(body[0], ast.Return):
                continue
            val = body[0].value
            if (isinstance(val, ast.Call) and val.args
                    and isinstance(val.args[0], ast.Name)
                    and val.args[0].id == "self"):
                out.append(m.name)
    return out


def _app_first_free_funcs(*dirs: Path) -> list[str]:
    """``<path>:<name>`` for every *top-level* (free) function under *dirs* whose
    first parameter is named ``app`` — the un-migrated god-object-first call
    contract WS-A A3 converts to ``ctx``-first."""
    out: list[str] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for py in sorted(d.rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            tree = parse_source(py)
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    a = node.args.args
                    if a and a[0].arg == "app":
                        out.append(f"{py.relative_to(REPO_ROOT)}:{node.name}")
    return out


def test_raw_self_delegator_budget():
    count = len(_raw_self_delegators(RUNTIME_APP))
    _enforce(
        count <= RAW_SELF_DELEGATOR_BUDGET,
        f"runtime_app.py has {count} raw-`self` delegators (`return fn(self, …)`), "
        f"over the {RAW_SELF_DELEGATOR_BUDGET} budget. WS-A A2/A4 flip the callee "
        "to `ctx` and forward `self._ctx` — the ratchet only moves down.",
    )


def test_raw_self_delegator_budget_not_slack():
    count = len(_raw_self_delegators(RUNTIME_APP))
    _enforce(
        count >= RAW_SELF_DELEGATOR_BUDGET - RAW_SELF_DELEGATOR_SLACK,
        f"runtime_app.py has only {count} raw-`self` delegators but the budget is "
        f"{RAW_SELF_DELEGATOR_BUDGET}: lower RAW_SELF_DELEGATOR_BUDGET to {count} "
        "so the ratchet keeps biting.",
    )


def test_app_first_func_budget():
    count = len(_app_first_free_funcs(CORE_DIR, FEATURES_DIR))
    _enforce(
        count <= APP_FIRST_FUNC_BUDGET,
        f"review/core+features have {count} `app`-first free functions, over "
        f"the {APP_FIRST_FUNC_BUDGET} budget. WS-A A3 migrates them to `ctx`-first "
        "cluster by cluster — the ratchet only moves down.",
    )


def test_app_first_func_budget_not_slack():
    count = len(_app_first_free_funcs(CORE_DIR, FEATURES_DIR))
    _enforce(
        count >= APP_FIRST_FUNC_BUDGET - APP_FIRST_FUNC_SLACK,
        f"only {count} `app`-first free functions remain but the budget is "
        f"{APP_FIRST_FUNC_BUDGET}: lower APP_FIRST_FUNC_BUDGET to {count} so the "
        "ratchet keeps biting.",
    )


def _core_feature_imports() -> list[str]:
    """``<file>:<line>`` for every ``review.features.*`` import under
    ``core/``. Module-scope ones are already 0 (``test_architecture_contracts``),
    so this is the function-local residual — terminal condition #3's frontier."""
    out: list[str] = []
    for py in sorted(CORE_DIR.rglob("*.py")):
        for node in ast.walk(parse_source(py)):
            mod = None
            if isinstance(node, ast.ImportFrom):
                mod = node.module
            elif isinstance(node, ast.Import):
                mod = node.names[0].name if node.names else None
            if mod and mod.startswith("review.features"):
                out.append(f"{py.name}:{node.lineno}")
    return out


def test_core_feature_import_budget():
    count = len(_core_feature_imports())
    _enforce(
        count <= CORE_FEATURE_IMPORT_BUDGET,
        f"review/core carries {count} feature imports, over the "
        f"{CORE_FEATURE_IMPORT_BUDGET} budget. Route a new core→feature call "
        "through a FeatureRegistry hook (with_hook) instead of importing the "
        "feature — the ratchet only moves down.",
    )


def test_core_feature_import_budget_not_slack():
    count = len(_core_feature_imports())
    _enforce(
        count >= CORE_FEATURE_IMPORT_BUDGET - CORE_FEATURE_IMPORT_SLACK,
        f"only {count} core→feature imports remain but the budget is "
        f"{CORE_FEATURE_IMPORT_BUDGET}: lower CORE_FEATURE_IMPORT_BUDGET to "
        f"{count} so the ratchet keeps biting.",
    )


def _persistence_app_entry_points() -> list[str]:
    """`<file>:<name>` for every PersistenceRegistry-facing entry point
    (`save_to_data_dir` / `load_from_data_dir` / `reset` / `save_notes` /
    `autosave_to_pipeline_info`) whose first parameter is still `app`. B4 makes
    the persistence layer ctx-native, so this ratchets to 0."""
    names = {"save_to_data_dir", "load_from_data_dir", "reset",
             "save_notes", "autosave_to_pipeline_info"}
    out: list[str] = []
    for py in sorted(PERSIST_DIR.glob("*.py")):
        for node in parse_source(py).body:
            if (isinstance(node, ast.FunctionDef) and node.name in names
                    and node.args.args and node.args.args[0].arg == "app"):
                out.append(f"{py.name}:{node.name}")
    return out


def test_persistence_is_ctx_native():
    offenders = _persistence_app_entry_points()
    _enforce(
        len(offenders) <= PERSIST_APP_PARAM_BUDGET,
        f"{len(offenders)} persistence entry points still take the god-object "
        f"`app`, over the {PERSIST_APP_PARAM_BUDGET} budget (B4 — the registry "
        f"passes `ctx`):\n  " + "\n  ".join(offenders),
    )
