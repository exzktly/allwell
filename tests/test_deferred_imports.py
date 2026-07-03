"""Ratchet guard: the count of *function-local* ``from well_viewer.features …``
imports may only go **down**.

Function-local (deferred) feature imports are the sanctioned escape hatch for a
genuine, isolated cycle break (see ``tests/test_layering.py``). They are not
free, though: a high count is the symptom of inter-feature cycles, and an audit
of this codebase found ~47% of all ``from well_viewer`` imports were deferred
cycle-workarounds. T2 broke the worst offenders (the scatter↔export cycle, via
``core/plot_style.py`` + ``scatter/collect.py``) and promoted the now-acyclic
deferred imports to module scope.

This guard locks that progress in: it counts every deferred
``from well_viewer.features...`` import and asserts the total never exceeds the
current budget. The budget is a *ceiling that only ratchets down* — when you
remove a deferred import (by breaking a cycle and promoting it to module level),
lower ``MAX_DEFERRED_FEATURE_IMPORTS`` to the new count in the same commit so the
win can't silently regress. Adding a new deferred feature import fails this test:
break the cycle properly (extract shared code down a layer, or invert the
dependency) instead.

A genuinely irreducible bidirectional interaction (e.g. crop↔review_image, where
crop is a draw-mode of the review-image canvas) may keep *one* documented
deferred import — those are part of the budget below.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "well_viewer"
FEATURE_PREFIX = "well_viewer.features"

# Ceiling. Seeded at the post-T2 count. ONLY EVER LOWER THIS NUMBER.
# Lowered to 136 when the QC debris-gate controls moved to the Cell Gating tab:
# the Segmentation panel's deferred ``build_qc_gate_controls`` import was dropped
# and the new home imports it at module scope.
# Lowered to 134 in the Image Browser consolidation: the unified container's
# sub-tab builders import at module scope (not a cycle — centre_view defers the
# container import), and the Playground sidebar refresh promoted to module scope.
# Raised to 135 for the image_table grid_ops module: grid_ops imports the rebuild
# helpers from controller, so controller defers its grid_ops import (to wire the
# row/column remove callbacks) to break the controller ↔ grid_ops cycle.
# Raised to 137 for the Saved Plots tab: core/centre_view defers its tab_view
# builder import and core/tab_controller defers its controller import (core must
# not import features at module scope) — two standard core → feature defers, not
# new inter-feature cycles. The six plot tabs wire the save button via a
# module-level import, so they add nothing here.
# Lowered to 124 in the feature-registry migration: centre_view's per-tab build
# closures, tab_controller's on_tab_change ladder, selection_controller's
# _refresh_tab_view ladder, and channel_state_controller's channel fan-out all
# moved their deferred feature imports into features/registry.py (excluded above),
# net-dropping the core count.
# Raised to 131 making every feature deletable: the six plot tabs + distribution
# now defer their saved_plots `wire_save_to_gallery` import (Saved Plots is a
# removable feature, so it can't be imported at module scope). The other
# inter-feature decouplings use the core/lazy.py proxy (a call, not an import
# statement), so they add nothing here.
# Lowered to 130 in the X3 batch-export atomicity work: base_panel's two
# function-local `batch_export.service` imports (`_save_figure` and the new
# staging call in `_run_batch_jobs`) were hoisted to a single module-level
# import — `service.py` is Qt-free, so it's safe at module scope — net-dropping
# the count by one.
# Raised to 131 in the A8 core-spine burn-down: ``invalidate_stats_cache`` went
# ctx-native and reaches the (Qt) per-FOV button refresh in
# ``features.preview.panel_view`` through a function-local import — the
# sanctioned escape hatch for a core module invoking a feature UI side-effect
# without naming the god-object.
MAX_DEFERRED_FEATURE_IMPORTS = 131


def _is_feature_import(node: ast.ImportFrom) -> bool:
    mod = node.module or ""
    return mod == FEATURE_PREFIX or mod.startswith(FEATURE_PREFIX + ".")


def _deferred_feature_imports(tree: ast.Module):
    """Yield ``(lineno, module)`` for every ``from well_viewer.features …``
    import that is NOT at module top level (i.e. nested in a function / method
    / conditional block)."""
    toplevel = {id(n) for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and _is_feature_import(node)
            and id(node) not in toplevel
        ):
            yield node.lineno, node.module


# ``features/registry.py`` is the single sanctioned feature-importer (the
# import-inversion seam): every build/hook callable lazily imports its feature
# module on call so the registry pulls no heavy deps at import time. Those lazy
# imports are *intentional* lazy-loading, not cycle workarounds, so they are
# excluded from this budget.
_EXCLUDED = {ROOT / "features" / "registry.py"}


def _collect():
    hits = []
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or path in _EXCLUDED:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for lineno, module in _deferred_feature_imports(tree):
            hits.append(f"{path.relative_to(ROOT.parent)}:{lineno} -> {module}")
    return hits


def test_deferred_feature_imports_do_not_exceed_budget():
    hits = _collect()
    count = len(hits)
    assert count <= MAX_DEFERRED_FEATURE_IMPORTS, (
        f"Deferred (function-local) 'from well_viewer.features …' imports rose to "
        f"{count}, above the budget of {MAX_DEFERRED_FEATURE_IMPORTS}. A deferred "
        f"feature import is a cycle-workaround — break the cycle (extract the "
        f"shared code into a lower layer, or invert the dependency) rather than "
        f"adding another. If you genuinely removed some, lower the budget instead.\n"
        + "\n".join(hits)
    )


def test_budget_is_not_set_too_high():
    """Keep the ceiling honest: if the real count drops well below the budget,
    the budget should have been lowered to match. Fails when the budget is more
    than 5 above the actual count (a reminder to ratchet it down)."""
    count = len(_collect())
    assert MAX_DEFERRED_FEATURE_IMPORTS - count <= 5, (
        f"Only {count} deferred feature imports remain but the budget is "
        f"{MAX_DEFERRED_FEATURE_IMPORTS}. Lower MAX_DEFERRED_FEATURE_IMPORTS to "
        f"{count} to lock in the improvement."
    )
