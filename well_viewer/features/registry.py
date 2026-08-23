"""The single assembly point for every Review-side feature descriptor.

This is the **only** module permitted to import ``well_viewer.features.*`` from
outside the features tree — the guardrail in
``tests/test_architecture_contracts.py`` enforces that core (``core/`` /
``data/`` / ``domain/`` / ``persistence/``) never does. Core reads features
exclusively through the ``FeatureRegistry`` this module builds.

Every callable here is a thin, *lazy* wrapper: it imports its feature module on
call, so importing this module (and building the registry at app startup) does
not eagerly pull heavy feature deps (TF / skimage / matplotlib-QtAgg). The hook
bodies are faithful relocations of the branches that previously lived in
``core/tab_controller.py``, ``core/selection_controller.py``, and
``core/channel_state_controller.py``.

To remove a feature: comment out its entry in ``FEATURES``. Its tab disappears
from the rail, its dispatch hooks stop firing, its persistence/fold-change
contributions drop out, and its ``AppState`` slice is no longer attached — with
no other edits and no startup error.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import replace

from well_viewer.core.feature_registry import (
    FeatureDescriptor,
    FeatureRegistry,
    StateSpec,
)

_logger = logging.getLogger("well_viewer.runtime_app")


def _lazy_build(module_path: str, attr: str):
    """Return a ``build(ctx, frame)`` that imports *attr* from *module_path* on
    call and invokes ``attr(ctx, frame)`` (audit A15).

    Collapses the uniform tab-builder wrappers into declarative descriptors so
    the registry isn't 40+ near-identical ``def _build_x`` functions. The module
    path stays a greppable string literal and the import is still deferred to
    call time, so building the registry pulls no heavy feature deps.
    """
    def build(ctx, frame) -> None:
        getattr(importlib.import_module(module_path), attr)(ctx, frame)
    return build


def _lazy_hook(module_path: str, attr: str):
    """Return a ``hook(ctx)`` that imports *attr* on call.

    The same deferral :func:`_lazy_build` uses, for the lifecycle hooks a
    descriptor carries. Keeping them lazy is what lets a feature stay deletable:
    the registry names its module as a string and nothing is imported until the
    hook actually fires.
    """
    def hook(ctx) -> None:
        getattr(importlib.import_module(module_path), attr)(ctx)
    return hook


def _lazy_state(module_path: str, attr: str):
    """Return a no-arg factory that imports *attr* from *module_path* and
    constructs it — the uniform AppState-slice constructor shape (audit A15)."""
    def make():
        return getattr(importlib.import_module(module_path), attr)()
    return make


# ─────────────────────────────────────────────────────────────────────────────
# Tab builders — uniform ``build(ctx, frame)`` adapters over each feature's
# entry point (some take ``ctx``, some take the legacy ``app``; Review CSV is an
# app method). Mirrors the former ``_build_*`` closures in ``centre_view``.
# ─────────────────────────────────────────────────────────────────────────────

# Uniform ``build(ctx, frame)`` tab builders — declarative lazy descriptors (A15).
_build_lineplot = _lazy_build("well_viewer.features.lineplot.tab_view", "build_line_graphs_tab")
_build_barplot = _lazy_build("well_viewer.features.barplot.tab_view", "build_bar_plots_tab")
_build_scatter = _lazy_build("well_viewer.features.scatter.tab_view", "build_scatter_tab")
_build_distribution = _lazy_build("well_viewer.features.distribution.tab_view", "build_distribution_tab")
_build_heatmap = _lazy_build("well_viewer.features.heatmap.tab_view", "build_heatmap_tab")
_build_image_browser = _lazy_build("well_viewer.features.image_browser.tab_view", "build_image_browser_tab")


def _build_smfish(ctx, frame) -> None:
    from PySide6.QtWidgets import QVBoxLayout
    if frame.layout() is None:
        QVBoxLayout(frame).setContentsMargins(0, 0, 0, 0)
    try:
        from well_viewer.features.smfish.tab_view import build_smfish_tab
        build_smfish_tab(ctx, frame)
    except Exception:
        _logger.exception("Build for smFISH tab failed")


def _build_stats(ctx, frame) -> None:
    from well_viewer.features.stats.tab_view import (
        build_stats_group_editor, build_stats_tab,
    )
    build_stats_tab(ctx, frame)
    # ``build_stats_group_editor`` takes ``ctx`` (its body drives the ctx-native
    # stats group-editor controllers); the sidebar host frame is an app-window
    # widget handle fetched through the blessed ``ctx.window`` accessor (a
    # widget-parenting need, not behaviour coupling).
    build_stats_group_editor(ctx, ctx.window._sidebar_stats_frame)


_build_multivariate = _lazy_build("well_viewer.features.multivariate.tab_view", "build_multivariate_tab")
_build_spatial = _lazy_build("well_viewer.features.spatial.tab_view", "build_spatial_tab")
_build_saved_plots = _lazy_build("well_viewer.features.saved_plots.tab_view", "build_saved_plots_tab")
_build_batch_export = _lazy_build("well_viewer.features.batch_export.tab_view", "build_batch_export_tab")


# Review CSV's builder is already ctx-native (``build_review_csv_tab(ctx, parent)``
# — the app's ``_build_review_csv_tab`` was only a ``self._ctx`` shim), so it joins
# the uniform declarative lazy descriptors instead of reaching through the app.
_build_review_csv = _lazy_build("well_viewer.features.review_csv.tab_view", "build_review_csv_tab")
_build_home = _lazy_build("well_viewer.features.home.tab_view", "build_home_tab")

_build_groups = _lazy_build("well_viewer.features.home.groups.tab_view",
                            "build_replicate_groups_centre")
_build_well_labels = _lazy_build("well_viewer.features.home.labels.tab_view",
                                 "build_label_grid")
_build_pipeline_info = _lazy_build("well_viewer.features.home.pipeline_info.tab_view",
                                   "build_pipeline_info_subtab")


def _build_cell_gating(ctx, frame) -> None:
    """Adapter: the Cell Gating builder takes no frame and finds its own.

    It has to. ``ensure_cell_gating_tab_built`` materializes this editor from
    the persistence hydrator, which has no sub-tab index to hand over, so the
    frame is passed through ``ctx.runtime.cell_gating`` instead of an argument.
    Stamping it here keeps that route working whichever way the build is reached.
    """
    from well_viewer.features.home.cell_gating.tab_view import build_cell_gating_subtab
    ctx.runtime.cell_gating.subtab_frame = frame
    build_cell_gating_subtab(ctx)


def _build_ratios(ctx, frame) -> None:
    """Adapter: the ratio editor is returned, not mounted.

    ``build_ratios_inline_panel`` parents the panel to *frame* but never adds it
    to a layout, so on its own it would paint at the origin at its size hint and
    never resize with the tab.
    """
    from well_viewer.features.home.ratios.tab_view import build_ratios_inline_panel
    layout = frame.layout()
    layout.addWidget(build_ratios_inline_panel(ctx, frame))
    layout.addStretch(1)



# ─────────────────────────────────────────────────────────────────────────────
# State factories — lazy constructors for each feature-owned ``AppState`` slice.
# ─────────────────────────────────────────────────────────────────────────────

# Uniform AppState-slice constructors — declarative lazy factories (A15).
_state_bar = _lazy_state("well_viewer.features.barplot.state", "BarPlotState")
_state_heatmap = _lazy_state("well_viewer.features.heatmap.state", "HeatmapState")
_state_distribution = _lazy_state("well_viewer.features.distribution.state", "DistributionState")
_state_scatter = _lazy_state("well_viewer.features.scatter.state", "ScatterState")
_state_smfish = _lazy_state("well_viewer.features.smfish.state", "SmfishState")
_state_stats = _lazy_state("well_viewer.features.stats.state", "StatsState")
_state_multivariate = _lazy_state("well_viewer.features.multivariate.state", "MultivariateState")
_state_spatial = _lazy_state("well_viewer.features.spatial.state", "SpatialState")
_state_review_image = _lazy_state("well_viewer.features.image_browser.review_image.state", "ReviewImageState")
_state_preview = _lazy_state("well_viewer.features.image_browser.review_image.state", "PreviewState")
_state_image_table = _lazy_state("well_viewer.features.image_browser.image_table.state", "ImageTableState")
_state_playground = _lazy_state("well_viewer.features.image_browser.playground.state", "PlaygroundState")
_state_home = _lazy_state("well_viewer.features.home.state", "HomeState")


# ─────────────────────────────────────────────────────────────────────────────
# on_activate hooks — relocated from ``tab_controller.on_tab_change``. Each does
# the per-tab sidebar-visibility + refresh its branch did; the generic
# scaffolding (hide-all, post-dispatch drains) stays in ``on_tab_change``.
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Home. Its hooks go through lazy proxies for the same reason every other
# feature's do: `features/registry.py` is the only module allowed to name a
# feature package, and a descriptor hook is how core reaches one without an
# import of its own.
# ─────────────────────────────────────────────────────────────────────────────
_HOME = "well_viewer.features.home.controller"
_activate_home = _lazy_hook(_HOME, "activate_home")
_loaded_home = _lazy_hook(_HOME, "on_home_dataset_loaded")
_sel_home = _lazy_hook(_HOME, "on_home_selection_changed")


def _activate_segmentation(ctx) -> None:
    from well_viewer.features.preview.controller import (
        sync_preview_well_for_image_tabs,
    )
    from well_viewer.features.preview.preview_view import refresh_preview_picker
    from well_viewer.features.image_browser.review_image.renderer import (
        refresh_review_image_ctx, update_preview_ctx,
    )
    sync_preview_well_for_image_tabs(ctx)
    ctx.widgets.get("_sidebar_preview_frame").setVisible(True)
    refresh_preview_picker(ctx)
    update_preview_ctx(ctx, ctx.state.preview.selected_well)
    refresh_review_image_ctx(ctx)


def _activate_image_table(ctx) -> None:
    from well_viewer.features.image_browser.image_table.builder_view import (
        builder_refresh,
    )
    from well_viewer.features.image_browser.image_table.controller import (
        image_table_refresh_empty_state, image_table_refresh_picker,
        image_table_repopulate_dropdowns,
    )
    frame = ctx.widgets.get_or_none("_sidebar_image_table_frame")
    if frame is not None:
        frame.setVisible(True)
    image_table_repopulate_dropdowns(ctx)
    image_table_refresh_picker(ctx)
    image_table_refresh_empty_state(ctx)
    # Redraw the builder shelf (trees + channel tray) from current state so the
    # channel chips reflect the loaded dataset. builder_refresh reads
    # ctx.state.channel and no-ops before the shelf is built, so this is a safe
    # idempotent refresh on every activation — without it the channel chips only
    # appeared after the first drag onto a Rows/Cols zone.
    builder_refresh(ctx)


def _activate_statistics(ctx) -> None:
    from well_viewer.features.stats.controller import (
        stats_sync_from_app, stats_update_tp_menu, stats_refresh_fov_indicator,
    )
    ctx.widgets.get("_sidebar_stats_frame").setVisible(True)
    # Auto-populate from rep-sets on first visit
    if not ctx.state.stats.groups and ctx.widgets.has("_stats_grp_inner"):
        stats_sync_from_app(ctx)
    if ctx.widgets.has("_stats_tp_cb"):
        stats_update_tp_menu(ctx)
    # Reflect the current FOV-replicate mode (may have been toggled on a plot tab).
    stats_refresh_fov_indicator(ctx)


def _activate_multivariate(ctx) -> None:
    from well_viewer.features.multivariate.controller import mv_on_activate
    ctx.widgets.get("_sidebar_main_frame").setVisible(True)
    ctx.coordinator.refresh_sidebar_map()
    mv_on_activate(ctx)


def _activate_spatial(ctx) -> None:
    from well_viewer.features.spatial.controller import sp_on_activate
    ctx.widgets.get("_sidebar_main_frame").setVisible(True)
    ctx.coordinator.refresh_sidebar_map()
    sp_on_activate(ctx)


def _activate_batch_export(ctx) -> None:
    ctx.widgets.get("_sidebar_main_frame").setVisible(True)
    ctx.coordinator.refresh_sidebar_map()
    state = ctx.runtime.batch_export.inline_state
    if state:
        from well_viewer.features.batch_export.tab_view import _show_batch_mode
        _show_batch_mode(ctx, state.get("mode", "line"))


def _activate_review_csv(ctx) -> None:
    ctx.widgets.get("_sidebar_main_frame").setVisible(True)
    ctx.coordinator.refresh_sidebar_map()
    # ``refresh_review_csv`` lives in ``image_browser.review_image``. Review CSV is
    # its own feature but borrows that hydration logic, so guard the cross-feature
    # call: when Image Browser has been removed the table just isn't hydrated
    # (removal contract — mirrors load_controller's guarded review_image import).
    try:
        from well_viewer.features.image_browser.review_image.controller import (
            refresh_review_csv,
        )
        refresh_review_csv(ctx)
    except Exception:
        from well_viewer.core.safe import swallowed
        swallowed("Review CSV: Image Browser removed; table not hydrated")


def _activate_smfish(ctx) -> None:
    from well_viewer.core.selection_controller import set_selected_wells
    from well_viewer.features.smfish.tab_view import smfish_sync_from_app
    ctx.widgets.get("_sidebar_main_frame").setVisible(True)
    sel = ctx.state.selection
    if len(sel.selected_wells) > 1:
        keep = (
            sel.last_sel
            if sel.last_sel in sel.selected_wells
            else next(iter(sel.selected_wells))
        )
        # Route through the central mutator so _prev_sel is snapshotted
        # properly -- bypassing it leaves the next selection diff
        # mis-attributing cross-tab additions / removals.
        set_selected_wells(ctx, {keep}, commit=False)
    ctx.coordinator.refresh_sidebar_map()
    smfish_sync_from_app(ctx)


def _activate_cell_browser(ctx) -> None:
    # Show the canonical well selector (plate) above the Cell Browser's own
    # filter sidebar — its gallery is scoped to the selected wells, so it
    # needs the same well picker every other tab has.
    ctx.widgets.get("_sidebar_main_frame").setVisible(True)
    ctx.coordinator.refresh_sidebar_map()
    frame = ctx.widgets.get_or_none("_sidebar_cell_browser_frame")
    if frame is not None:
        frame.setVisible(True)
    for _refresh in (ctx.runtime.cell_browser.refreshers or ()):
        _refresh(ctx)


def _activate_playground(ctx) -> None:
    from well_viewer.core.safe import swallowed
    from well_viewer.features.image_browser.playground.controller import (
        playground_activate,
    )
    # Playground is a single-well tab: it picks its channel/timepoint/FOV via
    # in-tab dropdowns, but its well comes from the ever-present sidebar plate.
    # Show that plate, then reconcile it with the frame's single well and render.
    ctx.widgets.get("_sidebar_main_frame").setVisible(True)
    ctx.coordinator.refresh_sidebar_map()
    try:
        playground_activate(ctx)
    except Exception:
        swallowed()


def _activate_saved_plots(ctx) -> None:
    from well_viewer.core.safe import swallowed
    # Gallery of previously saved plots — no plate selection needed; just
    # repaint from the (per-dataset) store on each visit.
    ctx.widgets.get("_sidebar_main_frame").setVisible(True)
    ctx.coordinator.refresh_sidebar_map()
    try:
        from well_viewer.features.saved_plots.controller import (
            refresh_saved_plots_gallery,
        )
        refresh_saved_plots_gallery(ctx)  # controller now takes ctx
    except Exception:
        swallowed()


def _activate_bar_plots(ctx) -> None:
    from well_viewer.features.barplot.controller import update_bar_tp_menu
    from well_viewer.features.barplot.renderer import redraw_bars
    ctx.widgets.get("_sidebar_main_frame").setVisible(True)
    ctx.coordinator.refresh_sidebar_map()
    update_bar_tp_menu(ctx)
    redraw_bars(ctx)


def _activate_scatter(ctx) -> None:
    from well_viewer.features.scatter.controller import update_scatter_menus
    from well_viewer.features.scatter.tab_view import scatter_redraw_active
    ctx.widgets.get("_sidebar_main_frame").setVisible(True)
    ctx.coordinator.refresh_sidebar_map()
    update_scatter_menus(ctx)
    scatter_redraw_active(ctx)


def _activate_heatmap(ctx) -> None:
    from well_viewer.core.plot_orchestrator import redraw_app
    ctx.widgets.get("_sidebar_main_frame").setVisible(True)
    ctx.coordinator.refresh_sidebar_map()
    if ctx.widgets.has("_heatmap_sidebar_frame"):
        try:
            from well_viewer.features.heatmap.sidebar_view import (
                refresh_heatmap_layout_sidebar,
            )
            refresh_heatmap_layout_sidebar(ctx)
        except Exception:
            _logger.exception("Heatmap sidebar refresh failed")
    redraw_app(ctx)


def _activate_default_plot(ctx) -> None:
    # Line Graphs / Distribution — unified picker shown, generic redraw.
    from well_viewer.core.plot_orchestrator import redraw_app
    ctx.widgets.get("_sidebar_main_frame").setVisible(True)
    ctx.coordinator.refresh_sidebar_map()
    redraw_app(ctx)


# ─────────────────────────────────────────────────────────────────────────────
# redraw hooks — a plot feature's figure-redraw callable, resolved by name from
# the registry so plot orchestration (redraw_app / tab activation) never imports
# the feature renderer (terminal-goal cond. 3). Lazy: ``() -> ((ctx) -> None)``.
# ─────────────────────────────────────────────────────────────────────────────

def _redraw_bar_plots():
    from well_viewer.features.barplot.renderer import redraw_bars
    return redraw_bars


def _redraw_line_graphs():
    from well_viewer.features.lineplot.controller import redraw_line_plots
    return redraw_line_plots


def _redraw_distribution():
    from well_viewer.features.distribution.controller import redraw_distribution
    return redraw_distribution


def _redraw_heatmap():
    from well_viewer.features.heatmap.controller import redraw_heatmap
    return redraw_heatmap


def _redraw_spatial():
    from well_viewer.features.spatial.renderer import render_spatial
    return render_spatial


def _redraw_multivariate():
    """Multivariate renders from its own cached result, exactly as Spatial
    does, so it is not reached by ``redraw_app``'s fan-out. Without a hook here
    the Properties sidebar's Reset-to-defaults had a Spatial branch and no
    Multivariate one, and repainted the wrong figure."""
    from well_viewer.features.multivariate.renderer import render_multivariate
    return render_multivariate


def _heatmap_view_state_io():
    """Heat Map's (snapshot, apply) view-state pair (lazy). ``view_state`` fans
    this out via the registry so it never names Heat Map — a profiled-out Heat Map
    isn't in the registry, so its ``view_state_io.py`` is never imported."""
    from well_viewer.features.heatmap.view_state_io import (
        apply_heatmap, snapshot_heatmap,
    )
    return snapshot_heatmap, apply_heatmap


