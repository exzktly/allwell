"""Line-mode batch export panel (also the base class for bar/scatter)."""

from __future__ import annotations

import copy
import csv
import json
import math
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from well_viewer.batch_export._common import (
    PLOT_BG,
    PLOT_SPN,
    WARN,
    get_color,
    BarGroup,
    _bar_render_items,
    _all_fluor_values,
    _extract_well_token,
    _PLATE_COLS,
    _PLATE_ROWS,
    WELL_COLORS,
    apply_ax_style,
    ask_name_dialog,
    btn_card,
    btn_danger,
    btn_primary,
    btn_secondary,
    _clear_layout_helper,
    _groups_with_loaded_wells,
    _logger,
    _CLR_DANGER,
    _CLR_SUCCESS_DARK,
    _CLR_PLACEHOLDER,
    _CLR_DISABLED_WELL,
    _CLR_ERR_BAR,
    _CLR_WHITE,
)
from well_viewer.batch_export.well_grid_button import _WellGridButton


def _attach_fc_row(row: dict, npt: tuple, band_lbl: str,
                   vs_control: bool, vs_t0: bool, control_label: str) -> None:
    """Add the four fold-change columns to a per-timepoint CSV row.

    Used by both the line and bar batch exporters so the column shape stays
    identical across paths.
    """
    fmean = npt[1] if len(npt) > 1 else float("nan")
    fspread = npt[2] if len(npt) > 2 else 0.0
    has_mean = isinstance(fmean, (int, float)) and not (isinstance(fmean, float) and math.isnan(fmean))
    has_spread = isinstance(fspread, (int, float)) and not (isinstance(fspread, float) and math.isnan(fspread))
    row["fold_change_mean"] = f"{fmean:.6f}" if has_mean else ""
    row[f"fold_change_{band_lbl.lower()}"] = f"{fspread:.6f}" if has_spread else ""
    parts: list = []
    if vs_control:
        parts.append("control")
    if vs_t0:
        parts.append("t0")
    row["fold_change_mode"] = "+".join(parts) or "off"
    row["fold_change_control"] = control_label if vs_control else ""


