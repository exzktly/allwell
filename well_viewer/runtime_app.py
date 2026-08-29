"""
well_viewer.py
--------------
GUI for exploring per-well fluorescence measurement CSVs from process_microscopy.py.

Open a directory or archive (zip / tar.gz) with the single "Open" button.
CSVs are loaded automatically; per-well image zips in the same folder are
discovered on demand for the preview panel.

Layout
------
  Left     – well list with row/column quick-selectors
  Centre   – plots: mean fluorescence above threshold ± SD/SEM, fraction, CDF
  Right    – fluorescence + mask image preview (hidden by default; "Show Preview" button)

Image preview notes
-------------------
  • Reads images directly from <well>_out.zip or <well>.zip without extraction.
  • Supports 16-bit TIFFs via tifffile (pip install tifffile).
  • LUT min/max editor on fluorescence panel; mouseover shows raw pixel value.
  • "Montage" button opens a popout window showing one FOV across all timepoints.

Dependencies:  pip install matplotlib pillow numpy tifffile
Usage:
    python well_viewer.py
    python well_viewer.py --data_dir /path/to/results
    python well_viewer.py --data_dir /path/to/results.zip
"""

from __future__ import annotations

# Legacy compatibility surface
# ----------------------------
# Canonical runtime module for the viewer.
# Implementation logic is incrementally delegated into well_viewer/* modules;
# keep exported names stable for package imports and entrypoints.

import argparse
from collections.abc import Iterable
import logging
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import (
    QTimer, Signal,
)
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QMainWindow, QWidget,
)

import pandas as pd
from matplotlib.figure import Figure
from well_viewer.domain.batch_models import BarGroup, ReplicateSet
from well_viewer.core.app_state import AppState
from well_viewer.core.app_runtime import AppRuntime
from well_viewer.features.registry import attach_feature_state, build_feature_registry
from well_viewer.core.event_bus import EventBus
from well_viewer.core.bus_wiring import wire_event_bus
from well_viewer.persistence.registry import build_persistence_registry
from well_viewer.viewer_context import ViewerContext
from well_viewer.core.widget_registry import WidgetRegistry


# ── Lazy controller proxies ──────────────────────────────────────────────────
# Most controller delegates are only invoked when the user interacts with a
# specific tab (redraw plots, click image, run stats, etc.) — never during
# import or build_ui. Importing them eagerly forces the matplotlib QtAgg
# backend, numpy, and a tower of view modules to load before the window
# paints, which dominates startup cost; binding removable-feature delegates
# lazily also keeps each feature deletable (the composition root never imports
# it at module scope). Shared helper lives in core/lazy.py.
from well_viewer.core.lazy import lazy as _lazy, lazy_names as _lazy_names


_bar_ordered_keys = _lazy("well_viewer.features.barplot.controller", "ordered_bar_keys")


_load_directory_controller = _lazy("well_viewer.core.load_controller", "load_directory")

_plot_save_bar_figure_orchestrator = _lazy("well_viewer.core.plot_orchestrator", "save_bar_figure")
_plot_save_line_figure_orchestrator = _lazy("well_viewer.core.plot_orchestrator", "save_line_figure")
_plot_save_matplotlib_fig_orchestrator = _lazy("well_viewer.core.plot_orchestrator", "save_matplotlib_fig")


_on_review_image_click_controller = _lazy("well_viewer.features.image_browser.review_image.controller", "on_review_image_click")
_select_review_csv_row_for_cell_controller = _lazy("well_viewer.features.image_browser.review_image.controller", "select_review_csv_row_for_cell")

_stats_collect_group_values = _lazy("well_viewer.features.stats.controller", "collect_group_values")
_stats_draw_ks_cdf = _lazy("well_viewer.features.stats.controller", "draw_ks_cdf")

from well_viewer.domain.viewer_state import read_pipeline_info as _read_pipeline_info_shared
from well_viewer.domain.viewer_state import parse_rc
import theme


try:
    import tifffile as _tifffile
    _TIFFFILE_AVAILABLE = True
except ImportError:
    _tifffile = None          # type: ignore[assignment]
    _TIFFFILE_AVAILABLE = False

try:
    from PIL import Image as _PILImage  # noqa: F401  (availability probe)
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

try:
    import numpy as _np
    _NP_AVAILABLE = True
except ImportError:
    _np = None                # type: ignore[assignment]
    _NP_AVAILABLE = False

# ── Module logger (GUI handler wired in at build time) ───────────────────────
_logger = logging.getLogger("well_viewer")

from well_viewer.domain.selections_model import color_for_well



# `_set_combo_values` was hoisted into well_viewer.ui_helpers so the new
# `channel_state_controller` can use it without a circular import back
# into runtime_app. Re-export the old private name for compatibility with
# call sites that still reference it via the runtime_app module.
from well_viewer.core.ui_helpers import set_combo_values as _set_combo_values  # noqa: F401


from well_viewer.data.data_loading import (
    load_well_csv,
)
from well_viewer.domain.ratio_models import RatioMetric


# Aliased to avoid collision with the WellViewerApp._extract_well_token method below.
from well_viewer.data.data_loading import extract_well_token as _extract_well_token


# =============================================================================
# Categorical label colourmap  (canonical: well_viewer/views/image_panel_view.py)
# =============================================================================


# =============================================================================
# Reusable image panel  (canonical: well_viewer/views/image_panel_view.py)
# =============================================================================