def _redraw_scatter():
    from well_viewer.features.scatter.tab_view import scatter_redraw_active
    return scatter_redraw_active


# ─────────────────────────────────────────────────────────────────────────────
# on_dataset_loaded hooks — a feature's post-load refresh, fanned out by
# ``load_controller`` from the registry so core never names the feature (nor
# imports its module when the feature is profiled out).
# ─────────────────────────────────────────────────────────────────────────────

def _loaded_image_table(ctx) -> None:
    # Only when the Image Table tab has actually been built (its picker widget
    # exists); on a lazy/unopened tab this is a no-op, matching the old guard.
    if not ctx.widgets.has("_image_table_refresh_picker") and not hasattr(
        ctx.window, "_image_table_refresh_picker"
    ):
        return
    from well_viewer.features.image_browser.image_table.build_layout import (
        image_table_reset_on_dataset_load,
    )
    from well_viewer.features.image_browser.image_table.controller import (
        image_table_refresh_empty_state, image_table_repopulate_dropdowns,
    )
    image_table_refresh_empty_state(ctx)
    # Refresh the dropdown options for the new dataset, then reset the grid to
    # fresh-tab defaults so a (re)load never inherits the previous dataset's grid.
    # This is also what makes the "Reset all saved state" trash-can reset the
    # Image Table: it deletes persistence.json then reloads, landing here with
    # nothing to restore. When there IS a saved grid, view_state.restore() (run
    # earlier in this same load, from load_controller.load_directory) may have
    # already force-built this tab and correctly re-applied it — in which case
    # ctx.runtime.image_table.restored_this_load is True and the reset below
    # must be skipped, or it would wipe out what was just restored.
    image_table_repopulate_dropdowns(ctx)
    if not ctx.runtime.image_table.restored_this_load:
        image_table_reset_on_dataset_load(ctx)


