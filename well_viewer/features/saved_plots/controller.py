"""Saved Plots controller — capture, gallery refresh, restore/delete/export.

Reaches the app only through the injected ``ctx`` (ARCHITECTURE §9); holds no
module-level state. The store lives in the feature runtime namespace
(``ctx.runtime.saved_plots.store``; see ``state.get_store``).
"""

from __future__ import annotations

import logging
from datetime import datetime

from well_viewer.core.lazy import lazy, lazy_names
from well_viewer.core.safe import swallow, swallowed
from well_viewer.domain.dataset_name import dataset_display_name
from well_viewer.persistence import view_state
from well_viewer.features.saved_plots._deck import build_saved_plots_deck
from well_viewer.features.saved_plots._report import build_saved_plots_report
from well_viewer.features.saved_plots.collect import build_summary, capture_params
from well_viewer.features.saved_plots.state import (
    SavedPlot, SavedPlotsStore, get_store,
)

_logger = logging.getLogger("well_viewer")

# Stats is a removable feature → import lazily (a call, not a module-scope
# ``import`` statement, so it's off the deferred-feature-import budget and honours
# the removability contract). See tests/test_deferred_imports + core/lazy.py.
_compute_pairwise, _deserialize_stats_groups, _stats_refresh_group_list, \
    _stats_refresh_map = lazy_names(
        "well_viewer.features.stats.controller",
        "compute_pairwise", "deserialize_stats_groups",
        "stats_refresh_group_list", "stats_refresh_map",
    )

# The synchronous gating recompute, for the export's redraw. Cell Gating is a
# removable sub-feature, so this is a proxy rather than an import; a build
# without it simply cannot finish a gating pass, and ``_regenerate_one`` treats
# that as "do not capture" rather than capturing the wrong population.
_gating_recompute_now = lazy(
    "well_viewer.features.home.cell_gating.controller",
    "cell_gating_recompute_now")

# The gallery view, bound the same way. A plain function-local ``import`` here
# would be a deferred feature import — a cycle workaround, which the budget in
# ``tests/test_deferred_imports`` exists to stop accumulating. The cycle is real
# (the tab imports this controller for its button handlers) and this is the
# sanctioned way through it: a proxy that resolves on first call and couples
# nothing at module load.
_scroll_to_saved_plot = lazy("well_viewer.features.saved_plots.tab_view",
                             "scroll_to_saved_plot")


def wire_save_to_gallery(ctx, card, tab_name: str) -> None:
    """Enable *card*'s hover "save to Saved Plots" button for *tab_name*.

    Also records the card against its tab. That map is what lets the PowerPoint
    export redraw a plot filed before the app captured vector: it has the tab
    name on the record and needs the live figure for it, and this hook is the one
    place in the app where those two facts meet. Built here rather than from a
    roster of card names so a tab added tomorrow is covered by wiring itself up.
    """
    if card is None or not hasattr(card, "set_save_handler"):
        return
    card.set_save_handler(
        lambda png, label, svg=b"", panel=False: save_plot_from_card(
            ctx, tab_name, png, label, svg, panel_only=panel)
    )
    by_tab = ctx.runtime.saved_plots.cards_by_tab
    if by_tab is None:
        by_tab = {}
    by_tab[tab_name] = card
    ctx.runtime.saved_plots.cards_by_tab = by_tab


def save_plot_from_card(ctx, tab: str, png_bytes: bytes, panel_label: str,
                        svg_bytes: bytes = b"", *,
                        panel_only: bool = False,
                        frames_bytes: bytes = b"") -> str:
    """Persist the captured panel + its params; refresh the gallery.

    *svg_bytes* is the same render in vector, captured by the card at the same
    moment. It is what the PowerPoint export later draws editable shapes from,
    and it is optional: a card that could not render vector still saves, and the
    plot exports as a picture.

    *frames_bytes* is the Image Table's alternative to it: a grid is not a chart
    and has no vector figure to capture, so it stores a per-field frame plan
    instead — each field's pixels, and its overlays as text and geometry — which
    the PowerPoint export places as one editable group per field. Optional in
    the same way, and empty for every other tab.

    Returns a short status string (shown as a toast by ``PlotCard``)."""
    params, thresholds = capture_params(ctx, tab)
    return save_figure_to_gallery(
        ctx, tab=tab, title=(panel_label or "").strip(),
        png_bytes=png_bytes, svg_bytes=svg_bytes, frames_bytes=frames_bytes,
        params=params, thresholds=thresholds, panel_only=panel_only,
    )