# =============================================================================
# GUI log handler  (canonical: well_viewer/views/status_view.py)
# =============================================================================

# Removable-feature delegates are bound through lazy proxies so the composition
# root never imports a removable feature at module scope (keeps each feature
# deletable; enforced by test_no_module_scope_imports_of_removable_features). The
# core / support-lib imports (channel_state_controller, plot_orchestrator,
# export, preview, …) stay direct.
redraw_scatter_agg_orchestrate = _lazy("well_viewer.features.scatter.controller", "redraw_scatter_agg_orchestrate")
redraw_scatter_orchestrate = _lazy("well_viewer.features.scatter.controller", "redraw_scatter_orchestrate")
stats_refresh_map = _lazy("well_viewer.features.stats.controller", "stats_refresh_map")
from well_viewer.core.ui_helpers import refresh_plot_empty_states
update_bar_tp_menu, bar_event_xdata, on_bar_drag_motion, toggle_swarm, toggle_violin, bar_well_display_label, rep_sets_active, collect_bar_items_for_app, refresh_bar_layout_btn_state, update_bar_tp_selection_display = _lazy_names("well_viewer.features.barplot.controller", "update_bar_tp_menu", "bar_event_xdata", "on_bar_drag_motion", "toggle_swarm", "toggle_violin", "bar_well_display_label", "rep_sets_active", "collect_bar_items_for_app", "refresh_bar_layout_btn_state", "update_bar_tp_selection_display")
from well_viewer.core.channel_state_controller import get_fluor_gate, get_all_fluor_gates, get_thresh_frac_on, set_thresh_frac_on, refresh_thresh_frac_quick_edit, ratio_label_for, active_channel_label, set_ratio_metrics, on_plot_channel_selected, on_metric_selected_global
from well_viewer.domain.selections_model import color_for_replicate_set, replicate_display_label, replicate_sets_loaded, well_display_label
from well_viewer.core.load_controller import load_path_shim
from well_viewer.core.plot_orchestrator import redraw_app
from well_viewer.features.preview.controller import refresh_preview_picker_shim
review_row_keys, review_norm_fov, review_collect_timepoints, refresh_review_csv, refresh_review_csv_rows, review_effective_included, review_load_rows = _lazy_names("well_viewer.features.image_browser.review_image.controller", "review_row_keys", "review_norm_fov", "review_collect_timepoints", "refresh_review_csv", "refresh_review_csv_rows", "review_effective_included", "review_load_rows")
set_review_image_source, refresh_review_image_source_btn, review_image_set_color, review_image_refresh_color_swatch, reset_review_image_colors, toggle_review_image_binary_mask, refresh_review_image_binary_btn, toggle_review_image_outline, refresh_review_image_outline_btn, draw_review_image, render_review_image_display, review_image_resolve_lut, review_image_auto_lut, review_image_commit_lut, review_image_zoom_step, apply_review_image_cursor, set_review_image_include_mode, invalidate_review_image_frame_cache, review_image_get_color = _lazy_names("well_viewer.features.image_browser.review_image.renderer", "set_review_image_source", "refresh_review_image_source_btn", "review_image_set_color", "review_image_refresh_color_swatch", "reset_review_image_colors", "toggle_review_image_binary_mask", "refresh_review_image_binary_btn", "toggle_review_image_outline", "refresh_review_image_outline_btn", "draw_review_image", "render_review_image_display", "review_image_resolve_lut", "review_image_auto_lut", "review_image_commit_lut", "review_image_zoom_step", "apply_review_image_cursor", "set_review_image_include_mode", "invalidate_review_image_frame_cache", "review_image_get_color")
open_scatter_cell_viewer = _lazy("well_viewer.features.scatter.cell_viewer_view", "open_scatter_cell_viewer")
col_for_scatter_axis, col_for_scatter_entry, scatter_axis_metric_key, refresh_scatter_metric_for_axis, refresh_scatter_agg_metric_for_axis, sync_scatter_agg_stat_combos, compose_scatter_agg_stat, update_tp_selection_display, update_scatter_tp_selection_display, on_scatter_axis_change, on_scatter_agg_axis_change = _lazy_names("well_viewer.features.scatter.controller", "col_for_scatter_axis", "col_for_scatter_entry", "scatter_axis_metric_key", "refresh_scatter_metric_for_axis", "refresh_scatter_agg_metric_for_axis", "sync_scatter_agg_stat_combos", "compose_scatter_agg_stat", "update_tp_selection_display", "update_scatter_tp_selection_display", "on_scatter_axis_change", "on_scatter_agg_axis_change")
from well_viewer.core.selection_controller import sel_by_id, sel_add, sel_set_hidden, groups_centre_refresh, rebuild_all, sel_color, refresh_sidebar_map_now, plate_commit_deferred, set_selected_wells, invalidate_stats_cache
stats_active_group, stats_collect_group_values_shim, stats_active_channel_entry, stats_active_val_col, stats_active_threshold, stats_active_statistic, stats_run_shim, stats_draw_ks_cdf_shim, on_stats_property_change = _lazy_names("well_viewer.features.stats.controller", "stats_active_group", "stats_collect_group_values_shim", "stats_active_channel_entry", "stats_active_val_col", "stats_active_threshold", "stats_active_statistic", "stats_run_shim", "stats_draw_ks_cdf_shim", "on_stats_property_change")
from well_viewer.core import tab_controller as _tabctl
from well_viewer.core.tab_controller import on_central_pane_changed, build_centre_shim, sync_section_nav_from_notebook, tab_history_back, tab_history_forward, current_centre_tab, sync_heatmap_well_drag_mode, toggle_sidebar, visible_centre_tabs, is_tab_visible, pane_mode
from well_viewer.core.ui_helpers import apply_theme, mute_color, norm_timepoint, toast, show_progress, step_progress, toggle_log
from well_viewer.core.centre_view import drain_pending_centre_builders
from well_viewer.core.plot_toggles import toggle_sem, toggle_fov_replicates, refresh_fov_btn_state
from well_viewer.core.worker_bridge import stop_runtime_callable, stop_runtime_workers


