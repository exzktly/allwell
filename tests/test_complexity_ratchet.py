"""Audit G6 — a function-level cyclomatic-complexity ratchet.

The monolith line-budgets (``test_monolith_budgets.py``) cap *file* size but not
*function* complexity, so a CC-98 function could hide under budget. This test
caps per-function cyclomatic complexity at ``THRESHOLD`` with a frozen allowlist
of the functions currently over it. The allowlist may only ever **shrink**:

  * a NEW function over the threshold fails the test (write it simpler, or —
    only with justification — add it to the allowlist), and
  * decomposing an allowlisted function below the threshold fails the test until
    you remove its now-stale entry, so the debt list stays honest.

Self-contained (no radon dependency, which CI does not install). The CC metric
here is a simple decision-point count; the absolute numbers differ from radon's
but the ratchet only cares about its own consistent baseline.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
THRESHOLD = 40

_DECISION = (
    ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler,
    ast.With, ast.AsyncWith, ast.IfExp, ast.Assert, ast.comprehension,
)
_MATCH_CASE = getattr(ast, "match_case", ())


def _cc(node: ast.AST) -> int:
    score = 1
    for n in ast.walk(node):
        if isinstance(n, _DECISION):
            score += 1
        elif isinstance(n, ast.BoolOp):
            score += len(n.values) - 1
        elif _MATCH_CASE and isinstance(n, _MATCH_CASE):
            score += 1
    return score


def _iter_source_files():
    for p in REPO_ROOT.rglob("*.py"):
        s = p.relative_to(REPO_ROOT).as_posix()
        # `.claude/worktrees/*` holds throwaway git worktrees (parallel-agent
        # scratch copies of the tree). Scanning them double-counts every source
        # file under a path that is not in the frozen baseline, so exclude the
        # whole `.claude/` tree alongside tests/_Docs/.git.
        if (
            s.startswith("tests/")
            or s.startswith("_Docs/")
            or s.startswith(".claude/")
            or "/.git/" in s
        ):
            continue
        yield p, s


def _functions_over_threshold() -> dict[str, int]:
    out: dict[str, int] = {}

    class V(ast.NodeVisitor):
        def __init__(self, relpath: str):
            self.rel = relpath
            self.stack: list[str] = []

        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            cc = _cc(node)
            if cc > THRESHOLD:
                out[f"{self.rel}::{'.'.join(self.stack)}"] = cc
            for c in node.body:
                self.visit(c)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node):
            self.stack.append(node.name)
            for c in node.body:
                self.visit(c)
            self.stack.pop()

    for p, rel in _iter_source_files():
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError:
            continue
        V(rel).visit(tree)
    return out


# Frozen baseline (CC > 40 as of the code-review remediation). SHRINK ONLY.
# The audit's prime decomposition targets — A4 (find_well_images_and_masks),
# A5 (redraw_line_plots, run_stats) — are all DONE (see notes below). Remove an
# entry when you take it under 40.
ALLOWLIST = {
    "well_viewer/features/barplot/collect.py::collect_bar_items",
    "well_viewer/features/barplot/collect.py::collect_bar_items._items_for_tp",
    # A4 DONE: find_well_images_and_masks (was CC 98) decomposed into
    # _merge_scan / _discover_from_layouts / _scan_raw_disk_files /
    # _raw_file_well_passes / _log_find_result — main orchestrator now CC 13,
    # every helper < 40. Removed from the allowlist.
    # Moved verbatim out of view_state.py to the Image Table feature (the
    # feature now owns its own view-state serialization via the ``view_state_io``
    # registry hook); CC unchanged, so it stays allowlisted at the new path.
    "well_viewer/features/image_browser/image_table/view_state_io.py::apply_image_table",
    "well_viewer/core/plot_style.py::apply_export_style_prefs",
    "well_viewer/core/centre_view.py::build_centre",
    "well_viewer/features/export/sidebar_view.py::ExportStyleSidebar._build_ui",
    "well_viewer/persistence/view_state.py::restore",
    "process_microscopy.py::main",
    # A5 DONE: redraw_line_plots (was CC 69) decomposed — the per-series painter
    # (_paint_series) and the legend/CDF finalizer (_finalize_line_axes) were
    # extracted; the function is now CC 28. Removed from the allowlist.
    "well_viewer/core/centre_view.py::build_centre._build_plotting",
    "well_viewer/features/barplot/collect.py::collect_bar_items_for_group",
    "services/remote_runner.py::stage_local_zips",
    "well_viewer/features/heatmap/controller.py::redraw_heatmap",
    "well_viewer/features/image_browser/cell_browser/collect.py::collect_cells",
    # DONE: the pipeline's Otsu estimator (was CC 55) moved to
    # pipeline/auto_threshold.py as estimate_thresholds_from_outputs and was
    # decomposed on the way — _SchemaReader (member-name → FOV-group),
    # _index_members, _sample_one_well and _otsu_per_channel are each separately
    # testable. Removed from the allowlist.
    "well_viewer/features/image_browser/image_table/export.py::_build_export_figure",
    "well_viewer/core/channel_state_controller.py::update_channel_selector",
    "well_viewer/core/selection_controller.py::refresh_sidebar_map_now",
    # A5 DONE: run_stats (was CC 52) decomposed — the pure report formatters
    # (_build_header_lines / _format_group_summary_lines / _format_pairwise_lines
    # / _resolve_sample_units / _sig_marker) were extracted and unit-tested; the
    # function is now CC 30. Removed from the allowlist.
    "well_viewer/features/barplot/controller.py::render_bar_items",
    "well_viewer/core/load_controller.py::load_directory",
    # DONE: collect_line_series dropped below the threshold once the control
    # denominators moved behind ``fold_change_pairing.ControlDenominators`` and
    # the per-member normalize call became ``normalize_member_pts``, shared with
    # the batch line runner. Removed from the allowlist.
    # DONE: draw_per_cell_bar_mode dropped below the threshold once the "Pooled"
    # toggle unified the two rset base-column branches (the old pool_rset_cells
    # and pool_reps branches merged into one). Removed from the allowlist.
    "well_viewer/features/barplot/renderer.py::draw_significance_brackets",
    "well_viewer/features/batch_export/bar_panel.py::BarBatchExportPanel._run_batch",
    "process_microscopy.py::sample_empty_well_images",
    # Was CC 40 (right at the line); the audit-E9 decode-failure counting +
    # bias WARN (mandatory correctness surfacing, mirroring the pipeline-side
    # estimator) nudged it to 42. The Review-side twin of the decompose noted
    # above is still owed.
    "well_viewer/data/auto_threshold.py::compute_auto_thresholds",
}


def test_no_new_high_complexity_functions():
    over = _functions_over_threshold()
    new = set(over) - ALLOWLIST
    assert not new, (
        "New function(s) exceed cyclomatic complexity "
        f"{THRESHOLD} — decompose them (or, with justification, add to "
        f"ALLOWLIST):\n" + "\n".join(f"  {k} (CC {over[k]})" for k in sorted(new))
    )


def test_complexity_allowlist_has_no_stale_entries():
    over = _functions_over_threshold()
    resolved = ALLOWLIST - set(over)
    assert not resolved, (
        "These allowlisted functions are now under the threshold (or were "
        "renamed/removed) — delete them from ALLOWLIST so the ratchet stays "
        "tight:\n" + "\n".join(f"  {k}" for k in sorted(resolved))
    )