def save_figure_to_gallery(ctx, *, tab: str, title: str,
                           png_bytes: bytes, svg_bytes: bytes = b"",
                           frames_bytes: bytes = b"",
                           params: dict | None = None,
                           thresholds: dict | None = None,
                           panel_only: bool = False,
                           persist: bool = True,
                           refresh: bool = True) -> str:
    """File one already-captured figure in the gallery.

    The half of :func:`save_plot_from_card` that does not read the live
    application: it is handed the bytes *and* the parameters rather than
    capturing either. That split is what lets a figure the user never had on
    screen reach the gallery — Batch Export renders one figure per export
    group off-screen, and the parameters that describe it are the *group's*,
    not whatever the plotting tab happens to be showing while the run is in
    flight.

    *params* / *thresholds* default to empty rather than to a snapshot of the
    live application. A record with no parameters restores nothing, which is a
    thin record; a record carrying somebody else's parameters restores a
    *different figure* under this one's title, which is the silent substitution
    the whole of §7 is about. Callers that can describe their figure pass it in;
    callers that cannot are better off with a record that plainly has none.

    *persist* and *refresh* are for a caller filing many figures at once — see
    :meth:`SavedPlotsStore.add`. Both default to the single-figure behaviour.

    Returns a short status string (shown as a toast by ``PlotCard``)."""
    store = get_store(ctx)
    store.ensure_loaded(ctx)
    if store.images_dir(ctx) is None:
        return "Save failed: load a dataset first"

    params = dict(params or {})
    thresholds = dict(thresholds or {})
    title = (title or "").strip() or tab or "Plot"
    pid = _fresh_plot_id(store)
    rec = SavedPlot(
        id=pid,
        created_at=datetime.now().isoformat(timespec="seconds"),
        tab=tab,
        title=title,
        png_filename=f"{pid}.png",
        svg_filename=f"{pid}.svg" if svg_bytes else "",
        frames_filename=f"{pid}.frames.zip" if frames_bytes else "",
        panel_only=bool(panel_only),
        params=params,
        thresholds=thresholds,
        summary=build_summary(tab, params, thresholds),
    )
    if not store.add(ctx, rec, png_bytes, svg_bytes, frames_bytes,
                     persist=persist):
        return "Save failed: could not write image"
    if refresh:
        refresh_saved_plots_gallery(ctx)
    return f"Saved “{title}” to Saved Plots"


def _fresh_plot_id(store) -> str:
    """A record id no record in *store* already holds.

    The id is a timestamp to microsecond resolution and names the record's files
    on disk, so a collision is not a cosmetic clash — the second record's PNG
    overwrites the first's and the gallery then shows one figure twice. One save
    a user clicks cannot collide; a batch filing forty figures in a loop of file
    copies can, and did not have to be fast to do it: two calls inside one
    microsecond is all it takes.
    """
    taken = {r.id for r in store.records}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    candidate, n = stamp, 2
    while candidate in taken:
        candidate = f"{stamp}-{n}"
        n += 1
    return candidate


def restore_saved_plot(ctx, plot_id: str, *, adopt_groups: bool = False) -> None:
    """Re-apply a saved plot's params and navigate to its originating tab.

    *adopt_groups* writes the record's replicate sets over the live ones. It is
    **off** for the Restore button and on for exactly one caller,
    :func:`regenerate_missing_vectors`, which replays records to recapture their
    vector — that caller has to draw each record's own figure, and it is the
    only one that can, because it snapshots the user's state in
    :class:`_LiveState` first and puts it back in a ``finally``. Restoring a
    view must not rewrite a declaration unless something is standing by to undo
    it; see :func:`_report_group_drift` for what the button does instead.
    """
    rec = get_store(ctx).get(plot_id)
    if rec is None:
        return
    # Re-apply the gating block (fluor gates / ON cutoffs / scatter-gate config)
    # FIRST so the Included population that shapes every downstream plot matches
    # the saved figure — the async gating worker recomputes it, and the redraws
    # below (and the worker's own completion redraw) reflect it.
    _restore_thresholds(ctx, rec.thresholds)
    # Restore the soloed wells the figure was drawn with, THEN re-run the saved
    # stats test so ``stats.pairwise`` is repopulated against the groups this
    # dataset actually has. Both happen before ``view_state.restore`` activates
    # the tab and does the final redraw — which draws the significance brackets
    # from the fresh results. The record's own replicate sets are read for the
    # drift report at the end and are never written back over the user's.
    _restore_solo_wells(ctx, rec.params.get("active_solo_wells"))
    if adopt_groups:
        _adopt_record_groups(ctx, rec.params.get("selections"))
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
    _report_batch_export_origin(ctx, rec)
    # Last of the three, because it is the one that describes the figure the
    # user is now looking at rather than the record behind it. Nothing to
    # report when the caller adopted the record's groups: the figure is then
    # drawn from exactly what the record carried.
    if not adopt_groups:
        _report_group_drift(ctx, rec)