class BatchExportPanel(QWidget):
    """Batch export panel — defines export groups and runs the export."""

    def __init__(
        self,
        app,
        parent: Optional[QWidget] = None,
        *,
        use_sidebar_groups: bool = False,
    ) -> None:
        super().__init__(parent)
        self._app = app
        self._use_sidebar_groups = bool(use_sidebar_groups)

        default_out = str(app._app_state.dataset.data_dir) if app._app_state.dataset.data_dir else ""
        export_prefs = getattr(app, "_export_style_prefs", {}) or {}
        default_fmt = str(export_prefs.get("format", "png")).lower()
        if default_fmt not in {"png", "svg", "eps", "pdf"}:
            default_fmt = "png"
        default_profile = str(export_prefs.get("export_profile", "Custom"))

        self._out_dir_value: str = default_out
        self._fmt_value: str = default_fmt
        self._export_profile_value: str = default_profile
        self._active_grp = -1

        # Panel-local fold-change normalization state. Independent of the
        # plot-tab state so a batch run isn't tied to whatever the on-screen
        # plot has set. Defaults inherited from the app at panel-creation
        # time so opening the panel "feels like" the active plot tab.
        self._fc_vs_control_on: bool = bool(getattr(app, "_fc_vs_control_on", False))
        self._fc_control_label: str = str(getattr(app, "_fc_control_label", "") or "")
        self._fc_vs_t0_on: bool = bool(getattr(app, "_fc_vs_t0_on", False))

        self._groups: List[BarGroup] = self._groups_from_rep_sets()
        self._auto_named_group_ids: set[int] = set()
        self._map_btns: Dict[str, _WellGridButton] = {}
        self._drag_adding = True
        self._drag_visited: set = set()

        self._build_ui()
        if not self._use_sidebar_groups:
            self._refresh_group_list()

    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        if self._use_sidebar_groups:
            right = QWidget()
            right_layout = QVBoxLayout(right)
            right_layout.setContentsMargins(0, 0, 0, 0)
            self._build_output_panel(right_layout)
            outer.addWidget(right, 1)
            return

        left = QWidget()
        left.setMinimumWidth(420)
        left.setMaximumWidth(560)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self._build_group_editor(left_layout)
        outer.addWidget(left)

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFrameShadow(QFrame.Plain)
        outer.addWidget(sep)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self._build_output_panel(right_layout)
        outer.addWidget(right, 1)

    def _build_group_editor(self, layout: QVBoxLayout) -> None:
        hdr1 = QHBoxLayout()
        hdr1.setContentsMargins(8, 4, 8, 4)
        title = QLabel("EXPORT GROUPS")
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        hdr1.addWidget(title)
        hdr1.addStretch(1)
        clear_btn = btn_secondary(None, "Clear All", self._grp_clear_all)
        add_btn = btn_primary(None, "+ Add", self._grp_add)
        hdr1.addWidget(clear_btn)
        hdr1.addWidget(add_btn)
        layout.addLayout(hdr1)

        hdr2 = QHBoxLayout()
        hdr2.setContentsMargins(8, 2, 8, 2)
        qs_btn = QToolButton()
        # Plain dropdown-style button \u2014 no visible arrow indicator.
        # Qt's `QToolButton::menu-indicator` is the small dropdown
        # caret Qt paints when a menu is attached; hide it via QSS.
        # InstantPopup keeps the "click to open menu" behaviour.
        qs_btn.setText("Quick Setup")
        qs_btn.setPopupMode(QToolButton.InstantPopup)
        qs_btn.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")
        qs_menu = QMenu(qs_btn)
        qs_menu.addAction("One group per row  (Row A = all A wells, \u2026)", self._quick_by_row)
        qs_menu.addAction("One group per column  (Col 01 = all col-01 wells, \u2026)", self._quick_by_col)
        qs_btn.setMenu(qs_menu)
        hdr2.addWidget(qs_btn)
        hdr2.addWidget(btn_secondary(None, "Save\u2026", self._save_groups))
        hdr2.addWidget(btn_secondary(None, "Load\u2026", self._load_groups))
        hdr2.addSpacing(12)
        hdr2.addWidget(btn_secondary(None, "Sync from app", self._sync_from_app))
        hdr2.addStretch(1)
        layout.addLayout(hdr2)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        layout.addWidget(sep)

        hint = QLabel("Left-drag wells onto the plate map to add them to the selected group.")
        hint.setWordWrap(True)
        hint.setContentsMargins(6, 2, 6, 2)
        layout.addWidget(hint)

        map_widget = QWidget()
        map_grid = QGridLayout(map_widget)
        map_grid.setSpacing(2)
        map_grid.setContentsMargins(4, 2, 4, 2)
        for ci, col in enumerate(_PLATE_COLS):
            lbl = QLabel(col)
            lbl.setAlignment(Qt.AlignCenter)
            map_grid.addWidget(lbl, 0, ci + 1)
        for ri, row in enumerate(_PLATE_ROWS):
            rlbl = QLabel(row)
            rlbl.setAlignment(Qt.AlignCenter)
            map_grid.addWidget(rlbl, ri + 1, 0)
            for ci, col in enumerate(_PLATE_COLS):
                tok = f"{row}{col}"
                btn = _WellGridButton(tok)
                self._map_btns[tok] = btn
                map_grid.addWidget(btn, ri + 1, ci + 1)

        # Per-button forwarding: enabled buttons consume mouse events, so
        # the parent-level handlers never fire. Forward directly instead.
        for _b in self._map_btns.values():
            _b.setMouseTracking(True)
            _b.mousePressEvent = self._plate_press
            _b.mouseMoveEvent = self._plate_move
            _b.mouseReleaseEvent = self._plate_release
        map_widget.mousePressEvent = self._plate_press
        map_widget.mouseMoveEvent = self._plate_move
        map_widget.mouseReleaseEvent = self._plate_release
        layout.addWidget(map_widget)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        layout.addWidget(sep2)

        self._grp_scroll = QScrollArea()
        self._grp_scroll.setWidgetResizable(True)
        self._grp_inner = QWidget()
        self._grp_inner_layout = QVBoxLayout(self._grp_inner)
        self._grp_inner_layout.setContentsMargins(4, 2, 4, 2)
        self._grp_inner_layout.addStretch(1)
        self._grp_scroll.setWidget(self._grp_inner)
        layout.addWidget(self._grp_scroll, 1)

    def _tok_at(self, global_pos) -> Optional[str]:
        for tok, btn in self._map_btns.items():
            if btn.isVisible() and btn.rect().contains(btn.mapFromGlobal(global_pos)):
                return tok
        return None

    def _plate_press(self, event) -> None:
        tok = self._tok_at(event.globalPosition().toPoint())
        if tok is None or tok not in self._app._app_state.dataset.well_paths:
            return
        grp = self._active_group()
        if grp is None:
            return
        self._drag_adding = tok not in grp.wells
        self._drag_visited = set()
        self._apply_drag(tok)

    def _plate_move(self, event) -> None:
        if not (event.buttons() & Qt.LeftButton):
            return
        tok = self._tok_at(event.globalPosition().toPoint())
        if tok and tok not in self._drag_visited:
            self._apply_drag(tok)

    def _plate_release(self, _event) -> None:
        if self._drag_visited:
            self._refresh_map()
            self._refresh_group_list()
        self._drag_visited = set()

    def _build_output_panel(self, layout: QVBoxLayout) -> None:
        self._build_output_header_and_io(layout)
        self._build_channel_row(layout, attr="_line_channel_cb")

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        layout.addWidget(sep)

        info = QLabel(
            "Each export group produces:\n"
            "  \u2022 One figure with one line per group member\n"
            "    (ReplicateSet = mean of its wells, Solo well = raw)\n"
            "  \u2022 One CSV with per-member time-series data\n\n"
            + ("Groups come from Sample Definitions in the left sidebar.\n"
               if self._use_sidebar_groups
               else "Groups are defined in the left panel.\n")
            + "No statistics are computed across group members.\n"
              "SD/SEM is only computed within a ReplicateSet."
        )
        info.setWordWrap(True)
        info.setContentsMargins(12, 4, 12, 4)
        layout.addWidget(info)

        self._build_run_row(layout)
        layout.addStretch(1)

    def _build_channel_row(self, layout: QVBoxLayout, *, attr: str) -> None:
        """Add a Channel: <combo> + Property: <combo> row.

        The channel combo is populated from ``_all_export_channels()`` so
        ratios are listed alongside real fluor channels. The Property combo
        lets the batch export pick the intensity flavour (Mean / Total /
        Max / Min / Std / smFISH Count) independently of the plot-tab
        ctxbar — and collapses to ``Calculated Val`` when the channel
        resolves to a ratio key.

        The two combos are stashed at ``self.<attr>`` and ``self.<attr +
        "_metric">`` so the panel's ``_run_batch`` can read them.
        """
        channels = self._all_export_channels()
        row = QHBoxLayout()
        row.setContentsMargins(12, 2, 12, 2)
        lbl = QLabel("Channel:")
        f = lbl.font(); f.setBold(True); lbl.setFont(f)
        row.addWidget(lbl)
        cb = QComboBox()
        cb.addItems(channels)
        default = str(self._app._app_state.channel.active_channel or "")
        if default and default in channels:
            cb.setCurrentText(default)
        else:
            cb.setCurrentIndex(0)
        cb.setMinimumWidth(140)
        setattr(self, attr, cb)
        row.addWidget(cb)

        prop_lbl = QLabel("Property:")
        pf = prop_lbl.font(); pf.setBold(True); prop_lbl.setFont(pf)
        row.addWidget(prop_lbl)
        prop_cb = QComboBox()
        prop_cb.setMinimumWidth(140)
        prop_attr = f"{attr}_metric"
        setattr(self, prop_attr, prop_cb)
        row.addWidget(prop_cb)
        # Populate the property combo based on the channel's nature
        # (ratio → only "Calculated Val", else full METRIC_ORDER) and
        # keep it in sync as the user changes channel.
        self._refresh_property_combo_for(attr)
        cb.currentIndexChanged.connect(
            lambda _i, _a=attr: self._refresh_property_combo_for(_a)
        )
        row.addStretch(1)
        layout.addLayout(row)

        # Fold-change controls get their own row below the Channel/Property
        # row — keeping them on the same line caused widget overlap on
        # narrower panel widths.
        fc_row = QHBoxLayout()
        fc_row.setContentsMargins(12, 2, 12, 2)
        self._build_fold_change_widgets(fc_row)
        fc_row.addStretch(1)
        layout.addLayout(fc_row)

    def _all_export_channels(self) -> List[str]:
        """Fluorescence channels + ratio metrics available for batch export."""
        channels = list(self._app._app_state.channel.fluor_channels or [])
        if not channels:
            channels = ["gfp"]
        for r in (self._app._app_state.channel.ratio_metrics or []):
            lbl = r.display_label()
            if lbl and lbl not in channels:
                channels.append(lbl)
        return channels

    def _refresh_property_combo_for(self, channel_attr: str) -> None:
        """Reshape the Property combo that pairs with ``channel_attr``.

        Mirrors :py:meth:`runtime_app.WellViewerApp._populate_metric_combo`:
        ratio channels collapse to a single ``Calculated Val`` entry, real
        channels show the full ``METRIC_ORDER`` set with ``smFISH Count``
        greyed out when the channel isn't an smFISH one.
        """
        from well_viewer.metric_labels import (
            METRIC_ORDER, METRIC_KEY_TO_LABEL, CALCULATED_VAL_LABEL,
        )
        ch_cb = getattr(self, channel_attr, None)
        prop_cb = getattr(self, f"{channel_attr}_metric", None)
        if ch_cb is None or prop_cb is None:
            return
        channel = str(ch_cb.currentText() or "")
        ratio_key = (
            getattr(self._app, "_label_to_channel_key", None) or {}
        ).get(channel)
        from well_viewer.ratio_models import is_ratio_key as _is_ratio_key
        is_ratio = bool(ratio_key and _is_ratio_key(ratio_key))

        prev = str(prop_cb.currentText() or "")
        blocked = prop_cb.blockSignals(True)
        try:
            prop_cb.clear()
            if is_ratio:
                prop_cb.addItem(CALCULATED_VAL_LABEL)
                prop_cb.setCurrentIndex(0)
                prop_cb.setEnabled(True)
                return
            prop_cb.addItems(METRIC_ORDER)
            prop_cb.setEnabled(True)
            if prev in METRIC_ORDER:
                prop_cb.setCurrentText(prev)
            else:
                fallback = METRIC_KEY_TO_LABEL.get(
                    self._app._app_state.channel.active_metric,
                    "Mean Intensity",
                )
                idx = prop_cb.findText(fallback)
                if idx >= 0:
                    prop_cb.setCurrentIndex(idx)
            sm_idx = prop_cb.findText("smFISH Count")
            if sm_idx >= 0:
                model = prop_cb.model()
                item = model.item(sm_idx) if model is not None else None
                if item is not None:
                    smf_ok = channel in (
                        self._app._app_state.channel.smfish_channels or []
                    )
                    item.setEnabled(smf_ok)
        finally:
            prop_cb.blockSignals(blocked)

    def _selected_export_metric_key(self, channel_attr: str) -> str:
        """Return the metric key (``mean_intensity`` / ``total_intensity`` / …)
        for the Property combo paired with ``channel_attr``. Falls back to
        ``app._app_state.channel.active_metric`` when the combo is missing or shows
        ``Calculated Val`` (ratio path — caller resolves the column via
        :py:meth:`_export_val_col_for`)."""
        from well_viewer.metric_labels import METRIC_LABEL_TO_KEY
        prop_cb = getattr(self, f"{channel_attr}_metric", None)
        if prop_cb is None:
            return self._app._app_state.channel.active_metric or "mean_intensity"
        label = str(prop_cb.currentText() or "")
        return METRIC_LABEL_TO_KEY.get(
            label, self._app._app_state.channel.active_metric or "mean_intensity",
        )

    @contextmanager
    def _app_val_col_scope(self, val_col: str):
        """Temporarily override the app's ``_active_val_col``.

        Several core helpers consult ``_active_val_col`` directly instead
        of accepting an explicit ``val_col`` parameter (e.g.
        ``_compute_rep_stats``), so a batch export that wants its
        (Channel, Property) selection to govern the figure has to swap
        the app's value for the duration of the render. Cache entries
        keyed by ``_active_val_col`` (``_stats_cache``) include the
        column in their key, so this swap doesn't pollute the cache.
        """
        app = self._app
        saved = app._app_state.channel.active_val_col
        try:
            app._app_state.channel.active_val_col = val_col
            yield
        finally:
            if saved is not None:
                app._app_state.channel.active_val_col = saved

    def _export_val_col_for(self, channel_attr: str) -> str:
        """Resolve the (Channel, Property) pair to a CSV column / ratio key.

        Routes through ``app._col_for_scatter_axis`` (the same helper the
        per-tab plot uses) so ratio labels become ``ratio:<name>`` and
        real channels become ``<channel>_<metric>``.
        """
        ch_cb = getattr(self, channel_attr, None)
        channel = str(ch_cb.currentText() or "") if ch_cb is not None else (
            self._app._app_state.channel.active_channel or ""
        )
        metric_key = self._selected_export_metric_key(channel_attr)
        if hasattr(self._app, "_col_for_scatter_axis"):
            return self._app._col_for_scatter_axis(channel, metric_key)
        # Fallback for older app versions.
        return f"{channel}_{metric_key}"

    def _resolved_channel_key(self, channel_entry: str) -> str:
        """Resolve a channel-combo entry to the key the rest of the app uses.

        The combo lists real fluor channels by their bare token ("gfp")
        and ratios by their display label ("GFP/MCHERRY"). Cell Gating
        thresholds and fluor gates are keyed by the bare token / full
        ratio key (``ratio:<name>``), not the display label, so any
        threshold or gate lookup needs to translate the display form
        back to its canonical key first — otherwise the lookup misses,
        the helper falls back to its default (50.0 for ThreshFracOn),
        and a ratio batch ends up filtering every cell out.
        """
        mapping = getattr(self._app, "_label_to_channel_key", None) or {}
        resolved = mapping.get(channel_entry)
        if resolved:
            return resolved
        return channel_entry

    def _refresh_channel_combos(self) -> None:
        """Repopulate channel combo-boxes from the current app state.

        Called from ``showEvent`` so that channels loaded after the panel was
        first constructed (e.g. after the user opens a data directory) appear
        without requiring a restart.
        """
        channels = self._all_export_channels()
        for attr in ("_line_channel_cb", "_bar_channel_cb"):
            cb = getattr(self, attr, None)
            if cb is None:
                continue
            prev = cb.currentText()
            cb.blockSignals(True)
            cb.clear()
            cb.addItems(channels)
            if prev in channels:
                cb.setCurrentText(prev)
            elif channels:
                cb.setCurrentIndex(0)
            cb.blockSignals(False)
            # Channel may now resolve to a ratio key — refresh the paired
            # Property combo's item set accordingly.
            self._refresh_property_combo_for(attr)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._refresh_channel_combos()
        if hasattr(self, "_fc_ctrl_combo"):
            self._repopulate_fc_control_combo()

    def _selected_export_channel(self) -> str:
        """Return the channel currently picked in the channel row, falling
        back to ``app._app_state.channel.active_channel`` when the row hasn't been built."""
        for attr in ("_line_channel_cb", "_bar_channel_cb"):
            cb = getattr(self, attr, None)
            if cb is not None:
                text = str(cb.currentText() or "").strip()
                if text:
                    return text
        return str(self._app._app_state.channel.active_channel or "")

    def _build_export_profile_row(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setContentsMargins(12, 2, 12, 2)
        lbl = QLabel("Style Preset:")
        f = lbl.font(); f.setBold(True); lbl.setFont(f)
        row.addWidget(lbl)
        self._profile_combo = QComboBox()
        self._profile_combo.addItems(self._export_profile_names())
        if self._export_profile_value not in self._export_profile_names():
            self._export_profile_value = "Custom"
        self._profile_combo.setCurrentText(self._export_profile_value)
        self._profile_combo.currentTextChanged.connect(self._on_export_profile_selected)
        row.addWidget(self._profile_combo)
        row.addStretch(1)
        layout.addLayout(row)
        self._apply_export_profile_to_prefs(self._export_profile_value)

    def _export_profile_names(self) -> List[str]:
        from well_viewer.figure_export_editor import _get_all_profile_names

        names = _get_all_profile_names(self._app)
        return names or ["Custom"]

    def _refresh_export_profile_choices(self) -> None:
        names = self._export_profile_names()
        self._profile_combo.blockSignals(True)
        self._profile_combo.clear()
        self._profile_combo.addItems(names)
        current = self._export_profile_value if self._export_profile_value in names else "Custom"
        self._profile_combo.setCurrentText(current)
        self._profile_combo.blockSignals(False)

    def _apply_export_profile_to_prefs(self, profile_name: str) -> None:
        from well_viewer.figure_export_editor import (
            EXPORT_PROFILES,
            _ensure_custom_export_profiles,
            _ensure_export_style_prefs,
        )

        name = str(profile_name or "Custom")
        prefs = _ensure_export_style_prefs(self._app)
        custom_profiles = _ensure_custom_export_profiles(self._app)
        overrides = EXPORT_PROFILES.get(name) or custom_profiles.get(name) or {}
        prefs["export_profile"] = name
        for key, value in overrides.items():
            prefs[key] = value
        fmt = str(prefs.get("format", self._fmt_cb.currentText() or "png")).lower()
        if fmt in {"png", "svg", "eps", "pdf"}:
            self._fmt_cb.setCurrentText(fmt)
            self._on_fmt_change(fmt)

    def _on_export_profile_selected(self, text: str) -> None:
        self._export_profile_value = text
        self._apply_export_profile_to_prefs(text)

    def _on_fmt_change(self, text: str = None) -> None:
        if text is None:
            text = self._fmt_cb.currentText()
        hints = {"png": "300 DPI raster", "svg": "vector \u2014 text editable",
                 "eps": "vector \u2014 text editable", "pdf": "vector"}
        self._fmt_hint.setText(hints.get(text, ""))

    def _browse_out_dir(self) -> None:
        current = self._out_dir_edit.text() or ""
        chosen = QFileDialog.getExistingDirectory(self, "Select output directory", current)
        if chosen:
            self._out_dir_edit.setText(chosen)

    @staticmethod
    def _add_bold_label(layout, text: str) -> QLabel:
        lbl = QLabel(text)
        f = lbl.font(); f.setBold(True); lbl.setFont(f)
        layout.addWidget(lbl)
        return lbl

    def _build_output_header_and_io(
        self, layout: QVBoxLayout, *, title: str = "OUTPUT SETTINGS",
    ) -> None:
        """Common OUTPUT header + Folder + Format + Profile rows."""
        hdr = QHBoxLayout()
        hdr.setContentsMargins(12, 4, 12, 4)
        self._add_bold_label(hdr, title)
        hdr.addStretch(1)
        layout.addLayout(hdr)

        out_row = QHBoxLayout()
        out_row.setContentsMargins(12, 6, 12, 6)
        self._add_bold_label(out_row, "Folder:")
        self._out_dir_edit = QLineEdit(self._out_dir_value)
        self._out_dir_edit.setMinimumWidth(240)
        out_row.addWidget(self._out_dir_edit, 1)
        out_row.addWidget(btn_secondary(None, "Browse\u2026", self._browse_out_dir))
        layout.addLayout(out_row)

        fmt_row = QHBoxLayout()
        fmt_row.setContentsMargins(12, 2, 12, 2)
        self._add_bold_label(fmt_row, "Format:")
        self._fmt_cb = QComboBox()
        self._fmt_cb.addItems(["png", "svg", "eps", "pdf"])
        self._fmt_cb.setCurrentText(self._fmt_value)
        self._fmt_cb.currentTextChanged.connect(self._on_fmt_change)
        fmt_row.addWidget(self._fmt_cb)
        self._fmt_hint = QLabel("300 DPI raster")
        fmt_row.addWidget(self._fmt_hint)
        fmt_row.addStretch(1)
        layout.addLayout(fmt_row)

        self._build_export_profile_row(layout)

    def _build_run_row(
        self, layout: QVBoxLayout, *, button_text: str = "▶  Run Batch Export",
    ) -> None:
        run_row = QHBoxLayout()
        run_row.setContentsMargins(12, 8, 12, 8)
        self._run_btn = QPushButton(button_text)
        self._run_btn.setProperty("variant", "primary")
        self._run_btn.clicked.connect(self._run_batch)
        run_row.addWidget(self._run_btn)
        self._prog_lbl = QLabel("")
        run_row.addWidget(self._prog_lbl)
        self._prog_bar = QProgressBar()
        self._prog_bar.setFixedWidth(180)
        self._prog_bar.setVisible(False)
        run_row.addWidget(self._prog_bar)
        run_row.addStretch(1)
        layout.addLayout(run_row)

    def _tp_select_all(self) -> None:
        for i in range(self._tp_lb.count()):
            self._tp_lb.item(i).setSelected(True)

    def _tp_clear_all(self) -> None:
        self._tp_lb.clearSelection()

    def _selected_tps(self) -> List[str]:
        return [it.text() for it in self._tp_lb.selectedItems()]

    _FC_NONE_LABEL = "—"
    _FC_T0_LABEL = "t0 (first timepoint)"

    def _build_fold_change_widgets(self, row: QHBoxLayout) -> None:
        """Append the Control + Baseline dropdowns to *row*.

        Two parallel combos — Control selects the well / rep-set whose
        mean each bar is divided by, Baseline picks a per-member
        reference timepoint. ``—`` in either combo disables that axis.
        """
        fc_lbl = QLabel("Fold change:")
        f = fc_lbl.font(); f.setBold(True); fc_lbl.setFont(f)
        row.addWidget(fc_lbl)

        row.addWidget(QLabel("Control"))
        self._fc_ctrl_combo = QComboBox()
        self._fc_ctrl_combo.setMinimumWidth(160)
        self._fc_ctrl_combo.setToolTip(
            "Divide each bar / curve by the picked well or replicate "
            "set's mean at the same timepoint. — disables this axis."
        )
        self._repopulate_fc_control_combo()
        row.addWidget(self._fc_ctrl_combo)

        row.addWidget(QLabel("Baseline"))
        self._fc_baseline_combo = QComboBox()
        self._fc_baseline_combo.setMinimumWidth(160)
        self._fc_baseline_combo.setToolTip(
            "Divide each bar / curve by its own value at the reference "
            "timepoint. — disables this axis."
        )
        self._fc_baseline_combo.addItem(self._FC_NONE_LABEL)
        self._fc_baseline_combo.addItem(self._FC_T0_LABEL)
        self._fc_baseline_combo.setCurrentIndex(1 if self._fc_vs_t0_on else 0)
        row.addWidget(self._fc_baseline_combo)

        def _on_ctrl_change(_i: int) -> None:
            text = self._fc_ctrl_combo.currentText()
            if text == self._FC_NONE_LABEL or not text:
                self._fc_vs_control_on = False
                self._fc_control_label = ""
            else:
                self._fc_vs_control_on = True
                self._fc_control_label = text

        def _on_baseline_change(_i: int) -> None:
            self._fc_vs_t0_on = (
                self._fc_baseline_combo.currentText() == self._FC_T0_LABEL
            )

        self._fc_ctrl_combo.currentIndexChanged.connect(_on_ctrl_change)
        self._fc_baseline_combo.currentIndexChanged.connect(_on_baseline_change)

    def _repopulate_fc_control_combo(self) -> None:
        combo = getattr(self, "_fc_ctrl_combo", None)
        if combo is None:
            return
        members: List[str] = []
        seen: set = set()
        for grp in self._groups_for_export() if hasattr(self, "_groups_for_export") else self._groups:
            for rset in grp.members:
                if rset.name and rset.name not in seen:
                    members.append(rset.name)
                    seen.add(rset.name)
            for w in grp.solo_wells:
                if w and w not in seen:
                    members.append(w)
                    seen.add(w)
        # Fall back to plate-loaded wells when no groups are defined yet —
        # users may want to pick a control before they've finished building
        # the group editor.
        if not members:
            for w in sorted((self._app._app_state.dataset.well_paths or {})):
                members.append(w)
        saved = self._fc_control_label
        blocked = combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem(self._FC_NONE_LABEL)
            for m in members:
                combo.addItem(m)
            if saved:
                idx = combo.findText(saved)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
        finally:
            combo.blockSignals(blocked)

    def _fc_state(self) -> tuple:
        """Return ``(vs_control_on, control_label, vs_t0_on)`` for this panel."""
        return (
            bool(self._fc_vs_control_on),
            str(self._fc_control_label or ""),
            bool(self._fc_vs_t0_on),
        )

    def _fc_active(self) -> bool:
        v, _, t0 = self._fc_state()
        return v or t0

    def _build_timepoints_section(self, layout: QVBoxLayout) -> None:
        """TIMEPOINTS header + multi-select list (shared by Bar/Scatter)."""
        tp_hdr = QHBoxLayout()
        tp_hdr.setContentsMargins(12, 0, 12, 2)
        self._add_bold_label(tp_hdr, "TIMEPOINTS")
        tp_hdr.addStretch(1)
        tp_hdr.addWidget(btn_secondary(None, "All", self._tp_select_all))
        tp_hdr.addWidget(btn_secondary(None, "None", self._tp_clear_all))
        layout.addLayout(tp_hdr)

        self._tp_lb = QListWidget()
        self._tp_lb.setSelectionMode(QAbstractItemView.MultiSelection)
        self._tp_lb.setMinimumHeight(120)
        layout.addWidget(self._tp_lb, 1)

    def _active_group(self) -> Optional[BarGroup]:
        if 0 <= self._active_grp < len(self._groups):
            return self._groups[self._active_grp]
        return None

    def _default_group_name(self, grp: BarGroup) -> str:
        labels = [r.name for r in grp.members]
        labels.extend(grp.solo_wells)
        if not labels:
            return "New Group"
        return ", ".join(labels)

    def _refresh_auto_group_name(self, grp: Optional[BarGroup]) -> None:
        if grp is None:
            return
        if id(grp) in self._auto_named_group_ids:
            grp.name = self._default_group_name(grp)

    def _apply_drag(self, tok: str) -> None:
        if tok in self._drag_visited:
            return
        self._drag_visited.add(tok)
        grp = self._active_group()
        if grp is None or tok not in self._app._app_state.dataset.well_paths:
            return
        rset = next((r for r in self._app._rep_sets_loaded() if tok in r.wells), None)
        if rset is not None:
            if self._drag_adding:
                if rset not in grp.members:
                    grp.members.append(rset)
            else:
                if rset in grp.members:
                    grp.members.remove(rset)
        else:
            if self._drag_adding:
                if tok not in grp.solo_wells:
                    grp.solo_wells.append(tok)
            else:
                if tok in grp.solo_wells:
                    grp.solo_wells.remove(tok)
        self._refresh_auto_group_name(grp)
        self._refresh_single_btn(tok)

    def _refresh_single_btn(self, tok: str) -> None:
        btn = self._map_btns.get(tok)
        if btn is None or tok not in self._app._app_state.dataset.well_paths:
            return
        for g in self._groups:
            if tok in g.wells:
                c = self._app._rank_color_rset(g)  # decision #1: colour by well-position rank
                btn.set_colors(c, _CLR_WHITE)
                return
        btn.set_colors(get_color("BG_CELL"), get_color("TXT_PRI"))

    def _refresh_map(self) -> None:
        avail = set(self._app._app_state.dataset.well_paths.keys())
        tok_color: Dict[str, str] = {}
        for grp in self._groups:
            c = self._app._rank_color_rset(grp)  # decision #1: colour by well-position rank
            for w in grp.wells:
                tok_color[w] = c   # last group wins — matches the main-viewer plates
        active_wells: set = set()
        grp = self._active_group()
        if grp:
            for w in grp.wells:
                active_wells.add(w)
        for tok, btn in self._map_btns.items():
            if tok not in avail:
                btn.set_colors(get_color("CLR_AVAIL_WELL"), get_color("TXT_MUT"), enabled=False)
            elif tok in tok_color:
                btn.set_colors(tok_color[tok], _CLR_WHITE, active=(tok in active_wells))
            else:
                btn.set_colors(get_color("BG_CELL"), get_color("TXT_PRI"))

    def _clear_layout(self, layout) -> None:
        _clear_layout_helper(layout)

    def _refresh_group_list(self) -> None:
        # Remove everything except the trailing stretch
        while self._grp_inner_layout.count() > 1:
            item = self._grp_inner_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            else:
                inner = item.layout()
                if inner is not None:
                    self._clear_layout(inner)

        if not self._groups:
            empty = QLabel("No export groups.  Click + Add or use Quick Setup.")
            empty.setContentsMargins(8, 8, 8, 8)
            self._grp_inner_layout.insertWidget(0, empty)
            self._refresh_map()
            return

        for gi, grp in enumerate(self._groups):
            card = self._build_group_card(gi, grp)
            self._grp_inner_layout.insertWidget(gi, card)

        self._refresh_map()

    def _build_group_card(self, gi: int, grp: BarGroup) -> QWidget:
        is_sel = (gi == self._active_grp)
        color = self._app._rank_color_rset(grp)  # decision #1: colour by well-position rank
        card = QFrame()
        card.setFrameShape(QFrame.StyledPanel)
        if is_sel:
            card.setStyleSheet(
                f"QFrame {{ border: 2px solid {color}; background: {get_color('BG_CELL')}; }}"
            )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(6, 4, 6, 4)

        hdr = QHBoxLayout()
        dot = QLabel("\u25cf")
        dot.setStyleSheet(f"color: {color};")
        hdr.addWidget(dot)
        name_lbl = QLabel(grp.name)
        f = name_lbl.font(); f.setBold(True); name_lbl.setFont(f)
        hdr.addWidget(name_lbl)
        n_mem, n_sol = len(grp.members), len(grp.solo_wells)
        parts = []
        if n_mem:
            parts.append(f"{n_mem} set{'s' if n_mem != 1 else ''}")
        if n_sol:
            parts.append(f"{n_sol} solo well{'s' if n_sol != 1 else ''}")
        if not parts:
            parts = ["empty"]
        summary = QLabel(f"  ({', '.join(parts)})")
        hdr.addWidget(summary)
        hdr.addStretch(1)
        hdr.addWidget(btn_card(None, "Rename", lambda i=gi: self._grp_rename(i)))
        hdr.addWidget(btn_card(None, "Clear", lambda i=gi: self._grp_clear(i)))
        hdr.addWidget(btn_danger(None, "\u2715", lambda i=gi: self._grp_delete(i)))
        card_layout.addLayout(hdr)

        if grp.members or grp.solo_wells:
            for rset in grp.members:
                mrow = QHBoxLayout()
                name_tag = QLabel(f"[{rset.name}]")
                name_tag.setStyleSheet(f"color: {color};")
                mrow.addWidget(name_tag)
                for w in rset.wells:
                    chip = QLabel(w)
                    chip.setStyleSheet(f"background: {color}; color: {_CLR_WHITE}; padding: 1px 3px;")
                    mrow.addWidget(chip)
                if is_sel:
                    mrow.addWidget(btn_danger(None, "\u2212", lambda g=gi, r=rset: self._grp_remove_member(g, r)))
                mrow.addStretch(1)
                card_layout.addLayout(mrow)
            for w in grp.solo_wells:
                srow = QHBoxLayout()
                s_lbl = QLabel(f"[solo] {w}")
                s_lbl.setStyleSheet(f"color: {color};")
                srow.addWidget(s_lbl)
                if is_sel:
                    srow.addWidget(btn_danger(None, "\u2212", lambda g=gi, wl=w: self._grp_remove_solo(g, wl)))
                srow.addStretch(1)
                card_layout.addLayout(srow)

        def _on_click(_event, _idx=gi):
            self._grp_select(_idx)
        card.mousePressEvent = _on_click
        return card

    def _grp_select(self, idx: int) -> None:
        self._active_grp = idx
        self._refresh_group_list()

    def _grp_add(self) -> None:
        grp = BarGroup("New Group")
        self._groups.append(grp)
        self._auto_named_group_ids.add(id(grp))
        self._refresh_auto_group_name(grp)
        self._active_grp = len(self._groups) - 1
        self._refresh_group_list()

    def _grp_rename(self, idx: int) -> None:
        if 0 <= idx < len(self._groups):
            name = ask_name_dialog(
                self, title="Rename group", prompt="Group name:",
                default=self._groups[idx].name,
            )
            if name:
                grp = self._groups[idx]
                grp.name = name
                self._auto_named_group_ids.discard(id(grp))
                self._refresh_group_list()

    def _grp_clear(self, idx: int) -> None:
        if 0 <= idx < len(self._groups):
            grp = self._groups[idx]
            grp.members.clear()
            grp.solo_wells.clear()
            self._refresh_auto_group_name(grp)
            self._refresh_group_list()

    def _grp_delete(self, idx: int) -> None:
        if 0 <= idx < len(self._groups):
            grp = self._groups.pop(idx)
            self._auto_named_group_ids.discard(id(grp))
            self._active_grp = min(self._active_grp, len(self._groups) - 1)
            self._refresh_group_list()

    def _grp_clear_all(self) -> None:
        if not self._groups:
            return
        ret = QMessageBox.question(
            self, "Clear all groups?",
            f"Remove all {len(self._groups)} group(s)?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if ret == QMessageBox.Yes:
            self._groups.clear()
            self._auto_named_group_ids.clear()
            self._active_grp = -1
            self._refresh_group_list()

    def _grp_remove_member(self, grp_idx: int, rset) -> None:
        if 0 <= grp_idx < len(self._groups):
            grp = self._groups[grp_idx]
            if rset in grp.members:
                grp.members.remove(rset)
            self._refresh_auto_group_name(grp)
            self._refresh_group_list()

    def _grp_remove_solo(self, grp_idx: int, well: str) -> None:
        if 0 <= grp_idx < len(self._groups):
            grp = self._groups[grp_idx]
            if well in grp.solo_wells:
                grp.solo_wells.remove(well)
            self._refresh_auto_group_name(grp)
            self._refresh_group_list()

    def _quick_by_row(self) -> None:
        self._groups.clear()
        self._auto_named_group_ids.clear()
        self._active_grp = -1
        for row_ltr in _PLATE_ROWS:
            row_rsets = [r for r in self._app._rep_sets_loaded()
                         if any(w[0].upper() == row_ltr for w in r.wells)]
            assigned_wells = {w for r in row_rsets for w in r.wells}
            row_solos = [tok for tok in self._app._app_state.dataset.well_paths
                         if tok[0].upper() == row_ltr and tok not in assigned_wells]
            if not row_rsets and not row_solos:
                continue
            grp = BarGroup(f"Row {row_ltr}", members=row_rsets, solo_wells=row_solos)
            self._groups.append(grp)
        if self._groups:
            self._active_grp = 0
        self._refresh_group_list()

    def _quick_by_col(self) -> None:
        self._groups.clear()
        self._auto_named_group_ids.clear()
        self._active_grp = -1
        for col in _PLATE_COLS:
            col_rsets = [r for r in self._app._rep_sets_loaded()
                         if any(w[1:] == col for w in r.wells)]
            assigned_wells = {w for r in col_rsets for w in r.wells}
            col_solos = [tok for tok in self._app._app_state.dataset.well_paths
                         if tok[1:] == col and tok not in assigned_wells]
            if not col_rsets and not col_solos:
                continue
            grp = BarGroup(f"Col {col}", members=col_rsets, solo_wells=col_solos)
            self._groups.append(grp)
        if self._groups:
            self._active_grp = 0
        self._refresh_group_list()

    def _sync_from_app(self) -> None:
        self._groups = self._groups_from_rep_sets()
        self._auto_named_group_ids.clear()
        self._active_grp = 0 if self._groups else -1
        self._refresh_group_list()

    def _groups_from_rep_sets(self) -> List[BarGroup]:
        return [BarGroup(r.name, members=[r]) for r in self._app._rep_sets_loaded()]

    def _save_groups(self) -> None:
        if not self._groups:
            QMessageBox.warning(self, "Nothing to save", "No groups defined yet.")
            return
        rep_set_names: List[str] = []
        seen_sets: set = set()
        grp_list = []
        for grp in self._groups:
            members_names = []
            for rset in grp.members:
                members_names.append(rset.name)
                if rset.name not in seen_sets:
                    seen_sets.add(rset.name)
                    rep_set_names.append(rset.name)
            grp_list.append({
                "name": grp.name,
                "hidden": grp.hidden,
                "members": members_names,
                "solo_wells": list(grp.solo_wells),
            })
        rep_list = []
        for rset in self._app._rep_sets_loaded():
            rep_list.append({"name": rset.name, "wells": list(rset.wells)})
        data = {"rep_sets": rep_list, "groups": grp_list}

        path_str, _ = QFileDialog.getSaveFileName(
            self, "Save export groups",
            "export_groups.json",
            "JSON (*.json);;All files (*.*)",
        )
        if not path_str:
            return
        try:
            with open(path_str, "w") as fh:
                json.dump(data, fh, indent=2)
            self._prog_lbl.setText(f"Saved \u2192 {Path(path_str).name}")
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))

    def _load_groups(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Load export groups", "",
            "JSON (*.json);;All files (*.*)",
        )
        if not path_str:
            return
        try:
            with open(path_str) as fh:
                data = json.load(fh)
        except Exception as exc:
            QMessageBox.critical(self, "Load failed", str(exc))
            return

        def _norm(tok: str) -> str:
            tok = tok.strip().upper()
            m = re.match(r"^([A-H])(\d{1,2})$", tok, re.I)
            return f"{m.group(1).upper()}{int(m.group(2)):02d}" if m else tok

        def _valid_tok(raw: str) -> Optional[str]:
            n = _norm(raw)
            return n if n in self._app._app_state.dataset.well_paths else None

        rep_by_name = {r.name: r for r in self._app._rep_sets_loaded()}

        new_groups: List[BarGroup] = []
        if isinstance(data, dict):
            for item in data.get("groups", []):
                grp = BarGroup(item.get("name", "Group"),
                               hidden=bool(item.get("hidden", False)))
                for rname in item.get("members", []):
                    if rname in rep_by_name:
                        grp.members.append(rep_by_name[rname])
                for raw_tok in item.get("solo_wells", []):
                    n = _valid_tok(raw_tok)
                    if n:
                        grp.solo_wells.append(n)
                new_groups.append(grp)
        elif isinstance(data, list):
            for item in data:
                name = str(item.get("name", "Group"))
                grp = BarGroup(name, hidden=bool(item.get("hidden", False)))
                for rdata in item.get("replicates", []):
                    rname = rdata.get("name", "R")
                    if rname in rep_by_name:
                        grp.members.append(rep_by_name[rname])
                new_groups.append(grp)

        if not new_groups:
            QMessageBox.warning(
                self, "No groups loaded",
                "The file contained no groups that could be matched to the "
                "currently loaded wells and replicate sets.",
            )
            return

        self._groups = new_groups
        self._auto_named_group_ids.clear()
        self._active_grp = 0
        self._refresh_group_list()
        self._prog_lbl.setText(f"Loaded {len(self._groups)} group(s) from {Path(path_str).name}")

    def _resolve_out_dir(self) -> Optional[Path]:
        val = self._out_dir_edit.text().strip()
        if val:
            p = Path(val)
            try:
                p.mkdir(parents=True, exist_ok=True)
                return p
            except OSError as exc:
                QMessageBox.critical(self, "Output directory error",
                                     f"Cannot create:\n{p}\n{exc}")
                return None
        d = self._app._app_state.dataset.data_dir
        if d and d.is_dir():
            return d
        QMessageBox.critical(self, "No output directory",
                             "Load data or choose an output folder.")
        return None

    def _run_batch_jobs(
        self, *, jobs: list, progress_text_fn, run_job_fn,
        success_text: str, status_text: str,
    ) -> None:
        from PySide6.QtWidgets import QApplication

        self._prog_bar.setVisible(True)
        self._prog_bar.setMaximum(max(len(jobs), 1))
        self._prog_bar.setValue(0)
        self._run_btn.setEnabled(False)
        errors: List[str] = []
        try:
            for step, job in enumerate(jobs, 1):
                self._prog_lbl.setText(progress_text_fn(job, step, len(jobs)))
                self._prog_bar.setValue(step - 1)
                QApplication.processEvents()
                err = run_job_fn(job)
                if err:
                    errors.append(err)
            self._prog_bar.setValue(len(jobs))
        finally:
            self._run_btn.setEnabled(True)
            self._prog_bar.setVisible(False)

        if errors:
            self._prog_lbl.setText(f"Done with {len(errors)} error(s). See log.")
            self._prog_lbl.setStyleSheet(f"color: {_CLR_DANGER};")
            for err in errors:
                _logger.error("Batch export error: %s", err)
            return
        self._prog_lbl.setText(success_text)
        self._prog_lbl.setStyleSheet(f"color: {_CLR_SUCCESS_DARK};")
        self._app._set_status(status_text)

    def _save_figure(self, fig, fig_path: Path, fmt: str) -> None:
        import matplotlib as _mpl
        import matplotlib.pyplot as _plt
        from well_viewer.figure_export_editor import (
            _ensure_export_style_prefs,
            apply_export_style_prefs,
        )

        apply_export_style_prefs(fig, _ensure_export_style_prefs(self._app))

        orig_svg = _mpl.rcParams.get("svg.fonttype", "path")
        orig_ps = _mpl.rcParams.get("ps.fonttype", 3)
        try:
            if fmt == "svg":
                _mpl.rcParams["svg.fonttype"] = "none"
            elif fmt == "eps":
                _mpl.rcParams["ps.fonttype"] = 42
            kw = dict(bbox_inches="tight", facecolor=PLOT_BG, format=fmt)
            if fmt == "png":
                kw["dpi"] = 300
            fig.savefig(str(fig_path), **kw)
        finally:
            _mpl.rcParams["svg.fonttype"] = orig_svg
            _mpl.rcParams["ps.fonttype"] = orig_ps
            _plt.close(fig)

    def _export_scatter_figure(
        self,
        draw: Callable[[object], None],
        *,
        xlabel: str,
        ylabel: str,
        title: str,
        fig_path: Path,
        fmt: str,
        legend_fontsize: int = 8,
    ) -> None:
        """Render a standard 8x6 scatter/errorbar figure and save it.

        ``draw(ax)`` is the caller's inner loop that adds series to the axes;
        axis labels, title, grid, legend, and export are applied here.
        """
        from matplotlib.figure import Figure as _Figure
        fig = _Figure(figsize=(8, 6), dpi=300, facecolor=PLOT_BG)
        ax = fig.add_subplot(1, 1, 1)
        draw(ax)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(True, alpha=0.3)
        if ax.get_legend_handles_labels()[0]:
            ax.legend(loc="best", fontsize=legend_fontsize, framealpha=0.0, facecolor="none")
        self._save_figure(fig, fig_path, fmt)

    def _groups_for_export(self) -> List[BarGroup]:
        if self._use_sidebar_groups:
            return self._groups_from_rep_sets()
        return list(self._groups)

    def _run_batch(self) -> None:
        groups_with_data = [g for g in self._groups_for_export()
                            if any(w in self._app._app_state.dataset.well_paths for w in g.wells)]
        if not groups_with_data:
            msg = (
                "Define at least one non-empty group in Sample Definitions."
                if self._use_sidebar_groups
                else "Define at least one non-empty group."
            )
            QMessageBox.warning(self, "No groups", msg)
            return
        self._log_sample_definitions_snapshot(groups_with_data)
        out_dir = self._resolve_out_dir()
        if out_dir is None:
            return

        _ch_selected = self._selected_export_channel() or self._app._app_state.channel.active_channel
        # ``_get_thresh_frac_on`` looks up by the canonical key (bare
        # channel token or ``ratio:<name>``), not by the dropdown's
        # display label \u2014 translate ratio labels first so the user's
        # configured ThreshFracOn is honoured instead of the 50.0
        # default that empties out the ratio plot.
        threshold = self._app._get_thresh_frac_on(
            self._resolved_channel_key(_ch_selected)
        )
        use_sem = self._app._use_sem
        band_lbl = "SEM" if use_sem else "SD"
        fmt = self._fmt_cb.currentText()

        def _progress(grp: BarGroup, step: int, total: int) -> str:
            return f"Exporting '{grp.name}' ({step}/{total})\u2026"

        fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0 = self._fc_state()
        fc_active = fc_vs_ctrl or fc_vs_t0

        def _run_group(grp: BarGroup) -> Optional[str]:
            safe = re.sub(r"[^A-Za-z0-9_\-]", "_", grp.name)
            csv_path = out_dir / f"batch_{safe}.csv"
            fig_path = out_dir / f"batch_{safe}.{fmt}"

            try:
                from well_viewer.batch_export._line_series_runner import (
                    collect_line_series_for_group,
                )
                from well_viewer.export_service import (
                    _well_labels_map,
                    line_metric_fieldnames, line_metric_row,
                    well_name_for, well_names_joined,
                )
                # Resolve the panel's (Channel, Property) selection into a
                # column / ratio key — independent of whatever the plot-tab
                # ctxbar has picked.
                _val_col = self._export_val_col_for("_line_channel_cb")
                _ch = _ch_selected
                _metric = self._selected_export_metric_key("_line_channel_cb")
                _cell_area_threshold = self._app._get_cell_area_threshold()
                _fluor_gates = self._app._get_all_fluor_gates()
                _well_labels = _well_labels_map(self._app)
                panel_fc_state = (fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0)
                # Two passes through the same shared collector: raw values
                # for the standard mean / spread columns, fold-change
                # values for the additive ``fold_change_*`` columns when
                # active. Mirrors the bar batch CSV's additive schema.
                series_raw = collect_line_series_for_group(
                    self._app, grp,
                    threshold=threshold, use_sem=use_sem,
                    val_col=_val_col,
                    cell_area_threshold=_cell_area_threshold,
                    fluor_gates=_fluor_gates,
                    fc_state=(False, "", False),
                )
                if fc_active:
                    series_fc = collect_line_series_for_group(
                        self._app, grp,
                        threshold=threshold, use_sem=use_sem,
                        val_col=_val_col,
                        cell_area_threshold=_cell_area_threshold,
                        fluor_gates=_fluor_gates,
                        fc_state=panel_fc_state,
                    )
                    fc_points_by_key = {
                        s.key: s.points for s in series_fc
                    }
                else:
                    fc_points_by_key = {}
                rows_out: List[dict] = []
                for series in series_raw:
                    if series.kind == "repset":
                        wells_str = ";".join(series.wells)
                        well_names_str = well_names_joined(wells_str, _well_labels)
                        member_type = "replicate_set"
                        n_wells = len(series.wells)
                    else:
                        wells_str = series.wells[0] if series.wells else series.key
                        well_names_str = well_name_for(wells_str, _well_labels)
                        member_type = "solo_well"
                        n_wells = 1
                    fc_pts = fc_points_by_key.get(series.key, [])
                    for i, pt in enumerate(series.points):
                        row = {
                            "group": grp.name,
                            "member": series.key,
                            "member_type": member_type,
                            "wells": wells_str,
                            "well_names": well_names_str,
                            "n_wells": n_wells,
                        }
                        row.update(line_metric_row(
                            pt.as_aggpoint(), ch=_ch, metric=_metric,
                            threshold=threshold, band_lbl=band_lbl,
                        ))
                        if fc_active and i < len(fc_pts):
                            _attach_fc_row(
                                row, fc_pts[i].as_aggpoint(), band_lbl,
                                fc_vs_ctrl, fc_vs_t0, fc_ctrl_lbl,
                            )
                        rows_out.append(row)
                if rows_out:
                    fieldnames = (
                        ["group", "member", "member_type",
                         "wells", "well_names", "n_wells"]
                        + line_metric_fieldnames(_ch, _metric, band_lbl)
                    )
                    if fc_active:
                        fieldnames += [
                            "fold_change_mean",
                            f"fold_change_{band_lbl.lower()}",
                            "fold_change_mode",
                            "fold_change_control",
                        ]
                    with open(csv_path, "w", newline="") as fh:
                        writer = csv.DictWriter(fh, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(rows_out)
            except OSError as exc:
                return f"{grp.name} CSV: {exc}"

            try:
                # ``_render_group_figure`` calls helpers (notably
                # ``_compute_rep_stats``) that read ``app._app_state.channel.active_val_col``
                # directly. Swap it for the duration of the render so a
                # batch export against a ratio column or a non-MFI
                # property draws the correct curves.
                with self._app_val_col_scope(_val_col):
                    fig = self._render_group_figure(
                        grp, threshold, use_sem, band_lbl,
                        fc_control_means=fc_control_means,
                        fc_vs_t0=fc_vs_t0,
                    )
                self._save_figure(fig, fig_path, fmt)
            except Exception as exc:
                return f"{grp.name} figure: {exc}"

            _logger.info("Batch: wrote %s and %s", csv_path.name, fig_path.name)
            return None

        self._run_batch_jobs(
            jobs=groups_with_data,
            progress_text_fn=_progress,
            run_job_fn=_run_group,
            success_text=f"\u2713 {len(groups_with_data)} group(s) \u2192 {out_dir.name}/",
            status_text=f"Batch export: {len(groups_with_data)} group(s) \u2192 {out_dir}",
        )

    def _log_sample_definitions_snapshot(self, groups_for_export: List[BarGroup]) -> None:
        sels = list(self._app._app_state.selection.selections or [])
        sel_summary = [f"{s.get('name', '?')}={len(s.get('wells') or [])}w" for s in sels]
        export_summary = [f"{g.name}={len(g.wells)}w" for g in groups_for_export]
        _logger.info(
            "Run Batch Export clicked | selections=%s | groups_for_export=%s",
            sel_summary, export_summary,
        )

    def _render_group_figure(
        self, grp: BarGroup, threshold: float, use_sem: bool, band_lbl: str,
        *,
        fc_control_means: Optional[Dict[float, float]] = None,
        fc_vs_t0: bool = False,
    ):
        from matplotlib.figure import Figure as _Figure
        from well_viewer import fold_change as _fc

        fc_active = bool(fc_control_means) or bool(fc_vs_t0)
        fig = _Figure(figsize=(10, 11), dpi=300, facecolor=PLOT_BG)
        ax_mean = fig.add_subplot(3, 1, 1)
        ax_frac = fig.add_subplot(3, 1, 2, sharex=ax_mean)
        ax_cdf = fig.add_subplot(3, 1, 3)
        fig.subplots_adjust(hspace=0.55, top=0.92, bottom=0.07, left=0.13, right=0.97)
        fig.suptitle(grp.name, fontsize=11, fontweight="bold", color=get_color("PLOT_TXT"), y=0.97)

        legend_kw = dict(fontsize=7, framealpha=0.0, facecolor="none",
                         edgecolor=PLOT_SPN, labelcolor=get_color("PLOT_TXT"))
        _ch = (self._selected_export_channel() or self._app._app_state.channel.active_channel).upper()
        from well_viewer.metric_labels import METRIC_KEY_TO_LABEL as _MLB
        _metric_key = self._selected_export_metric_key("_line_channel_cb")
        _metric_label = _MLB.get(_metric_key, "Mean Intensity")
        _fc_suffix = _fc.fold_change_suffix(
            bool(fc_control_means), bool(fc_vs_t0), self._fc_control_label,
        ) if fc_active else ""
        apply_ax_style(ax_mean, f"{_ch} {_metric_label} (above threshold) \u00b1 {band_lbl}{_fc_suffix}", f"{_ch} {_metric_label}{_fc_suffix}")
        apply_ax_style(ax_frac, "Fraction of Cells Above Threshold", "Fraction")
        apply_ax_style(ax_cdf, f"{_ch} {_metric_label} CDF", "Cumulative fraction")
        ax_frac.set_xlabel("Time (hours)", fontsize=8, labelpad=5)
        ax_frac.set_ylim(-0.05, 1.05)
        ax_cdf.set_xlabel(f"{_ch} {_metric_label}", fontsize=8, labelpad=5)
        ax_cdf.set_ylim(-0.02, 1.05)

        any_ts = any_cdf = False
        all_fluor_vals: List[float] = []

        _val_col = self._export_val_col_for("_line_channel_cb")
        _cell_area_threshold = self._app._get_cell_area_threshold()
        _fluor_gates = self._app._get_all_fluor_gates()
        from well_viewer.batch_export._line_series_runner import (
            collect_line_series_for_group,
        )
        fc_state_for_fig = (
            bool(fc_control_means),
            self._fc_control_label if fc_active else "",
            bool(fc_vs_t0),
        )
        series_list = collect_line_series_for_group(
            self._app, grp,
            threshold=threshold, use_sem=use_sem,
            val_col=_val_col,
            cell_area_threshold=_cell_area_threshold,
            fluor_gates=_fluor_gates,
            fc_state=fc_state_for_fig,
            include_cdf=True,
        )
        import numpy as _np
        for series in series_list:
            color = series.color
            display_name = series.display
            agg_times = [p.t for p in series.points if p.has_mean]
            agg_means = [p.mean for p in series.points if p.has_mean]
            agg_errs = [p.spread for p in series.points if p.has_mean]
            if agg_times:
                ax_mean.plot(agg_times, agg_means, color=color, lw=2, marker="o",
                             markersize=4, label=display_name, zorder=3)
                ax_mean.fill_between(
                    agg_times,
                    [m - e for m, e in zip(agg_means, agg_errs)],
                    [m + e for m, e in zip(agg_means, agg_errs)],
                    color=color, alpha=0.15, zorder=2,
                )
                vf = [(p.t, p.frac) for p in series.points if p.has_frac]
                if vf:
                    vt2, vf2 = zip(*vf)
                    ax_frac.plot(vt2, vf2, color=color, lw=2, marker="s",
                                 markersize=3, label=display_name, zorder=3)
                    ax_frac.fill_between(vt2, 0, vf2, color=color, alpha=0.10, zorder=2)
                any_ts = True
            fluor_vals = _np.sort(_np.asarray(series.cdf_vals, dtype=float))
            n = int(fluor_vals.size)
            if n:
                all_fluor_vals.extend(fluor_vals.tolist())
                cdf = _np.arange(1, n + 1) / n
                ax_cdf.plot(fluor_vals, cdf,
                            color=color, lw=1.8,
                            label=f"{display_name} (n={n:,})", zorder=3)
                any_cdf = True

        if any_ts:
            # Threshold is in raw-fluorescence units; once fold-change scaling
            # is applied the curves no longer live on that axis, so the line
            # would be misleading. Suppress it in that case.
            if not fc_active:
                ax_mean.axhline(threshold, color=WARN, lw=1.0, ls="--", alpha=0.8)
            # Skip the legend call entirely when no labeled artists were
            # plotted (e.g. all NaN means for a non-MFI property + a
            # threshold that filters every cell) — matplotlib otherwise
            # emits the noisy "No artists with labels found to put in
            # legend" UserWarning to stderr.
            if ax_mean.get_legend_handles_labels()[0]:
                ax_mean.legend(**legend_kw)
            if ax_frac.get_legend_handles_labels()[0]:
                ax_frac.legend(**legend_kw)
        if any_cdf:
            ax_cdf.axvline(threshold, color=WARN, lw=1.2, ls="--",
                           label=f"threshold={threshold:.2f}", zorder=5)
            if all_fluor_vals:
                lo, hi = min(all_fluor_vals), max(all_fluor_vals)
                ax_cdf.axvspan(threshold, hi, alpha=0.05, color=WARN, zorder=1)
                ax_cdf.set_xlim(lo, hi)
            if ax_cdf.get_legend_handles_labels()[0]:
                ax_cdf.legend(**legend_kw)

        return fig

