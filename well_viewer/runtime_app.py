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
import copy
import logging
import json
import math
import re
import shutil
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRubberBand,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

import matplotlib
import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from well_viewer.batch_models import BarGroup, ReplicateSet
from well_viewer import debug_flags as _debug_flags
from well_viewer.state import AppState, EventBus
from well_viewer.bus_wiring import wire_event_bus
from well_viewer.persistence.registry import build_persistence_registry
from well_viewer.viewer_context import ViewerContext
from well_viewer.views.widget_registry import WidgetRegistry


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


_bar_apply_ylims = _lazy("well_viewer.barplot_controller", "apply_bar_ylims")
_bar_collect_items = _lazy("well_viewer.barplot_controller", "collect_bar_items")
_bar_ordered_keys = _lazy("well_viewer.barplot_controller", "ordered_bar_keys")
_bar_render_items = _lazy("well_viewer.barplot_controller", "render_bar_items")


_resolve_ref_by_fov_tp = _lazy("well_viewer.image_resolver", "resolve_ref_by_fov_tp")

_refresh_preview_picker_view = _lazy("well_viewer.views.preview_view", "refresh_preview_picker")


_lineplot_redraw = _lazy("well_viewer.lineplot_controller", "redraw_line_plots")
_scatter_get_timepoints = _lazy("well_viewer.scatter_controller", "get_all_timepoints")

_load_directory_controller = _lazy("well_viewer.load_controller", "load_directory")
_load_path_controller = _lazy("well_viewer.load_controller", "load_path")

_plot_redraw_orchestrator = _lazy("well_viewer.plot_orchestrator", "redraw")
_plot_save_bar_figure_orchestrator = _lazy("well_viewer.plot_orchestrator", "save_bar_figure")
_plot_save_line_figure_orchestrator = _lazy("well_viewer.plot_orchestrator", "save_line_figure")
_plot_save_matplotlib_fig_orchestrator = _lazy("well_viewer.plot_orchestrator", "save_matplotlib_fig")

_montage_auto_lut_controller = _lazy("well_viewer.montage_controller", "montage_auto_lut")
_montage_tophat_done_controller = _lazy("well_viewer.montage_controller", "montage_tophat_done")

_on_review_image_click_controller = _lazy("well_viewer.review_image_controller", "on_review_image_click")
_select_review_csv_row_for_cell_controller = _lazy("well_viewer.review_image_controller", "select_review_csv_row_for_cell")

_stats_collect_group_values = _lazy("well_viewer.stats_controller", "collect_group_values")
_stats_draw_ks_cdf = _lazy("well_viewer.stats_controller", "draw_ks_cdf")
_stats_run_controller = _lazy("well_viewer.stats_controller", "run_stats")