def _report_batch_export_origin(ctx, rec) -> None:
    """Say so when the record being restored came out of a Batch Export run.

    Such a record carries the export group's parameters — its members, its
    channel, its column — so the restore puts the right group on the right tab.
    It does not carry Batch Export's own layout controls, which live on that
    panel rather than in view state: whether bars were drawn as a beeswarm,
    whether the timepoints were stacked into one figure, how the scatter was
    facetted. The restored figure can therefore be laid out differently from the
    file that was exported, and from the thumbnail in the gallery beside it.

    Left silent, that reads as a restore that went wrong, and the user re-clicks
    Restore rather than opening Batch Export — which is the one thing that
    reproduces the exported figure. Said last, after
    :func:`_report_missing_tab_block`, because it is the more specific of the
    two and a restore can legitimately have both to report.
    """
    origin = rec.params.get("batch_export")
    if not isinstance(origin, dict) or not origin:
        return
    with swallow("report a restored plot's Batch Export origin"):
        group = str(origin.get("group") or "")
        named = f" (group “{group}”)" if group else ""
        ctx.notify.set_status(
            f"Restored “{rec.title}”{named} — it was exported by Batch Export, "
            f"so this tab draws the group with its own display settings rather "
            f"than the batch panel's.")


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


def _restore_solo_wells(ctx, solo_wells) -> None:
    """Reproduce the soloed individual wells the figure was drawn with.

    The **view** half of what a record says about the plate: which of the wells
    already on it were being shown on their own. It is not a declaration — the
    user did not build it on a definitions tab and nothing on disk in
    ``sample_definitions`` holds it — so restoring it costs them nothing.
    Optional, because older records omit it; a no-op on any error.

    The record's replicate sets are deliberately **not** applied. See
    :func:`_report_group_drift` for what happens instead and why.

    Refreshed through ``refresh_sidebar_map`` rather than the master
    ``rebuild_all`` fan-out. That fan-out ends by queueing a sample-definitions
    autosave, because every one of its other callers has just edited a
    definition — and firing it here is how a restore came to write
    ``pipeline_info.json`` at all. Nothing this function changes belongs in that
    block, the plate's statistics cannot move for a solo change either, and the
    figure itself is redrawn a moment later by ``_reapply_target_tab``.
    """
    if not isinstance(solo_wells, (list, set, tuple)):
        return
    try:
        paths = ctx.state.dataset.well_paths
        ctx.state.selection.active_solo_wells = {
            w for w in solo_wells if w in paths}
        ctx.coordinator.refresh_sidebar_map()
    except Exception:
        _logger.debug("saved_plots: solo-well restore failed", exc_info=True)


def _adopt_record_groups(ctx, sels) -> None:
    """Put the record's replicate sets on the live state — for one caller only.

    :func:`regenerate_missing_vectors` replays each record to recapture the
    figure it drew, so it needs the groups that drew it. It is allowed to want
    them because it takes a :class:`_LiveState` snapshot first and restores it
    in a ``finally``; nothing else in the app has that protection, which is why
    ``adopt_groups`` defaults to off.

    **Deliberately not through ``rebuild_all``.** That fan-out ends by queueing
    a sample-definitions autosave, so replaying a gallery of records wrote each
    one's groups into ``pipeline_info.json`` in turn — and ``_LiveState``
    restores the *session*, not the file, so the user's groups came back on
    screen while the last replayed record's sat on disk until the next edit
    happened to overwrite them. The stats cache is invalidated by hand instead,
    because a group's membership does move every aggregate the figure is drawn
    from, and the redraw is the one ``_reapply_target_tab`` already does.
    """
    if not isinstance(sels, list) or not sels:
        return
    try:
        import copy

        from well_viewer.core.selection_controller import invalidate_stats_cache

        sel_state = ctx.state.selection
        sel_state.selections = copy.deepcopy(sels)
        sel_state.validate()  # re-mint ids / repair, and fix current_selection_id
        first = sel_state.selections[0] if sel_state.selections else None
        sel_state.current_selection_id = first.get("id") if first else None
        invalidate_stats_cache(ctx)
        ctx.coordinator.refresh_sidebar_map()
    except Exception:
        _logger.debug("saved_plots: adopting the record's groups failed",
                      exc_info=True)


def _report_group_drift(ctx, rec) -> None:
    """Say so when the record's own groups no longer describe this dataset.

    **A restore reproduces a view; it must never rewrite a declaration.** This
    one did. It replaced ``ctx.state.selection.selections`` — the replicate
    sets the user built by hand on Home → Sample Map → Replicates, with their
    names, their membership and their plot order — with the copy the record was
    saved with, and then reached the master redraw fan-out, whose tail queues a
    sample-definitions autosave. So opening a figure saved last week silently
    deleted this week's groups, out of the session *and* out of
    ``pipeline_info.json``, with nothing said and nothing to undo it.

    The groups stay the user's. The figure is drawn from them, which means a
    plate whose groups have moved on draws a figure that is not the thumbnail
    beside it — and that has to be said, for the reason every other report in
    this module exists: a restore that quietly produces a different picture is
    indistinguishable from one that failed, and the user re-clicks Restore.

    Named per group rather than counted, up to :data:`_DRIFT_NAMED`, because
    "2 groups differ" tells a reader to go and diff two lists by hand while
    "“Dose 10” holds different wells" is the whole answer.
    """
    from well_viewer.domain.selections_model import group_drift

    with swallow("report a restored plot's group drift"):
        missing, changed = group_drift(
            rec.params.get("selections"), ctx.state.selection.selections)
        if not missing and not changed:
            return
        parts = []
        if missing:
            parts.append(f"groups this dataset no longer defines "
                         f"({_named(missing)})")
        if changed:
            parts.append(f"groups that now hold different wells "
                         f"({_named(changed)})")
        ctx.notify.set_status(
            f"Restored “{rec.title}” — it was saved with "
            f"{' and '.join(parts)}. The figure is drawn from your current "
            f"groups; your sample definitions were left as they are.")