def _loaded_smfish(ctx) -> None:
    # Hydrate saved smFISH thresholds, then resync the tab's well→zip map /
    # channel list / extractor against the freshly loaded dataset.
    try:
        from well_viewer.persistence import smfish as _smfish_persist
        _smfish_persist.load_from_pipeline_info(ctx)
    except Exception:
        _logger.exception("Eager smFISH threshold load from pipeline_info failed")
    from well_viewer.features.smfish.tab_view import smfish_sync_from_app
    smfish_sync_from_app(ctx)


def _loaded_image_browser(ctx) -> None:
    # Drop the previous dataset's decoded-image caches so the Review Image /
    # Preview panels never show its pixels before the next redecode. The caches
    # live on the typed state slices (not the widgets), so this is valid even
    # when the tab was never built; feature_slice is None under a thin build.
    ri = ctx.state.feature_slice("review_image")
    if ri is not None:
        ri.clear_caches()
        # The armed drag-delete scope names wells from the *previous* plate, so
        # it is disarmed here rather than carried across the swap — and its
        # button relabelled, or the control row would still advertise a scope
        # that no longer exists.
        ri.reset_delete_scope()
        from well_viewer.features.preview.delete_scope_view import (
            refresh_delete_scope_button,
        )
        refresh_delete_scope_button(ctx)
    # The crop's scope is the other half of the same control. It is *not*
    # disarmed here — it is persisted with the crop region and hydrated from the
    # new dataset's persistence.json earlier in the load — but the button still
    # has to be relabelled, or it goes on advertising the previous plate's scope.
    from well_viewer.features.crop.controller import refresh_crop_scope_button
    refresh_crop_scope_button(ctx)
    pv = ctx.state.feature_slice("preview")
    if pv is not None:
        pv.clear_caches()


