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
_state_review_image = _lazy_state("well_viewer.features.image_browser.review_image.state", "ReviewImageState")
_state_preview = _lazy_state("well_viewer.features.image_browser.review_image.state", "PreviewState")


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


def _activate_cell_gating(ctx) -> None:
    from well_viewer.features.grouping.cell_gating_tab_view import (
        cell_gating_load_cell_areas, cell_gating_sync_scatter_gates,
    )
    ctx.widgets.get("_sidebar_main_frame").setVisible(True)
    ctx.refresh_sidebar_map()
    if ctx.widgets.has("_cell_gating_fluor_gate_edits"):
        cell_gating_load_cell_areas(ctx)
        cell_gating_sync_scatter_gates(ctx)


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


def _sel_sample_definitions(app) -> None:
    # Cell Gating is a sub-tab here; refresh its CDF if the user has opened it.
    if hasattr(app, "_cell_gating_fluor_gate_edits"):
        from well_viewer.features.grouping.cell_gating_tab_view import (
            cell_gating_load_cell_areas,
        )
        cell_gating_load_cell_areas(app._ctx)


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
        # ── Images ────────────────────────────────────────────────────────
        FeatureDescriptor(
            name="Image Browser", group="Images", build=_build_image_browser,
            lazy_only=True,
            states=(
                StateSpec("review_image", _state_review_image),
                StateSpec("preview", _state_preview),
            ),
        ),
        # ── Data ──────────────────────────────────────────────────────────
        FeatureDescriptor(
            name="Sample Definitions", group="Data",
            build=_build_sample_definitions,
            on_activate=_activate_sample_definitions,
            on_selection_changed=_sel_sample_definitions,
            per_well_selection=True,
        ),

        # ── Plotting sub-leaves (no rail entry; dispatch hooks only) ───────
        FeatureDescriptor(
            name="Bar Plots", parent="Plotting", build=_build_barplot,
            states=(StateSpec("bar", _state_bar),),
            on_activate=_activate_bar_plots,
            on_selection_changed=_sel_bar_plots,
            on_channel_changed=_channel_bar,
            fold_change_scope_factory=_fold_change_scope_bar,
        ),
        FeatureDescriptor(name="Line Graphs", parent="Plotting", build=_build_lineplot,
                          on_activate=_activate_default_plot),
        FeatureDescriptor(
            name="Scatter Plot", parent="Plotting", build=_build_scatter,
            on_activate=_activate_scatter,
            on_selection_changed=_sel_scatter,
            on_channel_changed=_channel_scatter,
            states=(StateSpec("scatter", _state_scatter),),
        ),
        FeatureDescriptor(
            name="Distribution", parent="Plotting", build=_build_distribution,
            on_activate=_activate_default_plot,
            on_channel_changed=_channel_distribution,
            states=(StateSpec("distribution", _state_distribution),),
        ),
        FeatureDescriptor(
            name="Heat Map", parent="Plotting", build=_build_heatmap,
            on_activate=_activate_heatmap,
            on_channel_changed=_channel_heatmap,
            states=(StateSpec("heatmap", _state_heatmap),),
        ),

        # ── Image Browser sub-leaf (dispatch hooks only) ───────────────────
        FeatureDescriptor(name="Segmentation", parent="Image Browser",
                          on_activate=_activate_segmentation),

        # ── Sample Definitions sub-leaf ────────────────────────────────────
        FeatureDescriptor(name="Cell Gating", parent="Sample Definitions",
                          on_activate=_activate_cell_gating),
    ]


def build_feature_registry(profile=None) -> FeatureRegistry:
    """Assemble the registry, filtered to *profile*.

    ``profile`` is a :class:`well_viewer.core.profile.Profile`; ``None`` means
    "resolve the active profile from the ``ALLWELL_PROFILE`` env var" (which
    defaults to the full feature set). This is the single seam that lets one
    codebase run as any declared subset — the thin build is a profile, not a
    fork. A descriptor is included when the profile admits it (a sub-leaf follows
    its ``parent``); everything else about the app then drops out for free via
    the existing removability contract.
    """
    from well_viewer.core.profile import active_profile
    if profile is None:
        profile = active_profile()
    return FeatureRegistry(d for d in _features() if profile.includes(d))


def attach_feature_state(state, registry: "FeatureRegistry | None" = None) -> None:
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