#: How many group names the drift sentence spells out before it counts the
#: rest. Three because the sentence sits on the status line, which is one line
#: — naming twelve would push the point off the end of it.
_DRIFT_NAMED = 3


def _named(names: list[str]) -> str:
    """*names* as a phrase — up to :data:`_DRIFT_NAMED` of them, then a count."""
    shown = ", ".join(f"\u201c{n}\u201d" for n in names[:_DRIFT_NAMED])
    rest = len(names) - _DRIFT_NAMED
    return f"{shown} and {rest} more" if rest > 0 else shown


def _restore_stats(ctx, cfg) -> None:
    """Re-run the saved stats config to regenerate the comparison store + groups.

    Every comparison the record carries, not just one. A saved plot stores one
    block per run the Statistics tab had recorded (``cfg["runs"]``), because a
    Bar plot draws brackets from every stored run that describes its bars — a
    Mean comparison and a Fraction comparison of the same groups, or two
    channels, are all on screen at once. A record written before the block held
    several runs carries its single run at the top level instead, and is read
    here as a one-run list.

    Deterministic re-run (same config + data -> same brackets). No-op for saved
    plots without a ``stats`` block (backward compatible) or on any error."""
    if not isinstance(cfg, dict):
        return
    wanted = 0
    computed: list[list] = []
    try:
        groups = _deserialize_stats_groups(ctx, cfg.get("groups"))
        blocks = cfg.get("runs")
        if not isinstance(blocks, list) or not blocks:
            blocks = [cfg]  # a record saved before the block held several runs
        blocks = [b for b in blocks if isinstance(b, dict)]
        wanted = len(blocks)
        use_fov = bool(cfg.get("use_fov", True))
        for block in blocks:
            target_t = block.get("target_t")
            records = _compute_pairwise(
                ctx, groups=groups,
                target_t=(float(target_t) if target_t is not None else None),
                val_col=block.get("val_col"),
                statistic=block.get("statistic", "mean"),
                threshold=block.get("threshold"), test=block.get("test", ""),
                use_fov=use_fov,
            )
            if records:
                computed.append(records)
        # Same gate as the bar renderer's. Saved Plots and Statistics are
        # separately removable, so a build with one and not the other reaches
        # here — and this sits inside a broad ``except`` that logs at DEBUG, so
        # the AttributeError did not crash, it just meant a restored plot
        # silently came back without its comparisons.
        stats_state = ctx.state.feature_slice("stats")
        if stats_state is not None:
            stats_state.groups = groups
            # Replace the live store wholesale rather than assigning
            # ``pairwise``, which records exactly one run: the record's runs are
            # what this plot was drawn with, and any comparison sitting in the
            # store from earlier in the session is not.
            stats_state.runs.clear()
            for records in computed:
                stats_state.record_run(records)
            # Writing the state is not showing it. The Statistics editor's
            # group list and its plate are painted by explicit refresh calls —
            # every other route that changes ``stats.groups`` (add, delete,
            # rename, clear, sync-from-home) funnels through them — and this one
            # went straight to the field. So a restored plot came back with its
            # groups live in every way except the one the user can see: the test
            # ran on them, "Load groups from Home" warned about overwriting
            # them, and the sidebar showed none of them.
            #
            # ``_activate_statistics`` does not cover it either: it re-seeds only
            # when the group list is *empty*, which is exactly what this has just
            # made false, so arriving at the tab repaints nothing.
            _refresh_stats_group_views(ctx)
    except Exception:
        _logger.debug("saved_plots: stats re-run failed", exc_info=True)
    _report_unreproduced_stats(ctx, wanted, len(computed))


def _report_unreproduced_stats(ctx, wanted: int, produced: int) -> None:
    """Say so when a restore could not reproduce every saved comparison.

    A test whose data is no longer there returns no records, and the plot then
    comes back with fewer brackets than it was saved with — or none. Silent,
    that is indistinguishable from a plot that never had them, which is the one
    thing the user cannot tell from the figure itself.
    """
    if produced >= wanted:
        return
    missing = wanted - produced
    comparisons = "comparison" if wanted == 1 else "comparisons"
    them = "it" if missing == 1 else "them"
    with swallow("saved_plots: unreproduced-stats report"):
        ctx.notify.set_status(
            f"Restored the plot, but {missing} of its {wanted} saved "
            f"{comparisons} could not be re-run — the data behind {them} is no "
            f"longer available, so {them} draws no significance brackets.")