def teardown_review_pane(app) -> None:
    """App-close teardown for the Review pane.

    ``WellViewerApp`` is mounted as a child widget of ``AllWellApp``, so its own
    ``closeEvent`` never fires — ``AllWellApp.closeEvent`` calls this instead
    (audit L3/L4). It (L3) signals any in-flight smFISH "Apply to All" worker to
    abort so it can't write CSVs after the UI is gone, and (L4) disconnects the
    application-global ``focusChanged`` watcher installed for Split View.
    """
    if app is None:
        return
    ctx = getattr(app, "_ctx", None)
    runtime = getattr(ctx, "runtime", None) if ctx is not None else None
    ev = runtime.smfish.cancel_event if runtime is not None else None
    if ev is not None:
        try:
            ev.set()
        except Exception:
            _logger.debug("teardown: smFISH cancel event set failed", exc_info=True)
    # Supersede any in-flight Cell Browser loader (audit E22) so its daemon
    # thread stops reading the zipfile cache once the pane is going away.
    loader = runtime.cell_browser.loader if runtime is not None else None
    if loader is not None:
        try:
            loader.cancel()
        except Exception:
            _logger.debug("teardown: cell-browser loader cancel failed", exc_info=True)
    # Cell Gating owns two QThreads — the inclusion pass and the auto-threshold
    # scan — and neither was stopped anywhere, so quitting mid-pass left one
    # running against a pane being destroyed (the inclusion pass writes
    # ``Included`` flags onto the row cache as it goes).
    stop_runtime_workers(runtime, "cell_gating", ("worker", "auto_worker"))
    # A per-cell export renames its temp file only at the end, so a writer
    # killed at exit leaves that temp hidden in the user's own folder.
    stop_runtime_callable(runtime, "export", "cell_stop")
    try:
        from well_viewer.core.split_view import _remove_split_focus_filter
        _remove_split_focus_filter(app._ctx)
    except Exception:
        _logger.debug("teardown: split-focus-filter removal failed", exc_info=True)