def _loaded_playground(ctx) -> None:
    # Playground's per-frame + reference-field caches are keyed by well/channel,
    # not by data dir, so they must be dropped on a dataset swap.
    pg = ctx.state.feature_slice("playground")
    if pg is not None:
        pg.clear_caches()


# ─────────────────────────────────────────────────────────────────────────────
# on_selection_changed hooks — relocated from
# ``selection_controller._refresh_tab_view`` (the branches that ``return redrew``
# without a generic ``_redraw``). Tabs with no hook fall through to the generic
# ``app._redraw()`` in the controller.
# ─────────────────────────────────────────────────────────────────────────────

def _sel_bar_plots(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
    from well_viewer.features.barplot.renderer import redraw_bars
    app._update_bar_tp_menu()
    redraw_bars(ctx)


def _sel_scatter(ctx) -> None:
    from well_viewer.features.scatter.controller import update_scatter_menus
    from well_viewer.features.scatter.tab_view import scatter_redraw_active
    update_scatter_menus(ctx)
    scatter_redraw_active(ctx)


def _sel_review_csv(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
    app._refresh_review_csv()


def _sel_multivariate(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
    from well_viewer.features.multivariate.controller import mv_recompute
    if hasattr(app, "_mv_canvas"):
        mv_recompute(ctx)


def _sel_spatial(ctx) -> None:
    app = ctx.window  # transitional bridge (A3)
    from well_viewer.features.spatial.controller import sp_on_selection_changed
    if hasattr(app, "_sp_canvas"):
        sp_on_selection_changed(ctx)


def _sel_smfish(ctx) -> None:
    from well_viewer.features.smfish.tab_view import smfish_sync_from_app
    smfish_sync_from_app(ctx)


def _sel_playground(ctx) -> None:
    from well_viewer.features.image_browser.playground.controller import (
        playground_sync_selected_well,
    )
    playground_sync_selected_well(ctx)


def _sel_cell_browser(ctx) -> None:
    # Keep the gallery in sync with the live well selection when both panes are
    # live in Split View — the user expects the Scatter and Cell Browser to track
    # each other. Outside split the gallery stays Generate-gated (deferred) so a
    # selection change doesn't trigger a heavy re-composite unprompted.
    if not ctx.runtime.split.active:
        return
    from well_viewer.features.image_browser.cell_browser.controller import (
        apply_filters,
    )
    apply_filters(ctx, live_sync=True)


# ─────────────────────────────────────────────────────────────────────────────
# on_channel_changed hooks — relocated from
# ``channel_state_controller.recalculate_threshold``'s per-feature refresh block.
# Each is ``hasattr``-guarded exactly as before, so it no-ops until the feature's
# widgets exist.
# ─────────────────────────────────────────────────────────────────────────────

def _channel_bar(ctx) -> None:
    from well_viewer.features.barplot.controller import update_bar_tp_menu
    if ctx.widgets.has("_bar_tp_cb"):
        update_bar_tp_menu(ctx)


def _channel_stats(ctx) -> None:
    if ctx.widgets.has("_stats_tp_cb"):
        from well_viewer.features.stats.controller import stats_update_tp_menu
        stats_update_tp_menu(ctx)


def _channel_distribution(ctx) -> None:
    if ctx.widgets.has("_distribution_tp_cb"):
        try:
            from well_viewer.features.distribution.tab_view import (
                refresh_distribution_timepoints,
            )
            refresh_distribution_timepoints(ctx)
        except Exception:
            _logger.exception("Distribution timepoint refresh failed")


def _channel_heatmap(ctx) -> None:
    if ctx.widgets.has("_heatmap_tp_cb"):
        try:
            from well_viewer.features.heatmap.tab_view import (
                refresh_heatmap_timepoints,
            )
            refresh_heatmap_timepoints(ctx)
        except Exception:
            _logger.exception("Heatmap timepoint refresh failed")


def _channel_multivariate(ctx) -> None:
    from well_viewer.features.multivariate.controller import mv_on_channel_changed
    if ctx.widgets.has("_mv_canvas"):
        mv_on_channel_changed(ctx)


def _channel_spatial(ctx) -> None:
    from well_viewer.features.spatial.controller import sp_on_channel_changed
    if ctx.widgets.has("_sp_canvas"):
        sp_on_channel_changed(ctx)


def _channel_scatter(ctx) -> None:
    if ctx.widgets.has("_scatter_ch_x_cb"):
        try:
            from well_viewer.features.scatter.controller import (
                redraw_scatter_agg_orchestrate,
                redraw_scatter_orchestrate,
                update_scatter_menus,
            )
            update_scatter_menus(ctx)
            redraw_scatter_orchestrate(ctx)
            redraw_scatter_agg_orchestrate(ctx)
        except Exception:
            _logger.exception("Scatter menu refresh failed")


# ─────────────────────────────────────────────────────────────────────────────
# Fold-change scope contributions (relocated from core/scope_registry.py).
# ─────────────────────────────────────────────────────────────────────────────

def _fold_change_scope_bar():
    from well_viewer.domain.fold_change_scopes import FoldChangeScope
    from well_viewer.features.barplot.renderer import redraw_bars
    return FoldChangeScope(
        name="bar",
        ctrl_combo_attr="_fc_ctrl_combo_bar",
        baseline_combo_attr="_fc_baseline_combo_bar",
        redraw=lambda a: redraw_bars(a._ctx),
        tab_names=("Bar Plots",),
    )


def _saved_plots_card_decorator():
    """The Saved Plots plot-card decorator (lazy). Plot tabs apply this via
    ``ui_helpers.apply_plot_card_decorators`` so they never import saved_plots —
    a profiled-out Saved Plots simply isn't in the registry, so nothing runs."""
    from well_viewer.features.saved_plots.controller import wire_save_to_gallery
    return wire_save_to_gallery


def _image_table_view_state_io():
    """Image Table's (snapshot, apply) view-state pair (lazy). ``view_state``
    fans this out via the registry so it never names Image Table — a profiled-out
    Image Table isn't in the registry, so its ``view_state_io.py`` is never
    imported and the tab's serialization simply doesn't run."""
    from well_viewer.features.image_browser.image_table.view_state_io import (
        apply_image_table, snapshot_image_table,
    )
    return snapshot_image_table, apply_image_table


# ─────────────────────────────────────────────────────────────────────────────
# The descriptor list — the single source of truth. Comment a line to remove
# a feature.
# ─────────────────────────────────────────────────────────────────────────────

def _features() -> list[FeatureDescriptor]:
    return [
        # ── Analysis ──────────────────────────────────────────────────────
        # "Plotting" is a bespoke composite container (built by
        # centre_view._build_plotting); build=None → the page-stack resolves
        # Home is first, and that is the whole of what makes it the landing
        # page: `centre_view.build_centre` adds pages in registry order and
        # calls `setCurrentIndex(0)`. No special case anywhere.
        FeatureDescriptor(
            name="Home", group="Home", build=_build_home,
            states=(StateSpec("home", _state_home),),
            on_activate=_activate_home,
            on_dataset_loaded=_loaded_home,
            on_selection_changed=_sel_home,
            # The definition editors are tabs of this page, and they edit wells
            # one at a time — so the rail plate stays an active per-well
            # selector here even when groups exist.
            per_well_selection=True,
        ),
        # its builder from its local fallback map. Its plot sub-tabs are the
        # sub-leaf descriptors below.
        FeatureDescriptor(name="Plotting", group="Analysis"),
        FeatureDescriptor(
            name="Statistics", group="Analysis", build=_build_stats,
            states=(StateSpec("stats", _state_stats),),
            on_activate=_activate_statistics,
            on_channel_changed=_channel_stats,
        ),
        FeatureDescriptor(
            name="Multivariate", group="Analysis", build=_build_multivariate,
            lazy_only=True,
            states=(StateSpec("multivariate", _state_multivariate),),
            on_activate=_activate_multivariate,
            on_selection_changed=_sel_multivariate,
            on_channel_changed=_channel_multivariate,
            redraw=_redraw_multivariate,
            per_well_selection=True,
        ),
        FeatureDescriptor(
            name="Spatial", group="Analysis", build=_build_spatial,
            lazy_only=True,
            states=(StateSpec("spatial", _state_spatial),),
            on_activate=_activate_spatial,
            on_selection_changed=_sel_spatial,
            on_channel_changed=_channel_spatial,
            redraw=_redraw_spatial,
            per_well_selection=True,
        ),
        # ── Images ────────────────────────────────────────────────────────
        FeatureDescriptor(
            name="Image Browser", group="Images", build=_build_image_browser,
            lazy_only=True,
            on_dataset_loaded=_loaded_image_browser,
            states=(
                StateSpec("review_image", _state_review_image),
                StateSpec("preview", _state_preview),
                # These two belong to the Image Table / Playground sub-tabs, whose
                # state modules are physically stripped from a subset bundle that
                # narrows Image Browser's sub-tabs. Tag them so build_feature_registry
                # skips the slice (and its import) when the sub-tab is profiled out.
                StateSpec("image_table", _state_image_table, subtab="Image Table"),
                StateSpec("playground", _state_playground, subtab="Playground"),
            ),
        ),
        FeatureDescriptor(
            name="smFISH", group="Images", build=_build_smfish, lazy_only=True,
            on_activate=_activate_smfish,
            on_selection_changed=_sel_smfish,
            on_dataset_loaded=_loaded_smfish,
            single_well_selection=True,
            states=(StateSpec("smfish", _state_smfish),),
        ),
        # ── Data ──────────────────────────────────────────────────────────
        FeatureDescriptor(
            name="Review CSV", group="Data", build=_build_review_csv,
            on_activate=_activate_review_csv,
            on_selection_changed=_sel_review_csv,
            per_well_selection=True,
        ),
        FeatureDescriptor(
            name="Saved Plots", group="Data", build=_build_saved_plots,
            lazy_only=True,
            on_activate=_activate_saved_plots,
            plot_card_decorator=_saved_plots_card_decorator,
        ),
        FeatureDescriptor(
            name="Batch Export", group="Data", build=_build_batch_export,
            on_activate=_activate_batch_export,
        ),

        # ── Plotting sub-leaves (no rail entry; dispatch hooks only) ───────
        FeatureDescriptor(
            name="Bar Plots", parent="Plotting", build=_build_barplot,
            states=(StateSpec("bar", _state_bar),),
            on_activate=_activate_bar_plots,
            on_selection_changed=_sel_bar_plots,
            on_channel_changed=_channel_bar,
            redraw=_redraw_bar_plots,
            fold_change_scope_factory=_fold_change_scope_bar,
        ),
        FeatureDescriptor(name="Line Graphs", parent="Plotting", build=_build_lineplot,
                          on_activate=_activate_default_plot,
                          redraw=_redraw_line_graphs),
        FeatureDescriptor(
            name="Scatter Plot", parent="Plotting", build=_build_scatter,
            on_activate=_activate_scatter,
            on_selection_changed=_sel_scatter,
            on_channel_changed=_channel_scatter,
            redraw=_redraw_scatter,
            states=(StateSpec("scatter", _state_scatter),),
        ),
        FeatureDescriptor(
            name="Distribution", parent="Plotting", build=_build_distribution,
            on_activate=_activate_default_plot,
            on_channel_changed=_channel_distribution,
            redraw=_redraw_distribution,
            states=(StateSpec("distribution", _state_distribution),),
        ),
        FeatureDescriptor(
            name="Heat Map", parent="Plotting", build=_build_heatmap,
            on_activate=_activate_heatmap,
            on_channel_changed=_channel_heatmap,
            redraw=_redraw_heatmap,
            view_state_io=_heatmap_view_state_io,
            states=(StateSpec("heatmap", _state_heatmap),),
        ),

        # ── Image Browser sub-leaves (dispatch hooks only) ─────────────────
        # Not removable: it has no catalog entry, so its source is never
        # stripped, and `removable=False` keeps its tab whatever a `[subtabs]`
        # line says. `thin` still lists it, which is now a no-op it can keep or
        # drop freely.
        FeatureDescriptor(name="Segmentation", parent="Image Browser",
                          on_activate=_activate_segmentation, removable=False),
        FeatureDescriptor(name="Image Table", parent="Image Browser",
                          on_activate=_activate_image_table,
                          on_dataset_loaded=_loaded_image_table,
                          view_state_io=_image_table_view_state_io),
        FeatureDescriptor(name="Cell Browser", parent="Image Browser",
                          on_activate=_activate_cell_browser,
                          on_selection_changed=_sel_cell_browser,
                          per_well_selection=True),
        FeatureDescriptor(name="Playground", parent="Image Browser",
                          on_activate=_activate_playground,
                          on_selection_changed=_sel_playground,
                          on_dataset_loaded=_loaded_playground,
                          single_well_selection=True),

        # ── Home sub-leaves ───────────────────────────────────────────────
        # Home enumerates its tabs from these, in this order, so a profile
        # cannot name a tab that is not declared here. No ``on_activate``:
        # dispatch is keyed on ``current_centre_tab``, which resolves Home's
        # tabs to "Home" rather than descending into them
        # (``tab_controller._resolve_leaf``); Home's own sub-tab handler does
        # the per-tab work. The first three are load-bearing for code that
        # never asks whether they are present — see ``removable``.
        FeatureDescriptor(name="Cell Gating", parent="Home",
                          build=_build_cell_gating, removable=False,
                          reads_well_selection=True),
        FeatureDescriptor(name="Groups", parent="Home",
                          build=_build_groups, removable=False,
                          reads_well_selection=True, subtab_inset=True),
        FeatureDescriptor(name="Well Labels", parent="Home",
                          build=_build_well_labels, removable=False,
                          reads_well_selection=True, subtab_inset=True),
        FeatureDescriptor(name="Ratios", parent="Home", build=_build_ratios),
        FeatureDescriptor(name="Pipeline Info", parent="Home",
                          build=_build_pipeline_info),
    ]


def build_feature_registry(profile=None) -> FeatureRegistry:
    """Assemble the registry, filtered to *profile*.

    ``profile`` is a :class:`well_viewer.core.profile.Profile`; ``None`` resolves
    the active profile from the ``ALLWELL_PROFILE`` env var (default = the full
    feature set). This is the single seam that lets one codebase run as any
    declared subset — the thin build is a profile, not a fork. A descriptor is
    kept when the profile admits it (a sub-leaf follows its ``parent``); the
    removability contract drops everything else for free.
    """
    from well_viewer.core.profile import active_profile
    if profile is None:
        profile = active_profile()

    # The sub-leaf names that survive, per parent — decided by the *same*
    # predicate that keeps the descriptors, so the two halves cannot disagree.
    # `includes_subtab` is deliberately not used here: it is a bare string check
    # against the `[subtabs]` allowlist and ignores `removable`, so tagging a
    # `StateSpec` with a non-removable sub-tab would keep the descriptor and drop
    # its slice — an app that boots and then raises `AttributeError` on first use.
    kept_subleaves: dict[str, set] = {}
    for leaf in _features():
        if leaf.parent and profile.includes(leaf):
            kept_subleaves.setdefault(leaf.parent, set()).add(leaf.name)

    def _admit_states(d):
        # Drop a surviving composite parent's sub-tab-tagged state slices whose
        # sub-tab the profile excludes. Descriptor-level ``includes`` keeps the
        # parent (e.g. Image Browser) while the sub-tab narrowing only removes the
        # separate sub-leaf descriptors — which carry no state — so without this
        # the parent's ``image_table`` / ``playground`` slices would still attach
        # and eagerly import their physically-stripped state modules.
        if not any(s.subtab for s in d.states):
            return d
        admitted = kept_subleaves.get(d.name, set())
        kept = tuple(s for s in d.states if not s.subtab or s.subtab in admitted)
        return d if kept == d.states else replace(d, states=kept)

    return FeatureRegistry(_admit_states(d) for d in _features()
                           if profile.includes(d))


def attach_feature_state(state, registry: FeatureRegistry | None = None) -> None:
    """Attach every feature-owned slice onto *state* (an ``AppState``).

    Called at app construction (and by test fixtures that build an ``AppState``
    directly). Idempotent. With a feature removed from ``FEATURES`` — or excluded
    by the active profile — its slice is simply never attached.
    """
    if registry is None:
        registry = build_feature_registry()
    for descriptor in registry.all():
        for spec in descriptor.states:
            setattr(state, spec.attr, spec.factory())