def _refresh_stats_group_views(ctx) -> None:
    """Repaint the Statistics group list and plate from ``stats.groups``.

    Kept out of the caller's ``except`` so a refresh failure is reported as
    itself rather than being read as "the stats re-run failed" — they are
    different problems and the second one is the misleading of the two.
    """
    try:
        _stats_refresh_group_list(ctx)
        _stats_refresh_map(ctx)
    except Exception:
        _logger.debug("saved_plots: stats group views did not refresh",
                      exc_info=True)


#: The Review page this feature owns, as the centre notebook names it. Stated
#: once so the navigation below and any future caller cannot drift from the
#: registry's ``FeatureDescriptor(name="Saved Plots")``.
TAB_NAME = "Saved Plots"


def focus_saved_plot(ctx, plot_id: str) -> bool:
    """Open the gallery at the card for *plot_id*.

    The other half of Home's saved-plot thumbnails: the strip there shows the
    figure small, and clicking one has to land the user on the same plot at full
    size rather than at the top of a gallery they then have to search. Returns
    False when the plot could not be reached, which the caller says out loud —
    silently arriving at the top of the list is the failure that reads as the
    feature simply not working.

    Navigation lives here rather than in Home because this feature owns its own
    page name, and because the gallery has to be *built* before there is a card
    to scroll to: selecting the page is what builds it (Saved Plots is
    ``lazy_only``), so the two steps cannot be separated across features.
    """
    notebook = getattr(ctx.window, "_notebook", None)
    if notebook is None or not notebook.setCurrentByName(TAB_NAME):
        return False
    refresh_saved_plots_gallery(ctx)
    return bool(_scroll_to_saved_plot(ctx, plot_id))


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
            ctx.window, "Export saved plot", suggested, "PNG image (*.png)")
        if dest:
            from pathlib import Path
            Path(dest).write_bytes(src.read_bytes())
    except Exception:
        _logger.debug("saved_plots: export failed for %r", plot_id, exc_info=True)


# ── progress ─────────────────────────────────────────────────────────────────
#
# An export blocks the GUI thread: it replays old plots onto their tabs, renders
# each figure to vector, parses every capture and writes a zip, all between the
# file dialog closing and the file appearing. Without a bar that is a frozen
# window and no account of why — and the redraw phase navigates the app while it
# runs, so the user watches their own tabs change with nothing saying what is
# happening.

def _progress_bar(ctx):
    """The shell's progress bar, or ``None`` when there is no shell.

    Reached through the widget registry rather than off ``ctx.window`` so a
    headless caller — the tests, a profile without the status row — simply gets
    no bar instead of an AttributeError.
    """
    widgets = getattr(ctx, "widgets", None)
    return widgets.get_or_none("_progress_bar") if widgets is not None else None


def _show_progress(ctx, total: int) -> None:
    bar = _progress_bar(ctx)
    if bar is None:
        return
    bar.setRange(0, max(1, int(total)))
    bar.setValue(0)
    bar.show()
    _pump()


def _step_progress(ctx, done: int, total: int, msg: str = "") -> None:
    bar = _progress_bar(ctx)
    if bar is not None:
        if bar.maximum() != max(1, int(total)):
            bar.setRange(0, max(1, int(total)))
        bar.setValue(int(done))
    if msg:
        with swallow("saved_plots: progress message"):
            ctx.notify.set_status(msg)
    _pump()


def _hide_progress(ctx) -> None:
    """Called on **every** exit path, including failure. A bar left running
    after an export died is its own false statement about what is happening."""
    bar = _progress_bar(ctx)
    if bar is None:
        return
    bar.setValue(0)
    bar.hide()
    _pump()


def _pump() -> None:
    """Repaint mid-export, without letting the user act on a half-done state.

    The work runs on the GUI thread, so nothing redraws unless the event loop is
    turned by hand. User input is excluded deliberately: the redraw phase moves
    the app from tab to tab and rewrites the live selection as it goes, and a
    click landing in the middle of that would act on a view that is about to be
    replaced — and then be undone by the restore at the end.
    """
    with swallow("saved_plots: progress repaint"):
        from PySide6.QtCore import QEventLoop
        from PySide6.QtWidgets import QApplication

        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)


def gallery_order(store) -> list:
    """The records in the order the gallery shows them — newest first.

    Gallery order, not storage order. The gallery shows newest first and the
    ▲/▼ buttons curate that sequence; an export that walked the records as
    stored came out in exactly the reverse of the order the user had arranged —
    silently, since both are plausible orderings of the same plots. Every export
    goes through this so the PDF and the deck cannot disagree about it.
    """
    return list(reversed(store.records))


def _export_destination(ctx, *, caption: str, default_name: str,
                        file_filter: str) -> str:
    """Ask where to write an export. ``""`` when the user cancelled."""
    from pathlib import Path
    from PySide6.QtWidgets import QFileDialog

    data_dir = getattr(ctx.state.dataset, "data_dir", None)
    suggested = str(Path(data_dir) / default_name) if data_dir else default_name
    dest, _flt = QFileDialog.getSaveFileName(ctx.window, caption, suggested,
                                             file_filter)
    return dest or ""


