"""Saved Plots controller — capture, gallery refresh, restore/delete/export.

Reaches the app only through the injected ``ctx`` (ARCHITECTURE §9); holds no
module-level state. The store lives in the feature runtime namespace
(``ctx.runtime.saved_plots.store``; see ``state.get_store``).
"""

from __future__ import annotations

import logging
from datetime import datetime

from well_viewer.core.lazy import lazy_names
from well_viewer.core.safe import swallowed
from well_viewer.persistence import view_state
from well_viewer.features.saved_plots._report import build_saved_plots_report
from well_viewer.features.saved_plots.collect import build_summary, capture_params
from well_viewer.features.saved_plots.state import SavedPlot, get_store

_logger = logging.getLogger("well_viewer")

# Stats is a removable feature → import lazily (a call, not a module-scope
# ``import`` statement, so it's off the deferred-feature-import budget and honours
# the removability contract). See tests/test_deferred_imports + core/lazy.py.
_compute_pairwise, _deserialize_stats_groups = lazy_names(
    "well_viewer.features.stats.controller",
    "compute_pairwise", "deserialize_stats_groups",
)


def wire_save_to_gallery(ctx, card, tab_name: str) -> None:
    """Enable *card*'s hover "save to Saved Plots" button for *tab_name*."""
    if card is None or not hasattr(card, "set_save_handler"):
        return
    card.set_save_handler(
        lambda png, label: save_plot_from_card(ctx, tab_name, png, label)
    )


def save_plot_from_card(ctx, tab: str, png_bytes: bytes, panel_label: str) -> str:
    """Persist the captured panel + its params; refresh the gallery.

    Returns a short status string (shown as a toast by ``PlotCard``)."""
    store = get_store(ctx)
    store.ensure_loaded(ctx)
    if store.images_dir(ctx) is None:
        return "Save failed: load a dataset first"

    params, thresholds = capture_params(ctx, tab)
    title = (panel_label or "").strip() or tab or "Plot"
    pid = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    rec = SavedPlot(
        id=pid,
        created_at=datetime.now().isoformat(timespec="seconds"),
        tab=tab,
        title=title,
        png_filename=f"{pid}.png",
        params=params,
        thresholds=thresholds,
        summary=build_summary(tab, params, thresholds),
    )
    if not store.add(ctx, rec, png_bytes):
        return "Save failed: could not write image"
    refresh_saved_plots_gallery(ctx)
    return f"Saved “{title}” to Saved Plots"


def restore_saved_plot(ctx, plot_id: str) -> None:
    """Re-apply a saved plot's params and navigate to its originating tab."""
    rec = get_store(ctx).get(plot_id)
    if rec is None:
        return
    # Re-apply the gating block (fluor gates / ON cutoffs / scatter-gate config)
    # FIRST so the Included population that shapes every downstream plot matches
    # the saved figure — the async gating worker recomputes it, and the redraws
    # below (and the worker's own completion redraw) reflect it.
    _restore_thresholds(ctx, rec.thresholds)
    # Restore the group-driven selections so the plot's bars/traces match the
    # saved figure (view_state doesn't snapshot them), THEN re-run the saved stats
    # test so ``stats.pairwise`` is repopulated against those groups. Both happen
    # before ``view_state.restore`` activates the tab and does the final redraw —
    # which draws the significance brackets from the fresh results.
    _restore_selections(ctx, rec.params.get("selections"),
                        rec.params.get("active_solo_wells"))
    _restore_stats(ctx, rec.params.get("stats"))
    try:
        view_state.restore(ctx, dict(rec.params))
    except Exception:
        _logger.debug("saved_plots: restore failed for %r", plot_id, exc_info=True)
    # view_state.restore ends with a tab activation, but ``setCurrentByName`` fires
    # no change (hence no style pass / no deferred per-tab apply) when the saved
    # tab is already current. Unconditionally flush the target tab's deferred
    # per-tab state (Heat Map / Bar / Scatter / Distribution / Image Table) and
    # force its redraw, so restored axis limits + Heat-Map display settings reach
    # the live figure regardless of whether the tab actually switched.
    target = rec.params.get("active_centre_tab") or rec.tab
    _reapply_target_tab(ctx, target)
    _report_missing_tab_block(ctx, rec, target)