class WellViewerApp(QWidget):

    def __init__(self, parent=None, data_path: Path | None = None) -> None:
        super().__init__(parent)

        # ── Typed domain state (runtime_app teardown, Phase 1) ───────────────
        # ChannelState owns the channel / ratio / threshold attributes that
        # were loose ``self._*`` fields; AppState aggregates the domain-state
        # objects and EventBus carries the cross-cutting signal catalogue.
        # The legacy attribute names survive as @property shims (search
        # "Phase 1 ChannelState shims" below) — ChannelState is the single
        # source of truth, so no call site needed to change.
        # Feature registry — the seam that makes tab-features removable. Built
        # before AppState so the feature-owned state slices can be attached
        # onto it (the import-inversion: core/app_state.py no longer imports
        # any feature state class; the registry contributes them here).
        self._features = build_feature_registry()
        self._app_state = AppState()
        attach_feature_state(self._app_state, self._features)
        self._event_bus = EventBus()
        # One ``name -> object`` attribute store shared by the widget registry
        # and the runtime namespaces (A8 T6): a handle registered as
        # ``_<feature>_<name>`` stays reachable through both ``ctx.widgets`` and
        # ``ctx.runtime.<feature>.<name>``, exactly as when both stamped one
        # ``self.__dict__``. Plain session storage, not a god-object handle.
        self._attr_store: dict = {}
        # Name-keyed store for Qt widget handles. Tab builders register their
        # figures / canvases / axes here; ``register`` also stamps the handle
        # onto ``self`` so the residual direct ``app._<widget>`` readers resolve.
        self._widget_registry = WidgetRegistry(self, store=self._attr_store)
        # Per-feature live-object namespaces (workers, decoded arrays, popup
        # figures) — A8 teardown T2/T6. Builders write through ctx.runtime; the
        # holder owns only the shared store above (no app reference).
        self._runtime = AppRuntime(store=self._attr_store)
        # The dependency-injection context handed to tab builders and
        # controllers in place of ``self``. Bundles the typed state, the bus,
        # the widget registry, and the runtime namespaces. No ``app`` escape
        # hatch (A8 T6): this instance is held privately as ``_window`` and is
        # reachable only through ctx's frozen named accessors.
        self._ctx = ViewerContext(
            state=self._app_state,
            bus=self._event_bus,
            widgets=self._widget_registry,
            runtime=self._runtime,
            features=self._features,
            _window=self,
        )
        # A8 T6: wire the residual god-object callbacks other layers invoke
        # through the runtime namespace as explicit live handles. These four are
        # the last method reach-throughs pending the god-method decomposition
        # roadmap (A4): stats Run / Property actions + barplot key/label helpers.
        self._runtime.stats.run = self._stats_run
        self._runtime.stats.property_change = self._stats_property_change
        self._runtime.barplot.current_keys = self._bar_current_keys
        self._runtime.barplot.well_display_label = self._bar_well_display_label
        # Phase 3 teardown: subscribe the EventBus's redraw / refresh signals.
        wire_event_bus(self, self._event_bus)
        # Phase 4 teardown: one debounce path + save/load dispatch for the data-dir JSON domains.
        self._persistence = build_persistence_registry(self._ctx)
        # Hold as a direct ctx collaborator (A5); post-construction — it takes ctx.
        object.__setattr__(self._ctx, "persistence", self._persistence)

        # Dataset state — data dir, well paths, row cache, timepoint / FOV
        # caches, pipeline_info, persistence doc — lives in
        # ``self._app_state.dataset`` (well_viewer/state/dataset.py); the
        # legacy names remain as @property shims further down.
        self._sidebar_map_refresh_pending: bool = False

        # Active fluorescent channel, ratio catalogue, and threshold range
        # now live in ``self._app_state.channel`` (well_viewer/state/channel.py).
        # ``_fluor_channels`` / ``_active_channel`` / ``_ratio_metrics`` /
        # ``_threshold*`` etc. remain as @property shims further down.

        # Heatmap layouts: arbitrary R×C grids decoupled from the physical
        # 8×12 plate. When empty, the heatmap controller synthesizes a plate
        # layout on the fly.
        self._runtime.heatmap.layouts = []

        # Per-tab view-state values waiting to apply on first tab visit
        # (populated by view_state.load_from_data_dir, drained by
        # _on_tab_change → view_state.apply_pending_tab_state).
        self._runtime.view_state.pending = None

        # Plot controls — widgets are assigned in _build_ui; keep placeholders
        # until then so callers can inspect default state. Plot options
        # (SEM/SD, per-FOV replicates, legend visibility, draw order,
        # fold-change) live in ``self._app_state.plot`` — see the shims below.
        # The button/observer lists below stay here: they are widget handles,
        # not state. attach_plot_toolbar() registers each SEM button in
        # _sem_btns, so these must exist BEFORE any plot tab is built.
        self._runtime.sem.btns = []
        self._runtime.sem.btn = None
        # Observers notified after _toggle_sem flips the state — for v2 widgets
        # (e.g. the Statistics SegmentedControl in ExportStyleSidebar) that don't
        # share the QPushButton API _sem_btns assumes.
        self._runtime.sem.observers = []   # callables (use_sem: bool) -> None
        self._runtime.fov.btns = []
        self._runtime.fov.btn = None
        self._runtime.fov.observers = []   # callables (use_fov: bool) -> None
        # Channel / timepoint / edit widget handles (_plot_chan_cb,
        # _review_image_chan_cb, _bar_tp_cb, _bar_swarm_cb, _bar_violin_cb,
        # _violin_bw_edit) are registered by the view builders via
        # WidgetRegistry; readers use ctx.widgets.get_or_none / has, so no
        # None-seed is needed here (A1).
        # Draw order and fold-change normalization state live in
        # ``self._app_state.plot`` (well_viewer/state/plot.py) — see the shims.
        # Plate selection and the unified saved-selections ("v2") model now
        # live in ``self._app_state.selection`` (well_viewer/state/selection.py).
        # ``_selections`` / ``_selected_wells`` / ``_active_solo_wells`` etc.
        # remain as @property shims further down.
        # _entry_edit / _cdf_xmin_edit / _cdf_xmax_edit are registered by the
        # Line-graph builder; readers use ctx.widgets.get_or_none (A1).

        # Plate-map well selection, the commit-coalescing flag, and the
        # plotting-tab solo-well overrides all live in
        # ``self._app_state.selection`` (see the shims below).

        # Preview / Review-image state — selected well, image maps, view
        # transform, drag bookkeeping, decoded-frame caches, overlay colours —
        # lives in ``self._app_state.preview`` and ``self._app_state.review_image``
        # (well_viewer/state/review_image.py); see the @property shims below.
        # Widget handles and the lazily-built montage arrays stay here.
        # _preview_fov_cb is registered by the preview/image builders; readers
        # use ctx.widgets.get_or_none / getattr(ctx.window, ..., None) (A1).
        self._runtime.review_image.tp_cb = None
        # Rubber-band rectangle for box-delete in include-edit mode; the
        # QRubberBand is parented to _review_image_label so it scrolls with
        # the image. Coordinates convert to mask-array space at release time.
        self._runtime.review_image.rubber_band = None
        self._runtime.review.included_overrides = {}
        self._runtime.review.csv_lookup_context = {}
        # Notes sub-tab text buffer now lives in the typed selection slice
        # (self._app_state.selection.notes_text). Data-dir autosave debouncing
        # lives in the PersistenceRegistry (self._persistence, built below).
        self._runtime.review_image.source_seg = None
        self._runtime.review_image.color_swatches = {}
        self._runtime.review_image.binary_btn = None
        self._runtime.review_image.outline_btn = None

        from well_viewer.core.app_shell_view import build_ui
        build_ui(self)
        self._apply_theme()
        # Gate the per-tab control bars to the no-data state on launch (they are
        # re-enabled the moment a dataset loads). Lazily-built tabs pick this up
        # on their first redraw via the same helper.
        refresh_plot_empty_states(self)

        if data_path is not None:
            QTimer.singleShot(100, lambda: self._load_path(data_path))

    # ── Threshold helpers ─────────────────────────────────────────────────────
    def _get_fluor_gate(self, channel: str) -> float: return get_fluor_gate(self._ctx, channel)

    def _get_all_fluor_gates(self) -> dict[str, float]: return get_all_fluor_gates(self._ctx)

    def _get_thresh_frac_on(self, channel: str | None = None) -> float: return get_thresh_frac_on(self._ctx, channel)

    def _set_thresh_frac_on(self, value, channel: str | None = None) -> None: return set_thresh_frac_on(self._ctx, value, channel)

    def _refresh_thresh_frac_quick_edit(self) -> None: return refresh_thresh_frac_quick_edit(self._ctx)

    # ── Mode (Review / Analyze) plumbing ─────────────────────────────────
    modeChanged = Signal(str)   # emitted with "review" or "analyze"

    def mountAnalyzePane(self, widget) -> None:
        """Mount *widget* as the Analyze peer of the centre stack (index 1).

        Hosting it here keeps Review's left rail visible in both modes;
        ``apply_sidebar_visibility`` drives the pane's own sidebar via the
        registered ``_analyze_pane`` handle."""
        self._widget_registry.register("_analyze_pane", widget)
        self._mount_secondary_pane(widget, 1)

    def mountDatasetsPane(self, widget) -> None:
        """Mount *widget* as the Datasets peer of the centre stack (index 2)."""
        self._widget_registry.register("_datasets_pane", widget)
        self._mount_secondary_pane(widget, 2)

    def _mount_secondary_pane(self, widget, index: int) -> None:
        # Panes mount in order (Review=0, Analyze=1, Datasets=2): a fresh mount
        # appends, a re-mount replaces only the pane already at that index.
        stack = getattr(self, "_central_pane_stack", None)
        if stack is None:
            return
        if stack.count() > index:
            old = stack.widget(index)
            stack.removeWidget(old)
            old.setParent(None)
            stack.insertWidget(index, widget)
        else:
            stack.addWidget(widget)

    def currentMode(self) -> str:
        stack = getattr(self, "_central_pane_stack", None)
        if stack is None:
            return "review"
        return pane_mode(stack.currentIndex())

    def _on_central_pane_changed(self, idx: int) -> None: return on_central_pane_changed(self._ctx, idx)

    def _toggle_sidebar(self) -> None: return toggle_sidebar(self._ctx)

    def _build_centre(self, parent) -> None: return build_centre_shim(self._ctx, parent)

    # ── Phase 10 (A1) section-nav <-> notebook bridge ─────────────────────
    _SECTION_ICONS = {
        "Home": "home",
        "Plotting":           "line-chart",
        "Statistics":         "sigma",
        "Multivariate":       "scatter-chart",
        "Spatial":            "waypoints",
        "Image Browser":      "layout-grid",
        "smFISH":             "dna",
        "Review CSV":         "file-spreadsheet",
        "Saved Plots":        "image",
        "Batch Export":       "boxes",
    }

    def _sync_section_nav_from_notebook(self) -> None: return sync_section_nav_from_notebook(self._ctx)

    def _on_section_nav_changed(self, key: str) -> None:
        if self._section_nav_building:
            return
        # Inert Split-View hook (None unless split_view is installed): lets the
        # feature leave split before the rail navigates. Returns False ⇒ proceed.
        hook = _tabctl.split_nav_intercept
        if hook is not None and hook(self._ctx, key):
            return
        self._notebook.setCurrentByName(key)

    def _tab_history_back(self) -> None: return tab_history_back(self._ctx)

    def _tab_history_forward(self) -> None: return tab_history_forward(self._ctx)

    # ── Statistics tab ────────────────────────────────────────────────────────
    # Tab / panel construction lives in ``views/stats_view.py`` — callers invoke
    # ``build_stats_tab`` / ``build_stats_group_editor`` / ``build_stats_results_panel``
    # there directly (teardown Phase 8).

    # Stats tab group-editor methods (delegate to stats_controller)

    def _stats_write_result(self, text: str) -> None:
        self._stats_result_text.setReadOnly(False)
        self._stats_result_text.setPlainText(text or "")
        self._stats_result_text.setReadOnly(True)

    def _stats_collect_group_values(self, grp: BarGroup, target_t: float) -> list[float]:
        return stats_collect_group_values_shim(self._ctx, grp, target_t)

    _STATS_STATISTIC_KEYS = {
        "Mean (above threshold)": "mean",
        "Mean (all cells)": "mean_all",
        "Median (above threshold)": "median",
        "Fraction above threshold": "fraction",
    }

    def _stats_active_val_col(self) -> str: return stats_active_val_col(self._ctx)

    def _stats_active_threshold(self) -> float: return stats_active_threshold(self._ctx)

    def _stats_active_statistic(self) -> str: return stats_active_statistic(self._ctx)

    def _stats_run(self) -> None: return stats_run_shim(self._ctx)

    def _stats_draw_ks_cdf(self, group_vals: list[tuple[str, list[float]]], tp_str: str) -> None:
        return stats_draw_ks_cdf_shim(self._ctx, group_vals, tp_str)

    # ── Preview sidebar picker ────────────────────────────────────────────────

    def _refresh_preview_picker(self) -> None: return refresh_preview_picker_shim(self._ctx)

    # ─────────────────────────────────────────────────────────────────────────
    # Replicate panel

    # ─────────────────────────────────────────────────────────────────────────
    # Well-label editor

    # ── unified-model (app._app_state.selection.selections) — the in-memory source of truth ──────

    def _sel_by_id(self, sid): return sel_by_id(self._ctx, sid)

    def _sel_id_for_well(self, tok) -> str | None:
        for s in self._app_state.selection.selections:
            if tok in (s.get("wells") or []):
                return s.get("id")
        return None

    def _sel_add(self, name = None, wells = None, replicates = None, source = 'user', *, make_current = True):
        return sel_add(self._ctx, name, wells, replicates, source, make_current=make_current)

    def _sel_set_hidden(self, sid, hidden, *, light = False) -> None:
        return sel_set_hidden(self._ctx, sid, hidden, light=light)

    def _sel_toggle_hidden(self, sid) -> None:
        s = self._sel_by_id(sid)
        if s is not None:
            self._sel_set_hidden(sid, not bool(s.get("hidden")))

    def _groups_centre_refresh(self) -> None: return groups_centre_refresh(self._ctx)

    def _rebuild_all(self, *, invalidate: bool = True) -> None:
        return rebuild_all(self._ctx, invalidate=invalidate)

    # ── Bar group persistence (delegates to well_viewer.persistence.bar_groups)

    def _bar_load_groups(self) -> None:
        from well_viewer.persistence import bar_groups as _bg_persist
        _bg_persist.load_via_dialog(self)

    # ── Persistence ──────────────────────────────────────────────────────────
    # The data-dir JSON domains (ratios / heatmap_layouts / cell_overrides /
    # line_order / view_state / notes) are saved and loaded through
    # ``self._persistence`` — see well_viewer/persistence/registry.py.

    def _view_state_reset(self) -> None:
        from well_viewer.persistence import view_state as _vs
        _vs.reset(self._ctx)

    # ── Sample definitions + Cell Gating persistence ─────────────────────────
    # Implementations live in ``well_viewer.persistence.{sample_definitions,cell_gating}``.

    def _load_sample_definitions_from_pipeline_info(self) -> bool:
        from well_viewer.persistence import sample_definitions as _sd
        return _sd.load_from_pipeline_info(self._ctx)

    def _load_gating_from_pipeline_info(self) -> bool:
        from well_viewer.persistence import cell_gating as _cg
        return _cg.load_from_pipeline_info(self._ctx)

    # ── decision-#1 colour: "the plate is the legend" — every well / rep-set /
    # group is coloured by *well-position rank*, so the same well always gets the
    # same colour everywhere (plate maps, line/bar/stats plots).
    def _rank_color_well(self, tok) -> str:
        return color_for_well(tok)

    def _sel_color(self, s) -> str: return sel_color(self._ctx, s)

    def _rank_color_rset(self, rset) -> str: return color_for_replicate_set(rset, self._app_state.selection.selections)

    # ── Montage zoom helpers ──────────────────────────────────────────────────

    def _apply_theme(self) -> None: return apply_theme(self._ctx)

    # ── Loading ───────────────────────────────────────────────────────────────

    def _browse(self) -> None:
        """Open a directory picker. Expects in/ + out/ subdirs or a flat CSV dir."""
        d = QFileDialog.getExistingDirectory(self, "Open results directory")
        if d:
            # Defer so the dialog closes and the window repaints before load
            QTimer.singleShot(50, lambda: self._load_path(Path(d)))

    def _load_path(self, path): return load_path_shim(self._ctx, path)

    def _load_directory(self, d: Path, label: str | None = None) -> None:
        self._drain_pending_centre_builders()
        _load_directory_controller(self._ctx, d, label=label)
        refresh_plot_empty_states(self)

    def _drain_pending_centre_builders(self) -> None: return drain_pending_centre_builders(self._ctx)

    def _read_pipeline_info(self, path: Path):
        return _read_pipeline_info_shared(path, logger=_logger, check_parent=True)

    def _load_well_csv(self, path: Path):
        return load_well_csv(path)

    def _extract_well_token(self, label: str):
        return _extract_well_token(label)

    def _cleanup_tmp(self) -> None:
        if self._app_state.dataset.tmp_dir and self._app_state.dataset.tmp_dir.exists():
            shutil.rmtree(self._app_state.dataset.tmp_dir, ignore_errors=True)
        self._app_state.dataset.tmp_dir = None

    # ── Plate-map sidebar helpers ─────────────────────────────────────────────

    def _parse_rc(self, label: str) -> tuple[str | None, str | None]:
        return parse_rc(label)

    def _rebuild_all_timepoints_cache(self) -> None:
        """Recompute the canonical timepoint list (delegates to DatasetState)."""
        self._app_state.dataset.rebuild_timepoints()

    def _rebuild_all_fovs_cache(self) -> None:
        """Recompute the canonical FOV list (delegates to DatasetState)."""
        self._app_state.dataset.rebuild_fovs()

    @staticmethod
    def _mute_color(color: str, factor: float = 0.5) -> str: return mute_color(color, factor)

    def _refresh_sidebar_map(self) -> None:
        """Debounce sidebar recoloring work to one pass per event-loop tick."""
        if self._sidebar_map_refresh_pending:
            return
        self._sidebar_map_refresh_pending = True
        QTimer.singleShot(0, self._refresh_sidebar_map_now)

    def _refresh_sidebar_map_now(self) -> None: refresh_sidebar_map_now(self._ctx)

    # ── v2 plate-widget bridge (WellPlateSelector ↔ WellViewerApp state) ──────
    # The left-rail plate is now a single ``widgets.WellPlateSelector`` instead
    # of an ``app._sidebar_btns`` grid of ``WellButton``s. ``app._app_state.selection.selected_wells``
    # stays the source of truth; ``_refresh_sidebar_map_now`` pushes appearance /
    # selection onto the widget, and the handlers below take user actions back.

    def _plate_commit_deferred(self) -> None: return plate_commit_deferred(self._ctx)

    def _set_selected_wells(self, new_set, *, commit = True) -> None:
        return set_selected_wells(self._ctx, new_set, commit=commit)

    _PLOTTING_TABS = frozenset({
        "Line Graphs", "Bar Plots", "Scatter Plot", "Distribution", "Heat Map",
    })

    # ── Ratio metric helpers ─────────────────────────────────────────────────

    def _ratio_label_for(self, ratio: RatioMetric) -> str: return ratio_label_for(self._ctx, ratio)

    def _ratio_dropdown_labels(self) -> list[str]:
        return [self._ratio_label_for(r) for r in self._app_state.channel.ratio_metrics]

    def _active_channel_label(self) -> str: return active_channel_label(self._ctx)

    def _set_ratio_metrics(self, ratios: Iterable[RatioMetric]) -> None: return set_ratio_metrics(self._ctx, ratios)

    # ── Active channel ───────────────────────────────────────────────────────

    def _on_plot_channel_selected(self, source = None) -> None: return on_plot_channel_selected(self._ctx, source)

    def _on_metric_selected_global(self) -> None: return on_metric_selected_global(self._ctx)

    # Named ``_stats_property_change`` (not ``_on_stats_property_change``) so the
    # stats results-panel builder can wire the Property combo through the
    # ``ctx.runtime.stats`` namespace (prefix ``_stats_``) without naming the
    # god-object; the body stays fully app-side.
    def _stats_property_change(self) -> None: return on_stats_property_change(self._ctx)
        # Stats display refreshes on its own when run; nothing to redraw here.

    def _toggle_sem(self) -> None: return toggle_sem(self._ctx)
        # _toggle_sem stays off the bus until Phase 9 relocates it (see teardown doc).

    # ── Per-FOV-replicates toggle ────────────────────────────────────────────

    def _use_fov_spread_active(self) -> bool:
        """Effective state of the per-FOV-replicates toggle for plotting."""
        return bool(self._app_state.plot.use_fov_replicates)

    def _toggle_fov_replicates(self) -> None: return toggle_fov_replicates(self._ctx)

    def _refresh_fov_btn_state(self) -> None: return refresh_fov_btn_state(self._ctx)
    def _refresh_bar_layout_btn_state(self) -> None: return refresh_bar_layout_btn_state(self._ctx)
    # ── Well selection (plate-map backed) ─────────────────────────────────────

    def _selected_labels(self) -> list[str]:
        """Return labels in a stable order (sorted) for consistent plot colours."""
        return sorted(self._app_state.selection.selected_wells, key=lambda lbl: self._parse_rc(lbl))

    def _get_rows(self, label: str) -> pd.DataFrame:
        # A8 residuals-ledger R1: the lazy load + QC/Crop projection moved to the
        # ctx-native data service ``data_loading.get_rows``; this stays as the
        # legacy god-object entry point and delegates through the context.
        from well_viewer.data.data_loading import get_rows
        return get_rows(self._ctx, label)

    def _invalidate_dataset_cache(self) -> None: self._app_state.dataset.cache.clear()

    # ── Preview panel ─────────────────────────────────────────────────────────

    def _norm_timepoint(self, value: object) -> str: return norm_timepoint(self._ctx, value)

    def _review_row_keys(self, row: dict) -> tuple[str, str, str]: return review_row_keys(self._ctx, row)

    # ── Plotting ──────────────────────────────────────────────────────────────

    def _redraw(self) -> None: return redraw_app(self._ctx)

    # ── Bar plot tab ──────────────────────────────────────────────────────────

    def _current_centre_tab(self) -> str: return current_centre_tab(self._ctx)

    def _visible_centre_tabs(self) -> set[str]: return visible_centre_tabs(self._ctx)

    def _is_tab_visible(self, name: str) -> bool: return is_tab_visible(self._ctx, name)

    def _sync_heatmap_well_drag_mode(self, enable: bool) -> None: return sync_heatmap_well_drag_mode(self._ctx, enable)
            # Don't force-disable wells here — the per-tab branch in
            # ``_on_tab_change`` either hides the sidebar (preview / image
            # table tabs) or calls ``_refresh_sidebar_map`` which restores the
            # correct enabled state per well.

    def _show_line_sidebar(self) -> None:
        """No-op: the unified well picker is always visible for plot tabs."""
        pass

    def _build_review_csv_tab(self, parent) -> None:
        from well_viewer.features.review_csv.tab_view import build_review_csv_tab as _v
        _v(self._ctx, parent)

    def _refresh_review_csv(self) -> None: refresh_review_csv(self._ctx)

    def _refresh_review_csv_rows(self, rows=None) -> None: refresh_review_csv_rows(self._ctx, rows)

    def _update_bar_tp_menu(self) -> None: return update_bar_tp_menu(self._ctx)

    # ── Bar drag-and-drop reordering ─────────────────────────────────────────
    # Implementations live in ``well_viewer.barplot_controller``.

    def _bar_current_keys(self) -> list: return _bar_ordered_keys(self._ctx)

    # ── Bar plot export ───────────────────────────────────────────────────────

    def _well_display_label(self, lbl: str) -> str:
        return well_display_label(self._ctx, lbl)

    def _bar_well_display_label(self, lbl: str) -> str: return bar_well_display_label(self._ctx, lbl)

    def _replicate_display_label(self, rset: ReplicateSet) -> str: return replicate_display_label(self._ctx, rset)

    def _rep_sets_loaded(self) -> list[ReplicateSet]: return replicate_sets_loaded(self._app_state.selection.selections, self._app_state.dataset.well_paths)

    def _groups_from_rep_sets(self) -> list[BarGroup]:
        """One BarGroup per loaded selection (each wrapping its ReplicateSet)."""
        return [BarGroup(r.name, members=[r]) for r in self._rep_sets_loaded()]

    def _rep_sets_active(self) -> list[ReplicateSet]: return rep_sets_active(self._ctx)

    def _invalidate_stats_cache(self) -> None: return invalidate_stats_cache(self._ctx)

    def _invalidate_review_image_frame_cache(self) -> None: return invalidate_review_image_frame_cache(self._ctx)

    def _collect_bar_items(self, target_t: float, *, fc_state = None, miss_sink = None) -> tuple:
        return collect_bar_items_for_app(self._ctx, target_t, fc_state=fc_state, miss_sink=miss_sink)

    # ── Save current figure ───────────────────────────────────────────────────

    _FIG_FILETYPES = [
        ("PNG image",        "*.png"),
        ("SVG vector",       "*.svg"),
        ("PDF document",     "*.pdf"),
        ("EPS vector",       "*.eps"),
        ("All files",        "*.*"),
    ]

    def _save_matplotlib_fig(self, fig: Figure, default_name: str) -> None:
        """Show a save-file dialog and write *fig* to disk."""
        _plot_save_matplotlib_fig_orchestrator(
            self._ctx,
            fig,
            default_name,
            plot_bg=theme.CPub.bg,
        )

    # ── Scatter Plot tab ───────────────────────────────────────────────────────

    def _col_for_scatter_axis(self, channel_entry: str, metric_key: str) -> str:
        return col_for_scatter_axis(self._ctx, channel_entry, metric_key)

    def _col_for_scatter_entry(self, entry: str) -> str: return col_for_scatter_entry(self._ctx, entry)

    # ── Per-axis scatter metric helpers ────────────────────────────────────

    _SCATTER_AGG_METRIC_EXTRA = {"Fraction above threshold": "fraction_above_threshold"}

    def _scatter_axis_metric_key(self, axis: str, *, agg = False) -> str:
        return scatter_axis_metric_key(self._ctx, axis, agg=agg)

    def _refresh_scatter_metric_for_axis(self, axis: str) -> None: return refresh_scatter_metric_for_axis(self._ctx, axis)

    def _compose_scatter_agg_stat(self, channel_entry: str, metric_label: str) -> str:
        return compose_scatter_agg_stat(self._ctx, channel_entry, metric_label)

    def _open_scatter_cell_viewer(self, well_label: str, filename: str, nuclear_id: str, row_idx: int) -> None:
        # App-native shim: still exercised by tests/test_scatter_cell_viewer_dock.py.
        # The opener + viewer are ctx-native — thread the context, not the god-object.
        return open_scatter_cell_viewer(self._ctx, well_label, filename, nuclear_id, row_idx)

    # ── Log / status helpers ──────────────────────────────────────────────────

    def _set_status(self, msg: str) -> None:
        self._status_lbl.setText(msg)

    def _toast(self, msg: str, *, kind = 'info', msec = 3500) -> None: return toast(self._ctx, msg, kind=kind, msec=msec)

    def _show_progress(self, maximum: int, msg: str = '') -> None: return show_progress(self._ctx, maximum, msg)

    def _step_progress(self, value: int, msg: str = '') -> None: return step_progress(self._ctx, value, msg)

    def _hide_progress(self) -> None:
        """Remove the progress bar."""
        self._progress_bar.setVisible(False)
        self._progress_bar.setValue(0)

    def _toggle_log(self) -> None: return toggle_log(self._ctx)

    def _clear_log(self) -> None:
        if hasattr(self, "_log_text") and self._log_text is not None:
            self._log_text.clear()

# =============================================================================
# Entry point
# =============================================================================

def main() -> None:
    ap = argparse.ArgumentParser(description="Well Viewer",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--data_dir", type=Path, default=None,
                    help="Directory of per-well CSVs, or a .zip / .tar.gz archive.")
    args = ap.parse_args()
    qapp = QApplication.instance() or QApplication(sys.argv)
    win = QMainWindow()
    win.setWindowTitle("Well Viewer")
    app = WellViewerApp(data_path=args.data_dir)
    win.setCentralWidget(app)
    win.resize(1400, 900)
    win.show()
    qapp.exec()


__all__ = ["WellViewerApp", "BarGroup", "ReplicateSet", "main"]


if __name__ == "__main__":
    main()