def _dataset_name(ctx) -> str:
    """The dataset name for an export's cover page.

    Through the shared rule rather than ``data_dir.name``: Review loads
    ``<dataset_root>/out``, so the folder's own name is the literal "out" on
    every pipeline dataset, and both exports opened by naming the dataset that.
    """
    return dataset_display_name(getattr(ctx.state.dataset, "data_dir", None))


def _records_to_export(ctx) -> SavedPlotsStore | None:
    """The loaded store, or ``None`` after saying there is nothing to export."""
    store = get_store(ctx)
    store.ensure_loaded(ctx)
    if not store.records:
        ctx.notify.set_status("No saved plots to export.")
        return None
    return store


def export_report_pdf(ctx) -> None:
    """Export all saved plots as a multi-page PDF report (cover + one page each)."""
    store = _records_to_export(ctx)
    if store is None:
        return
    try:
        from pathlib import Path

        dest = _export_destination(
            ctx, caption="Export Saved Plots report",
            default_name="saved_plots_report.pdf",
            file_filter="PDF document (*.pdf)")
        if not dest:
            return
        n = build_saved_plots_report(
            gallery_order(store),
            lambda r: store.image_path(ctx, r),
            dest,
            dataset_name=_dataset_name(ctx),
            summary_for=lambda r: r.summary,
        )
        ctx.notify.set_status(f"Saved Plots report → {Path(dest).name} ({n} plots)")
    except Exception as exc:  # surface to the user
        _logger.debug("saved_plots: report export failed", exc_info=True)
        ctx.notify.set_status(f"Report export failed: {exc}")


def export_deck_pptx(ctx) -> None:
    """Export all saved plots as a PowerPoint deck of editable figures.

    The sibling of :func:`export_report_pdf`, and the difference is the whole
    point of it: the PDF embeds each plot's captured snapshot, while this rebuilds
    the figures out of PowerPoint shapes, so a bar can be recoloured and an axis
    label retyped in the deck itself.

    A plot saved before the app began capturing vector has no such capture and is
    placed as a picture instead. That is reported rather than glossed: the slide
    says so, and so does the status line, because a deck where some figures are
    editable and some are not is otherwise indistinguishable from one where all
    of them are — until the user tries to click a bar in a talk.
    """
    store = _records_to_export(ctx)
    if store is None:
        return
    try:
        from pathlib import Path

        dest = _export_destination(
            ctx, caption="Export Saved Plots to PowerPoint",
            default_name="saved_plots.pptx",
            file_filter="PowerPoint presentation (*.pptx)")
        if not dest:
            return
        records = gallery_order(store)
        # One bar across both phases, because the user is waiting for one thing.
        # The redraw phase is the slow half and the one that visibly moves the
        # app from tab to tab, so it gets its own steps rather than being folded
        # into a single "working…" that sits still for most of the export.
        redraws = sum(1 for r in records if _wants_redraw(r))
        total = redraws + len(records) + 1
        _show_progress(ctx, total)
        try:
            regenerated = regenerate_missing_vectors(
                ctx, store, records,
                on_progress=lambda done, title: _step_progress(
                    ctx, done, total,
                    f"Redrawing “{title}” so it can export as editable shapes "
                    f"({done} of {redraws})…"))
            result = build_saved_plots_deck(
                records,
                lambda r: store.vector_path(ctx, r) or store.held_vector(r),
                lambda r: store.image_path(ctx, r),
                dest,
                frames_path_for=lambda r: store.frames_path(ctx, r),
                dataset_name=_dataset_name(ctx),
                summary_for=lambda r: r.summary,
                regenerated=regenerated,
                on_progress=lambda done, slides, title: _step_progress(
                    ctx, redraws + done, total,
                    (f"Building slide {done + 1} of {slides - 1}…" if title
                     else f"Writing {Path(dest).name}…")),
            )
            # Land on the full count before the bar goes away. Without it the
            # last thing the user sees is a bar stopped short of the end, which
            # reads as an export that gave up rather than one that finished.
            _step_progress(ctx, total, total)
        finally:
            _hide_progress(ctx)
        if result.unsupported:
            _logger.info("saved_plots: deck export met %s",
                         ", ".join(result.unsupported))
        ctx.notify.set_status(
            f"PowerPoint deck → {Path(dest).name} ({result.summary()})")
    except Exception as exc:  # surface to the user
        _logger.debug("saved_plots: deck export failed", exc_info=True)
        ctx.notify.set_status(f"PowerPoint export failed: {exc}")