from well_viewer.viewer_state import read_pipeline_info as _read_pipeline_info_shared
from well_viewer.ui_helpers import (
    ask_name_dialog as _ui_ask_name_dialog,
    bind_drag as _bind_drag,
    btn_card as _btn_card,
    btn_danger as _btn_danger,
    btn_primary as _btn_primary,
    btn_secondary as _btn_secondary,
    clear_layout as _clear_layout,
    make_fluor_thumb,
    make_overlay_thumb,
    make_scrollable_canvas as _ui_make_scrollable_canvas,
    safe_int_or_none as _safe_int_or_none,
    selected_list_values as _selected_list_values,
    wells_multiselect_listbox as _wells_multiselect_listbox,
)
from ui.theme import (
    ACCENT,
    ACCENT_DARK,
    BG_APP,
    BG_CELL,
    BG_HOVER,
    BG_PANEL,
    BG_SIDE,
    BORDER,
    button_bg,
    button_text,
    button_text_disabled,
    CLR_AVAIL_HOVER,
    CLR_AVAIL_WELL,
    CLR_DANGER,
    CLR_DANGER_DARK,
    CLR_DANGER_BG,
    CLR_DANGER_HOVER,
    CLR_ERR_BAR,
    CLR_ERROR_BG_DARK,
    CLR_ERROR_TEXT_SOFT,
    CLR_MUTED_DISABLED,
    CLR_MUTED_TEXT_SOFT,
    CLR_OFF_WHITE,
    CLR_PLACEHOLDER,
    CLR_SLATE_BG,
    CLR_SLATE_TEXT,
    CLR_SUCCESS,
    CLR_SUCCESS_BG_DARK,
    CLR_SUCCESS_DARK,
    CLR_SUCCESS_TEXT_SOFT,
    CLR_WARN_BG,
    CLR_WARN_DARK,
    CLR_WARN_TEXT,
    CLR_WHITE,
    FM_BOLD,
    FM_MONO,
    FM_TINY,
    FM_TITLE,
    FM_UI,
    PLOT_BG,
    PLOT_GRD,
    PLOT_SPN,
    PLOT_TXT,
    TAB_BG,
    TAB_BG_ACTIVE,
    TAB_BORDER,
    TAB_FG,
    TAB_FG_ACTIVE,
    TOOLTIP_BG,
    TOOLTIP_FG,
    TXT_MUT,
    TXT_PRI,
    TXT_SEC,
    WARN,
    WELL_COLOR_1,
    WELL_COLOR_2,
    WELL_COLOR_3,
    WELL_COLOR_4,
    WELL_COLOR_5,
    WELL_COLOR_6,
    WELL_COLOR_7,
    WELL_COLOR_8,
    WELL_COLOR_9,
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

from well_viewer.plate_layout import WELL_COLORS
from well_viewer.selections_model import well_rank as _sel_well_rank

NO_SELECTION_MSG = "No wells or well groups selected.\nSelect wells on the left panel or define groups to plot."


# `_set_combo_values` was hoisted into well_viewer.ui_helpers so the new
# `channel_state_controller` can use it without a circular import back
# into runtime_app. Re-export the old private name for compatibility with
# call sites that still reference it via the runtime_app module.
from well_viewer.ui_helpers import set_combo_values as _set_combo_values  # noqa: E402, F401



from well_viewer.plot_style import apply_ax_style


from well_viewer.data_loading import (
    AggPoint,
    _all_fluor_values,
    _all_fluor_values_filtered,
    _beeswarm_jitter,
    aggregate_with_threshold_df,
    detect_fluor_channels,
    detect_nuclear_channel_token,
    detect_review_image_channels,
    detect_smfish_channels,
    df_included_mask,
    load_well_csv,
    merge_fluor_channels,
    normalize_channel_tokens,
    parse_timepoint_hours,
    parse_well_token,
    resolve_value_series,
)
from well_viewer.ratio_models import (
    RatioMetric,
    build_ratio_index,
    is_ratio_key,
    ratio_name_from_key,
    ratios_from_dict,
    ratios_to_dict,
)


from well_viewer.image_discovery import (
    ImgRef,
    find_well_images_and_masks,
    open_imgref_as_array,
)

# Aliased to avoid collision with the WellViewerApp._extract_well_token method below.
from well_viewer.data_loading import extract_well_token as _extract_well_token  # noqa: F401


# =============================================================================
# Categorical label colourmap  (canonical: well_viewer/views/image_panel_view.py)
# =============================================================================


# =============================================================================
# Reusable image panel  (canonical: well_viewer/views/image_panel_view.py)
# =============================================================================


# =============================================================================
# GUI log handler  (canonical: well_viewer/views/status_view.py)
# =============================================================================

from well_viewer.barplot_renderer import redraw_bars
from well_viewer.grouping_controller import rep_quick_pairs
from well_viewer.montage_controller import on_montage_fluor_motion
from well_viewer.montage_controller import on_montage_shift_wheel
from well_viewer.montage_controller import on_montage_wheel
from well_viewer.review_image_renderer import refresh_review_image
from well_viewer.review_image_renderer import update_preview
from well_viewer.scatter_controller import redraw_scatter_agg_orchestrate
from well_viewer.scatter_controller import redraw_scatter_orchestrate
from well_viewer.selection_controller import on_plate_sel_change
from well_viewer.stats_controller import stats_refresh_colors
from well_viewer.stats_controller import stats_refresh_map
from well_viewer.ui_helpers import refresh_plot_empty_states
from well_viewer.views.grouping_view import rep_panel_refresh
from well_viewer.views.label_grid_view import label_grid_refresh
from functools import partial
from well_viewer.channel_state_controller import recalculate_threshold
from well_viewer.channel_state_controller import refresh_metric_combo_for_channel
from well_viewer.channel_state_controller import set_active_channel
from well_viewer.channel_state_controller import update_channel_selector
from well_viewer.montage_controller import montage_redraw_at_zoom
from well_viewer.tab_controller import on_notebook_current_changed
from well_viewer.montage_controller import refresh_preview_montage
from well_viewer.channel_state_controller import set_active_image_channel
from well_viewer.channel_state_controller import set_active_metric
from well_viewer.channel_state_controller import populate_metric_combo
from well_viewer.selection_controller import labels_apply_affix
from well_viewer.selection_controller import update_active_group_label

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
        # Movie Montage square-region crop. State lives in a CropTool
        # instance; legacy attribute names are preserved as read-only
        # delegates further down so existing readers (export_service,
        # the montage redraw, etc.) keep working.
        from well_viewer.crop_tool import CropTool as _CropTool
        self._montage_crop_tool = _CropTool(
            on_change=lambda: montage_redraw_at_zoom(self),
        )
        self._montage_crop_btn = None
        self._montage_crop_status_lbl = None

        from well_viewer.views.app_shell_view import build_ui
        build_ui(self)
        self._apply_theme()

        if data_path is not None:
            QTimer.singleShot(100, lambda: self._load_path(data_path))

    @staticmethod
    def _position_root_on_screen(root, *, preferred_w: int, preferred_h: int) -> None:
        """Qt: no-op; window placement handled by QMainWindow / QWidget defaults."""
        return
        w = min(preferred_w, max(1000, sw - margin))
        h = min(preferred_h, max(800, sh - margin))
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        root.geometry(f"{w}x{h}+{x}+{y}")

    # ── Threshold helpers ─────────────────────────────────────────────────────

    def _get_cell_area_threshold(self) -> float:
        """Get cell area threshold from the Cell Gating tab."""
        edit = getattr(self, "_cell_gating_area_edit", None)
        if edit is not None:
            try:
                return float(edit.text())
            except ValueError:
                return 0.0
        return 0.0

    def _get_fluor_gate(self, channel: str) -> float:
        """Get FluorGating threshold for a channel."""
        if hasattr(self, "_cell_gating_area_edit"):
            from well_viewer.tabs.cell_gating_tab_view import cell_gating_get_fluor_gate
            return cell_gating_get_fluor_gate(self, channel)
        return 0.0

    def _get_all_fluor_gates(self) -> Dict[str, float]:
        """Get FluorGating thresholds for all loaded channels.

        Returns a dict mapping channel name -> gate threshold, e.g., {"gfp": 10.0, "mcherry": 20.0}.
        Used by aggregate_with_threshold_df to apply consistent gating across all channels.
        """
        gates = {}
        for channel in self._app_state.channel.fluor_channels:
            gates[channel] = self._get_fluor_gate(channel)
        return gates

    def _get_thresh_frac_on(self, channel: Optional[str] = None) -> float:
        """Get ThreshFracOn threshold. Uses active channel if not specified."""
        if channel is None:
            channel = self._app_state.channel.active_channel
        # _redraw can fire before the cell-gating tab is built (e.g. from
        # sidebar releases during early load); fall back to the default.
        if not hasattr(self, "_cell_gating_thresh_frac_edits"):
            return self._app_state.channel.threshold
        from well_viewer.tabs.cell_gating_tab_view import cell_gating_get_thresh_frac_on
        return cell_gating_get_thresh_frac_on(self, channel)

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

    def _on_central_pane_changed(self, idx: int) -> None:
        # Mode-seg lives in all_well's titlebar; emit modeChanged so the
        # shell can re-sync its own seg without poking widgets here.
        mode = "analyze" if idx == 1 else "review"
        # Hide the section / well-picker sidebar when Analyze is active —
        # it carries Review-only affordances. Show it again on Review.
        sidebar = getattr(self, "_sidebar_root", None)
        if sidebar is not None:
            sidebar.setVisible(mode == "review")
        self.modeChanged.emit(mode)

    def _build_centre(self, parent) -> None:
        from well_viewer.views.centre_view import build_centre as _build_centre_view
        _build_centre_view(self, parent)
        # Phase 10 (A1): mirror the notebook's tab list into the rail nav.
        # The nav drives the (hidden-tab-bar) QTabWidget; the QTabWidget's
        # currentChanged signal keeps the nav in sync if anything else
        # changes the index (deferred builders, programmatic select_by_text).
        self._sync_section_nav_from_notebook()
        self._section_nav.currentChanged.connect(self._on_section_nav_changed)
        self._notebook.currentChanged.connect(partial(on_notebook_current_changed, self))

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

    def _sync_section_nav_from_notebook(self) -> None:
        nav = getattr(self, "_section_nav", None)
        nb = getattr(self, "_notebook", None)
        if nav is None or nb is None:
            return
        self._section_nav_building = True
        try:
            existing = set(nav.items())
            for title in nb.pageNames():
                if title in existing:
                    continue
                nav.addItem(title, icon=self._SECTION_ICONS.get(title), key=title)
                self._section_nav_keys.append(title)
            cur = nb.currentName() if nb.count() else None
            if cur:
                nav.setCurrentKey(cur)
        finally:
            self._section_nav_building = False

    def _on_section_nav_changed(self, key: str) -> None:
        if self._section_nav_building:
            return
        self._notebook.setCurrentByName(key)


    def _tab_history_back(self) -> None:
        hist = getattr(self, "_tab_history", None)
        if not hist:
            return
        idx = self._tab_history_idx - 1
        if idx < 0:
            return
        self._tab_history_idx = idx
        self._tab_history_replaying = True
        try:
            self._notebook.setCurrentByName(hist[idx])
        finally:
            self._tab_history_replaying = False

    def _tab_history_forward(self) -> None:
        hist = getattr(self, "_tab_history", None)
        if not hist:
            return
        idx = self._tab_history_idx + 1
        if idx >= len(hist):
            return
        self._tab_history_idx = idx
        self._tab_history_replaying = True
        try:
            self._notebook.setCurrentByName(hist[idx])
        finally:
            self._tab_history_replaying = False

    # ── Statistics tab ────────────────────────────────────────────────────────
    # Tab / panel construction lives in ``views/stats_view.py`` — callers invoke
    # ``build_stats_tab`` / ``build_stats_group_editor`` / ``build_stats_results_panel``
    # there directly (teardown Phase 8).

    # Stats tab group-editor methods (delegate to stats_controller)

    def _stats_active_group(self) -> Optional[BarGroup]:
        from well_viewer.stats_controller import stats_active_group
        return stats_active_group(self)


    def _stats_refresh_single_btn(self, tok: str) -> None:
        stats_refresh_map(self)











    def _stats_write_result(self, text: str) -> None:
        self._stats_result_text.setReadOnly(False)
        self._stats_result_text.setPlainText(text or "")
        self._stats_result_text.setReadOnly(True)


    def _stats_collect_group_values(
        self, grp: BarGroup, target_t: float
    ) -> List[float]:
        return _stats_collect_group_values(
            self, grp, target_t,
            val_col=self._stats_active_val_col(),
            threshold=self._stats_active_threshold(),
            statistic=self._stats_active_statistic(),
        )

    _STATS_STATISTIC_KEYS = {
        "Mean (above threshold)": "mean",
        "Mean (all cells)": "mean_all",
        "Median (above threshold)": "median",
        "Fraction above threshold": "fraction",
    }

    def _stats_active_channel_entry(self) -> str:
        cb = getattr(self, "_stats_channel_cb", None)
        if cb is None:
            return self._app_state.channel.active_channel or ""
        text = cb.currentText().strip()
        return text if text and text != "—" else (self._app_state.channel.active_channel or "")

    def _stats_active_val_col(self) -> str:
        """Resolve the selected channel-dropdown entry to a value-column key."""
        entry = self._stats_active_channel_entry()
        if not entry:
            return self._app_state.channel.active_val_col
        try:
            return self._col_for_scatter_entry(entry)
        except Exception:
            return self._app_state.channel.active_val_col

    def _stats_active_threshold(self) -> float:
        """Use the per-channel ThreshFracOn from the Cell Gating tab.

        For ratio columns the channel-specific threshold doesn't apply; fall
        back to the global threshold so a sensible cutoff still exists.
        """
        val_col = self._stats_active_val_col()
        if is_ratio_key(val_col):
            return float(self._app_state.channel.threshold)
        # val_col is "<channel>_mean_intensity" or "<channel>_smfish_count".
        if "_" in val_col:
            channel = val_col.split("_", 1)[0]
            return float(self._get_thresh_frac_on(channel))
        return float(self._get_thresh_frac_on(self._app_state.channel.active_channel))

    def _stats_active_statistic(self) -> str:
        cb = getattr(self, "_stats_statistic_cb", None)
        if cb is None:
            return "mean"
        return self._STATS_STATISTIC_KEYS.get(cb.currentText(), "mean")

    def _stats_run(self) -> None:
        _stats_run_controller(
            self,
            collect_group_values_fn=self._stats_collect_group_values,
            draw_ks_cdf_fn=self._stats_draw_ks_cdf,
        )

    def _stats_draw_ks_cdf(
        self,
        group_vals: List[Tuple[str, List[float]]],
        tp_str: str,
    ) -> None:
        _stats_draw_ks_cdf(self, group_vals, tp_str, WELL_COLORS)

    # ── Preview sidebar picker ────────────────────────────────────────────────

    def _refresh_preview_picker(self) -> None:
        from ui.theme import get_color
        _refresh_preview_picker_view(
            self,
            button_bg=get_color("button_bg"),
            button_text=get_color("button_text"),
            button_text_disabled=get_color("button_text_disabled"),
            accent=get_color("ACCENT"),
            clr_white=get_color("CLR_WHITE"),
            clr_accent_dark=get_color("ACCENT_DARK"),
            extract_well_token_fn=_extract_well_token,
        )

    # ── Image Table tab ───────────────────────────────────────────────────────






















    # ─────────────────────────────────────────────────────────────────────────
    # Replicate panel
    # ─────────────────────────────────────────────────────────────────────────


    # ── Replicate-panel plate map ─────────────────────────────────────────────

    def _rep_refresh_map(self) -> None:
        """Push selection state onto the GROUPS-panel plate (a WellPlateSelector):
        every selection's wells take that selection's rank colour; the *current*
        selection's wells are shown as the plate's selection (sunken/accent), which
        is also what a drag on the plate edits."""
        plate = getattr(self, "_rep_map_plate", None)
        if plate is None:
            return
        plate.setEnabledWells(list(self._app_state.dataset.well_paths.keys()))
        colors: Dict[str, str] = {}
        for s in self._app_state.selection.selections:
            c = self._sel_color(s)
            for tok in (s.get("wells") or []):
                if tok in self._app_state.dataset.well_paths:
                    colors[tok] = c
        plate.clearWellColors()
        plate.setWellColors(colors)
        cur = self._sel_by_id(self._app_state.selection.current_selection_id)
        cur_wells = [w for w in (cur.get("wells") or []) if w in self._app_state.dataset.well_paths] if cur else []
        plate.setSelectedWellIds(cur_wells)


    def _rep_select(self, idx: int) -> None:
        # Legacy bridge: select the idx-th rep_set-source selection.
        sels = [s for s in self._app_state.selection.selections if s.get("source") == "rep_set"]
        if 0 <= idx < len(sels):
            self._sel_select(sels[idx].get("id"))

    def _rep_add(self) -> None:
        """Open dialog to create a new named ReplicateSet."""
        dlg = QDialog(self)
        dlg.setWindowTitle("New Replicate Set")
        dlg.setModal(True)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel("Name:"))
        name_edit = QLineEdit(f"Rep {len(self._app_state.selection.selections)+1}")
        v.addWidget(name_edit)
        v.addWidget(QLabel("Select wells:"))
        available = sorted(self._app_state.dataset.well_paths.keys(),
                           key=lambda l: self._parse_rc(l))
        lb = _wells_multiselect_listbox(dlg, available)
        v.addWidget(lb, 1)
        btn_row = QHBoxLayout()
        v.addLayout(btn_row)
        ok_btn = QPushButton("Create")
        ok_btn.setProperty("variant", "primary")
        cancel_btn = QPushButton("Cancel")
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        btn_row.addStretch(1)

        def _ok():
            sel = _selected_list_values(lb)
            if not sel:
                QMessageBox.warning(dlg, "No wells", "Select at least one well.")
                return
            name = name_edit.text().strip() or f"Rep {len(self._app_state.selection.selections) + 1}"
            dlg.accept()
            # _sel_add enforces well-exclusivity (the new group takes the wells).
            self._sel_add(name, list(sel), replicates=[list(sel)], source="rep_set")

        ok_btn.clicked.connect(_ok)
        cancel_btn.clicked.connect(dlg.reject)
        dlg.exec()

    def _rep_clear_all(self) -> None:
        if not self._app_state.selection.selections:
            return
        resp = QMessageBox.question(
            self, "Clear all groups?",
            f"Remove all {len(self._app_state.selection.selections)} group(s)?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if resp == QMessageBox.Yes:
            self._sel_clear_all()

    # ─────────────────────────────────────────────────────────────────────────
    # ─────────────────────────────────────────────────────────────────────────

    def _ask_name_dialog(self, default: str) -> Optional[str]:
        return _ui_ask_name_dialog(self, title="Name", prompt="Name:",
                                   default=default)

    # ─────────────────────────────────────────────────────────────────────────
    # Well-label editor
    # ─────────────────────────────────────────────────────────────────────────



    def _labels_clear_all(self) -> None:
        if self._app_state.dataset.well_labels:
            self._app_state.dataset.well_labels.clear()
            label_grid_refresh(self)
            self._invalidate_stats_cache()


    def _labels_add_prefix(self) -> None:
        labels_apply_affix(self, where="prefix")

    def _labels_add_suffix(self) -> None:
        labels_apply_affix(self, where="suffix")

    # ── unified-model (app._app_state.selection.selections) — the in-memory source of truth ──────

    def _sel_by_id(self, sid):
        for s in self._app_state.selection.selections:
            if s.get("id") == sid:
                return s
        return None

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

    def _sel_strip_wells(self, wells, *, keep_id=None) -> None:
        """A well belongs to ≤1 group: remove ``wells`` from every selection but
        ``keep_id``, pruning their replicates to the survivors. Also drop the
        same wells from ``self._app_state.selection.selected_wells`` so the plotting tabs don't
        keep treating them as solo wells once they've joined a group (the
        sidebar plate becomes passive in rep_mode and the user can't update
        the set themselves)."""
        from well_viewer.selections_model import _deoverlap_replicates
        wset = set(wells or [])
        if not wset:
            return
        for s in self._app_state.selection.selections:
            if s.get("id") == keep_id:
                continue
            nw = [w for w in (s.get("wells") or []) if w not in wset]
            if nw != s.get("wells"):
                s["wells"] = nw
                s["replicates"] = _deoverlap_replicates(s.get("replicates"), allowed=set(nw))
        if wset & self._app_state.selection.selected_wells:
            self._app_state.selection.selected_wells = self._app_state.selection.selected_wells - wset

    def _sel_add(self, name=None, wells=None, replicates=None, source="user",
                 *, make_current=True):
        from well_viewer.selections_model import make_selection
        used_names = {s.get("name") for s in self._app_state.selection.selections}
        used_ids = {s.get("id") for s in self._app_state.selection.selections}
        sel = make_selection(name=name, wells=wells, replicates=replicates,
                             source=source, used_names=used_names, used_ids=used_ids,
                             fallback_color_idx=len(self._app_state.selection.selections))
        self._sel_strip_wells(sel["wells"], keep_id=sel["id"])
        self._app_state.selection.selections.append(sel)
        if make_current:
            self._app_state.selection.current_selection_id = sel["id"]
        self._rebuild_all()
        return sel["id"]

    def _sel_rename(self, sid, name) -> None:
        from well_viewer.selections_model import _unique_name as _uq
        s = self._sel_by_id(sid)
        if s is None:
            return
        new = _uq(name, {x.get("name") for x in self._app_state.selection.selections if x.get("id") != sid})
        if new == s.get("name"):
            return
        s["name"] = new
        self._rebuild_all()

    def _sel_delete(self, sid) -> None:
        i = self._sel_index_of(sid)
        if i < 0:
            return
        self._app_state.selection.selections.pop(i)
        if self._app_state.selection.current_selection_id == sid:
            self._app_state.selection.current_selection_id = (
                self._app_state.selection.selections[min(i, len(self._app_state.selection.selections) - 1)].get("id")
                if self._app_state.selection.selections else None)
        self._rebuild_all()

    def _sel_set_hidden(self, sid, hidden, *, light=False) -> None:
        s = self._sel_by_id(sid)
        if s is None:
            return
        if bool(s.get("hidden")) == bool(hidden):
            return
        s["hidden"] = bool(hidden)
        if light:
            self._refresh_sidebar_map()
        else:
            self._rebuild_all()

    def _sel_set_color(self, sid, color) -> None:
        """Recolour a saved selection. ``color`` is a ``#RRGGBB`` hex string
        (or anything accepted by QColor); empty / falsy clears the override
        and lets the rank-based colour fall back through."""
        s = self._sel_by_id(sid)
        if s is None:
            return
        try:
            from PySide6.QtGui import QColor
            qc = QColor(color) if color else QColor()
            new = qc.name(QColor.HexRgb).upper() if qc.isValid() else ""
        except Exception:
            new = str(color or "").strip()
        if (s.get("color") or "") == new:
            return
        if new:
            s["color"] = new
        else:
            s.pop("color", None)
        self._rebuild_all()

    def _sel_export_one(self, sid) -> None:
        """Export a single saved selection (by id) to a standalone JSON file."""
        s = self._sel_by_id(sid)
        if s is None:
            return
        from PySide6.QtWidgets import QFileDialog
        import json as _json
        default_name = f"{s.get('name') or 'selection'}.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export selection", default_name, "JSON (*.json)"
        )
        if not path:
            return
        try:
            from well_viewer.selections_model import to_block
            block = to_block([s], current_id=s.get("id"))
            with open(path, "w", encoding="utf-8") as f:
                _json.dump(block, f, indent=2)
            self._toast(f"Exported '{s.get('name')}' to {path}.", kind="success")
        except Exception as exc:
            self._toast(f"Export failed: {exc}", kind="danger", msec=6000)

    def _sel_toggle_hidden(self, sid) -> None:
        s = self._sel_by_id(sid)
        if s is not None:
            self._sel_set_hidden(sid, not bool(s.get("hidden")))

    def _sel_set_composition(self, sid, wells=None, replicates=None) -> None:
        from well_viewer.selections_model import _clean_wells, _deoverlap_replicates
        s = self._sel_by_id(sid)
        if s is None:
            return
        if wells is not None:
            s["wells"] = _clean_wells(wells)
            self._sel_strip_wells(s["wells"], keep_id=sid)
        src = replicates if replicates is not None else s.get("replicates")
        s["replicates"] = _deoverlap_replicates(src, allowed=set(s.get("wells") or []))
        self._rebuild_all()

    def _sel_select(self, sid) -> None:
        if sid not in {s.get("id") for s in self._app_state.selection.selections}:
            return
        if sid == self._app_state.selection.current_selection_id:
            return
        self._app_state.selection.current_selection_id = sid
        self._groups_centre_refresh()
        self._refresh_sidebar_map()

    def _sel_reorder(self, ids) -> None:
        by_id = {s.get("id"): s for s in self._app_state.selection.selections}
        new = [by_id[i] for i in ids if i in by_id]
        for s in self._app_state.selection.selections:
            if s not in new:
                new.append(s)
        if new != self._app_state.selection.selections:
            self._app_state.selection.selections = new
            self._rebuild_all()

    def _sel_clear_all(self) -> None:
        self._app_state.selection.selections = []
        self._app_state.selection.current_selection_id = None
        self._rebuild_all()

    def _sel_duplicate(self, sid):
        s = self._sel_by_id(sid)
        if s is None:
            return None
        return self._sel_add(name=f"{s.get('name', 'Selection')} copy",
                             wells=[], replicates=None, source="user")

    def _enforce_well_exclusivity(self) -> None:
        """Invariant: a well belongs to **at most one group**. Keep each well in
        the first selection that has it; strip it from the rest. The edit
        helpers already enforce this (edited group wins); this is a safety net
        and cleans up pre-existing overlaps from old saved data."""
        seen: set = set()
        for s in self._app_state.selection.selections:
            if not isinstance(s, dict):
                continue
            kept = []
            for w in (s.get("wells") or []):
                if w in seen:
                    continue
                seen.add(w)
                kept.append(w)
            if kept != s.get("wells"):
                from well_viewer.selections_model import _deoverlap_replicates
                s["wells"] = kept
                s["replicates"] = _deoverlap_replicates(s.get("replicates"), allowed=set(kept))

    def _groups_centre_refresh(self) -> None:
        """Refresh all Sample Definitions panels.

        Card lists are only rebuilt when the tab is active (expensive).
        Plate maps are always updated (cheap).
        """
        self._enforce_well_exclusivity()
        tab_visible = False
        nb = getattr(self, "_notebook", None)
        if nb is not None:
            try:
                tab_visible = nb.currentName() == "Sample Definitions"
            except Exception:
                pass

        if tab_visible:
            rep_panel_refresh(self)
            label_grid_refresh(self)
        self._rep_refresh_map()
        update_active_group_label(self)


    def _rebuild_all(self) -> None:
        """
        Single authoritative refresh called after ANY data change.
        Always synchronous — no debounce — because it is only called on
        explicit user actions (button clicks / dialog OK), never during drag.
        """
        self._invalidate_stats_cache()
        self._groups_centre_refresh()          # Sample Definitions: GROUPS panel + map
        self._refresh_sidebar_map()            # line-graph picker: rep colours
        redraw_bars(self)
        self._redraw()
        try:
            from well_viewer.tabs.scatter_tab_view import scatter_redraw_active
            scatter_redraw_active(self)
        except Exception:
            _logger.exception("Scatter redraw failed")
        if hasattr(self, "_notebook"):
            if self._current_centre_tab() == "Line Graphs":
                self._show_line_sidebar()

    # ── Quick-group helpers (delegates to grouping_controller) ────────────────

    def _rep_quick_row_pairs(self) -> None:
        self._rep_quick_pair_dir = "row"
        self._rep_quick_iter_order = "row"
        rep_quick_pairs(self)

    def _rep_quick_col_pairs(self) -> None:
        self._rep_quick_pair_dir = "col"
        self._rep_quick_iter_order = "col"
        rep_quick_pairs(self)


    def _rep_quick_pairs_from_dropdowns(self) -> None:
        """Read dropdown values and update state, then call _rep_quick_pairs()."""
        # Map dropdown display values to internal values
        pair_dir_display = self._rep_quick_pair_dir_var.currentText()
        self._rep_quick_pair_dir = "row" if "Rows" in pair_dir_display else "col"

        iter_order_display = self._rep_quick_iter_order_var.currentText()
        self._rep_quick_iter_order = "row" if "Across" in iter_order_display else "col"

        rep_quick_pairs(self)

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

    def _open_ratio_panel(self) -> None:
        """Bring the Sample Definitions tab forward — the ratio editor lives there now."""
        nb = getattr(self, "_notebook", None)
        if nb is None:
            return
        nb.setCurrentByName("Sample Definitions")

    # ── Persistence ──────────────────────────────────────────────────────────
    # The data-dir JSON domains (ratios / heatmap_layouts / cell_overrides /
    # line_order / view_state / notes) are saved and loaded through
    # ``self._persistence`` — see well_viewer/persistence/registry.py.

    def _view_state_reset(self) -> None:
        from well_viewer.persistence import view_state as _vs
        _vs.reset(self)

    def _notes_schedule_save(self) -> None:
        """Capture the Notes text and queue a debounced save.

        The widget read must stay here (the registry never touches widgets);
        the debounce + disk write live in the PersistenceRegistry.
        """
        edit = getattr(self, "_notes_edit", None)
        if edit is not None:
            self._notes_text = edit.toPlainText()
        self._persistence.schedule_save("notes")

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

    def _rank_color_wells(self, wells) -> str:
        """Colour for a set of wells — the rank colour of its lowest well, so all
        of its wells (and its line/bar trace) share one colour."""
        ranks = [_sel_well_rank(w) for w in (wells or [])]
        ranks = [r for r in ranks if r < (1 << 30)]
        return WELL_COLORS[(min(ranks) if ranks else 0) % len(WELL_COLORS)]

    def _sel_color(self, s) -> str:
        """Effective colour for a selection dict: explicit ``color`` override
        first, otherwise the rank-based fallback so groups without a user-set
        colour stay consistent with their wells."""
        if not isinstance(s, dict):
            return self._rank_color_wells(getattr(s, "wells", None))
        c = s.get("color")
        if isinstance(c, str) and c.strip():
            return c.strip()
        return self._rank_color_wells(s.get("wells"))

    def _rank_color_rset(self, rset) -> str:
        """Colour for a ReplicateSet / BarGroup (or anything with a ``wells``
        attribute) — honours the per-selection ``color`` override when an entry
        in ``_selections`` shares its ``name``, otherwise falls back to the
        rank colour of its lowest well."""
        name = getattr(rset, "name", None)
        if name:
            for s in self._app_state.selection.selections:
                if s.get("name") == name:
                    return self._sel_color(s)
        return self._rank_color_wells(getattr(rset, "wells", None))

    def _rep_color_for(self, lbl: str) -> Optional[str]:
        """Return the colour for the selection that owns *lbl*, or None if the
        well is not in any selection."""
        for s in self._app_state.selection.selections:
            if lbl in (s.get("wells") or []):
                return self._sel_color(s)
        return None



    def _update_tophat_controls(self, preloaded: Optional[bool] = None) -> None:
        """Sync the top-hat row UI to the actual preload state.

        *preloaded* = True  → tophat images were loaded from disk for this FOV;
                              lock controls, show badge, display filtered images.
        *preloaded* = False → no preloaded images; enable manual filtering.
        *preloaded* = None  → read from self._montage_tophat_preloaded (default).

        This is the single source of truth for the tophat control state.
        Called from _refresh_preview_montage (after FOV-level coverage is known)
        and from _update_preview (to reset before a new well loads).
        """
        if not hasattr(self, "_th_checkbox"):
            return
        if preloaded is None:
            preloaded = getattr(self, "_montage_tophat_preloaded", False)

        # Block toggled() so the programmatic state sync doesn't re-kick off
        # the tophat filter thread via _montage_tophat_toggled.
        _prev = self._th_checkbox.blockSignals(True)
        try:
            if preloaded:
                self._th_checkbox.setChecked(True)
                self._th_checkbox.setEnabled(False)
                self._th_checkbox.setText("Top-hat background subtraction")
                self._th_radius_entry.setEnabled(False)
                self._th_preload_badge.setText("\u25cf from output zip")
            else:
                self._th_checkbox.setChecked(False)
                self._th_checkbox.setEnabled(True)
                self._th_checkbox.setText("Top-hat background subtraction")
                self._th_radius_entry.setEnabled(True)
                self._th_preload_badge.setText("")
        finally:
            self._th_checkbox.blockSignals(_prev)



    # _montage_make_thumb and _montage_make_overlay_thumb replaced by
    # module-level make_fluor_thumb() / make_overlay_thumb()

    # ── Movie Montage square-region crop (delegates to CropTool) ─────────────

    @property
    def _montage_crop(self):
        return self._montage_crop_tool.crop

    @property
    def _montage_crop_mode(self) -> bool:
        return self._montage_crop_tool.mode

    @property
    def _montage_crop_drag(self):
        # Legacy read-only flag: existing dispatchers only test for None-ness.
        return True if self._montage_crop_tool.is_dragging else None

    def _montage_label_to_image_xy(self, label, lx: int, ly: int):
        return self._montage_crop_tool.label_to_image_xy(label, lx, ly)

    def _on_montage_crop_press(self, label, event) -> None:
        self._montage_crop_tool.begin_drag(label, event)

    def _on_montage_crop_drag(self, event) -> None:
        self._montage_crop_tool.update_drag(event)

    def _on_montage_crop_release(self, event) -> None:
        self._montage_crop_tool.end_drag(event)

    def _toggle_montage_crop_mode(self) -> None:
        self._montage_crop_tool.toggle_mode()

    def _refresh_montage_crop_indicator(self) -> None:
        self._montage_crop_tool._refresh_indicator()


    def _montage_tophat_done(self, filtered_arrays: list, partial: bool = False) -> None:
        _montage_tophat_done_controller(self, filtered_arrays, partial=partial)

    def _montage_auto_lut(self, redraw: bool = True, force: bool = True) -> None:
        _montage_auto_lut_controller(self, redraw=redraw, force=force)




    # ── Montage zoom helpers ──────────────────────────────────────────────────







    def _apply_theme(self) -> None:
        """Apply the application-wide stylesheet.

        Single source of truth is the repo-root ``theme`` module. Previously
        this re-applied the legacy ``ui/theme`` per-theme QSS, which clobbered
        ``theme.qss()`` whenever a ``WellViewerApp`` was constructed (including
        embedded under AllWell).
        """
        try:
            import theme  # type: ignore
            app = QApplication.instance()
            if app is not None:
                app.setStyleSheet(theme.qss())
        except Exception:
            pass

    def _on_theme_change(self, theme_name: str = None) -> None:
        """Re-apply QSS stylesheet when theme switches."""
        new_theme = theme_name or self._theme_name
        self._theme_name = new_theme
        self._apply_theme()

        if hasattr(self, '_rep_cards_frame') and self._rep_cards_frame:
            rep_panel_refresh(self)
        if hasattr(self, '_stats_fig') and self._stats_fig:
            stats_refresh_colors(self)
        if hasattr(self, "_sidebar_btns"):
            self._sidebar_map_refresh_pending = False
            self._refresh_sidebar_map_now()
        try:
            from well_viewer.ui_helpers import refresh_plot_toolbar_icons
            refresh_plot_toolbar_icons(self)
        except Exception:
            pass

    # ── Loading ───────────────────────────────────────────────────────────────

    def _browse(self) -> None:
        """Open a directory picker. Expects in/ + out/ subdirs or a flat CSV dir."""
        d = QFileDialog.getExistingDirectory(self, "Open results directory")
        if d:
            # Defer so the dialog closes and the window repaints before load
            QTimer.singleShot(50, lambda: self._load_path(Path(d)))

    def _load_path(self, path):
        # Drain any pending deferred tab builds so post-load redraw paths
        # (e.g., _redraw_bars in _recalculate_threshold call sites) hit
        # fully-constructed tabs instead of unbuilt placeholders.
        from well_viewer import status_signal as _status_signal
        self._drain_pending_centre_builders()
        _status_signal.busy_push()
        try:
            _load_path_controller(self, path)
        except Exception:
            _status_signal.signal_failed()
            raise
        finally:
            _status_signal.busy_pop()
        refresh_plot_empty_states(self)

    def _load_directory(self, d: Path, label: Optional[str] = None) -> None:
        self._drain_pending_centre_builders()
        _load_directory_controller(self, d, label=label)
        refresh_plot_empty_states(self)


    def _drain_pending_centre_builders(self) -> None:
        """Force-build any centre tab whose body was deferred at startup.

        Tabs marked lazy-only (Cell Gating, smFISH) are intentionally
        skipped — they construct only when the user actually clicks the
        tab. The tab-switch handler in ``centre_view`` builds them inline
        on first access.
        """
        pending = getattr(self, "_centre_pending_builders", None)
        build = getattr(self, "_centre_build_pending", None)
        if not pending or build is None:
            return
        lazy_only = getattr(self, "_centre_lazy_only_titles", frozenset())
        # Snapshot keys — _centre_build_pending mutates the dict.
        for title in list(pending.keys()):
            if title in lazy_only:
                continue
            build(title)

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
    def _mute_color(hex_color: str, factor: float = 0.5) -> str:
        """Blend *hex_color* 50% toward a neutral mid-grey (#94A3B8).

        Used to dim hidden rep-sets on the plate map so they are visually
        distinct from visible ones while still showing their hue.
        """
        grey = (0x94, 0xA3, 0xB8)
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        mr = int(r + (grey[0] - r) * factor)
        mg = int(g + (grey[1] - g) * factor)
        mb = int(b + (grey[2] - b) * factor)
        return f"#{mr:02X}{mg:02X}{mb:02X}"

    def _rep_visible_sets(self) -> "List[ReplicateSet]":
        """Alias for _rep_sets_active — visible (non-hidden) loaded rep-sets."""
        return self._rep_sets_active()

    def _refresh_sidebar_map(self) -> None:
        """Debounce sidebar recoloring work to one pass per event-loop tick."""
        if self._sidebar_map_refresh_pending:
            return
        self._sidebar_map_refresh_pending = True
        QTimer.singleShot(0, self._refresh_sidebar_map_now)

    def _refresh_sidebar_map_now(self) -> None:
        """Push current selection / rep-set state onto the sidebar plate widget.

        Rep-set mode: each set's wells take that set's colour (muted when the
        set is hidden); the plate runs in "passive" mode so clicks toggle a
        set's visibility. Per-well mode: selected wells take the accent colour
        and the plate runs in "select" mode (drag-to-select). Wells with no
        loaded data are disabled. The count / group-hint labels are updated
        exactly as before.
        """
        plate = getattr(self, "_sidebar_plate", None)
        if plate is None:
            self._sidebar_map_refresh_pending = False
            return

        # Some tabs need per-well selection even when groups exist:
        # Sample Definitions (Cell Gating / Add Prefix / Add Suffix all
        # need user-chosen wells), and Review CSV (the user has to pick
        # which wells' rows to load). For those tabs the plate stays in
        # ``select`` mode and ``_selected_wells`` is honoured.
        try:
            tab_name = self._current_centre_tab()
        except Exception:
            tab_name = ""
        per_well_tab = tab_name in ("Sample Definitions", "Review CSV")
        rep_mode = bool(self._app_state.selection.selections) and not per_well_tab

        colors: Dict[str, object] = {}
        if rep_mode:
            for s in self._app_state.selection.selections:
                full_c = self._sel_color(s)
                shade = self._mute_color(full_c) if s.get("hidden") else full_c
                for tok in (s.get("wells") or []):
                    if tok in self._app_state.dataset.well_paths:
                        colors[tok] = shade
        else:
            for tok in self._app_state.selection.selected_wells:
                if tok in self._app_state.dataset.well_paths:
                    colors[tok] = self._rank_color_well(tok)

        # smFISH suppresses row/col header selection and forces single-well.
        smfish = False
        nb = getattr(self, "_notebook", None)
        if nb is not None:
            try:
                # smFISH is now a sub-tab inside the Segmentation parent,
                # so the top-level current name reads as "Segmentation"
                # whether the user is on the Segmentation or smFISH sub-
                # tab. Descend via ``_current_centre_tab`` to recover the
                # leaf name.
                smfish = self._current_centre_tab() == "smFISH"
            except Exception:
                smfish = False

        plate.setEnabledWells(list(self._app_state.dataset.well_paths.keys()))
        plate.setSelectionMode("passive" if rep_mode else "select")
        plate.setRowColumnSelectable(not smfish)
        plate.setSingleSelectionMode(smfish and not rep_mode)
        plate.clearWellColors()
        plate.setWellColors(colors)
        # Plotting tabs in rep-mode still honour ``_selected_wells`` so a
        # click on a well in a saved group promotes that group's wells to
        # the raised + bright (``_paint_lit``) look. Other rep-mode tabs
        # (Image Table / smFISH / etc) keep the legacy "no per-well
        # selection" override.
        plotting_tab = tab_name in self._PLOTTING_TABS
        if rep_mode and not plotting_tab:
            plate.setSelectedWellIds([])
        elif rep_mode and plotting_tab:
            # In rep-mode on a plotting tab the "active" set is exactly the
            # union of every non-hidden group's wells. ``_selected_wells``
            # is not used here — clicks toggle each group's ``hidden`` flag
            # so the visual state and the plot's ``_rep_sets_active()``
            # filter stay in lock-step. Solo wells (not in any group) live
            # in ``_active_solo_wells``.
            visible_wells = {
                w for s in self._app_state.selection.selections if not s.get("hidden")
                for w in (s.get("wells") or []) if w in self._app_state.dataset.well_paths
            }
            visible_wells |= {
                w for w in (self._app_state.selection.active_solo_wells or set())
                if w in self._app_state.dataset.well_paths
            }
            plate.setSelectedWellIds(list(visible_wells))
        else:
            plate.setSelectedWellIds(list(self._app_state.selection.selected_wells))

        # Count label / hint
        loaded  = self._rep_sets_loaded() if rep_mode else []
        n_vis   = len(self._rep_sets_active()) if rep_mode else len(self._app_state.selection.selected_wells)
        n_loaded = len(loaded)
        if hasattr(self, "_sel_count_lbl"):
            if rep_mode:
                n_hid = n_loaded - n_vis
                self._sel_count_lbl.setText((f"{n_vis}/{n_loaded} set(s) visible"
                          if n_hid else f"{n_loaded} set(s) — all visible"))
            else:
                self._sel_count_lbl.setText((f"{n_vis} well{'s' if n_vis != 1 else ''} selected"
                          if n_vis else "No wells selected"))
        if hasattr(self, "_sel_count_chip"):
            total = len(self._app_state.dataset.well_paths) if True else 96
            if rep_mode:
                # Count *wells* across every visible group + active solo
                # well, not groups. Earlier we showed group count here,
                # which read as "1 / 96" for a group with 4 members.
                active_wells: set = set()
                for s in self._app_state.selection.selections:
                    if s.get("hidden"):
                        continue
                    for w in (s.get("wells") or []):
                        if w in self._app_state.dataset.well_paths:
                            active_wells.add(w)
                for w in (self._app_state.selection.active_solo_wells or set()):
                    if w in self._app_state.dataset.well_paths:
                        active_wells.add(w)
                self._sel_count_chip.setText(f"{len(active_wells)} / {total or 96}")
            else:
                self._sel_count_chip.setText(f"{n_vis} / {total or 96}")
        if hasattr(self, "_line_group_hint"):
            if rep_mode:
                self._line_group_hint.setText("Click a well to toggle its set's visibility on the plot.")
            else:
                self._line_group_hint.setText("")

        self._sidebar_map_refresh_pending = False

    # ── v2 plate-widget bridge (WellPlateSelector ↔ WellViewerApp state) ──────
    # The left-rail plate is now a single ``widgets.WellPlateSelector`` instead
    # of an ``app._sidebar_btns`` grid of ``WellButton``s. ``app._app_state.selection.selected_wells``
    # stays the source of truth; ``_refresh_sidebar_map_now`` pushes appearance /
    # selection onto the widget, and the handlers below take user actions back.


    def _plate_commit_deferred(self) -> None:
        """Run the deferred commit scheduled by a paint event. No-op if
        a drag_finished handler already cleared the pending flag (which
        means it already committed in the same tick)."""
        if not self._app_state.selection.plate_commit_pending:
            return
        self._app_state.selection.plate_commit_pending = False
        on_plate_sel_change(self)

    def _set_selected_wells(self, new_set, *, commit: bool = True) -> None:
        """Single mutation point for ``_selected_wells``.

        Callers that mutated the set directly (heatmap drag, smFISH tab
        activation, batch-export panels) bypassed ``_prev_sel`` tracking
        and the redraw fan-out, so the next selection diff in
        ``selection_controller.on_plate_sel_change`` mis-attributed
        additions / removals across tabs. Funnel everything through here.
        """
        new_set = set(new_set or [])
        if new_set == self._app_state.selection.selected_wells:
            return
        # Snapshot the previous selection so on_plate_sel_change can
        # compute an accurate diff if it runs after this.
        self._app_state.selection.prev_sel = set(self._app_state.selection.selected_wells)
        self._app_state.selection.selected_wells = new_set
        if hasattr(self, "_refresh_sidebar_map"):
            self._refresh_sidebar_map()
        if commit and hasattr(self, "_on_plate_sel_change"):
            on_plate_sel_change(self)


    _PLOTTING_TABS = frozenset({
        "Line Graphs", "Bar Plots", "Scatter Plot", "Distribution", "Heat Map",
    })








    # ── Threshold ─────────────────────────────────────────────────────────────


    # ── Ratio metric helpers ─────────────────────────────────────────────────

    def _ratio_label_for(self, ratio: RatioMetric) -> str:
        """Return the dropdown label for a ratio (e.g. ``"GFP/MCHERRY"``).

        Disambiguates by appending ``[name]`` if two ratios share a label.
        """
        base = ratio.display_label()
        same_base = [r for r in self._app_state.channel.ratio_metrics if r.display_label() == base]
        if len(same_base) > 1:
            return f"{base} [{ratio.name}]"
        return base

    def _ratio_dropdown_labels(self) -> List[str]:
        return [self._ratio_label_for(r) for r in self._app_state.channel.ratio_metrics]

    def _channel_key_for_label(self, label: str) -> str:
        """Map a dropdown label back to the ``_set_active_channel`` argument.

        Real channels return their lowercase token; ratios return their
        ``ratio:<name>`` key.
        """
        mapping = getattr(self, "_label_to_channel_key", None) or {}
        if label in mapping:
            return mapping[label]
        # Fallback when the dropdown was populated before the mapping was
        # built — assume real channel and lowercase the label.
        return str(label or "").lower()

    def _active_channel_label(self) -> str:
        """Return the dropdown label corresponding to the active channel."""
        if is_ratio_key(self._app_state.channel.active_val_col):
            ratio_name = ratio_name_from_key(self._app_state.channel.active_val_col)
            for r in self._app_state.channel.ratio_metrics:
                if r.name == ratio_name:
                    return self._ratio_label_for(r)
            return ratio_name.upper()
        return self._app_state.channel.active_channel.upper()

    def _rebuild_ratio_index(self) -> None:
        """Refresh the resolver-friendly ratio index and any dependent UI."""
        self._app_state.channel.ratio_index = build_ratio_index(self._app_state.channel.ratio_metrics)
        # If the active val_col references a deleted ratio, fall back to the
        # first real fluorescence channel so plots stay valid.
        if is_ratio_key(self._app_state.channel.active_val_col) and self._app_state.channel.active_val_col not in self._app_state.channel.ratio_index:
            fallback = (self._app_state.channel.fluor_channels or ["gfp"])[0]
            self._app_state.channel.active_channel = fallback
            self._app_state.channel.active_val_col = f"{fallback}_{self._app_state.channel.active_metric}"
        if hasattr(self, "_update_channel_selector"):
            update_channel_selector(self)
        self._invalidate_stats_cache()
        if hasattr(self, "_redraw"):
            try:
                self._redraw()
            except Exception:
                pass

    def _set_ratio_metrics(self, ratios: Iterable[RatioMetric]) -> None:
        """Replace the ratio list and rebuild the index + UI."""
        self._app_state.channel.ratio_metrics = list(ratios)
        self._rebuild_ratio_index()
        panel = getattr(self, "_ratio_panel", None)
        if panel is not None:
            try:
                panel.refresh_from_app()
            except Exception:
                pass
        # Cell Gating shows one row per fluor channel + one per ratio. Pull
        # the ratio rows into the table whenever the ratio set changes.
        if hasattr(self, "_cell_gating_area_edit"):
            try:
                from well_viewer.tabs.cell_gating_tab_view import cell_gating_load_cell_areas
                cell_gating_load_cell_areas(self)
            except Exception:
                pass
        # Ratio additions become virtual channel entries in every plot-tab
        # combo (including the global ctxbar chip), so refresh those now —
        # otherwise the new ratio doesn't appear in the dropdown until the
        # next dataset reload.
        if hasattr(self, "_update_channel_selector"):
            try:
                update_channel_selector(self)
            except Exception:
                pass

    # ── Active channel ───────────────────────────────────────────────────────




    def _on_plot_channel_selected(self, source=None) -> None:
        """Channel-switch handler for line/bar/distribution/heatmap plot tabs.

        Each plot tab connects its channel combobox via a lambda that
        captures the source combobox and passes it as ``source`` here.
        ``QObject.sender()`` is unreliable when the slot is a Python
        lambda in PySide6, and ``self._plot_chan_var`` is bound to the
        line-tab combobox only — so falling back to either of those
        leaks the line-tab value when other tabs change channels and
        ``_set_active_channel`` short-circuits because the "new" channel
        equals the current one. Prefer the explicit ``source`` widget.
        """
        label = ""
        if source is not None and hasattr(source, "currentText"):
            try:
                label = str(source.currentText() or "")
            except Exception:
                label = ""
        if not label:
            try:
                sender = self.sender()
            except Exception:
                sender = None
            if sender is not None and hasattr(sender, "currentText"):
                try:
                    label = str(sender.currentText() or "")
                except Exception:
                    label = ""
        if not label:
            label = self._plot_chan_var.currentText()
        # Route real channels and ratios via the label→key map so ratio
        # selections (e.g. "GFP/MCHERRY") resolve to a ``ratio:<name>`` key.
        set_active_channel(self, self._channel_key_for_label(label))

    def _on_preview_channel_selected(self, _e=None) -> None:
        """Channel-switch handler for the Movie Montage tab."""
        selected_ui_value = ""
        if hasattr(self, "_chan_cb_preview"):
            selected_ui_value = str(self._chan_cb_preview.currentText() or "").strip()
        if not selected_ui_value and hasattr(self, "_montage_chan_var"):
            selected_ui_value = self._montage_chan_var.currentText()
        if _debug_flags.movie_montage_debug_enabled():
            _logger.debug(
                "preview_channel_selected ui_value=%r active_before=%r",
                selected_ui_value,
                self._app_state.channel.active_image_channel,
            )
        set_active_image_channel(self, selected_ui_value.lower())

    def _on_metric_selected(self) -> None:
        """Handle the per-tab (legacy hidden) metric selector change."""
        from well_viewer.metric_labels import METRIC_LABEL_TO_KEY
        metric_label = self._metric_var.currentText()
        metric = METRIC_LABEL_TO_KEY.get(metric_label, "mean_intensity")
        set_active_metric(self, metric)

    def _on_metric_selected_global(self) -> None:
        """Handle the global Property combo (ctxbar) change."""
        from well_viewer.metric_labels import METRIC_LABEL_TO_KEY
        cb = getattr(self, "_plotting_metric_cb", None)
        if cb is None:
            return
        metric_label = str(cb.currentText() or "")
        metric = METRIC_LABEL_TO_KEY.get(metric_label, "mean_intensity")
        # smFISH count only makes sense on smfish channels; gracefully fall
        # back to mean intensity so the user sees a redraw instead of an
        # empty plot.
        if metric == "smfish_count" and self._app_state.channel.active_channel not in self._app_state.channel.smfish_channels:
            metric = "mean_intensity"
        set_active_metric(self, metric)

    def _on_stats_property_change(self) -> None:
        """Stats tab's per-tab Property combo → propagate to global state."""
        from well_viewer.metric_labels import METRIC_LABEL_TO_KEY
        cb = getattr(self, "_stats_property_cb", None)
        if cb is None:
            return
        metric = METRIC_LABEL_TO_KEY.get(str(cb.currentText() or ""), "mean_intensity")
        if metric == "smfish_count" and self._app_state.channel.active_channel not in self._app_state.channel.smfish_channels:
            metric = "mean_intensity"
        set_active_metric(self, metric)
        # Stats display refreshes on its own when run; nothing to redraw here.







    def _toggle_sem(self) -> None:
        self._invalidate_stats_cache()
        is_sem = self._app_state.plot.toggle_sem()
        text = "SEM" if is_sem else "SD"
        variant = "sem" if is_sem else "sem_warn"
        for btn in list(getattr(self, "_sem_btns", []) or []):
            btn.setText(text)
            btn.setProperty("variant", variant)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        for obs in list(getattr(self, "_sem_observers", []) or []):
            try:
                obs(is_sem)
            except Exception:
                pass
        self._redraw()
        if self._current_centre_tab() == "Bar Plots":
            redraw_bars(self)
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

    def _toggle_fov_replicates(self) -> None:
        self._app_state.plot.toggle_fov_replicates()
        self._invalidate_stats_cache()
        self._refresh_fov_btn_state()
        self._redraw()
        if self._current_centre_tab() == "Bar Plots":
            redraw_bars(self)

    def _refresh_fov_btn_state(self) -> None:
        """Sync every per-FOV toggle button's text/variant state."""
        is_on = bool(self._app_state.plot.use_fov_replicates)
        text = "FOV ✓" if is_on else "FOV"
        variant = "toggle_active" if is_on else "toggle"
        tooltip = (
            "Compute error bands across per-FOV means.\n"
            "With replicate sets active, FOVs are pooled across all wells in each set;\n"
            "otherwise FOVs are pooled within each well. Pairs with the SEM/SD toggle."
        )
        for btn in list(getattr(self, "_fov_btns", []) or []):
            btn.setEnabled(True)
            btn.setText(text)
            btn.setToolTip(tooltip)
            btn.setProperty("variant", variant)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        for obs in list(getattr(self, "_fov_observers", []) or []):
            try:
                obs(is_on)
            except Exception:
                pass

    # ── Well selection (plate-map backed) ─────────────────────────────────────



    def _select_invert(self) -> None:
        """Phase 13 (B7): flip the current selection — every loaded well that
        was selected becomes unselected, and vice versa. Only wells with data
        (``_well_paths``) participate."""
        if not True:
            return
        cur = set(self._app_state.selection.selected_wells)
        new = {w for w in self._app_state.dataset.well_paths.keys() if w not in cur}
        self._app_state.selection.selected_wells = new
        # Re-use the standard refresh path the click handlers go through.
        self._refresh_sidebar_map()
        if hasattr(self, "_on_plate_sel_change"):
            on_plate_sel_change(self)

    def _selected_labels(self) -> List[str]:
        """Return labels in a stable order (sorted) for consistent plot colours."""
        return sorted(self._app_state.selection.selected_wells, key=lambda lbl: self._parse_rc(lbl))

    def _get_rows(self, label: str) -> pd.DataFrame:
        if label not in self._app_state.dataset.cache:
            self._app_state.dataset.cache[label] = load_well_csv(self._app_state.dataset.well_paths[label])
        return self._app_state.dataset.cache[label]



    # ── Preview panel ─────────────────────────────────────────────────────────


    def _on_preview_sel_change(self, _e=None) -> None:
        refresh_preview_montage(self)

    def _norm_timepoint(self, value: object) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        parsed = parse_timepoint_hours(raw)
        if parsed is not None:
            return f"{parsed:g}"
        return raw

    def _review_row_keys(self, row: dict) -> Tuple[str, str, str]:
        def _norm(v: object) -> str:
            s = str(v or "").strip()
            if not s:
                return ""
            try:
                return f"{float(s):g}"
            except Exception:
                return s
        def _pick(*names: str) -> object:
            for name in names:
                if name in row:
                    return row.get(name, "")
            lowered = {str(k).lower(): v for k, v in row.items()}
            for name in names:
                key = name.lower()
                if key in lowered:
                    return lowered[key]
            return ""
        return (
            _norm(_pick("fov", "FOV")),
            self._norm_timepoint(_pick("timepoint", "tp", "time", "time_h", "timepoint_hours")),
            _norm(_pick("nucleus_id", "nucleus id", "nucleusId", "nucleusID")),
        )


    @staticmethod
    def _review_norm_fov(v: object) -> str:
        s = str(v or "").strip()
        if not s:
            return ""
        try:
            return f"{float(s):g}"
        except Exception:
            return s

    @staticmethod
    def _review_tp_sort_key(tp: str) -> Tuple[int, float, str]:
        parsed = parse_timepoint_hours(str(tp))
        if parsed is not None:
            return (0, parsed, str(tp))
        return (1, 0.0, str(tp))

    def _review_collect_timepoints(self, fov: str) -> list:
        """Sorted list of timepoints available for ``fov`` (fluor + pipeline)."""
        _norm = self._review_norm_fov
        tp_values = sorted(
            {
                self._norm_timepoint(tp)
                for (f, tp) in self._app_state.preview.fluor.keys()
                if _norm(f) == fov and self._norm_timepoint(tp)
            },
            key=self._review_tp_sort_key,
        )
        pipeline_tp_values = sorted(
            {
                self._norm_timepoint(tp)
                for tp in (self._app_state.dataset.pipeline_info or {}).get("available_timepoints", [])
                if self._norm_timepoint(tp)
            },
            key=self._review_tp_sort_key,
        )
        if pipeline_tp_values:
            tp_values = sorted(set(tp_values) | set(pipeline_tp_values), key=self._review_tp_sort_key)
        return tp_values


    def _toggle_review_image_source(self) -> None:
        """Flip raw vs top-hat preference for the Review Image fluorescence channel."""
        self._app_state.review_image.show_raw = not bool(self._app_state.review_image.show_raw)
        # The LUT range for raw vs top-hat differs significantly, so drop the
        # cached LUT for the active channel so the new image gets a fresh
        # auto-fit instead of a stale clip.
        chan = str(self._app_state.channel.active_image_channel or "").lower()
        if chan in self._app_state.review_image.lut_by_channel:
            del self._app_state.review_image.lut_by_channel[chan]
        self._refresh_review_image_source_btn()
        self._app_state.review_image.preserve_view_on_refresh = True
        refresh_review_image(self)

    def _refresh_review_image_source_btn(self) -> None:
        """Sync the raw/top-hat toggle button's caption + variant with state."""
        btn = getattr(self, "_review_image_raw_btn", None)
        if btn is None:
            return
        on = bool(self._app_state.review_image.show_raw)
        btn.setChecked(on)
        btn.setText("Raw" if on else "Top-hat")
        btn.setProperty("variant", "toggle")
        btn.style().unpolish(btn)
        btn.style().polish(btn)
        btn.setToolTip(
            "Showing the unprocessed fluorescence frame.\n"
            "Click to switch back to the top-hat-filtered image."
            if on else
            "Showing the top-hat-filtered fluorescence frame (default).\n"
            "Click to switch to the unprocessed raw image."
        )

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
        attr = f"_review_image_{which}_color"
        rgb_clamped = tuple(max(0, min(255, int(c))) for c in rgb)
        setattr(self, attr, rgb_clamped)
        self._review_image_refresh_color_swatch(which)
        self._app_state.review_image.preserve_view_on_refresh = True
        refresh_review_image(self)

    def _review_image_refresh_color_swatch(self, which: str) -> None:
        swatches = getattr(self, "_review_image_color_swatches", {}) or {}
        btn = swatches.get(which)
        if btn is None:
            return
        r, g, b = self._review_image_get_color(which)
        # Explicit border-radius on every side so the bottom corners stay
        # rounded — the global QSS rule was being clipped by the swatch's
        # tight fixed size, leaving square lower corners.
        btn.setStyleSheet(
            f"QPushButton {{ background-color: rgb({r},{g},{b}); "
            f"border: 1px solid #444; border-radius: 4px; "
            f"min-width: 28px; min-height: 18px; padding: 0; }}"
        )


    def _reset_review_image_colors(self) -> None:
        """Restore the default colors for boundary / selection / tint."""
        for which, default in self._REVIEW_IMAGE_COLOR_DEFAULTS.items():
            setattr(self, f"_review_image_{which}_color", tuple(default))
            self._review_image_refresh_color_swatch(which)
        self._app_state.review_image.preserve_view_on_refresh = True
        refresh_review_image(self)

    # ── Binary mask toggle ────────────────────────────────────────────────

    def _toggle_review_image_binary_mask(self) -> None:
        """Flip the binary-mask overlay on/off and refresh the canvas."""
        self._app_state.review_image.binary_mask = not bool(
            self._app_state.review_image.binary_mask
        )
        self._refresh_review_image_binary_btn()
        self._app_state.review_image.preserve_view_on_refresh = True
        refresh_review_image(self)

    def _refresh_review_image_binary_btn(self) -> None:
        btn = getattr(self, "_review_image_binary_btn", None)
        if btn is None:
            return
        on = bool(self._app_state.review_image.binary_mask)
        btn.setChecked(on)
        btn.setText("Binary: On" if on else "Binary: Off")
        btn.setToolTip(
            "Binary mask: cells above the active threshold are drawn white,\n"
            "cells below are drawn black, on a black background.\n"
            "Uses the same gating as the bar plots, filtered to the\n"
            "displayed FOV/timepoint."
        )
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def _toggle_review_image_outline(self) -> None:
        """Flip the cell-outline overlay on/off and refresh the canvas."""
        self._app_state.review_image.show_outline = not bool(
            self._app_state.review_image.show_outline
        )
        self._refresh_review_image_outline_btn()
        self._app_state.review_image.preserve_view_on_refresh = True
        refresh_review_image(self)

    def _refresh_review_image_outline_btn(self) -> None:
        btn = getattr(self, "_review_image_outline_btn", None)
        if btn is None:
            return
        on = bool(self._app_state.review_image.show_outline)
        btn.setChecked(on)
        btn.setText("Outline: On" if on else "Outline: Off")
        btn.style().unpolish(btn)
        btn.style().polish(btn)




    def _draw_review_image(
        self,
        fluor_arr,
        mask_arr,
        include_by_nid: Dict[int, bool],
        *,
        fit_lut: bool = False,
        preserve_view: bool = False,
        boundary=None,
        above_by_nid: Optional[Dict[int, bool]] = None,
    ) -> None:
        from well_viewer.review_image_renderer import draw_review_image
        draw_review_image(
            self, fluor_arr, mask_arr, include_by_nid,
            fit_lut=fit_lut, preserve_view=preserve_view, boundary=boundary,
            above_by_nid=above_by_nid,
        )

    def _render_review_image_display(self, *, pan_only: bool = False) -> None:
        from well_viewer.review_image_renderer import render_review_image_display
        render_review_image_display(self, pan_only=pan_only)

    def _review_image_resolve_lut(self, arr) -> Tuple[float, float]:
        chan = str(self._app_state.channel.active_image_channel or "").lower()
        if hasattr(self, "_review_lmin_entry") and hasattr(self, "_review_lmax_entry"):
            try:
                lo = float(self._review_lmin_entry.text().strip())
                hi = float(self._review_lmax_entry.text().strip())
                if hi > lo:
                    self._app_state.review_image.lut_by_channel[chan] = (lo, hi)
                    return lo, hi
            except Exception:
                pass
        saved = self._app_state.review_image.lut_by_channel.get(chan)
        if saved is not None and saved[1] > saved[0]:
            return saved
        lo = float(arr.min())
        hi = float(arr.max())
        if hi <= lo:
            hi = lo + 1.0
        self._app_state.review_image.lut_by_channel[chan] = (lo, hi)
        return lo, hi

    def _review_image_auto_lut(self) -> None:
        arr = self._app_state.review_image.last_fluor_arr
        if arr is None or not _NP_AVAILABLE:
            return
        arr_np = _np.asarray(arr, dtype=_np.float32)
        lo = float(arr_np.min())
        hi = float(arr_np.max())
        if hi <= lo:
            hi = lo + 1.0
        self._app_state.review_image.lut_by_channel[str(self._app_state.channel.active_image_channel or "").lower()] = (lo, hi)
        if hasattr(self, "_review_lmin_entry") and hasattr(self, "_review_lmax_entry"):
            self._review_lmin_entry.setText(f"{lo:.0f}")
            self._review_lmax_entry.setText(f"{hi:.0f}")
        self._app_state.review_image.preserve_view_on_refresh = True
        refresh_review_image(self)

    def _review_image_commit_lut(self) -> None:
        arr = self._app_state.review_image.last_fluor_arr
        if arr is None:
            return
        lo, hi = self._review_image_resolve_lut(_np.asarray(arr, dtype=_np.float32))
        if hasattr(self, "_review_lmin_entry") and hasattr(self, "_review_lmax_entry"):
            self._review_lmin_entry.setText(f"{lo:.0f}")
            self._review_lmax_entry.setText(f"{hi:.0f}")
        self._app_state.review_image.preserve_view_on_refresh = True
        refresh_review_image(self)

    def _review_image_zoom_step(self, direction: int) -> None:
        steps = [0.25, 0.33, 0.5, 0.67, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0]
        cur = self._app_state.review_image.zoom
        idx = min(range(len(steps)), key=lambda i: abs(steps[i] - cur))
        idx = max(0, min(len(steps) - 1, idx + direction))
        self._app_state.review_image.zoom = steps[idx]
        self._render_review_image_display()

    def _review_image_zoom_fit(self) -> None:
        self._app_state.review_image.zoom = 1.0
        self._app_state.review_image.pan_x = 0.0
        self._app_state.review_image.pan_y = 0.0
        self._render_review_image_display()







    def _on_review_image_click(self, event: Any) -> None:
        _on_review_image_click_controller(self, event, _logger)

    def _apply_review_image_cursor(self) -> None:
        """Set the Review Image cursor based on include-edit mode.

        ForbiddenCursor (the ⊘ 'no entry' icon) indicates remove-cell mode;
        PointingHandCursor indicates normal select/highlight mode. Applied
        to both the label and the scroll viewport so it does not flicker
        back to the arrow over scrollbar gutters or padding.
        """
        enabled = bool(self._app_state.review_image.include_edit_mode)
        cursor = Qt.CrossCursor if enabled else Qt.PointingHandCursor
        if hasattr(self, "_review_image_label"):
            self._review_image_label.setCursor(cursor)
        canvas = getattr(self, "_review_image_canvas", None)
        if canvas is not None:
            canvas.setCursor(cursor)
            vp = canvas.viewport()
            if vp is not None:
                vp.setCursor(cursor)

    def _select_review_csv_row_for_cell(self, fov: str, tp: str, nucleus_id: str) -> None:
        _select_review_csv_row_for_cell_controller(self, fov, tp, nucleus_id, _logger)

    def _set_review_image_include_mode(self, enabled: bool) -> None:
        self._app_state.review_image.include_edit_mode = bool(enabled)
        self._apply_review_image_cursor()
        btn = getattr(self, "_review_image_delete_btn", None)
        if btn is not None:
            btn.setChecked(bool(enabled))
        if enabled:
            self._set_status(
                "Delete mode ON — click a cell to exclude it, or drag a rectangle to bulk-exclude."
            )
        else:
            self._set_status("Delete mode OFF.")

    def _toggle_selected_review_cell(self) -> None:
        self._set_review_image_include_mode(not self._app_state.review_image.include_edit_mode)






    # ── Export ────────────────────────────────────────────────────────────────



    # ── Batch export ──────────────────────────────────────────────────────────

    def _open_batch_export(self) -> None:
        if not self._app_state.dataset.well_paths:
            QMessageBox.warning(self, "No data", "Load data before opening Batch Export.")
            return
        if hasattr(self, "_notebook"):
            self._notebook.setCurrentByName("Batch Export")
        if hasattr(self, "_batch_export_set_mode"):
            self._batch_export_set_mode("line")

    # ── Montage ───────────────────────────────────────────────────────────────

    # ── Plotting ──────────────────────────────────────────────────────────────

    def _redraw(self) -> None:
        # Keep the EmptyState/figure stacks in sync with the current data
        # state before any matplotlib work. When no data is loaded the
        # plot is hidden behind the EmptyState page, so we can skip the
        # actual redraw — but `_plot_redraw_orchestrator` still tolerates
        # the empty state via its own no-data branch.
        from well_viewer import status_signal as _status_signal
        refresh_plot_empty_states(self)
        # `_plot_redraw_orchestrator` fans out across the default-scope
        # tabs (line / distribution / heat map). Bar / scatter /
        # scatter-aggregate are scope-specific and re-render through
        # their own per-tab shims — see `plot_orchestrator.redraw`'s
        # docstring for the contract.
        with _status_signal.warn_scope():
            try:
                _plot_redraw_orchestrator(
                    self,
                    lineplot_redraw=_lineplot_redraw,
                    apply_ax_style=apply_ax_style,
                    all_fluor_values=_all_fluor_values,
                    all_fluor_values_filtered=_all_fluor_values_filtered,
                    warn=WARN,
                )
            except Exception:
                _status_signal.signal_failed()
                raise
        from well_viewer.figure_export_editor import apply_export_style_to_current
        apply_export_style_to_current(self, self._line_fig, getattr(self, "_line_canvas", None))

        # Keep the Export Configurator's line-order lists in sync with the
        # current selection / replicate sets when the sidebar is open.
        for sb in (getattr(self, "_export_style_sidebars", {}) or {}).values():
            try:
                if getattr(sb, "_is_line_fig", lambda: False)():
                    sb._refresh_line_order_lists()
            except Exception:
                pass

    # ── Bar plot tab ──────────────────────────────────────────────────────────

    def _current_centre_tab(self) -> str:
        """Return the effective current tab name.

        When the "Plotting" or "Segmentation" top-level tab is active
        the real content lives in a nested QStackedWidget. This helper
        resolves that one level of indirection so callers can always
        compare against leaf names like "Bar Plots", "Heat Map",
        "Segmentation", or "smFISH". (The Segmentation parent and its
        default leaf share the same name; descending is still correct —
        the inner stack returns "Segmentation" or "smFISH" depending on
        which sub-tab is active.)
        """
        nb = getattr(self, "_notebook", None)
        if nb is None:
            return ""
        try:
            tab = nb.currentName()
        except Exception:
            return ""
        if tab == "Plotting":
            plotting_nb = getattr(self, "_plotting_notebook", None)
            if plotting_nb is not None and plotting_nb.count() > 0:
                try:
                    return plotting_nb.currentName()
                except Exception:
                    pass
        elif tab == "Segmentation":
            cs_nb = getattr(self, "_cell_segmentation_notebook", None)
            if cs_nb is not None and cs_nb.count() > 0:
                try:
                    return cs_nb.currentName()
                except Exception:
                    pass
        return tab


    def _sync_heatmap_well_drag_mode(self, enable: bool) -> None:
        """Toggle WELL_MIME drag/drop on the sidebar plate widget.

        When *enable* is True, the plate exports the well under the pointer via
        the WELL_MIME format on left-button drag (clicks stop toggling
        selection) and accepts dropped tokens as a "return to palette" action
        that unassigns the well from the active heat-map layout (handled by
        ``_on_sidebar_plate_well_dropped``). When False, drag/drop is disabled
        and the plate reverts to plain selection behaviour.
        """
        plate = getattr(self, "_sidebar_plate", None)
        if plate is None:
            return
        from well_viewer.views.heatmap_layout_sidebar_view import WELL_MIME
        if enable:
            plate.setDragMime(WELL_MIME)
            plate.setAcceptDropMime(WELL_MIME)
        else:
            plate.setDragMime(None)
            plate.setAcceptDropMime(None)
            # Don't force-disable wells here — the per-tab branch in
            # ``_on_tab_change`` either hides the sidebar (preview / image
            # table tabs) or calls ``_refresh_sidebar_map`` which restores the
            # correct enabled state per well.

    def _sync_preview_well_for_image_tabs(self) -> None:
        """Keep current preview well unless the active selection supplies one."""
        cur_sel = self._sel_by_id(self._app_state.selection.current_selection_id)
        sel_wells = (cur_sel.get("wells") or []) if cur_sel else []

        # Preserve explicit user choice when still valid.
        current = self._app_state.preview.selected_well
        if current in self._app_state.dataset.well_paths:
            # If the active selection has wells, prefer its first loaded well.
            for tok in sel_wells:
                if tok in self._app_state.dataset.well_paths:
                    self._app_state.preview.selected_well = tok
                    return
            return

        # If no valid current well, use the active selection's first loaded well.
        for tok in sel_wells:
            if tok in self._app_state.dataset.well_paths:
                self._app_state.preview.selected_well = tok
                return

        # Final fallback: keep previous behavior of choosing first available well.
        if self._app_state.dataset.well_paths:
            self._app_state.preview.selected_well = sorted(self._app_state.dataset.well_paths.keys(), key=self._parse_rc)[0]

    def _run_tab_switch_smoke_checks(
        self,
        prev_tab: Optional[str],
        tab: str,
        prev_selected: set,
    ) -> None:
        """Debug guardrails for Line/Bar/Batch sidebar continuity."""
        watched = {"Line Graphs", "Bar Plots", "Batch Export"}
        if tab not in watched:
            return

        # Batch Export should share the same in-memory selection objects used by
        # the line/bar/scatter render paths.
        expected_ids = (
            id(self._app_state.selection.selected_wells),
            id(self._app_state.selection.selections),
        )
        if not hasattr(self, "_selection_model_identity"):
            self._selection_model_identity = expected_ids
        elif self._selection_model_identity != expected_ids:
            _logger.warning(
                "Selection model identity changed across tab switch: "
                "_selected_wells/_selections should be shared."
            )

        # UI smoke check: switching among Line/Bar/Batch must not mutate the
        # current per-well selection by itself.
        if prev_tab in watched and prev_selected != self._app_state.selection.selected_wells:
            _logger.warning(
                "Tab switch altered selected wells unexpectedly: %s -> %s",
                prev_tab, tab,
            )

    def _show_line_sidebar(self) -> None:
        """No-op: the unified well picker is always visible for plot tabs."""
        pass

    def _build_review_csv_tab(self, parent) -> None:
        from well_viewer.tabs.review_csv_tab_view import build_review_csv_tab as _v
        _v(self._ctx, parent)

    def _refresh_review_csv(self) -> None:
        from well_viewer.review_image_controller import refresh_review_csv
        refresh_review_csv(self)

    def _refresh_review_csv_rows(self, rows=None) -> None:
        from well_viewer.review_image_controller import refresh_review_csv_rows
        refresh_review_csv_rows(self, rows)

    def _review_effective_included(self, label: str, row: dict) -> str:
        """Effective Included value for a cached row.

        User overrides set via the Review Image tab take precedence over the
        gating-computed ``Included`` field stored on the cached row. Overrides
        are kept in a separate dict so they survive subsequent gating recomputes
        (which rewrite the canonical row['Included']).
        """
        fov, tp, nid = self._review_row_keys(row)
        if fov and tp and nid:
            ovr = self._review_included_overrides.get((label, fov, tp, nid))
            if ovr is not None:
                return ovr
        return str(row.get("Included", "1"))


    def _review_load_rows(self, label: str) -> List[dict]:
        """Materialize the cached DataFrame for ``label`` as a list of row dicts.

        Used by the Review CSV table widget and the Review Image overlay
        builders. Mutations to returned dicts are local — write back to the
        canonical store via ``_apply_review_overrides_to_cache`` (which
        updates the cached DataFrame's ``Included`` column directly).
        """
        try:
            df = self._get_rows(label)
        except Exception:
            return []
        if df is None or df.empty:
            return []
        return df.to_dict("records")

    def _update_bar_tp_menu(self) -> None:
        """
        Populate the timepoint dropdown with the union of all timepoints
        present across all loaded wells.

        The available timepoints are always drawn from the full loaded dataset,
        not from the current selection — the selection controls what is plotted,
        but the dropdown must remain usable even when nothing is selected yet.
        """
        if not self._app_state.dataset.well_paths:
            _set_combo_values(self._bar_tp_cb, ["—"])
            self._bar_tp_cb.setCurrentText("—")
            return

        all_tps: set = set(self._app_state.dataset.all_timepoints_cache)

        # If no timepoints were found the schema had no timepoint field.
        # Use 0.0 as a single synthetic timepoint so bar plots remain usable.
        if not all_tps and self._app_state.dataset.well_paths:
            all_tps.add(0.0)

        sorted_tps = sorted(all_tps)
        tp_strs    = [f"{t:.4g}" for t in sorted_tps]

        cur = self._bar_tp_cb.currentText()
        _set_combo_values(self._bar_tp_cb, tp_strs)
        if cur in tp_strs:
            self._bar_tp_cb.setCurrentText(cur)
        elif tp_strs:
            self._bar_tp_cb.setCurrentText(tp_strs[0])
        else:
            self._bar_tp_cb.setCurrentText("—")

    # ── Bar drag-and-drop reordering ─────────────────────────────────────────
    # Implementations live in ``well_viewer.barplot_controller``.

    def _bar_event_xdata(self, event) -> "Optional[float]":
        from well_viewer.barplot_controller import bar_event_xdata
        return bar_event_xdata(self, event)

    def _bar_current_keys(self) -> List:
        return _bar_ordered_keys(self)

    def _bar_idx_at_x(self, xdata: float, n: int) -> int:
        from well_viewer.barplot_controller import bar_idx_at_x
        return bar_idx_at_x(xdata, n)



    def _on_bar_drag_motion(self, event) -> None:
        from well_viewer.barplot_controller import on_bar_drag_motion
        on_bar_drag_motion(self, event, accent_color=ACCENT)


    def _apply_bar_ylims(
        self,
        ax_mean: "Axes",
        ax_frac: "Axes",
        ax_n=None,
    ) -> None:
        """Apply user-specified y-axis limits to the bar panels."""
        _bar_apply_ylims(self, ax_mean, ax_frac, ax_n=ax_n)

    def _toggle_swarm(self) -> None:
        """Toggle beeswarm / bar mode and update the button appearance."""
        self._bar_swarm = not self._bar_swarm
        on = self._bar_swarm
        self._swarm_btn.setChecked(on)
        if on and self._bar_violin:
            # Swarm and violin are mutually exclusive
            self._bar_violin = False
            self._violin_btn.setChecked(False)
            self._violin_slider.setEnabled(False)
        redraw_bars(self)

    def _toggle_violin(self) -> None:
        """Toggle violin / bar mode and update the button appearance."""
        self._bar_violin = not self._bar_violin
        on = self._bar_violin
        self._violin_btn.setChecked(on)
        self._violin_slider.setEnabled(on)
        if on and self._bar_swarm:
            # Mutually exclusive with beeswarm
            self._bar_swarm = False
            self._swarm_btn.setChecked(False)
        redraw_bars(self)



    # ── Bar plot export ───────────────────────────────────────────────────────

    def _well_display_label(self, lbl: str) -> str:
        """Return the custom display label for *lbl* if defined, else its token."""
        tok = _extract_well_token(lbl) or lbl
        return self._app_state.dataset.well_labels.get(tok, tok)

    def _bar_well_display_label(self, lbl: str) -> str:
        """Bar-plot-safe well label that avoids numeric-only tick text."""
        disp = str(self._well_display_label(lbl))
        if re.match(r"^\s*\d+\s*$", disp):
            return _extract_well_token(lbl) or disp
        return disp

    def _replicate_display_label(self, rset: "ReplicateSet") -> str:
        """Human-friendly x-axis label for a replicate set.

        If the replicate set name appears auto-generated (e.g. ``"1"``,
        ``"Replicate 2"``, ``"R3"``), use member well labels so the x-axis
        remains interpretable.
        """
        name = str(getattr(rset, "name", "") or "").strip()
        if not name:
            name = "Replicate"

        generic_name = bool(
            re.match(
                r"^(?:\d+|r\s*\d+|rep(?:licate)?\s*\d+|group\s*\d+|set\s*\d+|batch\s*\d+)$",
                name,
                re.I,
            )
        )
        if not generic_name:
            return name

        wells = [self._well_display_label(w) for w in rset.wells if w in self._app_state.dataset.well_paths]
        if not wells:
            return name
        if len(wells) <= 3:
            return ",".join(wells)
        return f"{','.join(wells[:3])} +{len(wells)-3}"

    def _rep_sets_loaded(self) -> "List[ReplicateSet]":
        """One ReplicateSet per selection that has ≥1 loaded well (ignores hidden),
        in ``_selections`` order — derived straight from the unified model."""
        out: "List[ReplicateSet]" = []
        for s in self._app_state.selection.selections:
            wells = s.get("wells") or []
            if any(w in self._app_state.dataset.well_paths for w in wells):
                out.append(ReplicateSet(s.get("name") or "", list(wells)))
        return out

    def _groups_from_rep_sets(self) -> "List[BarGroup]":
        """One BarGroup per loaded selection (each wrapping its ReplicateSet)."""
        return [BarGroup(r.name, members=[r]) for r in self._rep_sets_loaded()]

    def _rep_sets_active(self) -> "List[ReplicateSet]":
        """One ReplicateSet per visible (non-hidden) selection with ≥1 loaded
        well — these are exactly the traces drawn on the plots."""
        out: "List[ReplicateSet]" = []
        for s in self._app_state.selection.selections:
            if s.get("hidden"):
                continue
            wells = s.get("wells") or []
            if any(w in self._app_state.dataset.well_paths for w in wells):
                out.append(ReplicateSet(s.get("name") or "", list(wells)))
        return out





    def _invalidate_stats_cache(self) -> None:
        """Discard cached group statistics. Call whenever group definitions change."""
        self._app_state.stats.cache: dict = {}
        # Replicate-set visibility may have shifted, so re-check whether the
        # per-FOV toggle is still applicable. Cheap; only walks _fov_btns.
        if hasattr(self, "_refresh_fov_btn_state"):
            self._refresh_fov_btn_state()
        # Cell gating writes new ``Included`` flags onto cached rows, which
        # the Review Image include map mirrors. Bumping the version key
        # forces the next refresh to recompute the include map (cheap)
        # while reusing the cached image arrays + boundary mask (expensive).
        self._app_state.review_image.override_version += 1

    def _invalidate_review_image_frame_cache(self) -> None:
        """Drop the decoded fluor / mask / boundary cache.

        Call on data load (well paths change) and on dataset re-scan so the
        next refresh re-decodes from disk.
        """
        self._app_state.review_image.frame_cache = None
        self._app_state.review_image.include_cache.clear()
        self._app_state.review_image.override_version += 1


    def _collect_bar_items(self, target_t: float, *,
                           fc_state=None, miss_sink=None) -> tuple:
        """Return ``(use_groups, list[BarItem], band_lbl)`` for *target_t*.

        Thin wrapper around :func:`barplot_controller.collect_bar_items`.
        ``fc_state`` lets the on-tab CSV exporter request raw items (pass
        ``FC_STATE_OFF``) alongside the normalized items the renderer
        consumes. ``miss_sink`` collects timepoints where a vs-control
        denominator couldn't be resolved so the caller can surface a
        single status warning at the end of the redraw.
        """
        return _bar_collect_items(
            self, target_t, fc_state=fc_state, miss_sink=miss_sink,
        )





    def _open_bar_batch_export(self) -> None:
        """Switch Batch Export tab to the inline bar-plot export builder."""
        if not self._app_state.dataset.well_paths:
            QMessageBox.warning(self, "No data", "Load data before opening Bar Batch Export.")
            return
        if hasattr(self, "_notebook"):
            self._notebook.setCurrentByName("Batch Export")
        if hasattr(self, "_batch_export_set_mode"):
            self._batch_export_set_mode("bar")

    def _open_scatter_cells_batch_export(self) -> None:
        """Switch Batch Export tab to the inline scatter-cells export builder."""
        if not self._app_state.dataset.well_paths:
            QMessageBox.warning(self, "No data", "Load data before opening Scatter Cells Batch Export.")
            return
        if hasattr(self, "_notebook"):
            self._notebook.setCurrentByName("Batch Export")
        if hasattr(self, "_batch_export_set_mode"):
            self._batch_export_set_mode("scatter_cells")

    def _open_scatter_agg_batch_export(self) -> None:
        """Switch Batch Export tab to the inline aggregate-scatter export builder."""
        if not self._app_state.dataset.well_paths:
            QMessageBox.warning(self, "No data", "Load data before opening Scatter Aggregate Batch Export.")
            return
        if hasattr(self, "_notebook"):
            self._notebook.setCurrentByName("Batch Export")
        if hasattr(self, "_batch_export_set_mode"):
            self._batch_export_set_mode("scatter_agg")

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

    def _open_export_style_panel(self, plot_key: str) -> None:
        """Open the floating Export Style sidebar for ``plot_key``'s figure.

        Each plot tab (line / bar / scatter cells / scatter agg /
        distribution / heatmap) pre-allocates a right-side dock when its
        view is built. This routes the per-card "▸" style button to the
        ExportStyleSidebar mounted in that dock.
        """
        mapping = {
            "line":          ("_line_fig",         "_line_canvas",         "line_figure"),
            "bar":           ("_bar_fig",          "_bar_canvas",          "bar_figure"),
            "scatter_cells": ("_scatter_fig",      "_scatter_canvas",      "scatter_figure"),
            "scatter_agg":   ("_scatter_agg_fig",  "_scatter_agg_canvas",  "scatter_agg_figure"),
            "distribution":  ("_distribution_fig", "_distribution_canvas", "distribution_figure"),
            "heatmap":       ("_heatmap_fig",      "_heatmap_canvas",      "heatmap_figure"),
        }
        spec = mapping.get(plot_key)
        if spec is None:
            self._set_status(f"Unknown export panel: {plot_key}")
            return
        fig_attr, canvas_attr, default_name = spec
        fig = getattr(self, fig_attr, None)
        canvas = getattr(self, canvas_attr, None)
        if fig is None:
            self._set_status("Plot is not ready yet.")
            return
        # If a sidebar for this figure is already open, toggle it off so
        # the button behaves as an open/close affordance.
        existing = (getattr(self, "_export_style_sidebars", {}) or {}).get(id(fig))
        if existing is not None and existing.isVisible():
            try:
                existing._close_dock()
            except Exception:
                pass
            return
        from well_viewer.figure_export_editor import launch_export_editor
        launch_export_editor(self, fig, default_name, plot_bg=PLOT_BG, canvas=canvas)

    # ── Scatter Plot tab ───────────────────────────────────────────────────────

    def _col_for_scatter_axis(self, channel_entry: str, metric_key: str) -> str:
        """Map a scatter (channel_entry, metric_key) pair to a CSV column or ratio key.

        Ratio channels collapse to the ratio key regardless of metric (ratios
        don't carry a metric suffix). Otherwise builds ``<channel>_<metric>``.
        ``metric_key`` may be ``fraction_above_threshold`` for the agg scatter
        — that's a synthetic key that downstream aggregation code treats as a
        Fraction-On aggregation against the channel's mean-intensity column.
        """
        ratio_key = (getattr(self, "_label_to_channel_key", None) or {}).get(channel_entry)
        if ratio_key and is_ratio_key(ratio_key):
            return ratio_key
        if metric_key in ("fraction_above_threshold", "smfish_count"):
            # Fraction-on uses MFI as the underlying value column; smFISH
            # uses the dedicated count column.
            if metric_key == "smfish_count":
                return f"{channel_entry}_smfish_count"
            return f"{channel_entry}_mean_intensity"
        return f"{channel_entry}_{metric_key}"

    def _col_for_scatter_entry(self, entry: str) -> str:
        """Legacy single-string entry → column resolver (kept for stats/export back-compat).

        "gfp" -> "gfp_mean_intensity"
        "gfp (spots)" -> "gfp_smfish_count"
        "GFP/MCHERRY" (a ratio dropdown label) -> "ratio:GFP/MCHERRY"
        """
        ratio_key = (getattr(self, "_label_to_channel_key", None) or {}).get(entry)
        if ratio_key and is_ratio_key(ratio_key):
            return ratio_key
        if entry.endswith(" (spots)"):
            ch = entry[:-8]  # Remove " (spots)"
            return f"{ch}_smfish_count"
        else:
            metric = self._app_state.channel.active_metric or "mean_intensity"
            if metric == "smfish_count":
                # The scatter dropdown uses the " (spots)" suffix for smfish;
                # fall back to mean intensity for the plain channel entry.
                metric = "mean_intensity"
            return f"{entry}_{metric}"

    # ── Per-axis scatter metric helpers ────────────────────────────────────

    _SCATTER_AGG_METRIC_EXTRA = {"Fraction above threshold": "fraction_above_threshold"}

    def _scatter_axis_metric_key(self, axis: str, *, agg: bool = False) -> str:
        """Return the metric key for axis ``x``/``y`` on the active scatter tab."""
        from well_viewer.metric_labels import METRIC_LABEL_TO_KEY
        attr = (
            f"_scatter_agg_metric_{axis}_cb" if agg
            else f"_scatter_metric_{axis}_cb"
        )
        cb = getattr(self, attr, None)
        if cb is None:
            return "mean_intensity"
        label = str(cb.currentText() or "")
        if agg and label in self._SCATTER_AGG_METRIC_EXTRA:
            return self._SCATTER_AGG_METRIC_EXTRA[label]
        return METRIC_LABEL_TO_KEY.get(label, "mean_intensity")

    def _refresh_scatter_metric_for_axis(self, axis: str) -> None:
        """Reshape the cells-scatter Property combo for the selected channel."""
        ch_cb = getattr(self, f"_scatter_ch_{axis}_cb", None)
        m_cb = getattr(self, f"_scatter_metric_{axis}_cb", None)
        if ch_cb is None or m_cb is None:
            return
        channel = str(ch_cb.currentText() or "")
        populate_metric_combo(self, 
            m_cb, channel_entry=channel, smfish_channel=channel,
        )

    def _refresh_scatter_agg_metric_for_axis(self, axis: str) -> None:
        """Reshape the agg-scatter Property combo for the selected channel.

        Real channels get the full intensity set plus ``Fraction above
        threshold``; ratio channels collapse to ``Calculated Val``.
        """
        ch_cb = getattr(self, f"_scatter_agg_ch_{axis}_cb", None)
        m_cb = getattr(self, f"_scatter_agg_metric_{axis}_cb", None)
        if ch_cb is None or m_cb is None:
            return
        channel = str(ch_cb.currentText() or "")
        populate_metric_combo(self, 
            m_cb, channel_entry=channel, smfish_channel=channel,
            include_fraction=True,
        )

    def _on_scatter_axis_change(self, axis: str) -> None:
        self._refresh_scatter_metric_for_axis(axis)
        redraw_scatter_orchestrate(self)

    def _on_scatter_agg_axis_change(self, axis: str) -> None:
        self._refresh_scatter_agg_metric_for_axis(axis)
        redraw_scatter_agg_orchestrate(self)

    def _sync_scatter_agg_stat_combos(self) -> None:
        """Mirror the new per-axis (channel, metric) state into the legacy
        ``_scatter_agg_stat_x/y_cb`` string combos so any code path that still
        reads them (export_service, save-figure) sees a valid stat string.
        """
        for axis in ("x", "y"):
            stat_cb = getattr(self, f"_scatter_agg_stat_{axis}_cb", None)
            if stat_cb is None:
                continue
            ch_cb = getattr(self, f"_scatter_agg_ch_{axis}_cb", None)
            m_cb = getattr(self, f"_scatter_agg_metric_{axis}_cb", None)
            channel = str(ch_cb.currentText() if ch_cb else "")
            metric_label = str(m_cb.currentText() if m_cb else "Mean Intensity")
            stat_str = self._compose_scatter_agg_stat(channel, metric_label)
            blocked = stat_cb.blockSignals(True)
            try:
                if stat_cb.findText(stat_str) < 0:
                    stat_cb.addItem(stat_str)
                stat_cb.setCurrentText(stat_str)
            finally:
                stat_cb.blockSignals(blocked)

    def _compose_scatter_agg_stat(self, channel_entry: str, metric_label: str) -> str:
        """Build the legacy stat-string from a (channel, metric) pair.

        Mirrors ``parse_statistic`` in scatter_controller: keeps the same
        prefix vocabulary so older callers and the savefile naming still
        recognise the result.
        """
        ratio_key = (getattr(self, "_label_to_channel_key", None) or {}).get(channel_entry)
        if ratio_key and is_ratio_key(ratio_key):
            return f"Mean Ratio {channel_entry}"
        ch_upper = (channel_entry or "").upper()
        if metric_label == "Fraction above threshold":
            return f"Fraction On {ch_upper}"
        if metric_label == "smFISH Count":
            return f"smFISH Count {ch_upper}"
        # All intensity flavours map to the per-cell mean of values above
        # threshold via _aggregate_group; surface a metric-aware suffix so
        # axes/titles reflect the selected property.
        return f"Mean Fluorescence {ch_upper} ({metric_label})"


    def _update_tp_selection_display(self) -> None:
        """Update the aggregate scatter label showing selected timepoints."""
        count = sum(1 for v in self._scatter_agg_tp_selections.values() if v)
        total = len(self._scatter_agg_tp_selections)
        label_text = f"(All {count} selected)" if count == total else f"({count}/{total} selected)"
        if hasattr(self, "_scatter_agg_tp_label") and self._scatter_agg_tp_label is not None:
            self._scatter_agg_tp_label.setText(label_text)

    def _update_scatter_tp_selection_display(self) -> None:
        """Update the per-cell scatter label showing selected timepoints.

        Mirrors _update_tp_selection_display so the two pickers behave
        identically (label format included).
        """
        count = sum(1 for v in self._scatter_tp_selections.values() if v)
        total = len(self._scatter_tp_selections)
        label_text = f"(All {count} selected)" if count == total else f"({count}/{total} selected)"
        if hasattr(self, "_scatter_tp_label") and self._scatter_tp_label is not None:
            self._scatter_tp_label.setText(label_text)




    def _open_scatter_cell_viewer(
        self,
        well_label: str,
        filename: str,
        nuclear_id: str,
        row_idx: int,
    ) -> None:
        """Open or update the scatter cell viewer window."""
        from well_viewer.scatter_callbacks import ScatterCellViewer

        existing = getattr(self, "_scatter_cell_viewer", None)
        if existing is None or not existing.isVisible():
            self._scatter_cell_viewer = ScatterCellViewer(
                self,
                self,
                well_label,
                filename,
                nuclear_id,
                row_idx,
            )
            self._scatter_cell_viewer.show()
        else:
            existing.update_cell(well_label, filename, nuclear_id, row_idx)
            existing.raise_()
            existing.activateWindow()


    def _save_scatter_figure(self) -> None:
        """Save the current Scatter plot figure at high resolution."""
        from well_viewer.plot_orchestrator import save_scatter_figure as _plot_save_scatter_figure_orchestrator

        _plot_save_scatter_figure_orchestrator(self, plot_bg=PLOT_BG)


    def _export_scatter_agg_data(self) -> None:
        """Export aggregate scatter plot data to CSV."""
        from well_viewer.export_service import export_scatter_agg_data as _export_scatter_agg_data
        # Keep the legacy stat combos in sync with the new per-axis
        # (channel, property) state — the export reads from those.
        try:
            self._sync_scatter_agg_stat_combos()
        except Exception:
            pass

        _export_scatter_agg_data(self)

    def _save_scatter_agg_figure(self) -> None:
        """Save the current aggregate scatter plot figure at high resolution."""
        from well_viewer.plot_orchestrator import save_scatter_agg_figure as _plot_save_scatter_agg_figure_orchestrator

        _plot_save_scatter_agg_figure_orchestrator(self, plot_bg=PLOT_BG)


    # ── Legend interaction ────────────────────────────────────────────────────


    # ── Log / status helpers ──────────────────────────────────────────────────

    def _set_status(self, msg: str) -> None:
        self._status_lbl.setText(msg)

    def _toast(self, msg: str, *, kind: str = "info", msec: int = 3500) -> None:
        """Show a transient v2 Toast over the main window.

        ``kind`` matches widgets.toast.Toast: 'info' / 'success' / 'warn' /
        'danger'. Falls back to _set_status if anything goes wrong so the
        message is never silently lost.
        """
        try:
            from widgets.toast import Toast
            Toast.show_message(self, msg, kind=kind, msec=msec)
        except Exception:
            pass
        self._set_status(msg)

    def _show_progress(self, maximum: int, msg: str = "") -> None:
        """Display the progress bar and set its maximum value."""
        self._progress_bar.setValue(0)
        self._progress_bar.setMaximum(max(1, maximum))
        self._progress_bar.setVisible(True)
        if msg:
            self._set_status(msg)
        QApplication.processEvents()

    def _step_progress(self, value: int, msg: str = "") -> None:
        """Advance the progress bar to *value* and repaint immediately."""
        self._progress_bar.setValue(value)
        if msg:
            self._set_status(msg)
        QApplication.processEvents()

    def _hide_progress(self) -> None:
        """Remove the progress bar."""
        self._progress_bar.setVisible(False)
        self._progress_bar.setValue(0)

    def _toggle_log(self) -> None:
        self._log_visible = not self._log_visible
        if self._log_visible:
            self._log_frame.setVisible(True)
            self._log_btn.setText("Log ▼")
        else:
            self._log_frame.setVisible(False)
            self._log_btn.setText("Log ▲")

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
