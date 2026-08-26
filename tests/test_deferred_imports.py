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
# Lowered to 128 in the E3 shared-plot-toggle relocation: toggle_sem /
# toggle_fov_replicates / refresh_fov_btn_state moved into core.plot_toggles,
# which reaches the bar renderer + bar-layout refresh through core.lazy proxies
# (a call, not an import statement) rather than the two function-local
# ``from features.barplot...`` imports the toggles used to carry; and
# invalidate_stats_cache's per-FOV refresh import now targets core.plot_toggles
# instead of features.preview — three deferred feature imports removed.
# Lowered to 124 in A8 D4 (review-image draw web → ctx): the renderer's four
# function-local ``review_image.controller`` imports (resolve-refs / include-map
# / threshold-map / flat-field field) were promoted to a single module-level
# ``review_image.collect`` import — collect is the Qt-free data half and never
# imports the renderer, so there is no cycle to defer.
# Raised to 125 for the image_table build_layout module: the smart-layout builder
# cluster (flat + nested grid assembly) was split out of controller.py to hold its
# line budget. build_layout defers its ``controller`` import (for the Qt-side
# rebuild helpers ``image_table_rebuild_grid`` / ``image_table_rebuild_lut_row``,
# the latter defined below controller's re-export point so a module-level import
# would fail during partial init) — the standard one-defer cycle break for an
# extraction, exactly like grid_ops above.
#
# +1 (125 → 126) for the Image Table builder's per-cell override: ``controller.
# _cell_override_menu`` lazily imports ``grid_ops.image_table_cell_override_menu``
# — the same controller↔grid_ops cycle break the two grid_ops imports above use
# (grid_ops imports controller at module scope, so controller must defer).
#
# −8 (126 → 118): the Image Table view-state serialization moved out of
# ``persistence/view_state.py`` into the feature's own ``view_state_io.py`` (the
# ``view_state_io`` registry hook). ``view_state`` now fans out over the registry
# instead of deferring feature imports, netting fewer deferred feature imports
# across the persistence layer.
#
# Raised to 112 for the Image Table export/gallery-save path: ``export.py``'s
# ``_build_export_figure`` now shares (and pre-warms via) the same threaded
# prefetch helper ``controller._prefetch_cell_arrays`` uses, so a save run
# without a prior Generate is no longer forced to decode every cell
# sequentially. ``export`` is already imported at module scope by
# ``controller`` (the re-export point noted above), so the reverse edge —
# ``export`` needing one function out of ``controller`` — must stay a deferred
# import to avoid a real init-order cycle, not just a style choice.
# 112 -> 110: `ui_helpers.on_theme_change` was deleted as dead code, taking
# its function-local imports of grouping/tab_view and stats/controller.
# 110 -> 108: the reset scopes replaced `sample_definitions.clear_all`, whose
# body reached into grouping/label_grid_view and cell_gating/controller. The
# replacement (`persistence/reset_scopes.py`) reaches every feature through a
# stamped `ctx.runtime.<feature>` callback instead, so a module that clears
# state across the whole app imports no feature at all.
# 108 -> 104: the sample definitions became tabs of Home, and the page builder
# that assembled them (`grouping.replicate_panel_view.build_sample_definitions_centre`)
# was deleted with its four function-local imports of the cell-gating, ratio and
# pipeline-info builders. Home reaches those same builders through the registry
# instead: each tab is a descriptor whose `build` adapter in `features/registry.py`
# holds the only import of its package, so the escape hatch is not re-opened
# somewhere else.
# 104 -> 103: the per-cell export added nine modules that reach across feature
# packages, and every one of them resolved at module scope. Only the three that
# genuinely cannot — the export service's lazy `barplot` / `scatter` proxies,
# which exist so those features stay deletable — remain deferred, and the Save
# menu's own builder takes its callbacks as plain callables rather than
# importing anything. `export/service.py` also stopped deferring its two
# intra-package imports (`cell_scope_compute`, `worker`), which were never a
# cycle to begin with.
MAX_DEFERRED_FEATURE_IMPORTS = 103


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
