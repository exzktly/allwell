"""Line-mode batch export panel (also the base class for bar/scatter)."""

from __future__ import annotations

import csv
import math
import re

import theme
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from well_viewer.features.batch_export._common import (
    WELL_COLORS,
    BarGroup,
    _PLATE_COLS,
    _PLATE_ROWS,
    apply_ax_style,
    ask_name_dialog,
    btn_card,
    btn_danger,
    btn_primary,
    btn_secondary,
    _clear_layout_helper,
    _logger,
    unique_export_stem,
    _CLR_WHITE,
    get_color,
)
from well_viewer.features.batch_export.well_grid_button import _WellGridButton
# Qt-free export-execution service (A14 / X3) — safe to import at module level.
from well_viewer.features.batch_export.service import (
    commit_staged_export,
    discard_staging,
    new_staging_dir,
    save_export_figure,
)
# ── ctx-native god-method forms (A8 / Epic-A14 panel conversion) ─────────────
# Foundational-layer (core/domain/data) ctx forms — safe at module scope.
from well_viewer.core.lazy import lazy as _lazy
from well_viewer.core.channel_state_controller import (
    get_all_fluor_gates as _get_all_fluor_gates,
    get_thresh_frac_on as _get_thresh_frac_on,
)
# Sibling-feature ctx forms reached through core.lazy proxies — a call, not an
# import statement, so no module-load cycle and no deferred-import-budget hit.
_rep_sets_loaded = _lazy("well_viewer.features.grouping.controller", "rep_sets_loaded")
_col_for_scatter_axis = _lazy("well_viewer.features.scatter.controller", "col_for_scatter_axis")
_get_all_profile_names = _lazy("well_viewer.features.export.style", "get_all_profile_names_ctx")
_ensure_custom_export_profiles = _lazy("well_viewer.features.export.style", "ensure_custom_export_profiles_ctx")
# The Line-Graphs tab's shared renderer, reused by the batch line figure (proxy
# is callable, so ``_LineAxes(...)`` constructs the dataclass) — a call, not an
# import statement, so it stays off the deferred-import budget.
_render_line_series = _lazy("well_viewer.features.lineplot.controller", "render_line_series")
_LineAxes = _lazy("well_viewer.features.lineplot.controller", "LineAxes")

# Shared width for OUTPUT-SETTINGS row labels so the value controls line up in a
# single column (Folder / Format / Style Preset / Channel / Fold change).
_FIELD_LABEL_W = 108


