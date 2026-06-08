"""
well_viewer.py
--------------
GUI for exploring per-well fluorescence measurement CSVs from process_microscopy.py.

Open a directory or archive (zip / tar.gz) with the single "Open…" button.
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
import logging
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import (
    Qt, QTimer, Signal,
)
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QMainWindow, QPushButton, QRubberBand, QWidget,
)

import matplotlib
import pandas as pd
from matplotlib.figure import Figure
from well_viewer.domain.batch_models import BarGroup, ReplicateSet
from well_viewer.core.app_state import AppState
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
# paints, which dominates startup cost.
#
# Each lazy proxy below resolves its target on first call, then caches it.
# Subsequent calls are a single dict lookup + indirect call.
def _lazy(module_path: str, attr: str):
    cache: list = []
    def _proxy(*args, **kwargs):
        if not cache:
            from importlib import import_module
            cache.append(getattr(import_module(module_path), attr))
        return cache[0](*args, **kwargs)
    _proxy.__name__ = f"_lazy<{module_path}.{attr}>"
    return _proxy


_bar_apply_ylims = _lazy("well_viewer.features.barplot.controller", "apply_bar_ylims")
_bar_ordered_keys = _lazy("well_viewer.features.barplot.controller", "ordered_bar_keys")






_load_directory_controller = _lazy("well_viewer.core.load_controller", "load_directory")

_plot_save_bar_figure_orchestrator = _lazy("well_viewer.core.plot_orchestrator", "save_bar_figure")
_plot_save_line_figure_orchestrator = _lazy("well_viewer.core.plot_orchestrator", "save_line_figure")
_plot_save_matplotlib_fig_orchestrator = _lazy("well_viewer.core.plot_orchestrator", "save_matplotlib_fig")


_on_review_image_click_controller = _lazy("well_viewer.features.review_image.controller", "on_review_image_click")
_select_review_csv_row_for_cell_controller = _lazy("well_viewer.features.review_image.controller", "select_review_csv_row_for_cell")

_stats_collect_group_values = _lazy("well_viewer.features.stats.controller", "collect_group_values")
_stats_draw_ks_cdf = _lazy("well_viewer.features.stats.controller", "draw_ks_cdf")

from well_viewer.domain.viewer_state import read_pipeline_info as _read_pipeline_info_shared
from well_viewer.core.ui_helpers import (
    ask_name_dialog as _ui_ask_name_dialog, make_fluor_thumb, make_overlay_thumb,
)
from ui.theme import (
    ACCENT, ACCENT_DARK, PLOT_BG,
)


try:
    import tifffile as _tifffile
    _TIFFFILE_AVAILABLE = True
except ImportError:
    _tifffile = None          # type: ignore[assignment]
    _TIFFFILE_AVAILABLE = False

try:
    from PIL import Image as _PILImage
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

# ── Semantic colour aliases (use these instead of magic hex strings) ──────────
CLR_ACCENT_DARK = ACCENT_DARK

from well_viewer.domain.plate_layout import WELL_COLORS
from well_viewer.domain.selections_model import well_rank as _sel_well_rank

NO_SELECTION_MSG = "No wells or well groups selected.\nSelect wells on the left panel or define groups to plot."


# `_set_combo_values` was hoisted into well_viewer.ui_helpers so the new
# `channel_state_controller` can use it without a circular import back
# into runtime_app. Re-export the old private name for compatibility with
# call sites that still reference it via the runtime_app module.
from well_viewer.core.ui_helpers import set_combo_values as _set_combo_values  # noqa: E402, F401





from well_viewer.data.data_loading import (
    load_well_csv, parse_timepoint_hours,
)
from well_viewer.domain.ratio_models import RatioMetric



# Aliased to avoid collision with the WellViewerApp._extract_well_token method below.
from well_viewer.data.data_loading import extract_well_token as _extract_well_token  # noqa: F401


# =============================================================================
# Categorical label colourmap  (canonical: well_viewer/views/image_panel_view.py)
# =============================================================================


# =============================================================================
# Reusable image panel  (canonical: well_viewer/views/image_panel_view.py)
# =============================================================================


# =============================================================================
# GUI log handler  (canonical: well_viewer/views/status_view.py)
# =============================================================================

from well_viewer.features.grouping.controller import rep_quick_pairs
from well_viewer.features.scatter.controller import redraw_scatter_agg_orchestrate
from well_viewer.features.scatter.controller import redraw_scatter_orchestrate
from well_viewer.features.stats.controller import stats_refresh_map
from well_viewer.core.ui_helpers import refresh_plot_empty_states
from functools import partial
from well_viewer.core.selection_controller import labels_apply_affix
from well_viewer.features.barplot.controller import toggle_fov_replicates, update_bar_tp_menu, bar_event_xdata, on_bar_drag_motion, toggle_swarm, toggle_violin, bar_well_display_label, rep_sets_active, collect_bar_items_for_app, refresh_bar_layout_btn_state, update_bar_tp_selection_display
from well_viewer.core.channel_state_controller import get_fluor_gate, get_all_fluor_gates, get_thresh_frac_on, open_ratio_panel, ratio_label_for, channel_key_for_label, active_channel_label, rebuild_ratio_index, set_ratio_metrics, on_plot_channel_selected, on_preview_channel_selected, on_metric_selected, on_metric_selected_global
from well_viewer.features.export.service import export_scatter_agg_data
from well_viewer.features.export.style import open_export_style_panel
from well_viewer.features.grouping.controller import rep_refresh_map, rep_add, rep_quick_pairs_from_dropdowns, rank_color_wells, rank_color_rset, rep_color_for, replicate_display_label, rep_sets_loaded
from well_viewer.features.lineplot.controller import toggle_sem
from well_viewer.core.load_controller import load_path_shim
from well_viewer.core.plot_orchestrator import open_batch_export_shim, redraw_app, open_bar_batch_export_shim, open_scatter_cells_batch_export_shim, open_scatter_agg_batch_export_shim, save_scatter_figure as _plot_save_scatter_figure_orchestrator, save_scatter_agg_figure as _plot_save_scatter_agg_figure_orchestrator
from well_viewer.features.preview.controller import refresh_preview_picker_shim, sync_preview_well_for_image_tabs
from well_viewer.features.review_image.controller import review_row_keys, review_norm_fov, review_collect_timepoints, refresh_review_csv, refresh_review_csv_rows, review_effective_included, review_load_rows
from well_viewer.features.review_image.renderer import toggle_review_image_source, refresh_review_image_source_btn, review_image_set_color, review_image_refresh_color_swatch, reset_review_image_colors, toggle_review_image_binary_mask, refresh_review_image_binary_btn, toggle_review_image_outline, refresh_review_image_outline_btn, draw_review_image, render_review_image_display, review_image_resolve_lut, review_image_auto_lut, review_image_commit_lut, review_image_zoom_step, apply_review_image_cursor, set_review_image_include_mode, invalidate_review_image_frame_cache
from well_viewer.features.scatter.callbacks import open_scatter_cell_viewer
from well_viewer.features.scatter.controller import col_for_scatter_axis, col_for_scatter_entry, scatter_axis_metric_key, refresh_scatter_metric_for_axis, refresh_scatter_agg_metric_for_axis, sync_scatter_agg_stat_combos, compose_scatter_agg_stat, update_tp_selection_display, update_scatter_tp_selection_display
from well_viewer.core.selection_controller import rep_select, rep_clear_all, labels_clear_all, sel_by_id, sel_strip_wells, sel_add, sel_rename, sel_delete, sel_set_hidden, sel_set_color, sel_export_one, sel_set_composition, sel_select, sel_reorder, sel_duplicate, enforce_well_exclusivity, groups_centre_refresh, rebuild_all, sel_color, refresh_sidebar_map_now, plate_commit_deferred, set_selected_wells, select_invert, invalidate_stats_cache
from well_viewer.features.stats.controller import stats_active_group, stats_collect_group_values_shim, stats_active_channel_entry, stats_active_val_col, stats_active_threshold, stats_active_statistic, stats_run_shim, stats_draw_ks_cdf_shim, on_stats_property_change
from well_viewer.core.tab_controller import on_central_pane_changed, build_centre_shim, sync_section_nav_from_notebook, tab_history_back, tab_history_forward, current_centre_tab, sync_heatmap_well_drag_mode, run_tab_switch_smoke_checks
from well_viewer.core.ui_helpers import notes_schedule_save, apply_theme, on_theme_change, mute_color, norm_timepoint, toast, show_progress, step_progress, toggle_log
from well_viewer.core.centre_view import drain_pending_centre_builders
from well_viewer.features.preview.panel_view import refresh_fov_btn_state

class WellViewerApp(QWidget):

    def __init__(self, parent=None, data_path: Optional[Path] = None) -> None:
        super().__init__(parent)
        self._NP_AVAILABLE = _NP_AVAILABLE
        self._np = _np
        self._theme_name = "Dark"

        # ── Typed domain state (runtime_app teardown, Phase 1) ───────────────
        # ChannelState owns the channel / ratio / threshold attributes that
        # were loose ``self._*`` fields; AppState aggregates the domain-state
        # objects and EventBus carries the cross-cutting signal catalogue.
        # The legacy attribute names survive as @property shims (search
        # "Phase 1 ChannelState shims" below) — ChannelState is the single
        # source of truth, so no call site needed to change.
        self._app_state = AppState()
        self._event_bus = EventBus()
        # Name-keyed store for Qt widget handles. Tab builders register their
        # figures / canvases / axes here; ``__getattr__`` (below) forwards
        # legacy ``app._<widget>`` reads to it so reader call sites are
        # untouched while writers migrate tab by tab.
        self._widget_registry = WidgetRegistry(self)
        # The dependency-injection context handed to tab builders and
        # controllers in place of ``self``. Bundles the typed state, the bus,
        # and the widget registry (+ a transitional ``app`` escape hatch).
        self._ctx = ViewerContext(
            app=self,
            state=self._app_state,
            bus=self._event_bus,
            widgets=self._widget_registry,
        )
        # Phase 3 teardown: subscribe the EventBus's redraw / refresh signals.
        wire_event_bus(self, self._event_bus)
        # Phase 4 teardown: one debounce path + save/load dispatch for the
        # data-dir JSON domains, replacing the per-domain wrapper methods.
        self._persistence = build_persistence_registry(self)

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
        self._heatmap_layouts: List = []
        self._active_heatmap_layout_name: Optional[str] = None

        # Per-tab view-state values waiting to apply on first tab visit
        # (populated by view_state.load_from_data_dir, drained by
        # _on_tab_change → view_state.apply_pending_tab_state).
        self._view_state_pending: Optional[dict] = None

        # Plot controls — widgets are assigned in _build_ui; keep placeholders
        # until then so callers can inspect default state. Plot options
        # (SEM/SD, per-FOV replicates, legend visibility, draw order,
        # fold-change) live in ``self._app_state.plot`` — see the shims below.
        # The button/observer lists below stay here: they are widget handles,
        # not state. attach_plot_toolbar() registers each SEM button in
        # _sem_btns, so these must exist BEFORE any plot tab is built.
        self._sem_btns: List = []
        self._sem_btn = None
        # Observers notified after _toggle_sem flips the state — for v2 widgets
        # (e.g. the Statistics SegmentedControl in ExportStyleSidebar) that don't
        # share the QPushButton API _sem_btns assumes.
        self._sem_observers: List = []   # callables (use_sem: bool) -> None
        self._fov_btns: List = []
        self._fov_btn = None
        self._fov_observers: List = []   # callables (use_fov: bool) -> None
        # Channel / timepoint comboboxes are assigned to these attrs in the view builders.
        self._plot_chan_cb = None
        self._montage_chan_cb = None
        self._review_image_chan_cb = None
        self._bar_tp_cb = None
        self._bar_swarm_cb = None
        self._bar_violin_cb = None
        self._violin_bw_edit = None
        self._bar_ylim_mean_lo_edit = None
        self._bar_ylim_mean_hi_edit = None
        self._bar_ylim_frac_lo_edit = None
        self._bar_ylim_frac_hi_edit = None
        # Draw order and fold-change normalization state live in
        # ``self._app_state.plot`` (well_viewer/state/plot.py) — see the shims.
        # Plate selection and the unified saved-selections ("v2") model now
        # live in ``self._app_state.selection`` (well_viewer/state/selection.py).
        # ``_selections`` / ``_selected_wells`` / ``_active_solo_wells`` etc.
        # remain as @property shims further down.
        self._rep_quick_pair_dir   = "row"
        self._rep_quick_iter_order = "row"
        self._bar_quick_pair_dir   = "row"
        self._bar_quick_iter_order = "row"
        self._entry_edit = None
        self._cdf_xmin_edit = None
        self._cdf_xmax_edit = None

        # Plate-map well selection, the commit-coalescing flag, and the
        # plotting-tab solo-well overrides all live in
        # ``self._app_state.selection`` (see the shims below).

        # Preview / Review-image state — selected well, image maps, view
        # transform, drag bookkeeping, decoded-frame caches, overlay colours —
        # lives in ``self._app_state.preview`` and ``self._app_state.review_image``
        # (well_viewer/state/review_image.py); see the @property shims below.
        # Widget handles and the lazily-built montage arrays stay here.
        self._preview_fov_cb = None
        self._review_image_tp_cb = None
        # Rubber-band rectangle for box-delete in include-edit mode; the
        # QRubberBand is parented to _review_image_label so it scrolls with
        # the image. Coordinates convert to mask-array space at release time.
        self._review_image_rubber_band: Optional[QRubberBand] = None
        self._review_included_overrides: Dict[Tuple[str, str, str, str], str] = {}
        self._review_csv_lookup_context: Dict[str, str] = {}
        # Notes sub-tab text buffer. Data-dir autosave debouncing now lives in
        # the PersistenceRegistry (self._persistence, built below).
        self._notes_text: str = ""
        self._review_image_raw_btn = None
        self._review_image_color_swatches: Dict[str, object] = {}
        self._review_image_binary_btn = None
        self._review_image_outline_btn = None

        from well_viewer.core.app_shell_view import build_ui
        build_ui(self)
        self._apply_theme()

        if data_path is not None:
            QTimer.singleShot(100, lambda: self._load_path(data_path))

    @staticmethod
    def _position_root_on_screen(root, *, preferred_w: int, preferred_h: int) -> None:
        """Qt: no-op; window placement handled by QMainWindow / QWidget defaults."""
        return

    # ── Threshold helpers ─────────────────────────────────────────────────────
    def _get_fluor_gate(self, channel: str) -> float: return get_fluor_gate(self, channel)

    def _get_all_fluor_gates(self) -> Dict[str, float]: return get_all_fluor_gates(self)

    def _get_thresh_frac_on(self, channel: Optional[str] = None) -> float: return get_thresh_frac_on(self, channel)

    # ── UI ────────────────────────────────────────────────────────────────────


    # ── Mode (Review / Analyze) plumbing ─────────────────────────────────
    modeChanged = Signal(str)   # emitted with "review" or "analyze"

    def mountAnalyzePane(self, widget) -> None:
        """Mount *widget* as the Analyze peer of the centre stack.

        The mockup keeps the left rail (with the mode-seg, section nav,
        and plate) visible in both modes; hosting Analyze inside this
        widget's central stack is what makes that possible without
        hoisting the sidebar out into all_well.
        """
        stack = getattr(self, "_central_pane_stack", None)
        if stack is None:
            return
        # Replace any previously-mounted analyze pane.
        while stack.count() > 1:
            old = stack.widget(1)
            stack.removeWidget(old)
            old.setParent(None)
        stack.addWidget(widget)

    def setMode(self, name: str) -> None:
        """Programmatically switch mode (``"review"`` / ``"analyze"``)."""
        target = 1 if str(name).lower() == "analyze" else 0
        stack = getattr(self, "_central_pane_stack", None)
        if stack is None or stack.currentIndex() == target:
            return
        stack.setCurrentIndex(target)

    def currentMode(self) -> str:
        stack = getattr(self, "_central_pane_stack", None)
        if stack is None:
            return "review"
        return "analyze" if stack.currentIndex() == 1 else "review"

    def _on_central_pane_changed(self, idx: int) -> None: return on_central_pane_changed(self, idx)

    def _build_centre(self, parent) -> None: return build_centre_shim(self, parent)

    # ── Phase 10 (A1) section-nav <-> notebook bridge ─────────────────────
    _SECTION_ICONS = {
        "Plotting":           "line-chart",
        "Statistics":         "sigma",
        "Image Table":        "layout-grid",
        "Segmentation":       "scan-line",
        "Review CSV":         "file-spreadsheet",
        "Sample Definitions": "tag",
        "Batch Export":       "boxes",
    }

    def _sync_section_nav_from_notebook(self) -> None: return sync_section_nav_from_notebook(self)

    def _on_section_nav_changed(self, key: str) -> None:
        if self._section_nav_building:
            return
        self._notebook.setCurrentByName(key)


    def _tab_history_back(self) -> None: return tab_history_back(self)

    def _tab_history_forward(self) -> None: return tab_history_forward(self)

    # ── Statistics tab ────────────────────────────────────────────────────────
    # Tab / panel construction lives in ``views/stats_view.py`` — callers invoke
    # ``build_stats_tab`` / ``build_stats_group_editor`` / ``build_stats_results_panel``
    # there directly (teardown Phase 8).

    # Stats tab group-editor methods (delegate to stats_controller)

    def _stats_active_group(self) -> Optional[BarGroup]: return stats_active_group(self)


    def _stats_refresh_single_btn(self, tok: str) -> None: stats_refresh_map(self)











    def _stats_write_result(self, text: str) -> None:
        self._stats_result_text.setReadOnly(False)
        self._stats_result_text.setPlainText(text or "")
        self._stats_result_text.setReadOnly(True)


    def _stats_collect_group_values(self, grp: BarGroup, target_t: float) -> List[float]:
        return stats_collect_group_values_shim(self, grp, target_t)

    _STATS_STATISTIC_KEYS = {
        "Mean (above threshold)": "mean",
        "Mean (all cells)": "mean_all",
        "Median (above threshold)": "median",
        "Fraction above threshold": "fraction",
    }

    def _stats_active_channel_entry(self) -> str: return stats_active_channel_entry(self)

    def _stats_active_val_col(self) -> str: return stats_active_val_col(self)

    def _stats_active_threshold(self) -> float: return stats_active_threshold(self)

    def _stats_active_statistic(self) -> str: return stats_active_statistic(self)

    def _stats_run(self) -> None: return stats_run_shim(self)

    def _stats_draw_ks_cdf(self, group_vals: List[Tuple[str, List[float]]], tp_str: str) -> None:
        return stats_draw_ks_cdf_shim(self, group_vals, tp_str)

    # ── Preview sidebar picker ────────────────────────────────────────────────

    def _refresh_preview_picker(self) -> None: return refresh_preview_picker_shim(self)

    # ── Image Table tab ───────────────────────────────────────────────────────






















    # ─────────────────────────────────────────────────────────────────────────
    # Replicate panel
    # ─────────────────────────────────────────────────────────────────────────


    # ── Replicate-panel plate map ─────────────────────────────────────────────

    def _rep_refresh_map(self) -> None: return rep_refresh_map(self)


    def _rep_select(self, idx: int) -> None: return rep_select(self, idx)

    def _rep_add(self) -> None: return rep_add(self)

    def _rep_clear_all(self) -> None: return rep_clear_all(self)

    # ─────────────────────────────────────────────────────────────────────────
    # ─────────────────────────────────────────────────────────────────────────

    def _ask_name_dialog(self, default: str) -> Optional[str]:
        return _ui_ask_name_dialog(self, title="Name", prompt="Name:",
                                   default=default)

    # ─────────────────────────────────────────────────────────────────────────
    # Well-label editor
    # ─────────────────────────────────────────────────────────────────────────



    def _labels_clear_all(self) -> None: return labels_clear_all(self)


    def _labels_add_prefix(self) -> None: labels_apply_affix(self, where="prefix")

    def _labels_add_suffix(self) -> None: labels_apply_affix(self, where="suffix")

    # ── unified-model (app._app_state.selection.selections) — the in-memory source of truth ──────

    def _sel_by_id(self, sid): return sel_by_id(self, sid)

    def _sel_index_of(self, sid) -> int:
        for i, s in enumerate(self._app_state.selection.selections):
            if s.get("id") == sid:
                return i
        return -1

    def _sel_id_for_well(self, tok):
        for s in self._app_state.selection.selections:
            if tok in (s.get("wells") or []):
                return s.get("id")
        return None

    def _sel_strip_wells(self, wells, *, keep_id = None) -> None: return sel_strip_wells(self, wells, keep_id=keep_id)

    def _sel_add(self, name = None, wells = None, replicates = None, source = 'user', *, make_current = True):
        return sel_add(self, name, wells, replicates, source, make_current=make_current)

    def _sel_rename(self, sid, name) -> None: return sel_rename(self, sid, name)

    def _sel_delete(self, sid) -> None: return sel_delete(self, sid)

    def _sel_set_hidden(self, sid, hidden, *, light = False) -> None:
        return sel_set_hidden(self, sid, hidden, light=light)

    def _sel_set_color(self, sid, color) -> None: return sel_set_color(self, sid, color)

    def _sel_export_one(self, sid) -> None: return sel_export_one(self, sid)

    def _sel_toggle_hidden(self, sid) -> None:
        s = self._sel_by_id(sid)
        if s is not None:
            self._sel_set_hidden(sid, not bool(s.get("hidden")))

    def _sel_set_composition(self, sid, wells = None, replicates = None) -> None:
        return sel_set_composition(self, sid, wells, replicates)

    def _sel_select(self, sid) -> None: return sel_select(self, sid)

    def _sel_reorder(self, ids) -> None: return sel_reorder(self, ids)

    def _sel_clear_all(self) -> None:
        self._app_state.selection.selections = []
        self._app_state.selection.current_selection_id = None
        self._rebuild_all()

    def _sel_duplicate(self, sid): return sel_duplicate(self, sid)

    def _enforce_well_exclusivity(self) -> None: return enforce_well_exclusivity(self)

    def _groups_centre_refresh(self) -> None: return groups_centre_refresh(self)


    def _rebuild_all(self) -> None: return rebuild_all(self)

    # ── Quick-group helpers (delegates to grouping_controller) ────────────────

    def _rep_quick_row_pairs(self) -> None:
        self._rep_quick_pair_dir = "row"
        self._rep_quick_iter_order = "row"
        rep_quick_pairs(self)

    def _rep_quick_col_pairs(self) -> None:
        self._rep_quick_pair_dir = "col"
        self._rep_quick_iter_order = "col"
        rep_quick_pairs(self)


    def _rep_quick_pairs_from_dropdowns(self) -> None: return rep_quick_pairs_from_dropdowns(self)

    # ── Bar group persistence (delegates to well_viewer.persistence.bar_groups)

    def _bar_groups_from_dict(self, data) -> None:
        from well_viewer.persistence import bar_groups as _bg_persist
        _bg_persist.from_dict(self, data)

    def _bar_save_groups(self) -> None:
        from well_viewer.persistence import bar_groups as _bg_persist
        _bg_persist.save_via_dialog(self)

    def _bar_load_groups(self) -> None:
        from well_viewer.persistence import bar_groups as _bg_persist
        _bg_persist.load_via_dialog(self)

    def _open_ratio_panel(self) -> None: return open_ratio_panel(self)

    # ── Persistence ──────────────────────────────────────────────────────────
    # The data-dir JSON domains (ratios / heatmap_layouts / cell_overrides /
    # line_order / view_state / notes) are saved and loaded through
    # ``self._persistence`` — see well_viewer/persistence/registry.py.

    def _view_state_reset(self) -> None:
        from well_viewer.persistence import view_state as _vs
        _vs.reset(self)

    def _notes_schedule_save(self) -> None: return notes_schedule_save(self)

    # ── Sample Definitions + Cell Gating persistence ─────────────────────────
    # Implementations live in ``well_viewer.persistence.{sample_definitions,cell_gating}``.

    def _save_sample_definitions_to_pipeline_info(self) -> None:
        from well_viewer.persistence import sample_definitions as _sd
        _sd.save_to_pipeline_info(self)

    def _load_sample_definitions_from_pipeline_info(self) -> bool:
        from well_viewer.persistence import sample_definitions as _sd
        return _sd.load_from_pipeline_info(self)

    def _save_sample_definitions_all(self) -> None:
        from well_viewer.persistence import sample_definitions as _sd
        _sd.save_all(self)

    def _load_sample_definitions_all(self) -> None:
        from well_viewer.persistence import sample_definitions as _sd
        _sd.load_all(self)

    def _clear_sample_definitions_all(self) -> None:
        from well_viewer.persistence import sample_definitions as _sd
        _sd.clear_all(self)


    def _save_gating_to_pipeline_info(self) -> None:
        from well_viewer.persistence import cell_gating as _cg
        _cg.save_to_pipeline_info(self)

    def _load_gating_from_pipeline_info(self) -> bool:
        from well_viewer.persistence import cell_gating as _cg
        return _cg.load_from_pipeline_info(self)

    # ── decision-#1 colour: "the plate is the legend" — every well / rep-set /
    # group is coloured by *well-position rank*, so the same well always gets the
    # same colour everywhere (plate maps, line/bar/stats plots).
    def _rank_color_well(self, tok) -> str:
        return WELL_COLORS[_sel_well_rank(tok) % len(WELL_COLORS)]

    def _rank_color_wells(self, wells) -> str: return rank_color_wells(self, wells)

    def _sel_color(self, s) -> str: return sel_color(self, s)

    def _rank_color_rset(self, rset) -> str: return rank_color_rset(self, rset)

    def _rep_color_for(self, lbl: str) -> Optional[str]: return rep_color_for(self, lbl)






    # ── Montage zoom helpers ──────────────────────────────────────────────────







    def _apply_theme(self) -> None: return apply_theme(self)

    def _on_theme_change(self, theme_name: str = None) -> None: return on_theme_change(self, theme_name)

    # ── Loading ───────────────────────────────────────────────────────────────

    def _browse(self) -> None:
        """Open a directory picker. Expects in/ + out/ subdirs or a flat CSV dir."""
        d = QFileDialog.getExistingDirectory(self, "Open results directory")
        if d:
            # Defer so the dialog closes and the window repaints before load
            QTimer.singleShot(50, lambda: self._load_path(Path(d)))

    def _load_path(self, path): return load_path_shim(self, path)

    def _load_directory(self, d: Path, label: Optional[str] = None) -> None:
        self._drain_pending_centre_builders()
        _load_directory_controller(self, d, label=label)
        refresh_plot_empty_states(self)


    def _drain_pending_centre_builders(self) -> None: return drain_pending_centre_builders(self)

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

    def _on_close(self) -> None:
        self._cleanup_tmp()
        self.close()

    # ── Plate-map sidebar helpers ─────────────────────────────────────────────

    def _parse_rc(self, label: str) -> Tuple[Optional[str], Optional[str]]:
        m = re.search(r"([A-Ha-h])(\d{1,2})$", label)
        if not m:
            return None, None
        return m.group(1).upper(), f"{int(m.group(2)):02d}"


    def _rebuild_all_timepoints_cache(self) -> None:
        """Recompute the canonical timepoint list (delegates to DatasetState)."""
        self._app_state.dataset.rebuild_timepoints()

    def _rebuild_all_fovs_cache(self) -> None:
        """Recompute the canonical FOV list (delegates to DatasetState)."""
        self._app_state.dataset.rebuild_fovs()

    @staticmethod
    def _mute_color(self, factor: float = 0.5) -> str: return mute_color(self, factor)

    def _rep_visible_sets(self) -> "List[ReplicateSet]":
        """Alias for _rep_sets_active — visible (non-hidden) loaded rep-sets."""
        return self._rep_sets_active()

    def _refresh_sidebar_map(self) -> None:
        """Debounce sidebar recoloring work to one pass per event-loop tick."""
        if self._sidebar_map_refresh_pending:
            return
        self._sidebar_map_refresh_pending = True
        QTimer.singleShot(0, self._refresh_sidebar_map_now)

    def _refresh_sidebar_map_now(self) -> None: refresh_sidebar_map_now(self)

    # ── v2 plate-widget bridge (WellPlateSelector ↔ WellViewerApp state) ──────
    # The left-rail plate is now a single ``widgets.WellPlateSelector`` instead
    # of an ``app._sidebar_btns`` grid of ``WellButton``s. ``app._app_state.selection.selected_wells``
    # stays the source of truth; ``_refresh_sidebar_map_now`` pushes appearance /
    # selection onto the widget, and the handlers below take user actions back.


    def _plate_commit_deferred(self) -> None: return plate_commit_deferred(self)

    def _set_selected_wells(self, new_set, *, commit = True) -> None:
        return set_selected_wells(self, new_set, commit=commit)


    _PLOTTING_TABS = frozenset({
        "Line Graphs", "Bar Plots", "Scatter Plot", "Distribution", "Heat Map",
    })








    # ── Threshold ─────────────────────────────────────────────────────────────


    # ── Ratio metric helpers ─────────────────────────────────────────────────

    def _ratio_label_for(self, ratio: RatioMetric) -> str: return ratio_label_for(self, ratio)

    def _ratio_dropdown_labels(self) -> List[str]:
        return [self._ratio_label_for(r) for r in self._app_state.channel.ratio_metrics]

    def _channel_key_for_label(self, label: str) -> str: return channel_key_for_label(self, label)

    def _active_channel_label(self) -> str: return active_channel_label(self)

    def _rebuild_ratio_index(self) -> None: return rebuild_ratio_index(self)

    def _set_ratio_metrics(self, ratios: Iterable[RatioMetric]) -> None: return set_ratio_metrics(self, ratios)

    # ── Active channel ───────────────────────────────────────────────────────




    def _on_plot_channel_selected(self, source = None) -> None: return on_plot_channel_selected(self, source)

    def _on_preview_channel_selected(self, _e = None) -> None: return on_preview_channel_selected(self, _e)

    def _on_metric_selected(self) -> None: return on_metric_selected(self)

    def _on_metric_selected_global(self) -> None: return on_metric_selected_global(self)

    def _on_stats_property_change(self) -> None: return on_stats_property_change(self)
        # Stats display refreshes on its own when run; nothing to redraw here.







    def _toggle_sem(self) -> None: return toggle_sem(self)
        # _toggle_sem stays off the bus until Phase 9 relocates it (see teardown doc).

    # ── Per-FOV-replicates toggle ────────────────────────────────────────────

    def _fov_replicates_available(self) -> bool:
        """Per-FOV spread is always available; with replicate sets active it
        pools FOVs across all wells in each set instead of computing within a
        single well."""
        return True

    def _use_fov_spread_active(self) -> bool:
        """Effective state of the per-FOV-replicates toggle for plotting."""
        return bool(self._app_state.plot.use_fov_replicates)

    def _toggle_fov_replicates(self) -> None: return toggle_fov_replicates(self)

    def _refresh_fov_btn_state(self) -> None: return refresh_fov_btn_state(self)
    def _refresh_bar_layout_btn_state(self) -> None: return refresh_bar_layout_btn_state(self)
    def _update_bar_tp_selection_display(self) -> None: return update_bar_tp_selection_display(self)

    # ── Well selection (plate-map backed) ─────────────────────────────────────



    def _select_invert(self) -> None: return select_invert(self)

    def _selected_labels(self) -> List[str]:
        """Return labels in a stable order (sorted) for consistent plot colours."""
        return sorted(self._app_state.selection.selected_wells, key=lambda lbl: self._parse_rc(lbl))

    def _get_rows(self, label: str) -> pd.DataFrame:
        if label not in self._app_state.dataset.cache:
            self._app_state.dataset.cache[label] = load_well_csv(self._app_state.dataset.well_paths[label])
            # Project QC_Pass / Crop_Pass onto freshly-built frames (lazy wells).
            from well_viewer.features.qc.controller import apply_qc_to_cache
            from well_viewer.features.crop.controller import apply_crop_to_cache
            apply_qc_to_cache(self, label)
            apply_crop_to_cache(self, label)
        return self._app_state.dataset.cache[label]

    def _invalidate_dataset_cache(self) -> None: self._app_state.dataset.cache.clear()



    # ── Preview panel ─────────────────────────────────────────────────────────



    def _norm_timepoint(self, value: object) -> str: return norm_timepoint(self, value)

    def _review_row_keys(self, row: dict) -> Tuple[str, str, str]: return review_row_keys(self, row)


    @staticmethod
    def _review_norm_fov(self) -> str: return review_norm_fov(self)

    @staticmethod
    def _review_tp_sort_key(tp: str) -> Tuple[int, float, str]:
        parsed = parse_timepoint_hours(str(tp))
        if parsed is not None:
            return (0, parsed, str(tp))
        return (1, 0.0, str(tp))

    def _review_collect_timepoints(self, fov: str) -> list: return review_collect_timepoints(self, fov)


    def _toggle_review_image_source(self) -> None: return toggle_review_image_source(self)

    def _refresh_review_image_source_btn(self) -> None: return refresh_review_image_source_btn(self)

    # ── Review Image color customization ──────────────────────────────────

    _REVIEW_IMAGE_COLOR_DEFAULTS: Dict[str, Tuple[int, int, int]] = {
        "boundary": (255, 64, 64),
        "selected": (255, 230, 64),
        "tint": (255, 255, 255),
    }

    def _review_image_get_color(self, which: str) -> Tuple[int, int, int]:
        attr = f"_review_image_{which}_color"
        return tuple(getattr(self, attr, self._REVIEW_IMAGE_COLOR_DEFAULTS[which]))  # type: ignore[return-value]

    def _review_image_set_color(self, which: str, rgb: Tuple[int, int, int]) -> None:
        return review_image_set_color(self, which, rgb)

    def _review_image_refresh_color_swatch(self, which: str) -> None:
        return review_image_refresh_color_swatch(self, which)


    def _reset_review_image_colors(self) -> None: return reset_review_image_colors(self)

    # ── Binary mask toggle ────────────────────────────────────────────────

    def _toggle_review_image_binary_mask(self) -> None: return toggle_review_image_binary_mask(self)

    def _refresh_review_image_binary_btn(self) -> None: return refresh_review_image_binary_btn(self)

    def _toggle_review_image_outline(self) -> None: return toggle_review_image_outline(self)

    def _refresh_review_image_outline_btn(self) -> None: return refresh_review_image_outline_btn(self)




    def _draw_review_image(self, fluor_arr, mask_arr, include_by_nid: Dict[int, bool], *, fit_lut = False, preserve_view = False, boundary = None, above_by_nid = None) -> None:
        return draw_review_image(self, fluor_arr, mask_arr, include_by_nid, fit_lut=fit_lut, preserve_view=preserve_view, boundary=boundary, above_by_nid=above_by_nid)

    def _render_review_image_display(self, *, pan_only: bool = False) -> None:
        render_review_image_display(self, pan_only=pan_only)

    def _review_image_resolve_lut(self, arr) -> Tuple[float, float]: return review_image_resolve_lut(self, arr)

    def _review_image_auto_lut(self) -> None: return review_image_auto_lut(self)

    def _review_image_commit_lut(self) -> None: return review_image_commit_lut(self)

    def _review_image_zoom_step(self, direction: int) -> None: return review_image_zoom_step(self, direction)

    def _review_image_zoom_fit(self) -> None:
        self._app_state.review_image.zoom = 1.0
        self._app_state.review_image.pan_x = 0.0
        self._app_state.review_image.pan_y = 0.0
        self._render_review_image_display()







    def _on_review_image_click(self, event: Any) -> None: _on_review_image_click_controller(self, event, _logger)

    def _apply_review_image_cursor(self) -> None: return apply_review_image_cursor(self)

    def _select_review_csv_row_for_cell(self, fov: str, tp: str, nucleus_id: str) -> None:
        _select_review_csv_row_for_cell_controller(self, fov, tp, nucleus_id, _logger)

    def _set_review_image_include_mode(self, enabled: bool) -> None: return set_review_image_include_mode(self, enabled)

    def _toggle_selected_review_cell(self) -> None:
        self._set_review_image_include_mode(not self._app_state.review_image.include_edit_mode)






    # ── Export ────────────────────────────────────────────────────────────────



    # ── Batch export ──────────────────────────────────────────────────────────

    def _open_batch_export(self) -> None: return open_batch_export_shim(self)

    # ── Montage ───────────────────────────────────────────────────────────────

    # ── Plotting ──────────────────────────────────────────────────────────────

    def _redraw(self) -> None: return redraw_app(self)

    # ── Bar plot tab ──────────────────────────────────────────────────────────

    def _current_centre_tab(self) -> str: return current_centre_tab(self)


    def _sync_heatmap_well_drag_mode(self, enable: bool) -> None: return sync_heatmap_well_drag_mode(self, enable)
            # Don't force-disable wells here — the per-tab branch in
            # ``_on_tab_change`` either hides the sidebar (preview / image
            # table tabs) or calls ``_refresh_sidebar_map`` which restores the
            # correct enabled state per well.

    def _sync_preview_well_for_image_tabs(self) -> None: return sync_preview_well_for_image_tabs(self)

    def _run_tab_switch_smoke_checks(self, prev_tab: Optional[str], tab: str, prev_selected: set) -> None:
        return run_tab_switch_smoke_checks(self, prev_tab, tab, prev_selected)

    def _show_line_sidebar(self) -> None:
        """No-op: the unified well picker is always visible for plot tabs."""
        pass

    def _build_review_csv_tab(self, parent) -> None:
        from well_viewer.features.review_image.csv_tab_view import build_review_csv_tab as _v
        _v(self._ctx, parent)

    def _refresh_review_csv(self) -> None: refresh_review_csv(self)

    def _refresh_review_csv_rows(self, rows=None) -> None: refresh_review_csv_rows(self, rows)

    def _review_effective_included(self, label: str, row: dict) -> str:
        return review_effective_included(self, label, row)


    def _review_load_rows(self, label: str) -> List[dict]: return review_load_rows(self, label)

    def _update_bar_tp_menu(self) -> None: return update_bar_tp_menu(self)

    # ── Bar drag-and-drop reordering ─────────────────────────────────────────
    # Implementations live in ``well_viewer.barplot_controller``.

    def _bar_event_xdata(self, event) -> "Optional[float]": return bar_event_xdata(self, event)

    def _bar_current_keys(self) -> List: return _bar_ordered_keys(self)

    def _bar_idx_at_x(self, xdata: float, n: int) -> int:
        from well_viewer.features.barplot.controller import bar_idx_at_x
        return bar_idx_at_x(xdata, n)



    def _on_bar_drag_motion(self, event) -> None: on_bar_drag_motion(self, event, accent_color=ACCENT)


    def _apply_bar_ylims(
        self,
        ax_mean: "Axes",
        ax_frac: "Axes",
        ax_n=None,
    ) -> None:
        """Apply user-specified y-axis limits to the bar panels."""
        _bar_apply_ylims(self, ax_mean, ax_frac, ax_n=ax_n)

    def _toggle_swarm(self) -> None: return toggle_swarm(self)

    def _toggle_violin(self) -> None: return toggle_violin(self)



    # ── Bar plot export ───────────────────────────────────────────────────────

    def _well_display_label(self, lbl: str) -> str:
        """Return the custom display label for *lbl* if defined, else its token."""
        tok = _extract_well_token(lbl) or lbl
        return self._app_state.dataset.well_labels.get(tok, tok)

    def _bar_well_display_label(self, lbl: str) -> str: return bar_well_display_label(self, lbl)

    def _replicate_display_label(self, rset: 'ReplicateSet') -> str: return replicate_display_label(self, rset)

    def _rep_sets_loaded(self) -> 'List[ReplicateSet]': return rep_sets_loaded(self)

    def _groups_from_rep_sets(self) -> "List[BarGroup]":
        """One BarGroup per loaded selection (each wrapping its ReplicateSet)."""
        return [BarGroup(r.name, members=[r]) for r in self._rep_sets_loaded()]

    def _rep_sets_active(self) -> 'List[ReplicateSet]': return rep_sets_active(self)





    def _invalidate_stats_cache(self) -> None: return invalidate_stats_cache(self)

    def _invalidate_review_image_frame_cache(self) -> None: return invalidate_review_image_frame_cache(self)


    def _collect_bar_items(self, target_t: float, *, fc_state = None, miss_sink = None) -> tuple:
        return collect_bar_items_for_app(self, target_t, fc_state=fc_state, miss_sink=miss_sink)





    def _open_bar_batch_export(self) -> None: return open_bar_batch_export_shim(self)

    def _open_scatter_cells_batch_export(self) -> None: return open_scatter_cells_batch_export_shim(self)

    def _open_scatter_agg_batch_export(self) -> None: return open_scatter_agg_batch_export_shim(self)

    # ── Save current figure ───────────────────────────────────────────────────

    _FIG_FILETYPES = [
        ("PNG image",        "*.png"),
        ("SVG vector",       "*.svg"),
        ("PDF document",     "*.pdf"),
        ("EPS vector",       "*.eps"),
        ("All files",        "*.*"),
    ]

    def _save_matplotlib_fig(self, fig: "Figure", default_name: str) -> None:
        """Show a save-file dialog and write *fig* to disk."""
        _plot_save_matplotlib_fig_orchestrator(
            self,
            fig,
            default_name,
            plot_bg=PLOT_BG,
        )

    def _save_line_figure(self) -> None:
        """Save the current Line Graphs figure at high resolution."""
        _plot_save_line_figure_orchestrator(self, plot_bg=PLOT_BG)

    def _save_bar_figure(self) -> None:
        """Save the current Bar Plots figure at high resolution."""
        _plot_save_bar_figure_orchestrator(self, plot_bg=PLOT_BG)

    def _open_export_style_panel(self, plot_key: str) -> None: return open_export_style_panel(self, plot_key)

    # ── Scatter Plot tab ───────────────────────────────────────────────────────

    def _col_for_scatter_axis(self, channel_entry: str, metric_key: str) -> str:
        return col_for_scatter_axis(self, channel_entry, metric_key)

    def _col_for_scatter_entry(self, entry: str) -> str: return col_for_scatter_entry(self, entry)

    # ── Per-axis scatter metric helpers ────────────────────────────────────

    _SCATTER_AGG_METRIC_EXTRA = {"Fraction above threshold": "fraction_above_threshold"}

    def _scatter_axis_metric_key(self, axis: str, *, agg = False) -> str:
        return scatter_axis_metric_key(self, axis, agg=agg)

    def _refresh_scatter_metric_for_axis(self, axis: str) -> None: return refresh_scatter_metric_for_axis(self, axis)

    def _refresh_scatter_agg_metric_for_axis(self, axis: str) -> None:
        return refresh_scatter_agg_metric_for_axis(self, axis)

    def _on_scatter_axis_change(self, axis: str) -> None:
        self._refresh_scatter_metric_for_axis(axis)
        redraw_scatter_orchestrate(self)

    def _on_scatter_agg_axis_change(self, axis: str) -> None:
        self._refresh_scatter_agg_metric_for_axis(axis)
        redraw_scatter_agg_orchestrate(self)

    def _sync_scatter_agg_stat_combos(self) -> None: return sync_scatter_agg_stat_combos(self)

    def _compose_scatter_agg_stat(self, channel_entry: str, metric_label: str) -> str:
        return compose_scatter_agg_stat(self, channel_entry, metric_label)


    def _update_tp_selection_display(self) -> None: return update_tp_selection_display(self)

    def _update_scatter_tp_selection_display(self) -> None: return update_scatter_tp_selection_display(self)




    def _open_scatter_cell_viewer(self, well_label: str, filename: str, nuclear_id: str, row_idx: int) -> None:
        return open_scatter_cell_viewer(self, well_label, filename, nuclear_id, row_idx)


    def _save_scatter_figure(self) -> None:
        """Save the current Scatter plot figure at high resolution."""

        _plot_save_scatter_figure_orchestrator(self, plot_bg=PLOT_BG)


    def _export_scatter_agg_data(self) -> None: return export_scatter_agg_data(self)

    def _save_scatter_agg_figure(self) -> None:
        """Save the current aggregate scatter plot figure at high resolution."""

        _plot_save_scatter_agg_figure_orchestrator(self, plot_bg=PLOT_BG)


    # ── Legend interaction ────────────────────────────────────────────────────


    # ── Log / status helpers ──────────────────────────────────────────────────

    def _set_status(self, msg: str) -> None:
        self._status_lbl.setText(msg)

    def _toast(self, msg: str, *, kind = 'info', msec = 3500) -> None: return toast(self, msg, kind=kind, msec=msec)

    def _show_progress(self, maximum: int, msg: str = '') -> None: return show_progress(self, maximum, msg)

    def _step_progress(self, value: int, msg: str = '') -> None: return step_progress(self, value, msg)

    def _hide_progress(self) -> None:
        """Remove the progress bar."""
        self._progress_bar.setVisible(False)
        self._progress_bar.setValue(0)

    def _toggle_log(self) -> None: return toggle_log(self)

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
