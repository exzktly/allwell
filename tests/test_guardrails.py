"""CI guardrails for the ``runtime_app`` teardown.

These ratchet the god-object's decline. Each phase re-measures and tightens the
budgets; a phase that regrows ``runtime_app.py`` past its ceiling, or adds a
parallel state implementation, trips a guardrail.

Per the teardown plan the budgets are **warn-only through Phases 1-8** (a breach
emits a warning but the test still passes) and **hard-fail from Phase 9** — flip
``_HARD_FAIL`` then. The line budget rose slightly in Phase 1 because the
ChannelState ``@property`` shims are transitional; deleting them in later phases
brings it back down well below the Phase 0 baseline of 7028.
"""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_APP = REPO_ROOT / "well_viewer" / "runtime_app.py"
STATE_DIR = REPO_ROOT / "well_viewer" / "state"

# Flip to True at Phase 9 so the budgets hard-fail instead of warning.
_HARD_FAIL = False

# Ratcheting ceilings. Re-measured and lowered as the teardown proceeds.
RUNTIME_APP_LINE_BUDGET = 7060   # Phase 0 baseline 7028; +32 for Phase 1 shims
RUNTIME_APP_ATTR_BUDGET = 160    # Phase 0 baseline 166 (Phase 1: 162)


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
