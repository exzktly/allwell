"""Line-mode batch export panel (also the base class for bar/scatter)."""

from __future__ import annotations

import csv
import re

import theme
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Callable

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
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

from widgets.plate_geometry import PLATE_ALIGNMENT, apply_plate_geometry
from widgets.well_plate_selector import WellPlateSelector
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
from well_viewer.core.ui_helpers import build_hline_separator
from well_viewer.core.channel_state_controller import (
    get_all_fluor_gates as _get_all_fluor_gates,
    on_cutoff_for_column as _on_cutoff_for_column,
)
from util.well_token import canonical_well_label

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_FC_BASELINE_COMBO_MIN_W = 160
_FC_CTRL_COMBO_MIN_W = 160
_LEFT_MAX_W = 560
_LEFT_MIN_W = 420
_OUT_DIR_EDIT_MIN_W = 240
_PROG_BAR_W = 180
_TP_LB_MIN_H = 120
# Sibling-feature ctx forms reached through core.lazy proxies — a call, not an
# import statement, so no module-load cycle and no deferred-import-budget hit.
from well_viewer.domain.numeric import is_number
from well_viewer.domain.selections_model import replicate_sets_loaded


def _rep_sets_loaded(ctx):
    """All rep-sets with >=1 loaded well (hidden included), from typed state."""
    return replicate_sets_loaded(ctx.state.selection.selections,
                                 ctx.state.dataset.well_paths)


_col_for_scatter_axis = _lazy("well_viewer.features.scatter.controller", "col_for_scatter_axis")
_get_all_profile_names = _lazy("well_viewer.features.export.style", "get_all_profile_names_ctx")
_ensure_custom_export_profiles = _lazy("well_viewer.features.export.style", "ensure_custom_export_profiles_ctx")
#: The one slot in ``ctx.runtime.batch_export.export_groups_doc`` that every
#: Batch Export mode reads and writes. Export groups describe the dataset's
#: conditions, not the plot type they happen to be drawn as.
SHARED_PERSIST_KEY = "groups"

#: The four per-mode slots written before the modes shared one. Read once, in
#: this order, to seed the shared slot on a document that predates it; never
#: written. They are left in the document rather than deleted, so rolling the
#: app back finds them.
_LEGACY_PERSIST_KEYS = ("line", "bar", "scatter_cells", "scatter_agg")

# The Line-Graphs tab's shared renderer, reused by the batch line figure (proxy
# is callable, so ``_LineAxes(...)`` constructs the dataclass) — a call, not an
# import statement, so it stays off the deferred-import budget.
_render_line_series = _lazy("well_viewer.features.lineplot.controller", "render_line_series")
_LineAxes = _lazy("well_viewer.features.lineplot.controller", "LineAxes")

# Shared width for OUTPUT-SETTINGS row labels so the value controls line up in a
# single column (Folder / Format / Style Preset / Channel / Fold change).
_FIELD_LABEL_W = 108


def _norm_well_token(tok: str) -> str:
    """Canonicalise a well token (``a1`` → ``A01``) for membership tests.

    Anything that is not a 96-well position comes back upper-cased but
    otherwise untouched, so it simply fails the membership test it is being
    prepared for rather than being coerced into a plausible-looking key.
    """
    return canonical_well_label(tok) or tok.strip().upper()


def _attach_fc_row(row: dict, npt: tuple, band_lbl: str,
                   vs_control: bool, vs_t0: bool, control_label: str,
                   outcome: str = "") -> None:
    """Add the fold-change columns to a per-timepoint CSV row.

    Used by both the line and bar batch exporters so the column shape stays
    identical across paths — and identical to the on-tab CSV's, which is what
    lets someone compare an exported plate against a tab export at all.

    ``control_label`` is **this row's** control. Under per-sample controls the
    rows differ, and one value repeated down the column would misstate every
    denominator but one. ``outcome`` says why an empty ``fold_change_mean`` is
    empty; without it a normalization that failed reads the same as one that
    was never asked for.
    """
    fmean = npt[1] if len(npt) > 1 else float("nan")
    fspread = npt[2] if len(npt) > 2 else 0.0
    has_mean = is_number(fmean)
    has_spread = is_number(fspread)
    row["fold_change_mean"] = f"{fmean:.6f}" if has_mean else ""
    row[f"fold_change_{band_lbl.lower()}"] = f"{fspread:.6f}" if has_spread else ""
    parts: list = []
    if vs_control:
        parts.append("control")
    if vs_t0:
        parts.append("t0")
    row["fold_change_mode"] = "+".join(parts) or "off"
    row["fold_change_control"] = control_label if vs_control else ""
    row["fold_change_outcome"] = str(outcome or "") if vs_control else ""


