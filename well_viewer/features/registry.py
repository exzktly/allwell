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


def _build_sample_definitions(ctx, frame) -> None:
    # The Sample Definitions centre builder + its sub-builder tree are app-native
    # (they stamp app._rep_*/_sel_* widgets and wire the _clear_sample_definitions_all
    # / _groups_centre_refresh / _refresh_sidebar_map god-methods). The feature
    # layer must not name the god-object, so the single god-object unwrap for the
    # whole tab build lives behind the core ``build_sample_definitions_app_bridge``
    # seam (which reaches the builder via a lazy proxy — no core→feature import).
    from well_viewer.core.tab_controller import build_sample_definitions_app_bridge
    build_sample_definitions_app_bridge(ctx, frame)


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


# ─────────────────────────────────────────────────────────────────────────────
# on_activate hooks — relocated from ``tab_controller.on_tab_change``. Each does
# the per-tab sidebar-visibility + refresh its branch did; the generic
# scaffolding (hide-all, post-dispatch drains) stays in ``on_tab_change``.
# ─────────────────────────────────────────────────────────────────────────────

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
    from well_viewer.features.image_browser.image_table.controller import (
        image_table_refresh_picker, image_table_repopulate_dropdowns,
    )
    frame = ctx.widgets.get_or_none("_sidebar_image_table_frame")
    if frame is not None:
        frame.setVisible(True)
    image_table_repopulate_dropdowns(ctx)
    image_table_refresh_picker(ctx)


def _activate_sample_definitions(ctx) -> None:
    from well_viewer.core.selection_controller import groups_centre_refresh
    from well_viewer.features.grouping.cell_gating_tab_view import (
        cell_gating_sync_scatter_gates,
    )
    ctx.widgets.get("_sidebar_sample_frame").setVisible(True)
    groups_centre_refresh(ctx)
    # Cell Gating leads this tab; pick up any scatter gates drawn since the
    # last visit (refresh the summary + recompute Included if they changed).
    cell_gating_sync_scatter_gates(ctx)


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
    ctx.refresh_sidebar_map()
    mv_on_activate(ctx)


def _activate_spatial(ctx) -> None:
    from well_viewer.features.spatial.controller import sp_on_activate
    ctx.widgets.get("_sidebar_main_frame").setVisible(True)
    ctx.refresh_sidebar_map()
    sp_on_activate(ctx)


def _activate_batch_export(ctx) -> None:
    ctx.widgets.get("_sidebar_main_frame").setVisible(True)
    ctx.refresh_sidebar_map()
    state = ctx.runtime.batch_export.inline_state
    if state:
        from well_viewer.features.batch_export.tab_view import _show_batch_mode
        _show_batch_mode(ctx, state.get("mode", "line"))


def _activate_review_csv(ctx) -> None:
    from well_viewer.features.image_browser.review_image.controller import (
        refresh_review_csv,
    )
    ctx.widgets.get("_sidebar_main_frame").setVisible(True)
    ctx.refresh_sidebar_map()
    refresh_review_csv(ctx)


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
    ctx.refresh_sidebar_map()
    smfish_sync_from_app(ctx)


def _activate_cell_gating(ctx) -> None:
    from well_viewer.features.grouping.cell_gating_tab_view import (
        cell_gating_load_cell_areas, cell_gating_sync_scatter_gates,
    )
    ctx.widgets.get("_sidebar_main_frame").setVisible(True)
    ctx.refresh_sidebar_map()
    if ctx.widgets.has("_cell_gating_fluor_gate_edits"):
        cell_gating_load_cell_areas(ctx)
        cell_gating_sync_scatter_gates(ctx)


def _activate_cell_browser(ctx) -> None:
    # Show the canonical well selector (plate) above the Cell Browser's own
    # filter sidebar — its gallery is scoped to the selected wells, so it
    # needs the same well picker every other tab has.
    ctx.widgets.get("_sidebar_main_frame").setVisible(True)
    ctx.refresh_sidebar_map()
    frame = ctx.widgets.get_or_none("_sidebar_cell_browser_frame")
    if frame is not None:
        frame.setVisible(True)
    for _refresh in (ctx.runtime.cell_browser.refreshers or ()):
        _refresh(ctx)