class _LiveState:
    """Everything ``restore_saved_plot`` mutates, captured so it can be undone.

    ``view_state.snapshot`` is not enough and never was. It captures twenty keys
    and **none** of the three domains a replay actually replaces: the replicate
    ``selections`` (``_adopt_record_groups`` deep-copies the record's over
    them — which is what ``adopt_groups=True`` is, and this class is the reason
    that caller is allowed to ask for it), the cell-gating slice
    (``restore_gating_block`` clears every gate and writes the record's), and
    the Statistics groups and comparisons (``_restore_stats``). Restoring only
    what ``view_state`` knows about left the user's replicate sets, their gates
    and their stats groups replaced by the last regenerated plot's, with no way
    back — and the clobbered gating then reached ``pipeline_info.json`` on their
    next gate edit.

    So this snapshots by asking what the restore touches, not by asking what the
    snapshot helper happens to cover.
    """

    def __init__(self, ctx) -> None:
        import copy

        from well_viewer.domain.gating_state import build_gating_block

        self.view = view_state.snapshot(ctx)
        selection = ctx.state.selection
        self.selections = copy.deepcopy(list(selection.selections or []))
        self.current_selection_id = selection.current_selection_id
        self.solo_wells = set(selection.active_solo_wells or ())
        gating = ctx.state.cell_gating
        self.gating = build_gating_block(
            dict(gating.fluor_gates), dict(gating.thresh_frac_on),
            scatter_gates=list(gating.scatter_gates or []),
            use_scatter_gates=bool(gating.use_scatter_gates),
            use_scatter_thresh=bool(gating.use_scatter_thresh),
        )
        stats = ctx.state.feature_slice("stats")
        self.stats_groups = copy.deepcopy(getattr(stats, "groups", None)) \
            if stats is not None else None
        self.stats_pairwise = copy.deepcopy(getattr(stats, "pairwise", None)) \
            if stats is not None else None

    def restore(self, ctx) -> None:
        """Put every domain back, each independently of the others' failures."""
        import copy

        with swallow("saved_plots: restore selections after regenerating"):
            selection = ctx.state.selection
            selection.selections = copy.deepcopy(self.selections)
            selection.current_selection_id = self.current_selection_id
            selection.active_solo_wells = set(self.solo_wells)
        with swallow("saved_plots: restore gating after regenerating"):
            _restore_thresholds(ctx, self.gating)
        with swallow("saved_plots: restore stats after regenerating"):
            stats = ctx.state.feature_slice("stats")
            if stats is not None and self.stats_groups is not None:
                stats.groups = copy.deepcopy(self.stats_groups)
                stats.pairwise = copy.deepcopy(self.stats_pairwise)
                _refresh_stats_group_views(ctx)
        with swallow("saved_plots: restore the view after regenerating"):
            view_state.restore(ctx, dict(self.view))


def _wants_redraw(rec) -> bool:
    """Whether *rec* needs redrawing before the deck can make it editable.

    A record with a vector capture is already editable. So is an Image Table
    grid with a per-field frame plan — it exports as one group per field, which
    is the editable form a grid has, and there is no chart behind it to redraw.
    Counting those as needing repair charged the export a redraw phase for work
    it would then throw away.
    """
    return not getattr(rec, "svg_filename", "") and not getattr(
        rec, "frames_filename", "")


def regenerate_missing_vectors(ctx, store, records, *,
                               on_progress=None) -> set[str]:
    """Redraw plots that have no vector capture, so the deck can make them editable.

    A plot filed before the app began capturing vector is a PNG and nothing
    else, and no amount of reading it will produce shapes. What the record *does*
    carry is the parameters that drew it, and the app can draw them again — so
    the export replays each such record, captures the live figure as vector, and
    keeps it. The redraw is a one-off repair: the capture is stored on the
    record, so every later export of that plot is editable without paying for it
    again.

    *on_progress* is called ``(done, title)`` as each plot is dealt with, so the
    caller can move a bar: this phase replays plots onto their tabs and renders
    each figure, which is seconds apiece and visibly moves the app around.

    Returns the ids it regenerated, which the deck marks on their slides,
    because a redrawn figure is drawn from today's data rather than from the
    image the user filed.

    **Nothing starts unless the user's own state can be put back.** Replaying a
    record replaces their replicate sets, their gating and their stats groups, so
    a snapshot that failed would mean a repair that cannot be undone — and the
    old code simply skipped the restore in that case, silently. If the snapshot
    raises, this declines the whole repair and says so.
    """
    missing = [r for r in records if _wants_redraw(r)]
    if not missing:
        return set()
    cards_by_tab = ctx.runtime.saved_plots.cards_by_tab or {}

    live = None
    try:
        live = _LiveState(ctx)
    except Exception:
        _logger.debug("saved_plots: could not snapshot the live state",
                      exc_info=True)
        ctx.notify.set_status(
            "Older plots were exported as pictures: this session's settings "
            "could not be saved and restored, and redrawing them would have "
            "left your groups and gating replaced.")
        return set()

    regenerated: set[str] = set()
    skipped_tabs: set[str] = set()
    skipped_panels = 0
    done = 0

    def _report(rec) -> None:
        """Advance the caller's count. Every exit from the loop body calls this,
        including the skips: a record left alone still took a turn, and a bar
        that only moved on success would sit still through a gallery of plots
        that are all being skipped."""
        nonlocal done
        done += 1
        if on_progress is not None:
            with swallow("saved_plots: regeneration progress"):
                on_progress(done, rec.title or rec.tab or "plot")
    try:
        for rec in missing:
            card = _card_for_tab(ctx, cards_by_tab, rec.tab)
            if card is None or not hasattr(card, "captureVectorSvg"):
                skipped_tabs.add(rec.tab or "?")
                _report(rec)
                continue
            if not _redraw_keeps_the_scope(ctx, store, rec, card):
                skipped_panels += 1
                _report(rec)
                continue
            if _regenerate_one(ctx, store, rec, card):
                regenerated.add(rec.id)
            _report(rec)
        if regenerated:
            store.persist(ctx)      # one write for the batch, not one per record
    finally:
        live.restore(ctx)
    if skipped_panels:
        _logger.info("saved_plots: %d single-panel record(s) left as pictures; "
                     "a redraw would render the whole figure", skipped_panels)
    if skipped_tabs:
        # Named, because the slide's advice ("re-save it") is right for a tab
        # that draws no chart and useless for one that simply was not open.
        _logger.info("saved_plots: no figure to redraw for %s",
                     ", ".join(sorted(skipped_tabs)))
    if regenerated:
        _logger.info("saved_plots: redrew %d plot(s) that had no vector capture",
                     len(regenerated))
    return regenerated


