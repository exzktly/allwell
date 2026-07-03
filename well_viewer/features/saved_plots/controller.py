"""Saved Plots controller — capture, gallery refresh, restore/delete/export.

Reaches the app only through the ``app`` argument (ARCHITECTURE §9); holds no
module-level state. The store lives on ``app._saved_plots_store`` (see
``state.get_store``).
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
    """Enable *card*'s hover "save to Saved Plots" button for *tab_name*.

    A8 teardown: takes ``ctx`` so plot builders wire the gallery hook without
    naming the god-object. The capture itself still walks ``WellViewerApp`` (a
    later cluster), so the handler bridges through ``ctx.app``."""
    if card is None or not hasattr(card, "set_save_handler"):
        return
    card.set_save_handler(
        lambda png, label: save_plot_from_card(ctx.app, tab_name, png, label)
    )


def save_plot_from_card(app, tab: str, png_bytes: bytes, panel_label: str) -> str:
    """Persist the captured panel + its params; refresh the gallery.

    Returns a short status string (shown as a toast by ``PlotCard``)."""
    store = get_store(app)
    store.ensure_loaded(app)
    if store.images_dir(app) is None:
        return "Save failed: load a dataset first"

    params, thresholds = capture_params(app, tab)
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
        summary=build_summary(app, tab, params, thresholds),
    )
    if not store.add(app, rec, png_bytes):
        return "Save failed: could not write image"
    refresh_saved_plots_gallery(app)
    return f"Saved “{title}” to Saved Plots"


def restore_saved_plot(app, plot_id: str) -> None:
    """Re-apply a saved plot's params and navigate to its originating tab."""
    rec = get_store(app).get(plot_id)
    if rec is None:
        return
    # Restore the group-driven selections FIRST so the plot's bars/traces match
    # the saved figure (view_state doesn't snapshot them), THEN re-run the saved
    # stats test so ``stats.pairwise`` is repopulated against those groups. Both
    # happen before ``view_state.restore`` activates the tab and does the final
    # redraw — which draws the significance brackets from the fresh results.
    _restore_selections(app, rec.params.get("selections"),
                        rec.params.get("active_solo_wells"))
    _restore_stats(app, rec.params.get("stats"))
    try:
        view_state.restore(app, dict(rec.params))
    except Exception:
        _logger.debug("saved_plots: restore failed for %r", plot_id, exc_info=True)


def _restore_selections(app, sels, solo_wells=None) -> None:
    """Reproduce the saved group-driven plotting selection: the replicate-set
    ``selections`` AND the soloed individual wells (``active_solo_wells``), then
    refresh the sidebar + plots. Each part is optional (older saved plots omit
    them); a no-op on any error. Applied together so the single ``_rebuild_all``
    redraw sees the full restored selection."""
    try:
        import copy
        sel_state = app._app_state.selection
        changed = False
        if isinstance(sels, list) and sels:
            sel_state.selections = copy.deepcopy(sels)
            sel_state.validate()  # re-mint ids / repair, and fix current_selection_id
            first = sel_state.selections[0] if sel_state.selections else None
            sel_state.current_selection_id = first.get("id") if first else None
            changed = True
        if isinstance(solo_wells, (list, set, tuple)):
            paths = app._app_state.dataset.well_paths
            sel_state.active_solo_wells = {w for w in solo_wells if w in paths}
            changed = True
        if changed:
            app._rebuild_all()  # authoritative refresh: sidebar map + groups + redraw
    except Exception:
        _logger.debug("saved_plots: selection restore failed", exc_info=True)


def _restore_stats(app, cfg) -> None:
    """Re-run the saved stats config to regenerate ``stats.pairwise`` + groups.

    Deterministic re-run (same config + data → same brackets). No-op for saved
    plots without a ``stats`` block (backward compatible) or on any error."""
    if not isinstance(cfg, dict):
        return
    try:
        groups = _deserialize_stats_groups(app, cfg.get("groups"))
        target_t = cfg.get("target_t")
        pairwise = _compute_pairwise(
            app, groups=groups, target_t=(float(target_t) if target_t is not None else None),
            val_col=cfg.get("val_col"), statistic=cfg.get("statistic", "mean"),
            threshold=cfg.get("threshold"), test=cfg.get("test", ""),
            use_fov=bool(cfg.get("use_fov", True)),
        )
        app._app_state.stats.groups = groups
        app._app_state.stats.pairwise = pairwise
    except Exception:
        _logger.debug("saved_plots: stats re-run failed", exc_info=True)


def delete_saved_plot(app, plot_id: str) -> None:
    get_store(app).delete(app, plot_id)
    refresh_saved_plots_gallery(app)


def rename_saved_plot(app, plot_id: str, new_title: str) -> None:
    get_store(app).rename(app, plot_id, new_title)
    refresh_saved_plots_gallery(app)


def move_saved_plot(app, plot_id: str, up: bool) -> None:
    """Reorder a saved plot up/down in the gallery and repaint."""
    if get_store(app).move(app, plot_id, up):
        refresh_saved_plots_gallery(app)


def clear_saved_plots(app) -> None:
    get_store(app).clear(app)
    refresh_saved_plots_gallery(app)


def export_saved_plot(app, plot_id: str) -> None:
    """Copy a saved plot's PNG to a user-chosen location."""
    store = get_store(app)
    rec = store.get(plot_id)
    src = store.image_path(app, rec) if rec is not None else None
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


def export_report_pdf(app) -> None:
    """Export all saved plots as a multi-page PDF report (cover + one page each)."""
    store = get_store(app)
    store.ensure_loaded(app)
    if not store.records:
        _status(app, "No saved plots to export.")
        return
    try:
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog

        data_dir = getattr(app._app_state.dataset, "data_dir", None)
        suggested = str(Path(data_dir) / "saved_plots_report.pdf") if data_dir \
            else "saved_plots_report.pdf"
        dest, _flt = QFileDialog.getSaveFileName(
            None, "Export Saved Plots report", suggested, "PDF document (*.pdf)")
        if not dest:
            return
        n = build_saved_plots_report(
            store.records,
            lambda r: store.image_path(app, r),
            dest,
            dataset_name=(Path(data_dir).name if data_dir else ""),
            summary_for=lambda r: r.summary,
        )
        _status(app, f"Saved Plots report → {Path(dest).name} ({n} plots)")
    except Exception as exc:  # noqa: BLE001 — surface to the user
        _logger.debug("saved_plots: report export failed", exc_info=True)
        _status(app, f"Report export failed: {exc}")


def _status(app, msg: str) -> None:
    fn = getattr(app, "_set_status", None)
    if callable(fn):
        try:
            fn(msg)
        except Exception:
            swallowed()


def refresh_saved_plots_gallery(app) -> None:
    """Repaint the Saved Plots gallery if its tab has been built.

    The tab registers a repopulate callback (``app._saved_plots_repopulate``)
    at build time so this stays a one-way feature dependency (no controller →
    tab_view import)."""
    get_store(app).ensure_loaded(app)
    fn = getattr(app, "_saved_plots_repopulate", None)
    if not callable(fn):
        return
    try:
        fn()
    except Exception:
        swallowed()  # gallery repaint is best-effort; never block callers