def _activate_playground(ctx) -> None:
    from well_viewer.core.safe import swallowed
    from well_viewer.features.image_browser.playground.controller import (
        playground_repopulate_dropdowns,
    )
    # Playground picks its own well/channel/tp/FOV via in-tab dropdowns; show
    # the canonical plate selector for parity, then refresh its selectors for
    # any dataset loaded since it last built.
    ctx.widgets.get("_sidebar_main_frame").setVisible(True)
    ctx.refresh_sidebar_map()
    try:
        playground_repopulate_dropdowns(ctx)
    except Exception:
        swallowed()


def _activate_saved_plots(ctx) -> None:
    from well_viewer.core.safe import swallowed
    # Gallery of previously saved plots — no plate selection needed; just
    # repaint from the (per-dataset) store on each visit.
    ctx.widgets.get("_sidebar_main_frame").setVisible(True)
    ctx.refresh_sidebar_map()
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
    ctx.refresh_sidebar_map()
    update_bar_tp_menu(ctx)
    redraw_bars(ctx)


def _activate_scatter(ctx) -> None:
    from well_viewer.features.scatter.controller import update_scatter_menus
    from well_viewer.features.scatter.tab_view import scatter_redraw_active
    ctx.widgets.get("_sidebar_main_frame").setVisible(True)
    ctx.refresh_sidebar_map()
    update_scatter_menus(ctx)
    scatter_redraw_active(ctx)


def _activate_heatmap(ctx) -> None:
    from well_viewer.core.plot_orchestrator import redraw_app
    ctx.widgets.get("_sidebar_main_frame").setVisible(True)
    ctx.refresh_sidebar_map()
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
    ctx.refresh_sidebar_map()
    redraw_app(ctx)


# ─────────────────────────────────────────────────────────────────────────────
# on_selection_changed hooks — relocated from
# ``selection_controller._refresh_tab_view`` (the branches that ``return redrew``
# without a generic ``_redraw``). Tabs with no hook fall through to the generic
# ``app._redraw()`` in the controller.
# ─────────────────────────────────────────────────────────────────────────────

def _sel_bar_plots(app) -> None:
    from well_viewer.features.barplot.renderer import redraw_bars
    app._update_bar_tp_menu()
    redraw_bars(app._ctx)


def _sel_scatter(app) -> None:
    from well_viewer.features.scatter.controller import update_scatter_menus
    from well_viewer.features.scatter.tab_view import scatter_redraw_active
    update_scatter_menus(app._ctx)
    scatter_redraw_active(app._ctx)


def _sel_review_csv(app) -> None:
    app._refresh_review_csv()


def _sel_multivariate(app) -> None:
    from well_viewer.features.multivariate.controller import mv_recompute
    if hasattr(app, "_mv_canvas"):
        mv_recompute(app._ctx)


def _sel_spatial(app) -> None:
    from well_viewer.features.spatial.controller import sp_on_selection_changed
    if hasattr(app, "_sp_canvas"):
        sp_on_selection_changed(app._ctx)


def _sel_smfish(app) -> None:
    from well_viewer.features.smfish.tab_view import smfish_sync_from_app
    smfish_sync_from_app(app._ctx)


def _sel_sample_definitions(app) -> None:
    # Cell Gating is a sub-tab here; refresh its CDF if the user has opened it.
    if hasattr(app, "_cell_gating_fluor_gate_edits"):
        from well_viewer.features.grouping.cell_gating_tab_view import (
            cell_gating_load_cell_areas,
        )
        cell_gating_load_cell_areas(app._ctx)


def _sel_cell_browser(app) -> None:
    # Keep the gallery in sync with the live well selection when both panes are
    # live in Split View — the user expects the Scatter and Cell Browser to track
    # each other. Outside split the gallery stays Generate-gated (deferred) so a
    # selection change doesn't trigger a heavy re-composite unprompted.
    if not app._ctx.runtime.split.active:
        return
    from well_viewer.features.image_browser.cell_browser.controller import (
        apply_filters,
    )
    apply_filters(app._ctx, live_sync=True)


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
    if ctx.widgets.has("_heatmap_tp_slider"):
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


# ─────────────────────────────────────────────────────────────────────────────
# The descriptor list — the single source of truth. Comment a line to remove
# a feature.
# ─────────────────────────────────────────────────────────────────────────────