def _report_missing_tab_block(ctx, rec, tab: str) -> None:
    """Say so when the record carries no per-tab settings for the tab it names.

    A record saved before a tab began storing its own block restores the
    cross-cutting state and nothing tab-specific — the Image Table's wells,
    timepoints, channels and LUTs, say. Left silent that is indistinguishable
    from a restore that failed, and the user re-clicks Restore rather than
    re-saving the plot, which is the one thing that would fix it.

    Only for tabs that keep a block at all: most tabs have none and never had,
    and a warning there would be false.
    """
    if not tab:
        return
    try:
        if not view_state.owns_tab_block(ctx, tab):
            return
        blocks = rec.params.get("tabs")
        if isinstance(blocks, dict) and blocks.get(tab):
            return
        ctx.notify.set_status(
            f"Restored “{rec.title}” — but it carries no saved {tab} settings "
            f"(saved before this tab recorded them). Re-save it to capture them.")
    except Exception:
        _logger.debug("saved_plots: missing-block report failed", exc_info=True)


def _reapply_target_tab(ctx, tab: str) -> None:
    """Flush deferred per-tab state for *tab* and force its figure redraw.

    Robust delivery for state that only reaches the figure via a redraw's style
    pass (axis limits) or a deferred per-tab apply (Heat Map view_state_io) — both
    of which ``view_state.restore``'s tab activation skips when the tab is already
    the current one."""
    if not tab:
        return
    try:
        view_state.apply_pending_tab_state(ctx, tab)
    except Exception:
        _logger.debug("saved_plots: pending tab flush failed for %r", tab, exc_info=True)
    try:
        ctx.features.redraw(ctx, tab)  # no-op for non-plot tabs
    except Exception:
        _logger.debug("saved_plots: target-tab redraw failed for %r", tab, exc_info=True)


def _restore_thresholds(ctx, block) -> None:
    """Re-apply a saved plot's gating *block* (as produced by
    ``build_gating_block``): fluor gates, ON cutoffs, and the scatter-gate
    inclusion/thresholding config.

    Delegates to ``persistence.cell_gating.restore_gating_block``, which resets
    the live gating slice to defaults FIRST (so a gate the user added after saving
    is cleared), applies the saved block, and kicks the gating worker so the
    Included population is recomputed → plots update. No-op-safe on any error / an
    empty block (older records saved with all-default gating)."""
    from well_viewer.persistence.cell_gating import restore_gating_block
    try:
        restore_gating_block(ctx, block)
    except Exception:
        _logger.debug("saved_plots: threshold restore failed", exc_info=True)


def _restore_selections(ctx, sels, solo_wells=None) -> None:
    """Reproduce the saved group-driven plotting selection: the replicate-set
    ``selections`` AND the soloed individual wells (``active_solo_wells``), then
    refresh the sidebar + plots. Each part is optional (older saved plots omit
    them); a no-op on any error. Applied together so the single ``_rebuild_all``
    redraw sees the full restored selection."""
    try:
        import copy
        sel_state = ctx.state.selection
        changed = False
        if isinstance(sels, list) and sels:
            sel_state.selections = copy.deepcopy(sels)
            sel_state.validate()  # re-mint ids / repair, and fix current_selection_id
            first = sel_state.selections[0] if sel_state.selections else None
            sel_state.current_selection_id = first.get("id") if first else None
            changed = True
        if isinstance(solo_wells, (list, set, tuple)):
            paths = ctx.state.dataset.well_paths
            sel_state.active_solo_wells = {w for w in solo_wells if w in paths}
            changed = True
        if changed:
            # Master redraw fan-out (sidebar map + groups + bars/lines/scatter),
            # named as a genuine app-orchestration service on the context.
            ctx.coordinator.rebuild_all()
    except Exception:
        _logger.debug("saved_plots: selection restore failed", exc_info=True)