def _card_for_tab(ctx, cards_by_tab, tab: str):
    """The live plot card for *tab*, building the tab first if it has not been.

    Multivariate, Spatial and smFISH are ``lazy_only``: their bodies — and so
    their cards — exist only once the user has opened them. Without this, a
    plot saved from one of those tabs last week is skipped on export and its
    slide tells the user to re-save it, which is the outcome this whole repair
    exists to remove. The Image Table is the case that genuinely has no chart to
    redraw, and it stays skipped.
    """
    card = cards_by_tab.get(tab)
    if card is not None or not tab:
        return card
    with swallow(f"saved_plots: build {tab!r} to redraw its plot"):
        notebook = getattr(ctx.window, "_notebook", None)
        if notebook is not None and notebook.setCurrentByName(tab):
            # Selecting the page is what builds a lazy_only body, and building it
            # is what runs its plot-card decorators — which is where
            # ``cards_by_tab`` is filled.
            card = (ctx.runtime.saved_plots.cards_by_tab or {}).get(tab)
    return card


def _redraw_keeps_the_scope(ctx, store, rec, card) -> bool:
    """Whether redrawing *rec* would produce the same figure it stored.

    A redraw renders the whole card. For a record that saved **one panel** of a
    multi-panel figure that is a different figure — every panel, under this
    panel's title, beside this panel's parameters — and a picture of the right
    plot beats shapes of the wrong one.

    ``rec.panel_only`` answers this outright for anything saved since the field
    existed. For an older record it is ``None``: the record does not say, and
    there is nothing in it that does. An earlier version of this compared the
    stored image's aspect ratio against the live figure's, which is not evidence
    — a plot card takes its size from the widget, so opening the Properties
    sidebar alone changes the live figure's aspect by more than 40%, and the
    window it is exported from is rarely the window it was saved from.

    So the answer for an unknown record comes from the only thing that decides
    whether a redraw *can* change the scope: **how many panels the card draws
    now**. On a single-axis figure a redraw and a per-panel save are the same
    picture, so it is safe whatever the record meant. On a multi-panel figure it
    is not knowable, and not knowable means not redrawn.

    The cost is that an old whole-figure record from a multi-panel tab stays a
    picture. That is the right way to be wrong: re-saving it makes it editable,
    and nothing about it is misrepresented in the meantime.
    """
    if rec.panel_only is not None:
        return not rec.panel_only
    try:
        return len(card.figure.axes) <= 1
    except Exception:
        swallowed("saved_plots: could not count the figure's panels")
        return False


def _regenerate_one(ctx, store, rec, card) -> bool:
    """Replay one record onto its tab and keep the figure it draws.

    False on any failure — a plot that cannot be redrawn simply stays a picture,
    which is what it already was. One record failing must not abandon the rest
    of the export.
    """
    kept = False
    with swallow(f"saved_plots: redraw {rec.id!r} for the deck"):
        restore_saved_plot(ctx, rec.id, adopt_groups=True)
        # ``restore_saved_plot`` applies the record's gating by kicking a worker
        # thread and returning. Capturing here would read the *previous* pass's
        # Included flags, so the figure would be drawn under a gating that is
        # neither this record's nor the user's — a wrong population with a
        # confident caption. Finish the pass on this thread first.
        if _gating_recompute_now(ctx):
            _reapply_target_tab(ctx, rec.params.get("active_centre_tab") or rec.tab)
        svg = card.captureVectorSvg()
        if svg:
            # Keeping the record's capture on disk is the *repair*; making this
            # export editable only needs the bytes. A failed write used to throw
            # both away, so a full disk cost the user an editable deck as well as
            # the repair.
            kept = store.attach_vector(ctx, rec, svg, persist=False)
            if not kept:
                kept = store.hold_vector(ctx, rec, svg)
    return kept


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