def _norm_well_token(tok: str) -> str:
    """Canonicalise a well token (``a1`` → ``A01``) for membership tests."""
    tok = tok.strip().upper()
    m = re.match(r"^([A-H])(\d{1,2})$", tok, re.I)
    return f"{m.group(1).upper()}{int(m.group(2)):02d}" if m else tok


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

    #: Line & bar exports carry the Error-band (SEM/SD) + per-FOV-points spread
    #: controls in their OUTPUT SETTINGS; scatter panels turn this off.
    _INCLUDES_SPREAD = True

    def __init__(
        self,
        ctx,
        parent: QWidget | None = None,
        *,
        use_sidebar_groups: bool = False,
    ) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._use_sidebar_groups = bool(use_sidebar_groups)
        # Which slot in ctx.runtime.batch_export.export_groups_doc this panel
        # reads/writes. Resolved via a method so subclasses (bar / scatter) pick
        # their own key; safe to call here because the subclass state it needs
        # (e.g. _scatter_mode) is set before super().__init__.
        self._persist_key: str = self._compute_persist_key()
        # Suppress the persistence write-back while we build the UI and hydrate
        # groups from disk (otherwise the initial _refresh_group_list would echo
        # the just-loaded groups straight back to a scheduled save).
        self._suppress_persist: bool = True

        _ds = ctx.state.dataset
        default_out = str(_ds.data_dir) if _ds.data_dir else ""
        export_prefs = ctx.state.export_prefs.style_prefs or {}
        default_fmt = str(export_prefs.get("format", "png")).lower()
        if default_fmt not in {"png", "svg", "eps", "pdf"}:
            default_fmt = "png"
        default_profile = str(export_prefs.get("export_profile", "Custom"))

        self._out_dir_value: str = default_out
        self._fmt_value: str = default_fmt
        self._export_profile_value: str = default_profile

        # Panel-local fold-change normalization state. Independent of the
        # plot-tab state so a batch run isn't tied to whatever the on-screen
        # plot has set. Defaults inherited from the app at panel-creation
        # time so opening the panel "feels like" the active plot tab.
        _plot = ctx.state.plot
        self._fc_vs_control_on: bool = bool(_plot.fc_vs_control_on)
        self._fc_control_label: str = str(_plot.fc_control_label or "")
        self._fc_vs_t0_on: bool = bool(_plot.fc_vs_t0_on)

        # Hydrate from the persisted export groups (persistence.json), else start
        # empty. Batch-export groups are export-specific, not the Sample-
        # Definition replicate sets — the user opts in to seeding from the app
        # via the "Sync from app" button or the Quick Setup menu.
        self._groups: list[BarGroup] = self._groups_from_persistence()
        self._active_grp = 0 if self._groups else -1
        self._auto_named_group_ids: set[int] = set()
        self._map_btns: dict[str, _WellGridButton] = {}
        self._drag_adding = True
        self._drag_visited: set = set()

        self._build_ui()
        if not self._use_sidebar_groups:
            self._refresh_group_list()
        self._suppress_persist = False

    def _compute_persist_key(self) -> str:
        """Slot key in ``app._export_groups_doc`` for this panel (line plot)."""
        return "line"

    def _build_ui(self) -> None:
        from PySide6.QtWidgets import QBoxLayout
        outer = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
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

        # Handles for the responsive stack/unstack (see apply_split_orientation).
        self._split_outer = outer
        self._split_left = left
        self._split_right = right
        self._split_sep = sep
        self._split_right_index = 2   # left(0), sep(1), right(2)

    def resizeEvent(self, event) -> None:  # Qt override
        super().resizeEvent(event)
        self.apply_split_orientation()

    def apply_split_orientation(self, avail: int | None = None) -> None:
        """Side-by-side when the pane is wide enough for both panels, otherwise
        stacked vertically (plate map on top, OUTPUT SETTINGS below) so nothing is
        pushed off-edge.

        *avail* is the width actually available to the panel — the enclosing
        scroll area's viewport width, pushed in by ``_ResponsiveScroll`` (tab_view).
        We must NOT use ``self.width()``: with horizontal scroll off the panel is
        pinned to its own side-by-side minimum (wider than the viewport), so its
        width never reflects the narrow window and the flip would never fire. The
        flip point is the panels' real side-by-side minimum, with hysteresis so a
        scrollbar toggling near it doesn't oscillate the orientation."""
        from PySide6.QtWidgets import QBoxLayout, QFrame
        if self._use_sidebar_groups or not hasattr(self, "_split_outer"):
            return
        if avail is None:
            avail = self._viewport_width()
        need = (self._split_left.minimumSizeHint().width()
                + self._split_right.minimumSizeHint().width() + 24)
        stacked_now = (self._split_outer.direction()
                       == QBoxLayout.Direction.TopToBottom)
        if not stacked_now and avail < need:
            stack = True
        elif stacked_now and avail > need + 60:   # hysteresis
            stack = False
        else:
            return  # no change needed
        self._split_outer.setDirection(
            QBoxLayout.Direction.TopToBottom if stack
            else QBoxLayout.Direction.LeftToRight)
        self._split_sep.setFrameShape(QFrame.HLine if stack else QFrame.VLine)
        # Stacked: the plate map takes the full width; side-by-side: keep it capped.
        self._split_left.setMaximumWidth(16777215 if stack else 560)  # QWIDGETSIZE_MAX
        # Right panel fills leftover horizontal space when side-by-side; when
        # stacked it should sit at its natural height (no vertical slack grab).
        self._split_outer.setStretch(self._split_right_index, 0 if stack else 1)

    def _viewport_width(self) -> int:
        """Width available to the panel: the enclosing QScrollArea's viewport,
        else this widget's own width as a fallback."""
        from PySide6.QtWidgets import QScrollArea
        g = self.parentWidget()
        while g is not None:
            if isinstance(g, QScrollArea):
                return g.viewport().width()
            g = g.parentWidget()
        return self.width()

    def _build_group_editor(self, layout: QVBoxLayout) -> None:
        # Row 1: title + the two ways to *create* groups (Add / Quick Setup).
        hdr1 = QHBoxLayout()
        hdr1.setContentsMargins(8, 4, 8, 4)
        title = QLabel("EXPORT GROUPS")
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        hdr1.addWidget(title)
        hdr1.addStretch(1)
        hdr1.addWidget(btn_primary(None, "+ Add", self._grp_add))
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
        hdr1.addWidget(qs_btn)
        layout.addLayout(hdr1)

        # Row 2: Sync on the left; the destructive Clear All isolated on the
        # right so it isn't fat-fingered next to Add. Groups autosave to the
        # dataset's persistence.json, so there is no manual Save/Load route.
        hdr2 = QHBoxLayout()
        hdr2.setContentsMargins(8, 2, 8, 2)
        hdr2.addWidget(btn_secondary(None, "Sync from app", self._sync_from_app))
        hdr2.addStretch(1)
        hdr2.addWidget(btn_secondary(None, "Clear All", self._grp_clear_all))
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

    def _tok_at(self, global_pos) -> str | None:
        for tok, btn in self._map_btns.items():
            if btn.isVisible() and btn.rect().contains(btn.mapFromGlobal(global_pos)):
                return tok
        return None

    def _plate_press(self, event) -> None:
        tok = self._tok_at(event.globalPosition().toPoint())
        if tok is None or tok not in self._ctx.state.dataset.well_paths:
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
        Plotting's band — and collapses to ``Calculated Val`` when the channel
        resolves to a ratio key.

        The two combos are stashed at ``self.<attr>`` and ``self.<attr +
        "_metric">`` so the panel's ``_run_batch`` can read them.
        """
        channels = self._all_export_channels()
        row = QHBoxLayout()
        row.setContentsMargins(12, 2, 12, 2)
        lbl = QLabel("Channel:")
        f = lbl.font(); f.setBold(True); lbl.setFont(f)
        lbl.setMinimumWidth(_FIELD_LABEL_W)
        row.addWidget(lbl)
        cb = QComboBox()
        cb.addItems(channels)
        default = str(self._ctx.state.channel.active_channel or "")
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

    def _all_export_channels(self) -> list[str]:
        """Fluorescence channels + ratio metrics available for batch export."""
        channels = list(self._ctx.state.channel.fluor_channels or [])
        if not channels:
            channels = ["gfp"]
        for r in (self._ctx.state.channel.ratio_metrics or []):
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
        from well_viewer.data.metric_labels import (
            METRIC_ORDER, METRIC_KEY_TO_LABEL, CALCULATED_VAL_LABEL,
        )
        ch_cb = getattr(self, channel_attr, None)
        prop_cb = getattr(self, f"{channel_attr}_metric", None)
        if ch_cb is None or prop_cb is None:
            return
        channel = str(ch_cb.currentText() or "")
        ratio_key = (
            self._ctx.state.channel.label_to_channel_key
        ).get(channel)
        from well_viewer.domain.ratio_models import is_ratio_key as _is_ratio_key
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
                    self._ctx.state.channel.active_metric,
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
                    # Case-insensitive: this combo holds the displayed label
                    # ("GFP") while ``smfish_channels`` holds normalised tokens.
                    from well_viewer.domain.metric_registry import (
                        channel_supports_smfish,
                    )
                    item.setEnabled(channel_supports_smfish(
                        channel, self._ctx.state.channel.smfish_channels,
                    ))
        finally:
            prop_cb.blockSignals(blocked)

    def _selected_export_metric_key(self, channel_attr: str) -> str:
        """Return the metric key (``mean_intensity`` / ``total_intensity`` / …)
        for the Property combo paired with ``channel_attr``. Falls back to
        ``app._app_state.channel.active_metric`` when the combo is missing or shows
        ``Calculated Val`` (ratio path — caller resolves the column via
        :py:meth:`_export_val_col_for`)."""
        from well_viewer.data.metric_labels import METRIC_LABEL_TO_KEY
        prop_cb = getattr(self, f"{channel_attr}_metric", None)
        if prop_cb is None:
            return self._ctx.state.channel.active_metric or "mean_intensity"
        label = str(prop_cb.currentText() or "")
        return METRIC_LABEL_TO_KEY.get(
            label, self._ctx.state.channel.active_metric or "mean_intensity",
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
        chan = self._ctx.state.channel
        saved = chan.active_val_col
        try:
            chan.active_val_col = val_col
            yield
        finally:
            if saved is not None:
                chan.active_val_col = saved

    @contextmanager
    def _app_group_scope(self, grp: BarGroup):
        """Temporarily make the app's selection state describe just *grp*: one
        selection per replicate-set member, plus the group's solo wells as
        single-well selections.

        The plotting-tab collectors (``collect_line_series`` /
        ``collect_bar_items`` / ``collect_scatter_data``) read
        ``app._rep_sets_active()`` / ``app._app_state.selection.selected_wells``
        (both derived from ``app._app_state.selection.selections``). Swapping
        ``selections`` for the duration lets the batch export reuse those tab
        collectors verbatim — so a batch group behaves exactly like the tab,
        honouring replicate-set aggregation inside the group. Solo wells are
        represented as single-well selections so the mode-exclusive collectors
        (rep-sets *or* raw ``selected_wells``, never both) still include them:
        ``active_replicate_sets`` treats any non-hidden selection with a loaded
        well as a rep-set, and a one-well rep-set's stat equals the raw well's,
        so solos are numerically unchanged while pooled members get the tab's
        mean-of-per-well-means."""
        from well_viewer.domain.selections_model import make_selection
        sel = self._ctx.state.selection
        saved = (sel.selections, sel.current_selection_id, sel.selected_wells)
        try:
            used_names: set = set()
            used_ids: set = set()
            selections = [
                make_selection(name=m.name, wells=list(m.wells), source="rep_set",
                               used_names=used_names, used_ids=used_ids,
                               fallback_color_idx=i)
                for i, m in enumerate(grp.members)
            ]
            for j, w in enumerate(grp.solo_wells):
                selections.append(
                    make_selection(name=w, wells=[w], source="well",
                                   used_names=used_names, used_ids=used_ids,
                                   fallback_color_idx=len(grp.members) + j)
                )
            sel.selections = selections
            sel.current_selection_id = None
            sel.selected_wells = set(grp.solo_wells)
            yield
        finally:
            (sel.selections, sel.current_selection_id, sel.selected_wells) = saved

    def _export_val_col_for(self, channel_attr: str) -> str:
        """Resolve the (Channel, Property) pair to a CSV column / ratio key.

        Routes through ``app._col_for_scatter_axis`` (the same helper the
        per-tab plot uses) so ratio labels become ``ratio:<name>`` and
        real channels become ``<channel>_<metric>``.
        """
        ch_cb = getattr(self, channel_attr, None)
        channel = str(ch_cb.currentText() or "") if ch_cb is not None else (
            self._ctx.state.channel.active_channel or ""
        )
        metric_key = self._selected_export_metric_key(channel_attr)
        return _col_for_scatter_axis(self._ctx, channel, metric_key)

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
        mapping = self._ctx.state.channel.label_to_channel_key
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
        return str(self._ctx.state.channel.active_channel or "")

    def _build_export_profile_row(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setContentsMargins(12, 2, 12, 2)
        lbl = QLabel("Style Preset:")
        f = lbl.font(); f.setBold(True); lbl.setFont(f)
        lbl.setMinimumWidth(_FIELD_LABEL_W)
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

    def _export_profile_names(self) -> list[str]:
        names = _get_all_profile_names(self._ctx)
        return names or ["Custom"]

    def _apply_export_profile_to_prefs(self, profile_name: str) -> None:
        from well_viewer.features.export.style import (
            EXPORT_PROFILES,
            _ensure_export_style_prefs,
        )

        name = str(profile_name or "Custom")
        prefs = _ensure_export_style_prefs(self._ctx)
        custom_profiles = _ensure_custom_export_profiles(self._ctx)
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
    def _add_bold_label(layout, text: str, *, min_width: int = 0) -> QLabel:
        lbl = QLabel(text)
        f = lbl.font(); f.setBold(True); lbl.setFont(f)
        if min_width:
            lbl.setMinimumWidth(min_width)
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
        self._add_bold_label(out_row, "Folder:", min_width=_FIELD_LABEL_W)
        self._out_dir_edit = QLineEdit(self._out_dir_value)
        self._out_dir_edit.setMinimumWidth(240)
        out_row.addWidget(self._out_dir_edit, 1)
        out_row.addWidget(btn_secondary(None, "Browse\u2026", self._browse_out_dir))
        layout.addLayout(out_row)

        fmt_row = QHBoxLayout()
        fmt_row.setContentsMargins(12, 2, 12, 2)
        self._add_bold_label(fmt_row, "Format:", min_width=_FIELD_LABEL_W)
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
        if self._INCLUDES_SPREAD:
            self._build_spread_settings_rows(layout)

    def _build_spread_settings_rows(self, layout: QVBoxLayout) -> None:
        """Error-band (SEM/SD) + per-FOV-points toggles as OUTPUT SETTINGS rows.

        Line & bar only. Previously a separate top button row above the panels;
        moved here so the spread controls sit with the rest of the export config,
        aligned like Folder / Format. The buttons still register into the shared
        ``ctx.runtime.sem.btns`` / ``fov.btns`` banks so they stay in sync with
        the plot toolbars."""
        from well_viewer.core.plot_toggles import (
            refresh_fov_btn_state, toggle_fov_replicates, toggle_sem,
        )
        ctx = self._ctx

        eb_row = QHBoxLayout()
        eb_row.setContentsMargins(12, 2, 12, 2)
        self._add_bold_label(eb_row, "Error band:", min_width=_FIELD_LABEL_W)
        initial_sem = bool(ctx.state.plot.use_sem)
        sem_btn = QPushButton("SEM" if initial_sem else "SD")
        sem_btn.setProperty("variant", "sem" if initial_sem else "sem_warn")
        sem_btn.setToolTip(
            "Switch the spread used by line/bar batch exports between\n"
            "SEM (Standard Error of the Mean) and SD (Standard Deviation).\n"
            "Mirrors the SEM/SD toggle in the plot toolbars.")
        sem_btn.clicked.connect(lambda _=False: toggle_sem(ctx))
        eb_row.addWidget(sem_btn)
        eb_row.addStretch(1)
        layout.addLayout(eb_row)
        if ctx.runtime.sem.btns is None:
            ctx.runtime.sem.btns = []
        ctx.runtime.sem.btns.append(sem_btn)
        if not ctx.runtime.sem.btn:
            ctx.runtime.sem.btn = sem_btn

        fov_row = QHBoxLayout()
        fov_row.setContentsMargins(12, 2, 12, 6)
        self._add_bold_label(fov_row, "Per-FOV points:", min_width=_FIELD_LABEL_W)
        fov_btn = QPushButton("FOV")
        fov_btn.setProperty("variant", "toggle")
        fov_btn.clicked.connect(lambda _=False: toggle_fov_replicates(ctx))
        fov_row.addWidget(fov_btn)
        fov_row.addStretch(1)
        layout.addLayout(fov_row)
        if ctx.runtime.fov.btns is None:
            ctx.runtime.fov.btns = []
        ctx.runtime.fov.btns.append(fov_btn)
        if not ctx.runtime.fov.btn:
            ctx.runtime.fov.btn = fov_btn
        refresh_fov_btn_state(ctx)

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

    def _selected_tps(self) -> list[str]:
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
        fc_lbl.setMinimumWidth(_FIELD_LABEL_W)
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
        members: list[str] = []
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
            for w in sorted((self._ctx.state.dataset.well_paths or {})):
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

    def _active_group(self) -> BarGroup | None:
        if 0 <= self._active_grp < len(self._groups):
            return self._groups[self._active_grp]
        return None

    def _default_group_name(self, grp: BarGroup) -> str:
        labels = [r.name for r in grp.members]
        labels.extend(grp.solo_wells)
        if not labels:
            return "New Group"
        return ", ".join(labels)

    def _refresh_auto_group_name(self, grp: BarGroup | None) -> None:
        if grp is None:
            return
        if id(grp) in self._auto_named_group_ids:
            grp.name = self._default_group_name(grp)

    def _apply_drag(self, tok: str) -> None:
        if tok in self._drag_visited:
            return
        self._drag_visited.add(tok)
        grp = self._active_group()
        if grp is None or tok not in self._ctx.state.dataset.well_paths:
            return
        rset = next((r for r in _rep_sets_loaded(self._ctx) if tok in r.wells), None)
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

    def _group_color(self, index: int) -> str:
        """Distinct palette colour for export group *index*.

        Export groups are coloured by their position in the group list, NOT by
        their wells' plate rank — otherwise two groups spanning the same columns
        get the same hue (well-position ranking is column-based). Group-index
        colouring guarantees each group a distinct colour in the plate map,
        group-card dot, and member chips.
        """
        return WELL_COLORS[index % len(WELL_COLORS)]

    def _refresh_single_btn(self, tok: str) -> None:
        btn = self._map_btns.get(tok)
        if btn is None or tok not in self._ctx.state.dataset.well_paths:
            return
        # Last group containing the well wins, matching _refresh_map.
        owner = next((gi for gi in range(len(self._groups) - 1, -1, -1)
                      if tok in self._groups[gi].wells), None)
        if owner is not None:
            btn.set_colors(self._group_color(owner), _CLR_WHITE)
            return
        btn.set_colors(theme.Colors.panel_elevated, theme.Colors.text_primary)

    def _refresh_map(self) -> None:
        avail = set(self._ctx.state.dataset.well_paths.keys())
        tok_color: dict[str, str] = {}
        for gi, grp in enumerate(self._groups):
            c = self._group_color(gi)
            for w in grp.wells:
                tok_color[w] = c   # last group wins — matches the main-viewer plates
        active_wells: set = set()
        grp = self._active_group()
        if grp:
            for w in grp.wells:
                active_wells.add(w)
        for tok, btn in self._map_btns.items():
            if tok not in avail:
                btn.set_colors(theme.Colors.well_unavailable, theme.Colors.text_muted, enabled=False)
            elif tok in tok_color:
                btn.set_colors(tok_color[tok], _CLR_WHITE, active=(tok in active_wells))
            else:
                btn.set_colors(theme.Colors.panel_elevated, theme.Colors.text_primary)

    def _clear_layout(self, layout) -> None:
        _clear_layout_helper(layout)

    def _refresh_group_list(self) -> None:
        # Groups changed → mirror them into persistence.json (no-op while the
        # panel is still building or hydrating from disk).
        self._persist_groups()
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
        color = self._group_color(gi)
        card = QFrame()
        card.setFrameShape(QFrame.StyledPanel)
        if is_sel:
            card.setStyleSheet(
                f"QFrame {{ border: 2px solid {color}; background: {theme.Colors.panel_elevated}; }}"
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
            row_rsets = [r for r in _rep_sets_loaded(self._ctx)
                         if any(w[0].upper() == row_ltr for w in r.wells)]
            assigned_wells = {w for r in row_rsets for w in r.wells}
            row_solos = [tok for tok in self._ctx.state.dataset.well_paths
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
            col_rsets = [r for r in _rep_sets_loaded(self._ctx)
                         if any(w[1:] == col for w in r.wells)]
            assigned_wells = {w for r in col_rsets for w in r.wells}
            col_solos = [tok for tok in self._ctx.state.dataset.well_paths
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
        # In sidebar-groups mode there is no local group editor list to refresh
        # (the left panel is skipped in _build_ui); groups are resolved live
        # from the rep-sets at export time via _groups_for_export().
        if not self._use_sidebar_groups:
            self._refresh_group_list()

    def _groups_from_rep_sets(self) -> list[BarGroup]:
        return [BarGroup(r.name, members=[r]) for r in _rep_sets_loaded(self._ctx)]

    # \u2500\u2500 Export-group persistence (persistence.json["export_groups"]) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    def _serialize_groups(self) -> list[dict]:
        """Plain-dict form of the current export groups for persistence.json."""
        out: list[dict] = []
        for grp in self._groups:
            out.append({
                "name": grp.name,
                "hidden": grp.hidden,
                "members": [rset.name for rset in grp.members],
                "solo_wells": list(grp.solo_wells),
            })
        return out

    def _deserialize_groups(self, grp_list) -> list[BarGroup]:
        """Rebuild BarGroups from persisted dicts, matched to the live dataset.

        Group members are matched by replicate-set name against the currently
        loaded rep-sets, and solo wells are kept only when the token resolves to
        a loaded well \u2014 so a stale persisted set degrades gracefully rather than
        referencing wells/groups that no longer exist."""
        rep_by_name = {r.name: r for r in _rep_sets_loaded(self._ctx)}
        paths = self._ctx.state.dataset.well_paths
        new_groups: list[BarGroup] = []
        for item in grp_list or []:
            if not isinstance(item, dict):
                continue
            grp = BarGroup(str(item.get("name", "Group")),
                           hidden=bool(item.get("hidden", False)))
            for rname in item.get("members", []):
                if rname in rep_by_name:
                    grp.members.append(rep_by_name[rname])
            for raw_tok in item.get("solo_wells", []):
                tok = _norm_well_token(str(raw_tok))
                if tok in paths:
                    grp.solo_wells.append(tok)
            new_groups.append(grp)
        return new_groups

    def _groups_from_persistence(self) -> list[BarGroup]:
        """Hydrate this panel's groups from the batch-export groups doc."""
        doc = self._ctx.runtime.batch_export.export_groups_doc or {}
        return self._deserialize_groups(doc.get(self._persist_key))

    def _persist_groups(self) -> None:
        """Mirror the editor into the in-memory batch-export groups doc.

        In-memory only — the disk write is deferred to ``_save_export_groups_now``
        (called when a batch export actually runs). Otherwise every well click /
        group selection would rewrite ``persistence.json`` in the (possibly
        iCloud-synced) data dir and hammer the OS file-provider sync daemon.
        """
        if getattr(self, "_suppress_persist", False):
            return
        be = self._ctx.runtime.batch_export
        doc = be.export_groups_doc
        if doc is None:
            doc = {}
            be.export_groups_doc = doc
        doc[self._persist_key] = self._serialize_groups()

    def _save_export_groups_now(self) -> None:
        """Flush the export-group definitions to disk — called only when a batch
        export actually runs, so interactive editing never writes per click."""
        be = self._ctx.runtime.batch_export
        if be.export_groups_doc is None:
            be.export_groups_doc = {}
        be.export_groups_doc[self._persist_key] = self._serialize_groups()
        self._ctx.persistence.save("export_groups")

    def _reload_persisted_groups(self) -> None:
        """Re-hydrate from persistence (called after a dataset (re)load)."""
        self._suppress_persist = True
        try:
            self._groups = self._groups_from_persistence()
            self._auto_named_group_ids.clear()
            self._active_grp = 0 if self._groups else -1
            if not self._use_sidebar_groups:
                self._refresh_group_list()
        finally:
            self._suppress_persist = False

    def _resolve_out_dir(self) -> Path | None:
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
        d = self._ctx.state.dataset.data_dir
        if d and d.is_dir():
            return d
        QMessageBox.critical(self, "No output directory",
                             "Load data or choose an output folder.")
        return None

    def _run_batch_jobs(
        self, *, jobs: list, progress_text_fn, run_job_fn,
        success_text: str, status_text: str, out_dir: Path,
    ) -> None:
        """Run each job into a staging dir, committing atomically on full success.

        ``run_job_fn(job, write_dir)`` must write all of a job's files under
        ``write_dir`` (never straight into ``out_dir``). Only when every job
        succeeds are the staged files moved into ``out_dir`` + a manifest
        written (audit X3); any error discards the staging so a failed re-run
        leaves the previous complete export intact.
        """
        from PySide6.QtWidgets import QApplication

        # Persist the export-group definitions now — the ONLY place they hit
        # disk, so interactive well clicks never rewrite persistence.json in the
        # (possibly iCloud-synced) data dir. Never let a persistence hiccup abort
        # the export the user asked for.
        try:
            self._save_export_groups_now()
        except Exception:
            _logger.exception("Batch export: persisting export groups failed")

        self._prog_bar.setVisible(True)
        self._prog_bar.setMaximum(max(len(jobs), 1))
        self._prog_bar.setValue(0)
        self._run_btn.setEnabled(False)
        errors: list[str] = []
        staging = new_staging_dir(out_dir)
        try:
            for step, job in enumerate(jobs, 1):
                self._prog_lbl.setText(progress_text_fn(job, step, len(jobs)))
                self._prog_bar.setValue(step - 1)
                QApplication.processEvents()
                err = run_job_fn(job, staging)
                if err:
                    errors.append(err)
            self._prog_bar.setValue(len(jobs))
            if not errors:
                commit_staged_export(staging, out_dir)
            else:
                discard_staging(staging)
        except BaseException:
            # Never leave a half-committed export or a stray temp dir behind.
            discard_staging(staging)
            raise
        finally:
            self._run_btn.setEnabled(True)
            self._prog_bar.setVisible(False)

        if errors:
            self._prog_lbl.setText(f"Done with {len(errors)} error(s). See log.")
            self._prog_lbl.setStyleSheet(f"color: {get_color('CLR_DANGER')};")
            for err in errors:
                _logger.error("Batch export error: %s", err)
            return
        self._prog_lbl.setText(success_text)
        self._prog_lbl.setStyleSheet(f"color: {get_color('CLR_SUCCESS_DARK')};")
        self._ctx.notify.set_status(status_text)

    def _save_figure(self, fig, fig_path: Path, fmt: str) -> None:
        # The actual render-write is the Qt-free service (audit A14); the panel
        # just resolves this app's export-style prefs and the plot background.
        from well_viewer.features.export.style import _ensure_export_style_prefs
        save_export_figure(
            fig, fig_path, fmt,
            export_prefs=_ensure_export_style_prefs(self._ctx),
            plot_bg=theme.CPub.bg,
        )

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
        fig = _Figure(figsize=(8, 6), dpi=300, facecolor=theme.CPub.bg)
        ax = fig.add_subplot(1, 1, 1)
        draw(ax)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(True, alpha=0.3)
        if ax.get_legend_handles_labels()[0]:
            ax.legend(loc="best", fontsize=legend_fontsize, framealpha=0.0, facecolor="none")
        self._save_figure(fig, fig_path, fmt)

    def _groups_for_export(self) -> list[BarGroup]:
        if self._use_sidebar_groups:
            return self._groups_from_rep_sets()
        return list(self._groups)

    def _run_batch(self) -> None:
        groups_with_data = [g for g in self._groups_for_export()
                            if any(w in self._ctx.state.dataset.well_paths for w in g.wells)]
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

        _ch_selected = self._selected_export_channel() or self._ctx.state.channel.active_channel
        # ``_get_thresh_frac_on`` looks up by the canonical key (bare
        # channel token or ``ratio:<name>``), not by the dropdown's
        # display label \u2014 translate ratio labels first so the user's
        # configured ThreshFracOn is honoured instead of the 50.0
        # default that empties out the ratio plot.
        threshold = _get_thresh_frac_on(self._ctx,
            self._resolved_channel_key(_ch_selected)
        )
        use_sem = self._ctx.state.plot.use_sem
        band_lbl = "SEM" if use_sem else "SD"
        fmt = self._fmt_cb.currentText()

        def _progress(grp: BarGroup, step: int, total: int) -> str:
            return f"Exporting '{grp.name}' ({step}/{total})\u2026"

        fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0 = self._fc_state()
        fc_active = fc_vs_ctrl or fc_vs_t0
        used_stems: set = set()

        def _run_group(grp: BarGroup, write_dir: Path) -> str | None:
            safe = re.sub(r"[^A-Za-z0-9_\-]", "_", grp.name)
            # Uniquify so two groups whose names sanitise to the same stem don't
            # silently overwrite each other's CSV + figure.
            safe = unique_export_stem(f"batch_{safe}", used_stems)
            # Write into the staging dir (audit X3) — committed to out_dir only on
            # a fully-successful run.
            csv_path = write_dir / f"{safe}.csv"
            fig_path = write_dir / f"{safe}.{fmt}"

            # Resolve the panel's (Channel, Property) selection into a
            # column / ratio key — independent of whatever the plot-tab
            # Plotting's band has picked.
            _val_col = self._export_val_col_for("_line_channel_cb")
            _ch = _ch_selected
            _metric = self._selected_export_metric_key("_line_channel_cb")
            _fluor_gates = _get_all_fluor_gates(self._ctx)
            panel_fc_state = (fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0)

            # ``collect_line_series_for_group`` is the thin batch orchestrator
            # that reuses the tab's rep-set statistic (``compute_rep_stats``) for
            # members and ``aggregate_well`` for solos — so batch curves match
            # the tab while a solo well stays byte-for-byte identical to its
            # on-tab per-well export. The scope makes a replicate-set fold-change
            # control resolvable; ``_app_val_col_scope`` because the rep-set stat
            # reads ``active_val_col``.
            with self._app_group_scope(grp), self._app_val_col_scope(_val_col):
                try:
                    from well_viewer.features.batch_export._line_series_runner import (
                        collect_line_series_for_group,
                    )
                    from well_viewer.features.export.service import (
                        _well_labels_map,
                        line_metric_fieldnames, line_metric_row,
                        well_name_for, well_names_joined,
                    )
                    _well_labels = _well_labels_map(self._ctx)
                    # Two passes through the same shared collector: raw values
                    # for the standard mean / spread columns, fold-change
                    # values for the additive ``fold_change_*`` columns when
                    # active. Mirrors the bar batch CSV's additive schema.
                    series_raw = collect_line_series_for_group(
                        self._ctx, grp,
                        threshold=threshold, use_sem=use_sem,
                        val_col=_val_col, fluor_gates=_fluor_gates,
                        fc_state=(False, "", False),
                    )
                    if fc_active:
                        series_fc = collect_line_series_for_group(
                            self._ctx, grp,
                            threshold=threshold, use_sem=use_sem,
                            val_col=_val_col, fluor_gates=_fluor_gates,
                            fc_state=panel_fc_state,
                        )
                        fc_points_by_key = {s.key: s.points for s in series_fc}
                    else:
                        fc_points_by_key = {}
                    rows_out: list[dict] = []
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
                    fig = self._render_group_figure(
                        grp, threshold, use_sem, band_lbl,
                        fc_state=panel_fc_state, val_col=_val_col,
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
            out_dir=out_dir,
        )

    def _log_sample_definitions_snapshot(self, groups_for_export: list[BarGroup]) -> None:
        sels = list(self._ctx.state.selection.selections or [])
        sel_summary = [f"{s.get('name', '?')}={len(s.get('wells') or [])}w" for s in sels]
        export_summary = [f"{g.name}={len(g.wells)}w" for g in groups_for_export]
        _logger.info(
            "Run Batch Export clicked | selections=%s | groups_for_export=%s",
            sel_summary, export_summary,
        )

    def _render_group_figure(
        self, grp: BarGroup, threshold: float, use_sem: bool, band_lbl: str,
        *,
        fc_state: tuple = (False, "", False),
        val_col: str | None = None,
    ):
        """Render the batch line figure for *grp*, reusing the Line-Graphs tab's
        own renderer (``render_line_series``) so the figure is the SAME
        four-panel layout (mean / fraction / N(above) / CDF) the tab draws — no
        re-implementation to drift out of sync (this is what fixed the dropped
        N(above) panel). Series come from ``collect_line_series_for_group``,
        which reuses the tab's per-member statistic. Must be called inside
        ``_app_group_scope(grp)`` + ``_app_val_col_scope`` (``_run_group`` does):
        the rep-set stat reads ``active_val_col`` and a rep-set fold-change
        control resolves against the group's scoped selections."""
        from matplotlib.figure import Figure as _Figure
        from well_viewer.domain import fold_change as _fc
        from well_viewer.features.batch_export._line_series_runner import (
            collect_line_series_for_group,
        )

        fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0 = fc_state
        fc_active = fc_vs_ctrl or fc_vs_t0
        _val_col = val_col if val_col is not None else self._export_val_col_for("_line_channel_cb")

        # Four stacked panels — identical to the tab (lineplot/tab_view.py).
        fig = _Figure(figsize=(10, 13), dpi=300, facecolor=theme.CPub.bg)
        ax_mean = fig.add_subplot(4, 1, 1)
        ax_frac = fig.add_subplot(4, 1, 2, sharex=ax_mean)
        ax_n = fig.add_subplot(4, 1, 3, sharex=ax_mean)
        ax_cdf = fig.add_subplot(4, 1, 4)
        fig.subplots_adjust(hspace=0.55, top=0.93, bottom=0.06, left=0.13, right=0.97)
        fig.suptitle(grp.name, fontsize=11, fontweight="bold", color=theme.CPub.text, y=0.98)

        legend_kw = dict(fontsize=7, framealpha=0.0, facecolor="none",
                         edgecolor=theme.CPub.spine, labelcolor=theme.CPub.text)
        _ch = (self._selected_export_channel() or self._ctx.state.channel.active_channel).upper()
        from well_viewer.data.metric_labels import (
            METRIC_KEY_TO_LABEL as _MLB,
            threshold_qualifier, fraction_title,
            events_per_fov_title, events_total_title,
        )
        _metric_key = self._selected_export_metric_key("_line_channel_cb")
        _metric_label = _MLB.get(_metric_key, "Mean Intensity")
        _thr_q = threshold_qualifier(_metric_key)
        _fov_active = bool(self._ctx.state.plot.use_fov_replicates)
        _fc_suffix = _fc.fold_change_suffix(
            fc_vs_ctrl, fc_vs_t0, self._fc_control_label,
        ) if fc_active else ""
        apply_ax_style(ax_mean, f"{_ch} {_metric_label}{_thr_q} \u00b1 {band_lbl}{_fc_suffix}", f"{_ch} {_metric_label}{_fc_suffix}")
        apply_ax_style(ax_frac, f"{fraction_title(_metric_key)}{_fc_suffix}", "Fraction" + _fc_suffix)
        if _fov_active:
            _n_title, _n_ylabel = events_per_fov_title(_metric_key, band_lbl), "N(above)/FOV"
        else:
            _n_title, _n_ylabel = events_total_title(_metric_key), "N(above)"
        apply_ax_style(ax_n, f"{_n_title}{_fc_suffix}", _n_ylabel + _fc_suffix)
        apply_ax_style(ax_cdf, f"{_ch} {_metric_label} CDF", "Cumulative fraction")
        # Each time panel (mean / fraction / N) shows its own timepoints +
        # x-label, matching the on-tab figure (lineplot/controller.py). The
        # panels are well-separated by hspace, so each reads as its own graph.
        # Same rescale the on-tab Line figure applies, for the same reason: two
        # figures of the same series must not print two different time axes.
        from well_viewer.core.plot_style import apply_time_rescale
        from well_viewer.domain.time_rescale import axis_label as _time_axis_label
        _time_scale = self._ctx.state.plot.time_scale
        _time_lbl = _time_axis_label("Time (hours)", _time_scale)
        for _ax_time in (ax_mean, ax_frac, ax_n):
            _ax_time.set_xlabel(_time_lbl, fontsize=8, labelpad=5)
            apply_time_rescale(_ax_time, _time_scale)
        ax_frac.set_ylim(-0.05, 1.05)
        ax_cdf.set_xlabel(f"{_ch} {_metric_label}", fontsize=8, labelpad=5)
        ax_cdf.set_ylim(-0.02, 1.05)

        # Batch collector (tab rep-set stat for members, aggregate_well for
        # solos) feeds the SAME renderer the on-tab redraw uses, so the batch
        # figure gets the identical four panels (incl. N(above)).
        series_list = collect_line_series_for_group(
            self._ctx, grp,
            threshold=threshold, use_sem=use_sem,
            val_col=_val_col, fluor_gates=_get_all_fluor_gates(self._ctx),
            fc_state=fc_state, include_cdf=True,
        )
        _render_line_series(
            _LineAxes(ax_mean, ax_frac, ax_n, ax_cdf), series_list,
            fov_active=_fov_active, fc_active=fc_active, threshold=threshold,
            legend_kw=legend_kw, warn_color=theme.Colors.warn, muted_fg=theme.CPub.text,
            legend_visible=None,
            # Batch has always drawn the CDF threshold line and derives its
            # x-range from the data (cdf_xrange=None).
            show_threshold_line=True, cdf_xrange=None,
            draggable_legends=False,
        )
        return fig