def _features() -> "list[FeatureDescriptor]":
    return [
        # ── Analysis ──────────────────────────────────────────────────────
        # "Plotting" is a bespoke composite container (built by
        # centre_view._build_plotting); build=None → the page-stack resolves
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
            per_well_selection=True,
        ),
        FeatureDescriptor(
            name="Spatial", group="Analysis", build=_build_spatial,
            lazy_only=True,
            states=(StateSpec("spatial", _state_spatial),),
            on_activate=_activate_spatial,
            on_selection_changed=_sel_spatial,
            on_channel_changed=_channel_spatial,
            per_well_selection=True,
        ),
        # ── Images ────────────────────────────────────────────────────────
        FeatureDescriptor(
            name="Image Browser", group="Images", build=_build_image_browser,
            lazy_only=True,
            states=(
                StateSpec("review_image", _state_review_image),
                StateSpec("preview", _state_preview),
                StateSpec("image_table", _state_image_table),
                StateSpec("playground", _state_playground),
            ),
        ),
        FeatureDescriptor(
            name="smFISH", group="Images", build=_build_smfish, lazy_only=True,
            on_activate=_activate_smfish,
            on_selection_changed=_sel_smfish,
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
            name="Sample Definitions", group="Data",
            build=_build_sample_definitions,
            on_activate=_activate_sample_definitions,
            on_selection_changed=_sel_sample_definitions,
            per_well_selection=True,
        ),
        FeatureDescriptor(
            name="Saved Plots", group="Data", build=_build_saved_plots,
            lazy_only=True,
            on_activate=_activate_saved_plots,
        ),
        FeatureDescriptor(
            name="Batch Export", group="Data", build=_build_batch_export,
            on_activate=_activate_batch_export,
        ),

        # ── Plotting sub-leaves (no rail entry; dispatch hooks only) ───────
        FeatureDescriptor(
            name="Bar Plots", build=_build_barplot,
            states=(StateSpec("bar", _state_bar),),
            on_activate=_activate_bar_plots,
            on_selection_changed=_sel_bar_plots,
            on_channel_changed=_channel_bar,
            fold_change_scope_factory=_fold_change_scope_bar,
        ),
        FeatureDescriptor(name="Line Graphs", build=_build_lineplot,
                          on_activate=_activate_default_plot),
        FeatureDescriptor(
            name="Scatter Plot", build=_build_scatter,
            on_activate=_activate_scatter,
            on_selection_changed=_sel_scatter,
            on_channel_changed=_channel_scatter,
            states=(StateSpec("scatter", _state_scatter),),
        ),
        FeatureDescriptor(
            name="Distribution", build=_build_distribution,
            on_activate=_activate_default_plot,
            on_channel_changed=_channel_distribution,
            states=(StateSpec("distribution", _state_distribution),),
        ),
        FeatureDescriptor(
            name="Heat Map", build=_build_heatmap,
            on_activate=_activate_heatmap,
            on_channel_changed=_channel_heatmap,
            states=(StateSpec("heatmap", _state_heatmap),),
        ),

        # ── Image Browser sub-leaves (dispatch hooks only) ─────────────────
        FeatureDescriptor(name="Segmentation", on_activate=_activate_segmentation),
        FeatureDescriptor(name="Image Table", on_activate=_activate_image_table),
        FeatureDescriptor(name="Cell Browser", on_activate=_activate_cell_browser,
                          on_selection_changed=_sel_cell_browser,
                          per_well_selection=True),
        FeatureDescriptor(name="Playground", on_activate=_activate_playground),

        # ── Sample Definitions sub-leaf ────────────────────────────────────
        FeatureDescriptor(name="Cell Gating", on_activate=_activate_cell_gating),
    ]


def build_feature_registry() -> FeatureRegistry:
    """Assemble the registry. Called once from ``WellViewerApp.__init__``."""
    return FeatureRegistry(_features())


def attach_feature_state(state, registry: "FeatureRegistry | None" = None) -> None:
    """Attach every feature-owned slice onto *state* (an ``AppState``).

    Called at app construction (and by test fixtures that build an ``AppState``
    directly). Idempotent. With a feature removed from ``FEATURES`` its slice is
    simply never attached.
    """
    if registry is None:
        registry = build_feature_registry()
    for descriptor in registry.all():
        for spec in descriptor.states:
            setattr(state, spec.attr, spec.factory())