def _apply_split_sep_orientation(sep: QFrame, *, stacked: bool) -> None:
    """Point *sep* along the split's current axis, name and shape together.

    The Batch Export output panel folds from side-by-side to stacked when the
    viewport narrows, and its divider turns with it. That makes it the one
    divider in the app whose orientation is not fixed at build time, so it
    cannot take a fixed object name: ``theme.qss`` pins ``QFrame#HRule`` to
    ``max-height: 1px`` and ``QFrame#VRule`` to ``max-width: 1px``, and either
    name applied to the other orientation renders a hairline stretched along
    the wrong axis. Leaving it unnamed is worse still — an ``HLine`` / ``VLine``
    frame with no sanctioned name keeps Qt's default etched border, 3 px of
    near-white on this app's dark surfaces.

    An object-name change does not repaint on its own: Qt's stylesheet cache
    only re-evaluates a selector on an explicit repolish, the same reason
    ``widgets/_support.set_variant`` exists for dynamic properties.
    """
    sep.setFrameShape(QFrame.HLine if stacked else QFrame.VLine)
    sep.setObjectName("HRule" if stacked else "VRule")
    sep.style().unpolish(sep)
    sep.style().polish(sep)


class SkippedJob(str):
    """A job that produced nothing — a *notice*, not an error.

    ``_run_batch_jobs`` is all-or-nothing on purpose: any job returning a
    message discards the whole staging directory, so a failed re-run cannot
    leave a half-written export beside the previous complete one. That makes
    the return channel load-bearing, and it is why "this group had no rows at
    the selected timepoint" must not travel down it — reporting an empty group
    that way would throw away every *good* group's CSVs and figures in the same
    run and tell the user only "Done with 1 error(s)".

    Subclasses ``str`` so it is truthy and logs like the messages beside it,
    while the runner can tell the two apart by type.
    """


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
        # reads/writes. Every mode resolves to the same one — see
        # _compute_persist_key. Still a method call so a future mode that
        # genuinely needs its own slot has an obvious place to say so, and safe
        # to call here because the subclass state it might need (e.g.
        # _scatter_mode) is set before super().__init__.
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
        # This panel's own control, seeded from the plot tab and never
        # written back — a batch job must not follow the live figure.
        self._fc_control = _plot.fc_control
        # Per-sample controls are seeded the same way and for the same reason:
        # a panel opened while the tab shows a per-sample figure should export
        # that figure. The panel's own control combo offers no per-sample
        # entry, so the mapping is inherited and not editable here — a member
        # of an export group that the mapping does not name comes out blank
        # and says so, rather than quietly falling back to one control.
        self._fc_control_mode: str = str(_plot.fc_control_mode or "single")
        self._fc_control_map: dict = dict(_plot.fc_control_map or {})
        self._fc_control_render: str = str(_plot.fc_control_render or "self")
        self._fc_vs_t0_on: bool = bool(_plot.fc_vs_t0_on)

        # Hydrate from the persisted export groups (persistence.json), else start
        # empty. Batch-export groups are export-specific, not the Sample-
        # Definition replicate sets — the user opts in to seeding from the app
        # via the "Sync from app" button or the Quick Setup menu.
        self._groups: list[BarGroup] = self._groups_from_persistence()
        self._active_grp = 0 if self._groups else -1
        self._auto_named_group_ids: set[int] = set()
        self._drag_adding = True
        self._drag_visited: set = set()

        self._build_ui()
        if not self._use_sidebar_groups:
            self._refresh_group_list()
        self._suppress_persist = False

    def _compute_persist_key(self) -> str:
        """Slot key in ``app._export_groups_doc`` — shared by every mode.

        Line, Bar, Scatter (cells) and Scatter (aggregate) used to keep four
        private sets under ``"line"`` / ``"bar"`` / ``"scatter_cells"`` /
        ``"scatter_agg"``, so groups built in one mode were invisible in the
        others and anyone exporting the same conditions four ways built them
        four times. The groups are a property of the dataset, not of the plot
        type, so there is one slot; :data:`_LEGACY_PERSIST_KEYS` covers the
        documents written before that.
        """
        return SHARED_PERSIST_KEY

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
        left.setMinimumWidth(_LEFT_MIN_W)
        left.setMaximumWidth(_LEFT_MAX_W)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self._build_group_editor(left_layout)
        outer.addWidget(left)

        sep = QFrame()
        sep.setFrameShadow(QFrame.Plain)
        _apply_split_sep_orientation(sep, stacked=False)
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
        from PySide6.QtWidgets import QBoxLayout
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
        _apply_split_sep_orientation(self._split_sep, stacked=stack)
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

        layout.addWidget(build_hline_separator(None))

        hint = QLabel("Left-drag wells onto the plate map to add them to the selected group.")
        hint.setWordWrap(True)
        hint.setContentsMargins(6, 2, 6, 2)
        layout.addWidget(hint)

        # The app's plate control, in its "paint" selection model: the plate
        # reports the press/drag/release gesture and this panel applies it to
        # the active group. It owns no selection of its own, because export
        # group membership is not a set of clicked wells — dropping one well
        # can pull in the replicate set it belongs to, and several groups
        # overlap on the same plate with the last one winning the colour.
        #
        # This used to be 96 flat 28x22px QPushButtons in a QGridLayout, with
        # each one's mousePressEvent/mouseMoveEvent/mouseReleaseEvent
        # reassigned to this panel's methods (enabled buttons swallow mouse
        # events, so the container's handlers never fired). Rectangles on a
        # grid do not read as a plate, and this was the one well grid in the
        # app that shared nothing with the others.
        self._map_plate = WellPlateSelector()
        # "editor", not "rail": the rail role is exempt from the size caps
        # because Review's left-rail picker is the app's primary interaction
        # surface and scales to whatever width the rail gives it. This plate
        # sits in a panel above a group list, and uncapped it reserves a card
        # as tall as heightForWidth allows — 389px against the old grid's 212,
        # taking 164px from the group list in the side-by-side layout, with the
        # grid floating in the middle of a mostly-empty card.
        apply_plate_geometry(self._map_plate, role="editor")
        self._map_plate.setSelectionModel("paint")
        self._map_plate.setColoredWellsLit(True)
        self._map_plate.wellPaintPressed.connect(self._plate_press)
        self._map_plate.wellPaintDragged.connect(self._plate_dragged)
        self._map_plate.paintDragFinished.connect(self._plate_release)
        # Headers stay inert. The panel's only row/column action ("One group
        # per row / column") replaces every existing group, so putting it on a
        # header click would make a stray click on the plate's edge destroy the
        # groups the user had built.
        self._map_plate.setRowColumnSelectable(False)
        layout.addWidget(self._map_plate, 0, PLATE_ALIGNMENT)

        layout.addWidget(build_hline_separator(None))

        self._grp_scroll = QScrollArea()
        self._grp_scroll.setWidgetResizable(True)
        self._grp_inner = QWidget()
        self._grp_inner_layout = QVBoxLayout(self._grp_inner)
        self._grp_inner_layout.setContentsMargins(4, 2, 4, 2)
        self._grp_inner_layout.addStretch(1)
        self._grp_scroll.setWidget(self._grp_inner)
        layout.addWidget(self._grp_scroll, 1)

    def _all_well_tokens(self) -> list[str]:
        """Every plate position, so a colour map can clear the ones no group
        owns as well as set the ones that do."""
        return [f"{row}{col}" for row in _PLATE_ROWS for col in _PLATE_COLS]

    def _plate_press(self, tok: str) -> None:
        """Left-press on *tok*: latch the gesture's polarity and apply it.

        Whether the whole drag adds or removes is decided by the well it
        started on, so dragging across a group's own wells removes them and
        dragging across empty ones adds — one gesture, one direction, rather
        than each well toggling under the cursor.
        """
        if tok not in self._ctx.state.dataset.well_paths:
            return
        grp = self._active_group()
        if grp is None:
            return
        self._drag_adding = tok not in grp.wells
        self._drag_visited = set()
        self._apply_drag(tok)

    def _plate_dragged(self, tok: str) -> None:
        self._apply_drag(tok)

    def _plate_release(self) -> None:
        if self._drag_visited:
            self._refresh_map()
            self._refresh_group_list()
        self._drag_visited = set()

    def _build_output_panel(self, layout: QVBoxLayout) -> None:
        self._build_output_header_and_io(layout)
        self._build_channel_row(layout, attr="_line_channel_cb")

        layout.addWidget(build_hline_separator(None))

        info = QLabel(
            "Each export group produces:\n"
            "  \u2022 One figure with one line per group member\n"
            "    (ReplicateSet = mean of its wells, Solo well = raw)\n"
            "  \u2022 One CSV with per-member time-series data\n\n"
            + ("Groups come from Home → Groups.\n"
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
        cb.setMinimumWidth(theme.Metrics.combo_min_w)
        setattr(self, attr, cb)
        row.addWidget(cb)

        prop_lbl = QLabel("Property:")
        pf = prop_lbl.font(); pf.setBold(True); prop_lbl.setFont(pf)
        row.addWidget(prop_lbl)
        prop_cb = QComboBox()
        prop_cb.setMinimumWidth(theme.Metrics.combo_min_w)
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
            # Restored unconditionally. Guarding on ``saved is not None`` treated
            # "the app had no column selected" as "there is nothing to put back",
            # which is the opposite: it is precisely then that the batch's own
            # column stays behind on shared app state after the export, so the
            # next tab plot silently draws whatever the last exported group
            # happened to pick. ``None`` is the value that was there, so ``None``
            # is what goes back — the sibling ``_app_group_scope`` already does.
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
        mean-of-per-well-means.

        The swap **replaces** the list rather than adding to it, which is what
        made a fold-change control naming a replicate set in a *different* batch
        group resolve to nothing: ``resolve_control`` finds a ``kind="set"``
        reference by scanning exactly this list, and one batch group per
        condition with the control as its own group is the normal layout. Every
        curve in the group then drew blank. So any replicate set the panel's
        control names is carried in **hidden**, which is not a trick — hiding a
        group takes it off the plot and does not stop it being usable as a
        control: ``active_replicate_sets`` skips hidden entries, so nothing
        extra is drawn, and ``resolve_control`` does not, so the control
        resolves."""
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
            selections.extend(self._control_selections(
                saved[0], used_names, used_ids, len(selections)))
            sel.selections = selections
            sel.current_selection_id = None
            sel.selected_wells = set(grp.solo_wells)
            yield
        finally:
            (sel.selections, sel.current_selection_id, sel.selected_wells) = saved

    def _control_selections(self, live_selections, used_names: set,
                            used_ids: set, color_idx: int) -> list:
        """Hidden entries for the replicate sets this panel's control names.

        Carried into the group scope so a control belonging to another batch
        group still resolves. Hidden, so nothing new is drawn. Their wells come
        from the pre-swap list: the group being scoped does not contain them,
        which is the whole problem.
        """
        from well_viewer.domain.control_ref import KIND_SET
        from well_viewer.domain.selections_model import make_selection

        wanted: list[str] = []
        for ref in [self._fc_control, *(self._fc_control_map or {}).values()]:
            if ref is None or getattr(ref, "kind", "") != KIND_SET:
                continue
            if ref.name not in wanted:
                wanted.append(ref.name)
        by_name = {s.get("name"): s for s in (live_selections or [])}
        out: list = []
        for i, name in enumerate(wanted):
            if name in used_names:
                continue    # the group already carries it
            src = by_name.get(name)
            if src is None:
                # Names nothing that is loaded. Left alone so the outcome
                # vocabulary reports it as unresolved rather than a synthetic
                # empty group standing in for one the user deleted.
                continue
            out.append(make_selection(
                name=name, wells=list(src.get("wells") or []), source="rep_set",
                hidden=True, used_names=used_names, used_ids=used_ids,
                fallback_color_idx=color_idx + i,
            ))
        return out

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
        self._out_dir_edit.setMinimumWidth(_OUT_DIR_EDIT_MIN_W)
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
        self._prog_bar.setFixedWidth(_PROG_BAR_W)
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

    #: Shown in the Control combo when the panel inherited a PER-SAMPLE mapping
    #: from the plot tab. That mapping names a different control per member, so no
    #: single entry in this list describes it — and the panel deliberately does not
    #: let it be edited here (see ``__init__``). Before this entry existed the
    #: combo fell through to ``—``, i.e. it said "no control" on a run that was
    #: about to normalise every member against one.
    _FC_PER_SAMPLE_LABEL = "Per-sample (from the plot tab)"

    #: The item data that entry carries. A plain sentinel, not a ``ControlRef``:
    #: the handler must be able to tell "the user re-picked the inherited mode"
    #: from "the user named one control", and ``None`` already means ``—``.
    _FC_PER_SAMPLE = object()

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
        self._fc_ctrl_combo.setMinimumWidth(_FC_CTRL_COMBO_MIN_W)
        self._fc_ctrl_combo.setToolTip(
            "Divide each bar / curve by the picked well or replicate "
            "set's mean at the same timepoint. — disables this axis. "
            "\"Per-sample\" means the panel inherited a different control per "
            "member from the plot tab; picking any single entry here replaces "
            "that mapping for this export."
        )
        self._repopulate_fc_control_combo()
        row.addWidget(self._fc_ctrl_combo)

        row.addWidget(QLabel("Baseline"))
        self._fc_baseline_combo = QComboBox()
        self._fc_baseline_combo.setMinimumWidth(_FC_BASELINE_COMBO_MIN_W)
        self._fc_baseline_combo.setToolTip(
            "Divide each bar / curve by its own value at the reference "
            "timepoint. — disables this axis."
        )
        self._fc_baseline_combo.addItem(self._FC_NONE_LABEL)
        self._fc_baseline_combo.addItem(self._FC_T0_LABEL)
        self._fc_baseline_combo.setCurrentIndex(1 if self._fc_vs_t0_on else 0)
        row.addWidget(self._fc_baseline_combo)

        def _on_ctrl_change(_i: int) -> None:
            # Read the reference the item carries, not its text.
            ref = self._fc_ctrl_combo.currentData()
            if ref is self._FC_PER_SAMPLE:
                # Re-selecting the inherited mapping is not a control pick — it
                # leaves the panel exactly as it was seeded.
                return
            self._fc_vs_control_on = ref is not None
            self._fc_control = ref
            # Naming one control here is a request for one control. Leaving the
            # inherited per-sample mapping on would ignore the pick silently.
            self._fc_control_mode = "single"
            self._fc_control_map = {}
            # The inherited mapping is gone, so the entry that named it must go
            # too — leaving it listed would let the user re-select a mode the
            # panel is no longer in. Removed in place rather than by a full
            # repopulate: this runs inside the combo's own currentIndexChanged,
            # and clearing a combo from its own signal handler is re-entrant.
            self._drop_per_sample_entry()

        def _on_baseline_change(_i: int) -> None:
            self._fc_vs_t0_on = (
                self._fc_baseline_combo.currentText() == self._FC_T0_LABEL
            )

        self._fc_ctrl_combo.currentIndexChanged.connect(_on_ctrl_change)
        self._fc_baseline_combo.currentIndexChanged.connect(_on_baseline_change)

    def _drop_per_sample_entry(self) -> None:
        """Remove the inherited per-sample entry from the Control combo, if listed.

        Signals stay blocked: the caller is the combo's own change handler, and
        the removal must not look like a second pick."""
        combo = getattr(self, "_fc_ctrl_combo", None)
        if combo is None:
            return
        for i in range(combo.count()):
            if combo.itemData(i) is self._FC_PER_SAMPLE:
                blocked = combo.blockSignals(True)
                try:
                    combo.removeItem(i)
                finally:
                    combo.blockSignals(blocked)
                return

    def _repopulate_fc_control_combo(self) -> None:
        combo = getattr(self, "_fc_ctrl_combo", None)
        if combo is None:
            return
        from well_viewer.domain.control_ref import ControlRef

        # Replicate-set names and well tokens are tracked apart. One shared
        # "seen" set made a solo well whose token equalled a group name look
        # like a duplicate of it, so the well vanished from the list entirely —
        # the same collision the plot tab's combo had.
        entries: list[tuple[str, object]] = []
        set_names: set = set()
        seen_wells: set = set()
        for grp in self._groups_for_export() if hasattr(self, "_groups_for_export") else self._groups:
            for rset in grp.members:
                if rset.name and rset.name not in set_names:
                    entries.append((rset.name, ControlRef.for_replicate_set(rset.name)))
                    set_names.add(rset.name)
            for w in grp.solo_wells:
                if w and w not in seen_wells:
                    ref = ControlRef.for_well(w)
                    entries.append((ref.combo_text(disambiguate=w in set_names), ref))
                    seen_wells.add(w)
        # Fall back to plate-loaded wells when no groups are defined yet —
        # users may want to pick a control before they've finished building
        # the group editor.
        if not entries:
            for w in sorted((self._ctx.state.dataset.well_paths or {})):
                entries.append((w, ControlRef.for_well(w)))
        saved = self._fc_control
        # A per-sample mapping inherited from the plot tab is a real control
        # setting that no concrete entry in this list can stand for, so it gets
        # its own entry and the combo selects it. Offered ONLY while that mode is
        # actually in force: it is a statement about the panel's inherited state,
        # not a mode the user can switch into from here.
        per_sample = (str(self._fc_control_mode or "single") == "per_sample"
                      and bool(self._fc_control_map))
        blocked = combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem(self._FC_NONE_LABEL, None)
            if per_sample:
                combo.addItem(self._FC_PER_SAMPLE_LABEL, self._FC_PER_SAMPLE)
            for text, ref in entries:
                combo.addItem(text, ref)
            if per_sample:
                combo.setCurrentIndex(1)
            elif saved is not None:
                for i in range(combo.count()):
                    if combo.itemData(i) == saved:
                        combo.setCurrentIndex(i)
                        break
        finally:
            combo.blockSignals(blocked)

    def _fc_state(self):
        """This panel's own fold-change request.

        Panel-local by design: a batch job must not be tied to whatever the
        on-screen plot currently has set, so these fields are seeded from
        ``ctx.state.plot`` at construction and never written back.
        """
        from well_viewer.domain.fold_change_state import FoldChangeState
        return FoldChangeState(
            vs_control_on=bool(self._fc_vs_control_on),
            control=self._fc_control,
            vs_t0_on=bool(self._fc_vs_t0_on),
            control_mode=str(self._fc_control_mode or "single"),
            control_map=tuple(sorted((self._fc_control_map or {}).items())),
            control_render=str(self._fc_control_render or "self"),
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
        self._tp_lb.setMinimumHeight(_TP_LB_MIN_H)
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
            # Membership is tested by *name*, not by object identity.
            # ``rep_sets_loaded`` builds a fresh ``ReplicateSet`` on every call
            # and the class has no ``__eq__``, so ``rset in grp.members`` was
            # always False: dragging across two wells of one sample appended
            # that sample twice (the group then exports the condition twice),
            # and dragging a sample back off a group removed nothing at all.
            # The name is already the identity key everywhere else — it is what
            # ``_serialize_groups`` persists and what hydration matches on.
            existing = next((m for m in grp.members if m.name == rset.name), None)
            if self._drag_adding:
                if existing is None:
                    grp.members.append(rset)
            elif existing is not None:
                grp.members.remove(existing)
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
        """Repaint one well mid-drag, without the whole-plate pass.

        A drag can cross dozens of wells, and each one only changes its own
        colour; ``_refresh_map`` runs once at the end of the gesture.
        """
        if tok not in self._ctx.state.dataset.well_paths:
            return
        # Last group containing the well wins, matching _refresh_map.
        owner = next((gi for gi in range(len(self._groups) - 1, -1, -1)
                      if tok in self._groups[gi].wells), None)
        self._map_plate.setWellColor(
            tok, self._group_color(owner) if owner is not None else None)
        # The ring says "in the group being edited", so it has to move with the
        # colour. Left to the end-of-gesture ``_refresh_map``, a well dragged
        # out of the active group would sit ringed but uncoloured for the rest
        # of the drag.
        grp = self._active_group()
        self._map_plate.setWellRing(tok, bool(grp is not None and tok in grp.wells))

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
        # Wells the dataset does not have are disabled rather than tinted grey
        # by hand — the plate already draws an absent well as an empty socket,
        # and a disabled well ignores the paint gesture for free.
        # ``sorted(avail)``, never ``or None`` — ``None`` means "all 96
        # enabled", so a dataset with no wells at all would render a full plate
        # of live-looking wells that silently reject every click, instead of 96
        # empty sockets.
        self._map_plate.setEnabledWells(sorted(avail))
        self._map_plate.setWellColors(
            {tok: tok_color.get(tok) for tok in self._all_well_tokens()})
        # The active group's wells carry an accent ring on top of their group
        # colour: which group you are editing is a different question from
        # which group a well is in, so it gets its own channel rather than
        # overloading the fill.
        self._map_plate.setWellRings(sorted(active_wells & avail))

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
        """Hydrate the shared export groups from the batch-export groups doc.

        A document written before the modes shared a slot has no shared key and
        up to four per-mode ones. Those are merged, in
        :data:`_LEGACY_PERSIST_KEYS` order, de-duplicated by group name with the
        first occurrence winning — the common case being the same groups
        rebuilt in several modes, which collapses to exactly the one set the
        user meant. A union rather than picking one slot because the uncommon
        case is four genuinely different sets, and picking would silently
        delete three of them.

        Only when the shared key is absent. Once it exists it is the whole
        answer, so deleting a group cannot be undone by a legacy slot
        re-supplying it on the next load.
        """
        doc = self._ctx.runtime.batch_export.export_groups_doc or {}
        if self._persist_key in doc:
            return self._deserialize_groups(doc.get(self._persist_key))
        merged: list = []
        seen: set[str] = set()
        for key in _LEGACY_PERSIST_KEYS:
            for item in doc.get(key) or []:
                name = str(item.get("name", "")) if isinstance(item, dict) else ""
                if name in seen:
                    continue
                seen.add(name)
                merged.append(item)
        return self._deserialize_groups(merged)

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
        skipped: list[str] = []
        staging = new_staging_dir(out_dir)
        try:
            for step, job in enumerate(jobs, 1):
                self._prog_lbl.setText(progress_text_fn(job, step, len(jobs)))
                self._prog_bar.setValue(step - 1)
                QApplication.processEvents()
                err = run_job_fn(job, staging)
                if isinstance(err, SkippedJob):
                    skipped.append(str(err))
                elif err:
                    errors.append(err)
            self._prog_bar.setValue(len(jobs))
            if errors or len(skipped) == len(jobs):
                # Nothing was written when every group was skipped, and
                # committing then replaces the previous run's manifest with
                # ``{"files": [], "count": 0}`` while that run's actual files
                # stay on disk — an audit record contradicting the directory it
                # describes.
                discard_staging(staging)
            else:
                commit_staged_export(staging, out_dir)
        except BaseException:
            # Never leave a half-committed export or a stray temp dir behind.
            discard_staging(staging)
            raise
        finally:
            self._run_btn.setEnabled(True)
            self._prog_bar.setVisible(False)

        for note in skipped:
            # Logged before the error branch returns: a run that hit both a
            # skip and a real failure used to report only the failure, so the
            # groups that produced nothing went unmentioned in the one place
            # the user is told to look.
            _logger.info("Batch export: %s", note)
        if errors:
            note = (f" · {len(skipped)} with no data" if skipped else "")
            self._prog_lbl.setText(
                f"Done with {len(errors)} error(s){note}. See log.")
            self._prog_lbl.setStyleSheet(f"color: {get_color('CLR_DANGER')};")
            for err in errors:
                _logger.error("Batch export error: %s", err)
            return
        # A skipped group is not a failure, but it is also not a success to be
        # counted silently: the headline used to report the number of groups
        # *submitted*, so a run where half of them produced nothing looked
        # identical to one where they all did.
        if skipped:
            done = len(jobs) - len(skipped)
            self._prog_lbl.setText(
                f"✓ {done} group(s) → {out_dir.name}/ · {len(skipped)} with no data")
            self._prog_lbl.setStyleSheet(f"color: {get_color('CLR_SUCCESS_DARK')};")
            self._ctx.notify.set_status(
                f"{status_text} · {len(skipped)} group(s) had no data")
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
                "Define at least one non-empty group on Home → Groups."
                if self._use_sidebar_groups
                else "Define at least one non-empty group."
            )
            QMessageBox.warning(self, "No groups", msg)
            return
        self._log_sample_definitions_snapshot(groups_with_data)
        out_dir = self._resolve_out_dir()
        if out_dir is None:
            return

        # The (Channel, Property) selection is read ONCE, here, and every job in
        # the run uses this one answer. The runner pumps ``processEvents()``
        # between jobs (``_run_batch_jobs``), so a combo the user touches while
        # the export is running changes under it — and re-reading only *part* of
        # the pair per job produced the worst possible version of that: the
        # channel stayed on its snapshot while the column and the metric moved,
        # so a group was exported as "gfp" from ``mcherry_total_intensity``, in a
        # file named and titled for the wrong one of the two. A run now exports
        # one configuration or, if the user changes the controls, still one — the
        # one that was on screen when they pressed the button.
        _ch_selected = self._selected_export_channel() or self._ctx.state.channel.active_channel
        _val_col_selected = self._export_val_col_for("_line_channel_cb")
        _metric_selected = self._selected_export_metric_key("_line_channel_cb")
        # The cutoff lookup keys by the canonical key (bare channel token or
        # ``ratio:<name>``), not by the dropdown's display label -- translate
        # ratio labels first so the user's configured cutoff is honoured
        # instead of the default that empties out the plot. Which cutoff
        # (intensity or spot count) follows the selected column.
        threshold = _on_cutoff_for_column(
            self._ctx,
            _val_col_selected,
            channel=self._resolved_channel_key(_ch_selected),
        )
        use_sem = self._ctx.state.plot.use_sem
        band_lbl = "SEM" if use_sem else "SD"
        fmt = self._fmt_cb.currentText()

        def _progress(grp: BarGroup, step: int, total: int) -> str:
            return f"Exporting '{grp.name}' ({step}/{total})\u2026"

        _panel_fc = self._fc_state()
        fc_vs_ctrl = _panel_fc.vs_control_on
        fc_vs_t0 = _panel_fc.vs_t0_on
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
            _val_col = _val_col_selected
            _ch = _ch_selected
            _metric = _metric_selected
            _fluor_gates = _get_all_fluor_gates(self._ctx)
            # The panel's own request object, passed through rather than
            # rebuilt as a tuple — rebuilding it drops any field the
            # three legacy names do not cover.
            panel_fc_state = _panel_fc

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
                        _control_cell, _well_labels_map,
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
                        fc_series_by_key = {s.key: s for s in series_fc}
                    else:
                        fc_series_by_key = {}
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
                        fc_series = fc_series_by_key.get(series.key)
                        fc_pts = fc_series.points if fc_series is not None else []
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
                                per_fov=bool(
                                    self._ctx.state.plot.use_fov_replicates),
                            ))
                            if fc_active and i < len(fc_pts):
                                _attach_fc_row(
                                    row, fc_pts[i].as_aggpoint(), band_lbl,
                                    fc_vs_ctrl, fc_vs_t0,
                                    _control_cell(getattr(fc_series, "fc_control", None)),
                                    getattr(fc_series, "fc_outcome", ""),
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
                                "fold_change_outcome",
                            ]
                        with open(csv_path, "w", newline="") as fh:
                            writer = csv.DictWriter(fh, fieldnames=fieldnames)
                            writer.writeheader()
                            writer.writerows(rows_out)
                except OSError as exc:
                    return f"{grp.name} CSV: {exc}"

                if not rows_out:
                    # See the note in ``bar_panel``: a blank figure saved
                    # beside a missing CSV, reported as success, is the same
                    # silent shortfall. ``SkippedJob``, not a plain string —
                    # the runner discards the whole staging directory for any
                    # error, so reporting this as one would delete every other
                    # group's output too.
                    return SkippedJob(
                        f"{grp.name}: no data for this group — nothing exported")

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
        fc_state=None,
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

        _panel_fc = _fc.coerce_fc_state(fc_state)
        fc_vs_ctrl = _panel_fc.vs_control_on
        fc_vs_t0 = _panel_fc.vs_t0_on
        fc_active = _panel_fc.active
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
        # The exported figure's title counts the controls the exported figure
        # used, not the tab's — the panel carries its own groups, so its member
        # list and therefore its distinct-control count can differ.
        _fc_controls = None
        if fc_vs_ctrl and _panel_fc.per_member:
            from well_viewer.domain.fold_change_pairing import (
                build_per_member_controls,
            )
            _fc_members = (
                [r.name for r in grp.members] + list(grp.solo_wells))
            _fc_controls = build_per_member_controls(
                self._ctx, _fc_members, _panel_fc.control_map_dict,
                member_wells=(
                    {r.name: list(r.wells) for r in grp.members}
                    | {w: [w] for w in grp.solo_wells}),
                render=_panel_fc.control_render,
            )
        _fc_suffix = _fc.fold_change_suffix(
            fc_vs_ctrl, fc_vs_t0,
            self._fc_control.display_name if self._fc_control is not None else "",
            controls=_fc_controls,
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
        # Same rule as the on-tab figures (``barplot.renderer._set_frac_ylim``,
        # ``lineplot.controller``): a raw fraction is bounded so the clamp is a
        # good default, but under fold change the field is a ratio or a
        # difference and the clamp silently clips the effect out of the panel.
        # The batch twin of a fixed on-tab figure is exactly where a half-swept
        # rule hides — the exported PNG kept clipping after the tab stopped.
        if not fc_active:
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

