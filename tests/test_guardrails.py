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

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_APP = REPO_ROOT / "well_viewer" / "runtime_app.py"
STATE_DIR = REPO_ROOT / "well_viewer" / "state"
TABS_DIR = REPO_ROOT / "well_viewer" / "tabs"

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
RUNTIME_APP_LINE_BUDGET = 5585
# Phase 8 moved widget attrs out with their builders: _build_groups_centre (3),
# _build_cell_gating_subtab (1), _build_ui (14).
RUNTIME_APP_ATTR_BUDGET = 74

# Redundancy-metric ceilings (teardown-completion Phase 2). Each tracks a
# concrete duplication the teardown consumes; like the budgets above they only
# ratchet down. A PR that adds a parallel implementation trips one of these.
RUNTIME_APP_GETATTR_BUDGET = 206        # defensive getattr/hasattr probes
RUNTIME_APP_PERSIST_WRAPPER_BUDGET = 0   # save/load persistence wrappers
TAB_CTX_APP_UNWRAP_BUDGET = 12          # `app = ctx.app` unwraps in tabs/


def _enforce(ok: bool, message: str) -> None:
    """Hard-fail or warn depending on the phase (see ``_HARD_FAIL``)."""
    if ok:
        return
    if _HARD_FAIL:
        raise AssertionError(message)
    warnings.warn(message, stacklevel=2)


def _distinct_self_attrs(path: Path) -> set[str]:
    """Distinct ``self._*`` names that are assignment targets in *path*."""
    tree = ast.parse(path.read_text())
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
        f"WellViewerApp assigns {count} distinct self._* attributes, over the "
        f"{RUNTIME_APP_ATTR_BUDGET} budget. Move state into typed state objects.",
    )


def test_state_package_import_contract():
    """``well_viewer.state.*`` must never import runtime_app / views / tabs.

    That acyclic boundary is what lets controllers depend on the state package
    directly — the key unlock of the teardown. Unlike the budgets this is a
    hard invariant: a breach is a real import cycle, so it always fails.
    """
    if not STATE_DIR.is_dir():
        pytest.skip("well_viewer/state/ not created yet (introduced in Phase 1)")

    forbidden = (
        "well_viewer.runtime_app",
        "well_viewer.views",
        "well_viewer.tabs",
    )
    offenders: list[str] = []
    for py in sorted(STATE_DIR.rglob("*.py")):
        tree = ast.parse(py.read_text())
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
        "well_viewer/state/ broke its import contract:\n  "
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
    tree = ast.parse(path.read_text())
    return sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in ("getattr", "hasattr")
    )


def _persistence_wrapper_methods(path: Path) -> int:
    """Count the save/load persistence wrapper methods in *path*.

    Each merely forwards to a ``well_viewer.persistence.<domain>`` function;
    Phase 4 folds all of them into one ``PersistenceRegistry``.
    """
    tree = ast.parse(path.read_text())
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
        tree = ast.parse(py.read_text())
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
    count = _ctx_app_unwraps(TABS_DIR)
    _enforce(
        count <= TAB_CTX_APP_UNWRAP_BUDGET,
        f"well_viewer/tabs/ has {count} 'app = ctx.app' unwraps, over the "
        f"{TAB_CTX_APP_UNWRAP_BUDGET} budget. DI narrowing (Phases 6-7) removes "
        "one per builder — the ratchet only moves down.",
    )