def _restore_stats(ctx, cfg) -> None:
    """Re-run the saved stats config to regenerate ``stats.pairwise`` + groups.

    Deterministic re-run (same config + data → same brackets). No-op for saved
    plots without a ``stats`` block (backward compatible) or on any error."""
    if not isinstance(cfg, dict):
        return
    try:
        groups = _deserialize_stats_groups(ctx, cfg.get("groups"))
        target_t = cfg.get("target_t")
        pairwise = _compute_pairwise(
            ctx, groups=groups,
            target_t=(float(target_t) if target_t is not None else None),
            val_col=cfg.get("val_col"), statistic=cfg.get("statistic", "mean"),
            threshold=cfg.get("threshold"), test=cfg.get("test", ""),
            use_fov=bool(cfg.get("use_fov", True)),
        )
        # Same gate as the bar renderer's. Saved Plots and Statistics are
        # separately removable, so a build with one and not the other reaches
        # here — and this sits inside a broad ``except`` that logs at DEBUG, so
        # the AttributeError did not crash, it just meant a restored plot
        # silently came back without its comparisons.
        stats_state = ctx.state.feature_slice("stats")
        if stats_state is not None:
            stats_state.groups = groups
            stats_state.pairwise = pairwise
    except Exception:
        _logger.debug("saved_plots: stats re-run failed", exc_info=True)


def delete_saved_plot(ctx, plot_id: str) -> None:
    get_store(ctx).delete(ctx, plot_id)
    refresh_saved_plots_gallery(ctx)


def rename_saved_plot(ctx, plot_id: str, new_title: str) -> None:
    get_store(ctx).rename(ctx, plot_id, new_title)
    refresh_saved_plots_gallery(ctx)


def move_saved_plot(ctx, plot_id: str, up: bool) -> None:
    """Reorder a saved plot up/down in the gallery and repaint."""
    if get_store(ctx).move(ctx, plot_id, up):
        refresh_saved_plots_gallery(ctx)


def clear_saved_plots(ctx) -> None:
    get_store(ctx).clear(ctx)
    refresh_saved_plots_gallery(ctx)


def export_saved_plot(ctx, plot_id: str) -> None:
    """Copy a saved plot's PNG to a user-chosen location."""
    store = get_store(ctx)
    rec = store.get(plot_id)
    src = store.image_path(ctx, rec) if rec is not None else None
    if rec is None or src is None or not src.exists():
        return
    try:
        from PySide6.QtWidgets import QFileDialog

        suggested = f"{rec.tab.replace(' ', '_')}_{rec.id}.png"
        dest, _flt = QFileDialog.getSaveFileName(
            None, "Export saved plot", suggested, "PNG image (*.png)")
        if dest:
            from pathlib import Path
            Path(dest).write_bytes(src.read_bytes())
    except Exception:
        _logger.debug("saved_plots: export failed for %r", plot_id, exc_info=True)


def export_report_pdf(ctx) -> None:
    """Export all saved plots as a multi-page PDF report (cover + one page each)."""
    store = get_store(ctx)
    store.ensure_loaded(ctx)
    if not store.records:
        ctx.notify.set_status("No saved plots to export.")
        return
    try:
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog

        data_dir = getattr(ctx.state.dataset, "data_dir", None)
        suggested = str(Path(data_dir) / "saved_plots_report.pdf") if data_dir \
            else "saved_plots_report.pdf"
        dest, _flt = QFileDialog.getSaveFileName(
            None, "Export Saved Plots report", suggested, "PDF document (*.pdf)")
        if not dest:
            return
        # Gallery order, not storage order. The gallery shows newest first and
        # the ▲/▼ buttons curate that sequence; the report walked the records
        # as stored, so it came out in exactly the reverse of the order the
        # user had arranged — silently, since both are plausible orderings of
        # the same plots.
        n = build_saved_plots_report(
            list(reversed(store.records)),
            lambda r: store.image_path(ctx, r),
            dest,
            dataset_name=(Path(data_dir).name if data_dir else ""),
            summary_for=lambda r: r.summary,
        )
        ctx.notify.set_status(f"Saved Plots report → {Path(dest).name} ({n} plots)")
    except Exception as exc:  # surface to the user
        _logger.debug("saved_plots: report export failed", exc_info=True)
        ctx.notify.set_status(f"Report export failed: {exc}")


def refresh_saved_plots_gallery(ctx) -> None:
    """Repaint the Saved Plots gallery if its tab has been built.

    The tab registers a repopulate callback (``ctx.runtime.saved_plots.repopulate``)
    at build time so this stays a one-way feature dependency (no controller →
    tab_view import)."""
    get_store(ctx).ensure_loaded(ctx)
    fn = ctx.runtime.saved_plots.repopulate
    if not callable(fn):
        return
    try:
        fn()
    except Exception:
        swallowed()  # gallery repaint is best-effort; never block callers
