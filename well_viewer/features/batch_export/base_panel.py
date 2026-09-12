"""Line-mode batch export panel (also the base class for bar/scatter)."""

from __future__ import annotations

from well_viewer.data.metric_labels import spread_label

import csv
import re
import tempfile

from ui import theme
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Callable
from typing import NamedTuple

from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
from well_viewer.features.batch_export._cell_rows import write_cell_rows
from well_viewer.features.batch_export._destinations import DestinationSpool
from well_viewer.features.batch_export._panels import (
    AxesSpec, Panel, PanelAxis, figure_height, panel_axes, pin_scales,
)
# ── ctx-native god-method forms (A8 / Epic-A14 panel conversion) ─────────────
# Foundational-layer (core/domain/data) ctx forms — safe at module scope.
from well_viewer.core.lazy import lazy as _lazy
# Saved Plots is one-line removable, so its parameter capture is a proxy rather
# than an import statement — resolved on first call, and never called at all
# unless a run ticked one of the extra destinations.
_capture_params = _lazy("well_viewer.features.saved_plots.collect",
                        "capture_params")
from widgets.separator import build_hline_separator
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
_LIMIT_EDIT_W = 76
_OUT_DIR_EDIT_MIN_W = 240
_PROG_BAR_W = 180
_TP_LB_MIN_H = 120
# Sibling-feature ctx forms reached through core.lazy proxies — a call, not an
# import statement, so no module-load cycle and no deferred-import-budget hit.
from well_viewer.domain.numeric import is_number


#: The three answers the scale combos offer, and what each means to
#: ``_panels.pin_scales``. ``None`` is "leave it alone" and is deliberately not
#: the same as linear: a figure that derived a logarithmic axis from its own
#: data — the Response tab's dose axis — keeps it, where pinning ``False``
#: would take it linear for a user who never touched the control.
_SCALE_CHOICES = (
    ("Auto", None),
    ("Linear", False),
    ("Log", True),
)


def _parse_limit(text: str) -> float | None:
    """An axis bound the user typed, or ``None`` for blank and for nonsense.

    Unreadable reads as blank rather than as a kept previous value, the same
    rule the Response panel's fit bounds follow: a user who typed ``1OO`` and
    got an axis capped at 100 would have no way to tell the figure was not
    answering the question they asked. Blank is the honest answer to a field
    that says nothing, and the bound autoscales.
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _scale_choice(combo) -> bool | None:
    """The scale a combo is on — ``None`` for Auto. See :data:`_SCALE_CHOICES`."""
    if combo is None:
        return None
    index = combo.currentIndex()
    if 0 <= index < len(_SCALE_CHOICES):
        return _SCALE_CHOICES[index][1]
    return None


def _summarise_notes(notes) -> str:
    """One clause for a run's own shortfalls, however many there were.

    Counted rather than listed: forty groups that could not write a per-cell
    file would push everything else off a status line, and each note is already
    in the log with its group's name. The first is quoted so the sentence says
    what *kind* of problem it was rather than only how many.
    """
    notes = list(notes or [])
    if not notes:
        return ""
    if len(notes) == 1:
        return notes[0]
    return f"{notes[0]} (and {len(notes) - 1} more — see the log)"


def _rep_sets_loaded(ctx):
    """All rep-sets with >=1 loaded well (hidden included), from typed state."""
    from well_viewer.domain.plot_members import plot_replicate_sets_loaded
    return plot_replicate_sets_loaded(ctx)


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

#: What a ``QCheckBox``'s indicator and its gap to the text take, on top of the
#: text itself. Qt does not offer this as a metric that is right across styles,
#: so it is a measured constant rather than a computed one — generous by a few
#: pixels, because the cost of being short is a clipped panel name.
_CHECKBOX_INDICATOR_W = 26

#: Slack between the widest label and the first field, so the column does not
#: read as two words jammed together.
_LABEL_PADDING_W = 12


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
    # Only a point that HAS a normalized value reports a spread for it.
    # ``fold_change.blank_point`` blanks the mean and leaves the spread at 0.0,
    # so guarding the two independently put ``0.000000`` beside an empty mean
    # on exactly the rows whose outcome says the control never resolved.
    row["fold_change_mean"] = f"{fmean:.6f}" if has_mean else ""
    row[f"fold_change_{band_lbl.lower()}"] = (
        f"{fspread:.6f}" if has_mean and is_number(fspread) else "")
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


class _Destination(NamedTuple):
    """The "→ …" clause in two lengths: the progress label, and the status bar."""

    short: str
    long: str


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

    #: The Plotting leaf whose figure this mode exports. It is what a gallery
    #: record made from a batch figure names as its originating tab, so
    #: Restore takes the user to the tab the figure came from and the deck's
    #: slide badge says where it was drawn. Every subclass overrides it; a
    #: subclass that did not would file its figures under the line tab's name,
    #: which is a wrong answer rather than a missing one, so
    #: ``tests/test_batch_export_destinations.py`` pins the pairing.
    _SOURCE_TAB = "Line Graphs"

    #: Names this mode in the deck's file name (``batch_export_<key>.pptx``) and
    #: in a capture's id. A method rather than an attribute because the scatter
    #: panel is two modes in one class.
    def _mode_key(self) -> str:
        return "line"

    #: Every attribute a ``Channel:`` combo built by ``_build_channel_row`` can
    #: be stashed under, in the order a lookup should prefer them.
    #:
    #: **One roster, read by both the repopulate and the read-back**, because
    #: they were two identical tuples written inline and a mode whose combo
    #: joined only one of them fails in a way nothing reports: the combo is
    #: built, it is populated on the first show and never again, so a dataset
    #: opened after the panel silently exports against a channel list from
    #: before the load. A subclass adding a mode extends this rather than
    #: editing two loops.
    _CHANNEL_COMBO_ATTRS: tuple[str, ...] = ("_line_channel_cb", "_bar_channel_cb")

    #: The panels this mode's figure draws, in figure order — what the AXES
    #: block offers a row for, and what ``_panels.panel_axes`` lays out. A mode
    #: whose figure is a single axes declares one entry rather than none, so
    #: the axis controls read the same on every mode instead of appearing only
    #: on the stacked ones.
    _PANELS: tuple[Panel, ...] = (
        Panel("mean", "Value"),
        Panel("frac", "Fraction above cutoff"),
        Panel("n", "N above cutoff"),
        Panel("cdf", "CDF"),
    )

    #: Which of those share one x axis when drawn together. The line and bar
    #: stacks share a time axis; the CDF's x is intensity and does not.
    _SHARE_X: tuple[str, ...] = ("mean", "frac", "n")

    #: Which panels the **shared X row governs**. Not the same question as
    #: ``_SHARE_X``, though it has the same answer here: that one is about
    #: matplotlib's axis linking, this one about whose x the user is typing
    #: into. They come apart on the line mode's CDF, which plots intensity
    #: where its three siblings plot time — an X row filled in hours was
    #: clipping it to 0–48 intensity units, giving three correct panels and a
    #: fourth that looks empty with nothing saying why.
    #:
    #: ``None`` means every drawn panel, which is what a single-panel mode
    #: wants and what a stack whose panels really are one axis wants.
    _X_PANELS: tuple[str, ...] | None = ("mean", "frac", "n")

    #: The source tab's ``cell_scope_compute`` module, for the per-cell row
    #: export. A dotted module path rather than a proxy, because the modes'
    #: features are removable and the module is resolved only when a run has
    #: actually asked for the rows.
    _CELL_SCOPE: str = "well_viewer.features.lineplot.cell_scope_compute"

    #: The function in it. Named because Scatter's two modes have two: the
    #: aggregate tab's scope is a different plot with different markers and a
    #: different gate layer, and reading the per-cell one for it wrote a file
    #: whose own block said it was about the other page.
    _CELL_SCOPE_FN: str = "cell_scope"

    #: Whether this mode's Channel row carries its own fold-change controls.
    #: False only where the mode normalises through the shared plotting state
    #: rather than through the panel's own — see ``_build_channel_row``.
    _INCLUDES_FOLD_CHANGE = True

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
        #: The extra destinations for the run currently in flight, or ``None``
        #: between runs. Owned by ``_run_batch_jobs``, which is what creates it
        #: and what clears it, so a figure written outside a run cannot reach a
        #: destination belonging to the previous one.
        self._spool: DestinationSpool | None = None
        #: This run's AXES answer, or ``None`` between runs — owned by
        #: ``_run_batch_jobs`` exactly as ``_spool`` is, so a figure rendered
        #: outside a run cannot pick up the previous run's axes.
        self._run_axes: AxesSpec | None = None
        #: Shortfalls this run has hit, or ``None`` between runs. Same
        #: ownership as ``_spool`` and ``_run_axes``.
        self._run_notes: list[str] | None = None
        #: Only the scatter panel fills this; declared here so the shared
        #: runner can clear it without knowing which mode it is running.
        self._run_scatter_axes = None
        #: The destinations this run is writing to, or ``None`` between runs.
        #: See :meth:`_destinations` for why it is a snapshot.
        self._run_dests: frozenset | None = None

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

        self._build_summary_label(layout)
        self._build_run_row(layout)
        layout.addStretch(1)

    def _build_channel_row(self, layout: QVBoxLayout, *, attr: str,
                           include_fraction: bool = False) -> None:
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
        # Whether this row's Property combo offers ``Fraction above threshold``
        # — the one entry that is not an intensity column. Stashed on the combo
        # rather than passed to ``_refresh_property_combo_for``, because that
        # method is also called from the channel's own ``currentIndexChanged``
        # and from ``_refresh_channel_combos``, neither of which is holding the
        # flag. A flag only the build path knew would give the row the fraction
        # once and drop it the first time the channel moved.
        cb._include_fraction = bool(include_fraction)
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
        #
        # Not on every mode. The line, bar and scatter exports normalise through
        # this panel's *own* fold-change state, deliberately independent of the
        # plot tab; Response Curves normalises inside its collector, off the
        # shared ``ctx.state.plot`` fold-change state that every plotting page
        # shares. A panel-local control there would be a switch that does
        # nothing — offered, moved, and silently ignored by the export it
        # appears to configure — so that mode says where the setting lives
        # instead of duplicating it. See ``_INCLUDES_FOLD_CHANGE``.
        if not self._INCLUDES_FOLD_CHANGE:
            return
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
            FRACTION_LABEL,
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
            items = list(METRIC_ORDER)
            if getattr(ch_cb, "_include_fraction", False):
                items.append(FRACTION_LABEL)
            prop_cb.addItems(items)
            prop_cb.setEnabled(True)
            if prev in items:
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
        from well_viewer.data.metric_labels import (
            FRACTION_LABEL, FRACTION_METRIC_KEY, METRIC_LABEL_TO_KEY,
        )
        prop_cb = getattr(self, f"{channel_attr}_metric", None)
        if prop_cb is None:
            return self._ctx.state.channel.active_metric or "mean_intensity"
        label = str(prop_cb.currentText() or "")
        # The fraction is not one of the intensity flavours and is absent from
        # ``METRIC_LABEL_TO_KEY`` by design, so a plain ``.get`` on it falls
        # through to the *shared band's* metric — a row reading "Fraction above
        # threshold" exporting mean intensity, in a file titled for the
        # fraction. Only the rows that offer it can produce it, so nothing else
        # changes for the modes that do not.
        if label == FRACTION_LABEL:
            return FRACTION_METRIC_KEY
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
        well as a rep-set, and a one-well rep-set's *mean* equals the raw
        well's, so a solo's drawn value is unchanged while pooled members get
        the tab's mean-of-per-well-means.

        Its **spread** is not the same statistic, and one path can see the
        difference: a one-well set has no cross-well spread, while the well
        itself has a within-well one. The member's own numerator is unaffected
        — ``collect_line_series_for_group`` aggregates a solo with
        ``aggregate_well`` rather than through this list — but a fold-change
        control naming that well as a **legacy bare label** is classified by
        set-before-well precedence (``fold_change.classify_control_label``) and
        so takes the pseudo-set's denominator here and the well's on the tab,
        which changes the propagated ``fold_change_<band>``. A control saved
        the way the app writes them today — a ``ControlRef`` that states its
        own kind — is immune, because ``resolve_control`` never guesses for
        one; ``tests/test_batch_export_fold_change.py`` pins that.

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
            # ``name_is_verbatim`` travels with the member. This scope exists
            # so a batch group behaves exactly like the tab, and the collectors
            # it re-runs read the *rebuilt* selections through
            # ``active_replicate_sets`` — so a flag left behind here made
            # ``replicate_display_label`` relabel a variable-named group on the
            # exported figure and not on the tab's. Worse, within one export:
            # the line and bar panels read ``grp.members`` directly and kept the
            # name, so a `Dose` group came out ``10`` on one panel and
            # ``A01,A02`` on the next.
            selections = [
                make_selection(name=m.name, wells=list(m.wells), source="rep_set",
                               name_is_verbatim=getattr(m, "name_is_verbatim", False),
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

        Which controls those are comes from
        :meth:`_fold_change_control_refs`, because the answer is not the same
        on every mode — see that method.
        """
        from well_viewer.domain.control_ref import KIND_SET
        from well_viewer.domain.selections_model import make_selection

        wanted: list[str] = []
        for ref in self._fold_change_control_refs():
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
                name_is_verbatim=bool(src.get("name_is_verbatim")),
                hidden=True, used_names=used_names, used_ids=used_ids,
                fallback_color_idx=color_idx + i,
            ))
        return out

    def _fold_change_control_refs(self) -> list:
        """The control references this mode's export will actually normalise by.

        This panel's own, seeded from the plot tab at construction and never
        written back, because a batch job must not follow the live figure — and
        the panel's Control combo is what edits it.

        A mode with **no** fold-change controls of its own overrides this. Its
        collector reads the shared state live, so this snapshot is not what its
        export normalises by: taken at construction, a control the user set on
        the plotting band *after* opening the mode would be missing from the
        list, its replicate set would never be carried into the group scope,
        and ``resolve_control_wells`` — which scans exactly that scope — would
        find nothing. The figure then comes out empty under a y axis reading
        "÷ DMSO", while the plotting tab draws the same group correctly.
        """
        return [self._fc_control, *(self._fc_control_map or {}).values()]

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
        for attr in self._CHANNEL_COMBO_ATTRS:
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
        for attr in self._CHANNEL_COMBO_ATTRS:
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
        """Common OUTPUT header + Write to + Folder + Format + Profile rows."""
        hdr = QHBoxLayout()
        hdr.setContentsMargins(12, 4, 12, 4)
        self._add_bold_label(hdr, title)
        hdr.addStretch(1)
        layout.addLayout(hdr)

        self._build_write_to_row(layout)

        out_row = QHBoxLayout()
        out_row.setContentsMargins(12, 6, 12, 6)
        self._add_bold_label(out_row, "Folder:", min_width=_FIELD_LABEL_W)
        self._out_dir_edit = QLineEdit(self._out_dir_value)
        self._out_dir_edit.setMinimumWidth(_OUT_DIR_EDIT_MIN_W)
        out_row.addWidget(self._out_dir_edit, 1)
        self._out_dir_browse_btn = btn_secondary(None, "Browse\u2026",
                                                 self._browse_out_dir)
        out_row.addWidget(self._out_dir_browse_btn)
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
        if self._AXES_FOLLOW_HEADER:
            self._build_output_tail(layout)
        self._sync_destination_widgets()

    def _build_write_to_row(self, layout: QVBoxLayout) -> None:
        """Where this run's figures go — the first question, asked first.

        Three destinations, none of them privileged. A folder of image files
        used to be the base every run paid for, with the deck and the gallery
        as switches beneath **Format:** that could only *add* to it; so a user
        who wanted a deck got a folder of PNGs they had not asked for, and a
        user who wanted the gallery got the same. Nothing forced that. The
        deck and the gallery each render their own copy straight from the
        figure in memory (``DestinationSpool.capture``) and never open the
        file the folder write produces, so no destination depends on another.

        **Folder:** and **Format:** are consequences of this row rather than
        settings above it, and are disabled when nothing needs them — Format
        describes the image files alone, and the folder is needed by those and
        by the deck, but not by the gallery, whose records live in the
        dataset's own ``saved_plots/``.

        The image-files box is what the per-group summary CSVs follow: they
        are files in that folder like the figures are. A run without it saves
        no numbers, which the run summary says in as many words rather than
        leaving it to be discovered afterwards.
        """
        row = QHBoxLayout()
        row.setContentsMargins(12, 2, 12, 2)
        self._add_bold_label(row, "Write to:", min_width=_FIELD_LABEL_W)
        self._to_files_cb = QCheckBox("Image files")
        self._to_files_cb.setChecked(True)
        self._to_files_cb.setToolTip(
            "One figure per export group in the folder below, in the chosen "
            "format, with the summary CSV of its plotted numbers beside it.\n"
            "Untick for a run that produces only a deck or only gallery "
            "records \u2014 no images, and no CSVs.")
        row.addWidget(self._to_files_cb)
        self._to_deck_cb = None
        self._to_gallery_cb = None
        if self._has_saved_plots():
            self._to_deck_cb = QCheckBox("PowerPoint deck")
            self._to_deck_cb.setToolTip(
                "One deck in the folder below \u2014 a slide per group, each "
                "figure as editable PowerPoint shapes rather than a "
                "picture.\n"
                "Named per mode (batch_export_line.pptx and so on), not per "
                "run, so a later run into the same folder replaces it.\n"
                "A figure whose vector capture fails is placed as a picture "
                "and the slide says so.")
            row.addWidget(self._to_deck_cb)
            self._to_gallery_cb = QCheckBox("Saved Plots gallery")
            self._to_gallery_cb.setToolTip(
                "File each exported figure in the Saved Plots gallery, with "
                "the export group's own settings, so it can be restored, "
                "reordered and included in the gallery's PDF report.\n"
                "A record, not a file: it stays with the dataset rather than "
                "with the output folder, so a gallery-only run writes nothing "
                "to disk at all.\n"
                "Records are added only if the whole run succeeds.")
            row.addWidget(self._to_gallery_cb)
        row.addStretch(1)
        layout.addLayout(row)
        for box in (self._to_files_cb, self._to_deck_cb, self._to_gallery_cb):
            if box is not None:
                box.toggled.connect(
                    lambda _on: self._sync_destination_widgets())

    def _set_destination_row_enabled(self, on: bool) -> None:
        """Lock the Write to: row (and the per-cell box) for a run's duration.

        The snapshot in :meth:`_destinations` is what makes a mid-run click
        harmless; this is what makes it *look* harmless, so the panel never
        shows a destination that is not the one the run is using.
        """
        for name in ("_to_files_cb", "_to_deck_cb", "_to_gallery_cb",
                     "_cell_rows_cb"):
            box = getattr(self, name, None)
            if box is not None:
                box.setEnabled(on and (name == "_to_files_cb"
                                       or self._enabled_when_idle(name)))

    def _enabled_when_idle(self, name: str) -> bool:
        """Whether *name* would be enabled with no run in flight.

        Re-derived rather than remembered, so re-enabling after a run cannot
        hand back a control that ``_sync_destination_widgets`` had greyed for
        a reason that still holds.
        """
        if name == "_cell_rows_cb":
            return "files" in self._ticked_destinations()
        return True

    def _sync_destination_widgets(self) -> None:
        """Grey out what the chosen destinations do not use, and re-summarise.

        A control that cannot affect the run it sits above is worse than an
        absent one: **Format:** over a gallery-only run offers four answers to
        a question nobody asked, and the per-cell CSV is a file in a folder
        this run is not filling. Disabled rather than hidden, because these
        come back the moment the box above is re-ticked and a row that
        appears and vanishes under the cursor is harder to read than one that
        greys.
        """
        files = self._wants_image_files()
        for name in ("_fmt_cb", "_fmt_hint"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(files)
        disk = self._wants_disk_output()
        for name in ("_out_dir_edit", "_out_dir_browse_btn"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(disk)
        box = getattr(self, "_cell_rows_cb", None)
        if box is not None:
            box.setEnabled(files)
        self._refresh_run_summary()

    #: Whether the AXES block and the "Also …" rows go straight under Format,
    #: or wait for the mode to place them.
    #:
    #: False on the modes whose *axes are a choice* — Scatter picks a channel
    #: per axis, Response picks the dose variable — because there the AXES
    #: block above those rows asks for a range on an axis the user has not
    #: chosen yet, and Scatter ends up with two rows labelled "X:" a hundred
    #: pixels apart meaning different things. Those modes call
    #: :meth:`_build_output_tail` themselves, after the rows that say what the
    #: axes are.
    _AXES_FOLLOW_HEADER = True

    def _build_output_tail(self, layout: QVBoxLayout) -> None:
        """AXES, the per-cell CSV, and the spread rows."""
        self._build_axes_rows(layout)
        self._build_extra_output_rows(layout)
        if self._INCLUDES_SPREAD:
            self._build_spread_settings_rows(layout)
        # Again here because the modes that place this block themselves build
        # it *after* the header, so the header's own sync ran before the
        # per-cell checkbox existed.
        self._sync_destination_widgets()

    def _build_axes_rows(self, layout: QVBoxLayout) -> None:
        """The AXES block: a shared x row, then one row per panel.

        Every field is optional and blank means "leave it as it is", falling
        through to whatever the app-wide Export Style sidebar holds — which is
        the range a batch figure already came out with. A blank written as a
        number would turn "I did not set a bottom" into "the bottom is zero".

        The x row is shared because the panels of a stack share their x axis:
        the line and bar figures are one time axis read three ways, and a
        separate x range per panel would let a user set three ranges on one
        axis and see the last one win.
        """
        hdr = QHBoxLayout()
        hdr.setContentsMargins(12, 6, 12, 2)
        self._add_bold_label(hdr, "AXES")
        # Not "blank = auto", which is what this said and is not what happens:
        # an unfilled field falls through to whatever the app-wide Export Style
        # sidebar holds, and that may itself be a hard limit somebody set on a
        # different plot on a different day. A user who read "auto" and left the
        # fields empty got a folder of figures clipped to it, with nothing
        # saying so.
        hint = QLabel("blank = as the Export Style sidebar has it")
        hint.setEnabled(False)
        hdr.addWidget(hint)
        hdr.addStretch(1)
        layout.addLayout(hdr)

        # Column headers. Four unlabelled columns of numbers read as three
        # numbers and a word once they are filled, because the ``min`` / ``max``
        # placeholders disappear exactly when the block gets hard to scan.
        head = QHBoxLayout()
        head.setContentsMargins(12, 0, 12, 0)
        spacer = QLabel("")
        spacer.setFixedWidth(self._axes_label_w())
        head.addWidget(spacer)
        columns = [("min", _LIMIT_EDIT_W), ("max", _LIMIT_EDIT_W)]
        if self._AXES_SCALE_OFFERED:
            columns.append(("scale", theme.Metrics.combo_min_w))
        for text, width in columns:
            cap = QLabel(text)
            cap.setEnabled(False)
            cap.setFixedWidth(width)
            head.addWidget(cap)
        head.addStretch(1)
        layout.addLayout(head)

        self._x_min_edit = self._x_max_edit = self._x_scale_cb = None
        if not self._X_IS_NUMERIC:
            # No X row at all on a categorical axis — see ``_X_IS_NUMERIC``.
            self._build_panel_rows(layout)
            return
        x_row = QHBoxLayout()
        x_row.setContentsMargins(12, 2, 12, 2)
        # ``setFixedWidth``, not a minimum: a minimum let the widest row's
        # label push its own fields right, so a five-row grid meant to be
        # scanned down a column started its fields at three different x
        # positions.
        self._add_bold_label(x_row, self._x_row_label()).setFixedWidth(
            self._axes_label_w())
        self._x_min_edit = self._limit_edit("min")
        x_row.addWidget(self._x_min_edit)
        self._x_max_edit = self._limit_edit("max")
        x_row.addWidget(self._x_max_edit)
        if self._AXES_SCALE_OFFERED:
            self._x_scale_cb = self._scale_combo()
            x_row.addWidget(self._x_scale_cb)
        x_row.addStretch(1)
        layout.addLayout(x_row)
        self._build_panel_rows(layout)

    def _axes_label_w(self) -> int:
        """Width of the AXES block's first column, measured not guessed.

        ``_FIELD_LABEL_W`` is sized for the words the rest of the column uses
        ("Format:", "Also write:"); a panel row's label is whatever the mode
        called its panel, and at 108 px the line stack rendered "Fraction
        abov" and "N above cut". A clipped label is worse here than anywhere
        else in the panel, because the whole point of the row is which panel
        it governs — and Qt clips silently, so it reads as a name rather than
        as a truncation.
        """
        metrics = self.fontMetrics()
        widest = max((metrics.horizontalAdvance(p.label) for p in self._PANELS),
                     default=0)
        if len(self._PANELS) > 1:
            widest += _CHECKBOX_INDICATOR_W
        return max(_FIELD_LABEL_W, widest + _LABEL_PADDING_W)

    def _axes_label_w_ceiling(self) -> int:
        """The widest this column has any business being.

        Paired with :meth:`_axes_label_w` because a lower bound alone is
        satisfied by any number: a column nine thousand pixels wide clips
        nothing and pushes every min/max field off the screen, which is the
        same failure from the other side. Stated here so the guard can hold
        both ends rather than re-deriving one of them.
        """
        metrics = self.fontMetrics()
        widest = max((metrics.horizontalAdvance(p.label) for p in self._PANELS),
                     default=0)
        return max(_FIELD_LABEL_W,
                   widest + _CHECKBOX_INDICATOR_W + 2 * _LABEL_PADDING_W)

    def _build_panel_rows(self, layout: QVBoxLayout) -> None:
        """One row per panel this mode declares. See ``_build_axes_rows``."""
        #: ``{panel key: (include checkbox, min edit, max edit, scale combo)}``.
        #: Built from ``_PANELS`` rather than written out, so a mode that
        #: declares a panel gets its row by declaring it.
        self._panel_rows: dict = {}
        single = len(self._PANELS) == 1
        for panel in self._PANELS:
            row = QHBoxLayout()
            row.setContentsMargins(12, 2, 12, 2)
            # A single-panel mode has nothing to select between, so the
            # checkbox would be one that can only be wrong: unticking it asks
            # for a figure with no panels. Its label still names the row, so
            # the layout matches the stacked modes'.
            if single:
                box = None
                # "Y:", not the panel's own name. Under a row called "X:" the
                # only reading that makes the pairing obvious is "Y:", and
                # calling it "Plot:" actively obstructed it — nothing else in
                # the block said these were the *y* limits.
                label = self._add_bold_label(row, self._Y_ROW_LABEL)
                label.setFixedWidth(self._axes_label_w())
            else:
                box = QCheckBox(panel.label)
                box.setChecked(True)
                box.setFixedWidth(self._axes_label_w())
                box.setToolTip(
                    f"Draw the {panel.label} panel, and set its y range.\n"
                    "Unticked, the figure comes out with one fewer row rather "
                    "than with an empty frame where it was.")
                row.addWidget(box)
            lo = self._limit_edit("min")
            hi = self._limit_edit("max")
            scale = self._scale_combo() if self._AXES_SCALE_OFFERED else None
            for widget in (lo, hi, scale):
                if widget is not None:
                    row.addWidget(widget)
            if box is not None:
                # F6: an unticked panel is not drawn, so its three fields mean
                # nothing. Left enabled they were visually identical to a
                # ticked row's, which is the one cue that says what unticking
                # did.
                def _sync(on, _widgets=tuple(
                        w for w in (lo, hi, scale) if w is not None)):
                    for w in _widgets:
                        w.setEnabled(bool(on))
                box.toggled.connect(_sync)
            row.addStretch(1)
            layout.addLayout(row)
            self._panel_rows[panel.key] = (box, lo, hi, scale)

    #: What this mode's x axis actually is, for the X row's label. Named per
    #: mode because "X:" over a row of numbers says nothing about what unit to
    #: type: the line stack's x is time, Response's is the dose variable, and
    #: Scatter's is whatever channel its X axis was pointed at.
    _X_ROW_LABEL = "X (time, h):"

    #: What the single-panel modes' one y row is called. Not the panel's own
    #: name: under a row called "X …" the only reading that makes the pairing
    #: obvious is "Y …", and calling it "Plot:" actively obstructed it —
    #: nothing else in the block said these were the *y* limits.
    _Y_ROW_LABEL = "Y:"

    #: False where the mode's x is **categorical** — one position per group
    #: member, as the bar stack draws. A numeric bound there is not a zoom, it
    #: is three bars crushed into the left of the frame, and a log scale on it
    #: is meaningless. The row is not offered rather than offered and ignored.
    _X_IS_NUMERIC = True

    #: False where the mode already owns a scale control that does something
    #: this generic combo cannot. The scatter modes' ``X log`` / ``Y log``
    #: checkboxes apply ``log10(v + 1)``, chosen so cells at zero stay on the
    #: figure; a plain log scale drops them. Two controls for one axis is worse
    #: than either, and the one to keep is the one that is not lossy — so the
    #: combo is not offered there rather than offered and silently overriding.
    _AXES_SCALE_OFFERED = True

    def _x_row_label(self) -> str:
        return self._X_ROW_LABEL

    @staticmethod
    def _limit_edit(which: str) -> QLineEdit:
        edit = QLineEdit()
        edit.setFixedWidth(_LIMIT_EDIT_W)
        edit.setPlaceholderText(which)
        edit.setToolTip("Leave blank to autoscale this bound.")
        return edit

    @staticmethod
    def _scale_combo_labels() -> tuple[str, ...]:
        """What this mode's scale entries are called.

        Overridable because "Auto" is not equally informative everywhere: on a
        figure that derives its own log axis from the data, saying what it
        derives it *from* is the difference between a neutral default and a
        stated rule.
        """
        return tuple(label for label, _value in _SCALE_CHOICES)

    def _scale_combo(self) -> QComboBox:
        combo = QComboBox()
        for label in self._scale_combo_labels():
            combo.addItem(label)
        combo.setToolTip(
            "Auto leaves the scale to the figure — which for a dose axis means "
            "the one it derives from the doses, and elsewhere the app-wide "
            "Export Style setting.")
        return combo

    def _snapshot_axes(self) -> AxesSpec:
        """Read the AXES block once, for the whole of one run.

        Once, for the reason :class:`ScatterAxes` is read once: the runner pumps
        ``processEvents()`` between jobs, so a field edited mid-export would
        apply to some of the run's figures and not others — one output
        directory, one manifest, two different sets of axes and nothing in
        either file saying which it got.
        """
        rows = getattr(self, "_panel_rows", None)
        if not rows:
            return AxesSpec()
        panels = tuple(
            (panel.key, PanelAxis(
                included=(rows[panel.key][0] is None
                          or rows[panel.key][0].isChecked()),
                y_min=_parse_limit(rows[panel.key][1].text()),
                y_max=_parse_limit(rows[panel.key][2].text()),
                # ``None`` where the mode declined the column — its own control
                # owns the scale, and Auto is what leaves that alone.
                y_log=_scale_choice(rows[panel.key][3]),
            ))
            for panel in self._PANELS if panel.key in rows
        )
        return AxesSpec(
            x_min=(_parse_limit(self._x_min_edit.text())
                   if self._x_min_edit is not None else None),
            x_max=(_parse_limit(self._x_max_edit.text())
                   if self._x_max_edit is not None else None),
            x_log=_scale_choice(self._x_scale_cb),
            panels=panels,
        )

    def _axes_spec(self) -> AxesSpec:
        """This run's axes answer, or a neutral one outside a run.

        Neutral rather than a fresh read: a figure written outside a run — by a
        test, or by a future caller — must not silently pick up whatever the
        panel's fields happen to hold, which is the same ambient-state mistake
        the destination spool is scoped to avoid.
        """
        return self._run_axes if self._run_axes is not None else AxesSpec()

    def _panel_figure(self, fig, *, share_x: tuple[str, ...] | None = None):
        """Lay *fig* out with this run's kept panels. See ``_panels.panel_axes``."""
        return panel_axes(fig, self._PANELS, self._axes_spec(),
                          share_x=self._SHARE_X if share_x is None else share_x)

    def _axes_pairs(self, fig, spec: AxesSpec) -> list:
        """``(panel key, axes)`` for every axes on *fig*.

        A single-panel mode repeats its one key across however many axes the
        figure carries, because that is what its figure is: the faceted scatter
        draws one axes per group and they are all the same plot, so one scale
        answer governs all of them. A stacked mode pairs its kept panels with
        the figure's axes in order, which is the order ``panel_axes`` added
        them in.
        """
        drawn = spec.drawn_keys() or tuple(p.key for p in self._PANELS)
        if len(self._PANELS) == 1:
            return [(self._PANELS[0].key, ax) for ax in fig.axes]
        return list(zip(drawn, fig.axes))

    def _panel_figsize(self, width: float, height: float,
                       drawn: int) -> tuple[float, float]:
        """The figure size for a run that kept *drawn* of this mode's panels."""
        return (width, figure_height(height, len(self._PANELS), drawn))

    def _build_extra_output_rows(self, layout: QVBoxLayout) -> None:
        """What else goes into the output folder, beyond the figures.

        Just the per-cell CSV now. The two destinations that used to live down
        here moved up to **Write to:**, where they belong: they are answers to
        "where does this run's output go", and asking that question after
        Folder and Format had already been answered was what made both of them
        read as extras bolted onto a folder of PNGs.
        """
        self._build_also_write_row(layout)

    def _has_saved_plots(self) -> bool:
        """Whether the deck writer and the gallery are in this build.

        Both are Saved Plots' code — the record, and the writer that turns a
        captured SVG into PowerPoint shapes — so in a profile without that
        feature neither destination is offered. Absent rather than disabled: a
        greyed checkbox says "not now", and this is "not in this build", which
        are different sentences and only one of them is true.
        """
        return self._ctx.features.get("Saved Plots") is not None

    def _build_also_write_row(self, layout: QVBoxLayout) -> None:
        """The opt-in for the raw per-cell CSV beside each figure.

        Its own row rather than a fourth box on **Write to:**, because it is
        not a destination: it does not choose where the figures go, it adds a
        file next to them. It is also the only control here that can add
        hundreds of megabytes to a run, which is worth a row of its own to
        say.

        Off by default. The summary CSV every run already writes is the file
        most runs want; this one is every cell of every well, and a user who
        did not ask for it should not find their export ten times the size.
        Greyed out when **Image files** is off, since there is then no folder
        of files for it to sit beside.
        """
        row = QHBoxLayout()
        row.setContentsMargins(12, 2, 12, 6)
        self._add_bold_label(row, "Also write:", min_width=_FIELD_LABEL_W)
        self._cell_rows_cb = QCheckBox("Raw per-cell CSV")
        self._cell_rows_cb.setToolTip(
            "Beside each group's figure, write every cell row the figure was "
            "computed from — the same file the plot tabs' “Save cell data…” "
            "produces, with its scope block and marker columns.\n"
            "Large: one row per cell per well, not one per group.")
        row.addWidget(self._cell_rows_cb)
        row.addStretch(1)
        layout.addLayout(row)

    def _refresh_run_summary(self) -> None:
        """Rewrite what a run produces, from the controls as they are.

        It was a **static string**, and by the time this feature landed it was
        wrong in four ways at once: it named one figure and one CSV while the
        panel could be writing a second CSV, a slide deck and a gallery record
        per group, and drawing three of four panels. That block is the one
        place in the column that says in plain words what pressing Run does,
        and it is what a user returning after three weeks reads first — so it
        is the last text that should be guessing.

        Derived, therefore, from the same reads the run itself makes. Connected
        to every control that changes the answer (:meth:`_wire_run_summary`),
        because a summary that is right only until something is clicked is a
        static string with extra steps.
        """
        label = getattr(self, "_summary_lbl", None)
        if label is None:
            return
        lines = []
        if self._wants_image_files():
            lines.append("Each export group produces:")
            for line in self._figure_summary_lines():
                lines.append(f"  \u2022 {line}")
            if self._wants_cell_rows():
                lines.append("  \u2022 One raw per-cell CSV\n"
                             "    one row per cell, typically far larger than "
                             "the summary CSV")
        else:
            # The one shortfall a user chooses rather than suffers, and it is
            # still a shortfall: no figure files and — because the summary CSV
            # is a file in that same folder — none of the numbers behind them.
            # Said here, before Run, because afterwards it is an absence, and
            # an absence is what nobody notices.
            lines.append("No image files, and no CSVs: the numbers behind "
                         "each figure are not saved anywhere.")
        run_level = self._run_level_summary()
        if run_level:
            lines.append("")
            lines.append(run_level)
        lines.append("")
        lines.extend(self._summary_footnotes())
        label.setText("\n".join(line for line in lines if line is not None))

    def _run_level_summary(self) -> str:
        """The deck and the gallery, which are per **run** rather than per group.

        Kept out of the "Each export group produces:" bullets because neither
        is one of those: forty groups produce one deck between them. Split by
        verb rather than run together, because "writes" and "files" is the
        whole difference between the two rows that offer them — the deck lands
        in the output folder beside the figures, and the record lands in the
        application and stays with the dataset.
        """
        # Through the same predicates the run uses, not a second read of the
        # checkboxes: two answers to "is the deck being written" can drift,
        # and the summary is precisely the place a drift would be believed.
        deck = self._wants_deck()
        gallery = self._wants_gallery()
        clauses = []
        if deck:
            clauses.append("writes one PowerPoint deck into this folder, a "
                           "slide per group")
        if gallery:
            clauses.append("files a Saved Plots record per figure")
        if not clauses:
            return ""
        # "also" only when there is something for it to be *also* to. On a
        # deck-only run this is the whole of what the run does.
        lead = "The run also " if self._wants_image_files() else "The run "
        return lead + ", and ".join(clauses) + "."

    def _figure_summary_lines(self) -> list[str]:
        """The figure and CSV bullets, naming the panels this run will draw."""
        figure = self._summary_figure_lead()
        total = len(self._PANELS)
        if total > 1:
            drawn = self._snapshot_axes().drawn_keys()
            if drawn and len(drawn) < total:
                names = ", ".join(p.label for p in self._PANELS
                                  if p.key in drawn)
                figure = f"{figure} \u2014 {len(drawn)} of {total} panels ({names})"
            else:
                figure = f"{figure} \u2014 all {total} panels"
        note = self._summary_figure_note()
        if note:
            # Its own indented line rather than a comma and forty more
            # characters: the column this block lives in is about 800 px wide,
            # and a bullet that needs two of those wraps back under the "•"
            # where it reads as a second item.
            figure = f"{figure}\n    {note}"
        return [figure, self._summary_csv_line()]

    #: What this mode's summary CSV holds, in the words its own info text used.
    _SUMMARY_CSV_LINE = "One CSV with per-member time-series data"

    #: How the figure bullet opens. A mode whose run writes more than one
    #: figure per group says so here, because "One figure" beside a folder of
    #: forty is the kind of quietly wrong sentence this whole block replaced.
    _SUMMARY_FIGURE_LEAD = "One figure"

    #: Appended to the figure bullet, where a mode has more to say about what
    #: one figure *is*. The line mode's members are the thing worth naming.
    _SUMMARY_FIGURE_NOTE = ("one line per group member (ReplicateSet = mean of "
                            "its wells, Solo well = raw)")

    def _summary_figure_lead(self) -> str:
        """Overridden where a control decides how many figures a group gets."""
        return self._SUMMARY_FIGURE_LEAD

    def _summary_figure_note(self) -> str:
        return self._SUMMARY_FIGURE_NOTE

    def _summary_csv_line(self) -> str:
        return self._SUMMARY_CSV_LINE

    def _extra_summary_signals(self) -> list:
        """Mode-specific signals that change what the summary says.

        Connected by :meth:`_wire_run_summary` alongside the shared
        checkboxes. A mode that derives part of its summary from one of its
        own controls and does not list that control's signal here has written
        a summary that is right until the control is touched, which is the
        failure the derived block exists to end.
        """
        return []

    def _summary_footnotes(self) -> list[str]:
        """Mode-specific closing lines, unchanged from the old static text."""
        return [
            ("Groups come from Home \u2192 Sample Map \u2192 Replicates."
             if self._use_sidebar_groups
             else "Groups are defined in the left panel."),
            "No statistics are computed across group members.",
            "SD/SEM is only computed within a ReplicateSet.",
        ]

    def _wire_run_summary(self) -> None:
        """Recompute the summary whenever something it states can change."""
        widgets = [self._cell_rows_cb, self._to_gallery_cb, self._to_deck_cb]
        for box in widgets:
            if box is not None:
                box.toggled.connect(lambda _on: self._refresh_run_summary())
        for box, _lo, _hi, _scale in (getattr(self, "_panel_rows", None)
                                      or {}).values():
            if box is not None:
                box.toggled.connect(lambda _on: self._refresh_run_summary())
        for signal in self._extra_summary_signals():
            signal.connect(lambda *_a: self._refresh_run_summary())
        self._refresh_run_summary()

    def _ticked_destinations(self) -> frozenset:
        """What the checkboxes say **right now**. See :meth:`_destinations`."""
        out = set()
        box = getattr(self, "_to_files_cb", None)
        if box is None or box.isChecked():
            out.add("files")
        for name, key in (("_to_deck_cb", "deck"),
                          ("_to_gallery_cb", "gallery"),
                          ("_cell_rows_cb", "cells")):
            box = getattr(self, name, None)
            if box is not None and box.isChecked():
                out.add(key)
        return frozenset(out)

    def _destinations(self) -> frozenset:
        """What **this run** is writing to, fixed for its whole life.

        Snapshotted at the top of :meth:`_run_batch_jobs` for the same reason
        the spool and the axes are, and stated here rather than left implicit
        because getting it wrong destroys files. The runner pumps
        ``processEvents()`` between jobs, so the Write to: row is live while
        an export is running; read per job, unticking **Image files** halfway
        through gave the first groups their files, the rest none, and then —
        because the commit consults the same live answer — discarded the ones
        already written and reported the run a success. Ticking it halfway
        was worse: staging had been placed in a system temp dir, so the commit
        tried to ``os.replace`` across filesystems, which is the one thing
        ``new_staging_dir`` exists to prevent.

        The row is also disabled for the duration, so the two answers cannot
        visibly disagree; this is what makes them actually agree.
        """
        chosen = getattr(self, "_run_dests", None)
        return chosen if chosen is not None else self._ticked_destinations()

    def _wants_image_files(self) -> bool:
        """Whether this run writes a folder of per-group figures and CSVs.

        One of three destinations rather than the base every run pays for.
        Nothing about the other two needs it: ``DestinationSpool.capture``
        renders its own PNG and SVG straight from the in-memory figure, and
        never opens the file this writes — so a deck or a gallery record built
        from a run that wrote no image files is the same picture, not a
        degraded one.
        """
        return "files" in self._destinations()

    def _wants_deck(self) -> bool:
        return "deck" in self._destinations()

    def _wants_gallery(self) -> bool:
        return "gallery" in self._destinations()

    def _wants_disk_output(self) -> bool:
        """Whether anything at all lands in the output folder.

        False only for a gallery-only run, which is the one shape that touches
        no directory of the user's: its records live in the dataset's own
        ``saved_plots/``. The staging directory is still made and then
        discarded rather than committed, because committing would create the
        output folder and leave a manifest in it describing no files.
        """
        return self._wants_image_files() or self._wants_deck()

    def _out_path(self, write_dir, name: str) -> Path | None:
        """Where one of a job's files goes, or ``None`` when it writes none.

        **The one place a per-group output path is built**, on every mode, so
        "does this run produce files at all" is answered once rather than at
        each of the sites that would otherwise each have to remember — ten of
        them across four modules, seven feeding a ``with open(...)`` and the
        rest a ``savefig`` — and a mode that grows an eleventh gets the answer
        by asking here. ``tests/test_batch_export_destinations.py`` holds every
        site to it.

        ``None`` means "skip this write" at every call site, rather than a
        path into somewhere harmless: a run that was told to write no files
        must not create one, not even a temporary one it deletes.
        """
        if not self._wants_image_files():
            return None
        return Path(write_dir) / name

    def _wants_cell_rows(self) -> bool:
        # Gated on the folder too: the per-cell CSV *is* a file in it, so a run
        # writing no files cannot write this one either. The checkbox greys
        # out to say so rather than staying tickable and doing nothing.
        chosen = self._destinations()
        return "cells" in chosen and "files" in chosen

    def _cell_scope_kwargs(self, *, val_col: str | None,
                           timepoints=None) -> dict:
        """What this mode overrides on its source tab's scope.

        The scope a tab builds reads that tab's own controls — its column, its
        ticked timepoints, its axis combos — and a batch run overrides every
        one of them. Read unmodified, the per-cell file described **the plot on
        screen**, filed beside and named for a figure of something else: a bar
        run over t=24 and t=48 wrote two files of whatever timepoint the Bar tab
        happened to be on, each internally consistent, each naming a timepoint
        it did not contain.

        So each mode says what it exported, and the tab's own scope builder
        takes it. Overriding rather than rebuilding keeps one definition of what
        each tab's population *is* — its markers, its gate layer, its
        threshold rule — which is the half no batch panel should be restating.
        """
        kwargs: dict = {}
        if val_col is not None:
            kwargs["val_col"] = val_col
        if timepoints is not None:
            kwargs["timepoints"] = tuple(float(t) for t in timepoints)
        return kwargs

    def _write_cell_rows_for(self, grp, write_dir, stem: str,
                             *, val_col: str | None = None,
                             timepoints=None) -> None:
        """Write this group's per-cell CSV, if the run asked for one.

        **Enters the group scope itself**, for the reason
        :meth:`_capture_group_params` does: the scope the mode's source tab
        builds reads the *live* selections through
        ``cell_scope_compute.plotted_wells``, and the panels differ about
        whether a render has already established them — the line mode renders
        inside the scope and the bar mode never enters it at all. Relying on an
        ambient scope would give every bar group's file the same wells under a
        different group's name, which is a plausible file of the wrong cells.

        A shortfall is recorded, never raised. The figure and the summary CSV
        are what the run was primarily asked for, and losing them to a problem
        with an extra file would be the wrong trade — but it is not silent
        either: the note reaches the run's own report.
        """
        if not self._wants_cell_rows():
            return
        from importlib import import_module

        try:
            scope_module = import_module(self._CELL_SCOPE)
            build = getattr(scope_module, self._CELL_SCOPE_FN)
            column = val_col or self._ctx.state.channel.active_val_col
            kwargs = self._cell_scope_kwargs(val_col=val_col,
                                             timepoints=timepoints)
            with self._app_group_scope(grp), self._app_val_col_scope(column):
                scope = build(self._ctx, **kwargs)
                if scope is None:
                    # Some tabs decline to describe a population before a first
                    # draw. Said rather than written as an empty file.
                    self._note_shortfall(
                        f"{grp.name}: no cell scope — per-cell CSV not written")
                    return
                out = self._out_path(write_dir, f"{stem}_cells.csv")
                if out is None:
                    # Unreachable while both this and ``_wants_cell_rows`` read
                    # the run's one destination snapshot. Said rather than
                    # returning in silence anyway, because the day they stop
                    # agreeing is the day a user finds the file missing with
                    # nothing anywhere saying why.
                    self._note_shortfall(
                        f"{grp.name}: per-cell CSV not written — this run "
                        f"writes no files")
                    return
                rows = write_cell_rows(self._ctx, scope, out)
        except Exception as exc:
            _logger.exception("Batch export: per-cell CSV for %r", grp.name)
            self._note_shortfall(
                f"{grp.name}: per-cell CSV failed ({exc})")
            return
        if rows < 0:
            # Three different problems with three different fixes, so three
            # different sentences. "No loaded wells" over a corrupt cache sends
            # the user to fix a selection that is already correct.
            reason = {
                -1: "no loaded wells",
                -2: "none of the wells' rows would read",
                -3: "cell gating is still recomputing — the file would mix "
                    "two gating states",
            }.get(int(rows), "no rows")
            self._note_shortfall(
                f"{grp.name}: {reason} — per-cell CSV not written")

    def _note_shortfall(self, note: str) -> None:
        """Record something this run could not do, for its final report.

        A list rather than a status write per note: a run of forty groups that
        could write none of their per-cell files would otherwise overwrite its
        own message thirty-nine times and leave the last one looking like the
        only problem.
        """
        _logger.warning("Batch export: %s", note)
        if self._run_notes is not None:
            self._run_notes.append(note)

    def _new_destination_spool(self) -> DestinationSpool:
        """The extra destinations this run was asked for, read once.

        Read here rather than inside the job loop for the same reason every
        other control on this panel is (:class:`ScatterAxes`): the runner pumps
        ``processEvents()`` between jobs, so a checkbox the user touches
        mid-export would otherwise apply to some of the figures and not others,
        and the run would produce a deck missing exactly the groups that were
        exported before the click.
        """
        from well_viewer.domain.dataset_name import dataset_display_name

        return DestinationSpool(
            to_gallery=self._wants_gallery(),
            to_deck=self._wants_deck(),
            tab=self._SOURCE_TAB,
            mode_key=self._mode_key(),
            dataset_name=dataset_display_name(self._ctx.state.dataset.data_dir),
        )

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
        sem_btn = QPushButton(spread_label(initial_sem))
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

    def _build_summary_label(self, layout: QVBoxLayout) -> None:
        """The derived "Each export group produces:" block, above Run."""
        self._summary_lbl = QLabel("")
        self._summary_lbl.setWordWrap(True)
        self._summary_lbl.setContentsMargins(12, 4, 12, 4)
        layout.addWidget(self._summary_lbl)
        self._wire_run_summary()

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
        """The folder this run writes into — created only if it writes there.

        The creation is the point. A gallery-only run puts nothing in this
        folder, and this method used to ``mkdir(parents=True)`` it anyway,
        before the runner ever asked which destinations were chosen — so
        pressing Run with only the gallery ticked made two levels of empty
        directory named for an export that never touched them. The runner's
        own care not to create it (staging elsewhere, discarding rather than
        committing) was measured at a seam one step past this call.
        """
        val = self._out_dir_edit.text().strip()
        writing = self._wants_disk_output()
        if val:
            p = Path(val)
            if not writing:
                return p
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
        if not writing:
            # No folder is needed, so not having one is not a failure. The
            # gallery lives with the dataset, and the run is about to prove it
            # can find that; refusing here would block a run that writes
            # nothing on a setting it does not use.
            return Path(val) if val else Path.home()
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
        # Fixed before the first job, and before the staging directory is
        # chosen from it. Everything downstream — ``_out_path``, the spool, the
        # commit, the headline — reads this one answer. See ``_destinations``.
        self._run_dests = self._ticked_destinations()
        self._set_destination_row_enabled(False)
        errors: list[str] = []
        skipped: list[str] = []
        # A gallery-only run stages nowhere near the user's folder: it never
        # commits, and ``new_staging_dir`` creates ``out_dir`` to hold its
        # temp directory — so a run that writes nothing to disk was still
        # leaving an empty folder of the user's making behind it. Every other
        # run stages inside ``out_dir``, which is what keeps the commit's
        # ``os.replace`` calls on one filesystem.
        if self._wants_disk_output():
            staging = new_staging_dir(out_dir)
        else:
            staging = Path(tempfile.mkdtemp(prefix="allwell_batch_nodisk_"))
        # The extra destinations, read once before the first job (see
        # ``_new_destination_spool``) and cleared in the ``finally`` — so a
        # figure written outside a run, or by the next run, cannot reach this
        # run's spool.
        spool = self._spool = self._new_destination_spool()
        # Read here rather than in each mode's ``_run_batch``, so a mode gets
        # the axes controls by declaring its panels rather than by remembering
        # to snapshot them — and so the read happens once, before the first job,
        # for every mode at the same point in the run.
        self._run_axes = self._snapshot_axes()
        self._run_notes = []
        committed = False
        try:
            # Staged beside whatever it will be copied into: the output
            # folder for a run that writes there, and the dataset folder for a
            # gallery-only run, whose records go to that folder's
            # ``saved_plots/`` and which must leave ``out_dir`` untouched.
            spool.begin(out_dir if self._wants_disk_output()
                        else (self._ctx.state.dataset.data_dir or staging))
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
                # The deck is an output file of this run, so it is staged with
                # the figures and committed under the same manifest: a run that
                # fails after the deck is built leaves no deck behind either.
                if spool.to_deck:
                    self._prog_lbl.setText("Building PowerPoint deck\u2026")
                    QApplication.processEvents()
                spool.write_deck(staging)
                if self._wants_disk_output():
                    commit_staged_export(staging, out_dir)
                    spool.note_stale_deck(out_dir)
                else:
                    # A gallery-only run: the staging directory is empty by
                    # design, and committing it would create the output folder
                    # and leave a manifest in it describing no files — an audit
                    # record of an export that produced nothing on disk. The
                    # run still succeeded, which is what ``committed`` gates
                    # below, so the gallery is still filled.
                    discard_staging(staging)
                committed = True
        except BaseException:
            # Never leave a half-committed export or a stray temp dir behind.
            discard_staging(staging)
            raise
        finally:
            try:
                # The gallery is filled only by a run that committed. Filling it
                # as each figure is rendered would leave the gallery holding the
                # first K figures of a run whose output directory was rolled
                # back — half a run, presented as a run.
                if committed:
                    if spool.to_gallery:
                        self._prog_lbl.setText(
                            "Filing figures in Saved Plots\u2026")
                        QApplication.processEvents()
                    spool.file_in_gallery(self._ctx)
            finally:
                spool.discard()
                self._spool = None
                self._run_axes = None
                # Set by the scatter panel for the life of one run; cleared
                # here with the rest, so a figure written outside a run cannot
                # read the previous run's columns.
                self._run_scatter_axes = None
                run_notes = list(self._run_notes or [])
                self._run_notes = None
                self._run_dests = None
            self._run_btn.setEnabled(True)
            self._set_destination_row_enabled(True)
            self._prog_bar.setVisible(False)

        for note in skipped:
            # Logged before the error branch returns: a run that hit both a
            # skip and a real failure used to report only the failure, so the
            # groups that produced nothing went unmentioned in the one place
            # the user is told to look.
            _logger.info("Batch export: %s", note)
        # What the extra destinations produced, and what they could not.
        # Reported on every path out, including the failure ones: a run that
        # ended in errors still wrote whatever the destinations managed, and a
        # deck two slides short of the groups it names is exactly the shortfall
        # a user cannot see from the deck.
        dest_note = spool.summary()
        dest_short = " · ".join(
            part for part in (spool.shortfall(), _summarise_notes(run_notes))
            if part)
        if errors:
            note = (f" · {len(skipped)} with no data" if skipped else "")
            self._prog_lbl.setText(
                f"Done with {len(errors)} error(s){note}. See log.")
            self._prog_lbl.setStyleSheet(f"color: {get_color('CLR_DANGER')};")
            for err in errors:
                _logger.error("Batch export error: %s", err)
            if dest_short:
                self._ctx.notify.set_status(f"Batch export: {dest_short}")
            return
        # A skipped group is not a failure, but it is also not a success to be
        # counted silently: the headline used to report the number of groups
        # *submitted*, so a run where half of them produced nothing looked
        # identical to one where they all did.
        tail = " · ".join(part for part in (dest_note, dest_short) if part)
        tail = f" · {tail}" if tail else ""
        where = self._where_it_went(out_dir)
        if skipped:
            done = len(jobs) - len(skipped)
            self._prog_lbl.setText(
                f"✓ {done} group(s){where.short} · {len(skipped)} with no data")
            self._prog_lbl.setStyleSheet(f"color: {get_color('CLR_SUCCESS_DARK')};")
            self._ctx.notify.set_status(
                f"{status_text}{where.long} · {len(skipped)} group(s) had no "
                f"data{tail}")
            return
        self._prog_lbl.setText(f"{success_text}{where.short}")
        self._prog_lbl.setStyleSheet(f"color: {get_color('CLR_SUCCESS_DARK')};")
        self._ctx.notify.set_status(f"{status_text}{where.long}{tail}")

    def _where_it_went(self, out_dir: Path) -> _Destination:
        """The "→ …" clause, naming somewhere this run actually wrote.

        Each mode used to build its own headline before the run, interpolating
        the output folder unconditionally — so a gallery-only run announced
        ``✓ 2 group(s) → out/`` and gave the status bar an absolute path to a
        directory that does not exist and received nothing. Worse where an
        earlier run had left a deck in that folder: the sentence then points at
        a real directory whose only content is another run's file.

        Built here, from the destinations the run actually used, because the
        modes cannot know — the answer depends on a row none of them read.
        """
        if self._wants_image_files() or self._wants_deck():
            return _Destination(f" → {out_dir.name}/", f" → {out_dir}")
        return _Destination(" → Saved Plots", " → Saved Plots")

    def _save_figure(self, fig, fig_path: Path | None, fmt: str, *,
                     group: BarGroup | None, title: str,
                     val_col: str | None = None) -> None:
        """Write one batch figure, and feed the run's extra destinations.

        **The one place a batch figure is written**, on every panel — which is
        why it is also the one place the gallery and the deck are fed from. A
        second capture path beside it would be a second answer to "what did this
        run produce", and the two would disagree the first time a mode grew a
        figure the other did not know about.

        *group* and *title* are required rather than defaulted, and *group* may
        be ``None`` only for a figure that genuinely belongs to no export group.
        A default would let a call site forget them, and a forgotten *group* is
        not a missing record — it is a record whose parameters describe whatever
        the live plotting tab happens to be showing, filed under this group's
        name. ``tests/test_batch_export_destinations.py`` holds every call site
        to passing both.
        """
        from well_viewer.features.export.style import _ensure_export_style_prefs

        spec = self._axes_spec()
        # Pinned on the axes; the limits ride in the preferences below. Two
        # mechanisms because ``apply_export_style_prefs`` honours two: it reads
        # ``_aw_force_*log`` before the shared ``x_log``, and it reads limits
        # only from what it is handed. See ``_panels``.
        pin_scales(self._axes_pairs(fig, spec), spec, x_keys=self._X_PANELS)
        prefs = spec.prefs_overlay(_ensure_export_style_prefs(self._ctx),
                                   spec.drawn_keys(), x_keys=self._X_PANELS)

        spool = self._spool
        on_saved = None
        if spool is not None and spool.wanted:
            params, thresholds = self._capture_group_params(group, val_col)
            # A figure whose parameters could not be captured still reaches the
            # gallery — the picture is worth having — but it reaches it with
            # nothing to restore, and in the gallery that is indistinguishable
            # from a record whose Restore works. Counted here so the run's own
            # shortfall says how many.
            if group is not None and not params:
                spool.undescribed += 1

            def on_saved(saved_fig, _t=title, _p=params, _th=thresholds):
                spool.capture(saved_fig, title=_t, params=_p, thresholds=_th)

        save_export_figure(
            fig, fig_path, fmt,
            export_prefs=prefs,
            plot_bg=theme.CPub.bg,
            on_saved=on_saved,
        )

    def _capture_group_params(
        self, grp: BarGroup | None, val_col: str | None,
    ) -> tuple[dict, dict]:
        """The parameters that describe *grp*'s figure — not the live tab's.

        Taken inside ``_app_group_scope`` + ``_app_val_col_scope``, which is the
        whole point: ``saved_plots.collect.capture_params`` snapshots the
        application's *live* selections and column, and during a batch run those
        are whatever the user left the plotting tabs on. Captured outside the
        scope, every group in the run would file identical parameters — the
        tab's — under ten different group names, and each would restore to a
        figure that is not the one in the gallery beside it. Captured inside, the
        snapshot's ``selections`` are the group's own members and its column is
        the one the panel exported with.

        The scope is entered here rather than relied on from the caller because
        the panels differ: the line mode already renders inside it, and the bar
        mode never enters it at all (it hands the group straight to
        ``collect_bar_items_for_group``). A capture that trusted an ambient
        scope would be right on one panel and silently wrong on the next.

        Returns ``({}, {})`` for a figure with no group, and for a capture that
        raised. Empty rather than approximate: a record with no parameters is a
        picture in the gallery whose Restore does nothing, which is thin; a
        record carrying another figure's parameters restores a *different* plot
        under this one's title.
        """
        if grp is None:
            return {}, {}
        try:
            column = val_col or self._ctx.state.channel.active_val_col
            with self._app_group_scope(grp), self._app_val_col_scope(column):
                params, thresholds = _capture_params(self._ctx, self._SOURCE_TAB)
            # Stamped so the record can say where it came from. A batch figure's
            # parameters reproduce the *group*; Batch Export's own layout
            # controls (bars versus beeswarm, stacked timepoints, the scatter
            # facet mode) live on the panel and not in view state, so a Restore
            # can draw the same data in a different arrangement. The gallery's
            # summary and the restore's status line both read this — see
            # ``saved_plots.collect.build_summary``.
            params["batch_export"] = {
                "mode": self._mode_key(),
                "group": str(getattr(grp, "name", "") or ""),
            }
            return params, thresholds
        except Exception:
            _logger.exception(
                "Batch export: could not capture parameters for group %r",
                getattr(grp, "name", grp))
            return {}, {}

    def _export_scatter_figure(
        self,
        draw: Callable[[object], None],
        *,
        xlabel: str,
        ylabel: str,
        title: str,
        fig_path: Path | None,
        fmt: str,
        group: BarGroup | None,
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
        self._save_figure(fig, fig_path, fmt, group=group, title=title)

    def _run_is_refused(self) -> bool:
        """Every pre-flight a run has to clear, in one call.

        One entry point rather than a check per mode, because each of these
        was added at a different time and the modes that forgot to call the
        newest one would have failed silently in the way it exists to prevent.
        """
        if not self._wants_image_files() and not self._wants_deck() \
                and not self._wants_gallery():
            QMessageBox.warning(
                self, "No destination",
                "Nothing under Write to: is ticked, so this run would produce "
                "nothing at all. Choose at least one destination.")
            return True
        return self._axes_block_refuses_the_run()

    def _axes_block_refuses_the_run(self) -> bool:
        """Refuse a run the AXES block has asked for impossibly, and say why.

        Checked before the jobs rather than discovered inside one. Both of the
        things it catches produce a *file*, which is what makes them worth a
        dialog: a figure with no panels is not a smaller figure, it is a blank
        page, and a log axis pinned from zero is a frame matplotlib quietly
        rescales — so in each case a run would write forty of them and look
        like it had worked.
        """
        if self._snapshot_axes().draws_nothing():
            QMessageBox.warning(
                self, "No panels",
                "Every panel is unticked under AXES, so there is nothing to "
                "draw. Tick at least one.")
            return True
        complaints = self._axes_complaints()
        if complaints:
            QMessageBox.warning(
                self, "Axis limits",
                "The AXES block asks for a range that cannot be drawn:\n\n  \u2022 "
                + "\n  \u2022 ".join(complaints)
                + "\n\nClear or correct those fields and run again.")
            return True
        return False

    def _axes_complaints(self) -> list[str]:
        """Every AXES field that cannot mean what it says, named by its row.

        Two kinds, both of which used to go through silently:

        * **min at or above max.** The columns are headed ``min`` and ``max``,
          so this is a typo rather than a request for an inverted axis — and
          the equal case is not even that: matplotlib expands a zero-width
          range to an interval of its own choosing and draws a frame whose
          numbers are nothing the user typed.
        * **a log axis bounded at or below zero.** ``log`` of zero is not a
          position on the axis, so matplotlib drops the bound and autoscales.
          The figure then comes out with a range nobody asked for and no sign
          that the field was ignored.

        Reported together rather than one at a time, so a user who mistyped
        two rows fixes both before the next run.
        """
        spec = self._snapshot_axes()
        single = len(self._PANELS) == 1
        rows = [(self._x_row_label().rstrip(":"),
                 spec.x_min, spec.x_max, spec.x_log)]
        for panel in self._PANELS:
            axis = spec.for_panel(panel.key)
            if not axis.included:
                # An unticked panel is not drawn, so its fields describe
                # nothing. Refusing a run over them would block it on a value
                # that has no effect.
                continue
            name = self._Y_ROW_LABEL.rstrip(":") if single else panel.label
            rows.append((name, axis.y_min, axis.y_max, axis.y_log))
        out: list[str] = []
        for name, low, high, log in rows:
            if low is not None and high is not None and low >= high:
                out.append(f"{name}: min ({low:g}) is not below max "
                           f"({high:g}).")
            if log:
                below = [v for v in (low, high) if v is not None and v <= 0]
                if below:
                    out.append(
                        f"{name}: a log scale has no position for "
                        f"{below[0]:g}, so that bound would be dropped and the "
                        f"axis autoscaled.")
        return out

    def _groups_for_export(self) -> list[BarGroup]:
        if self._use_sidebar_groups:
            return self._groups_from_rep_sets()
        return list(self._groups)

    def _run_batch(self) -> None:
        if self._run_is_refused():
            return
        groups_with_data = [g for g in self._groups_for_export()
                            if any(w in self._ctx.state.dataset.well_paths for w in g.wells)]
        if not groups_with_data:
            msg = (
                "Define at least one non-empty group on Home → Sample Map → Replicates."
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
        band_lbl = spread_label(use_sem)
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
            csv_path = self._out_path(write_dir, f"{safe}.csv")
            fig_path = self._out_path(write_dir, f"{safe}.{fmt}")

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
                        _control_cell, figure_names_map,
                        line_metric_fieldnames, line_metric_row,
                        well_name_for, well_names_joined,
                    )
                    _well_labels = figure_names_map(self._ctx)
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
                        # The raw pass runs with fold change off and so cannot
                        # know which members the control render mode hides;
                        # the fold-change pass is the one that does. Without
                        # this the exported figure drew one curve while the CSV
                        # beside it carried rows for a hidden control, its
                        # fold-change cells blank — see the same guard in
                        # ``export/service.export_plot_data``.
                        series_raw = [s for s in series_raw
                                      if s.key in fc_series_by_key]
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
                        if csv_path is not None:
                            with open(csv_path, "w", newline="") as fh:
                                writer = csv.DictWriter(fh,
                                                        fieldnames=fieldnames)
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
                    self._save_figure(fig, fig_path, fmt,
                                      group=grp, title=grp.name,
                                      val_col=_val_col)
                except Exception as exc:
                    return f"{grp.name} figure: {exc}"

            self._write_cell_rows_for(grp, write_dir, safe, val_col=_val_col)
            _logger.info("Batch: finished %s", safe)
            return None

        self._run_batch_jobs(
            jobs=groups_with_data,
            progress_text_fn=_progress,
            run_job_fn=_run_group,
            success_text=f"\u2713 {len(groups_with_data)} group(s)",
            status_text=f"Batch export: {len(groups_with_data)} group(s)",
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
        spec = self._axes_spec()
        drawn = spec.drawn_keys() or tuple(p.key for p in self._PANELS)
        fig = _Figure(figsize=self._panel_figsize(10, 13, len(drawn)),
                      dpi=300, facecolor=theme.CPub.bg)
        # Only the panels this run kept reach the figure; the rest are drawn
        # onto a scratch figure so the shared renderer — which paints all four
        # members of its ``LineAxes`` — needs no new shape. See ``_panels``.
        laid = self._panel_figure(fig)
        ax_mean = laid.axes["mean"]
        ax_frac = laid.axes["frac"]
        ax_n = laid.axes["n"]
        ax_cdf = laid.axes["cdf"]
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
        from well_viewer.core.plot_annotations import (
            LABEL_FRAC_CAP as _LABEL_FRAC_CAP,
            value_label_settings,
        )
        from well_viewer.domain.line_colors import (
            line_color_mode as _line_color_mode,
        )
        from well_viewer.core.plot_style import apply_time_rescale
        from well_viewer.domain.time_rescale import axis_label as _time_axis_label
        from well_viewer.domain.time_rescale import rescale_for as _time_rescale_for
        _value_labels, _value_label_fmt = value_label_settings(self._ctx)
        _time_rescale = _time_rescale_for(self._ctx.state.plot)
        _time_lbl = _time_axis_label("Time (hours)", _time_rescale)
        for _ax_time in (ax_mean, ax_frac, ax_n):
            _ax_time.set_xlabel(_time_lbl, fontsize=8, labelpad=5)
            apply_time_rescale(_ax_time, _time_rescale)
        # Same rule as the on-tab figures (``barplot.renderer._set_frac_ylim``,
        # ``lineplot.controller``): a raw fraction is bounded so the clamp is a
        # good default, but under fold change the field is a ratio or a
        # difference and the clamp silently clips the effect out of the panel.
        # The batch twin of a fixed on-tab figure is exactly where a half-swept
        # rule hides — the exported PNG kept clipping after the tab stopped.
        if not fc_active:
            # Widened when the value labels are on, exactly as the on-tab Line
            # figure widens it: a trace at 1.0 otherwise has its label drawn
            # over the panel title.
            ax_frac.set_ylim(-0.05, _LABEL_FRAC_CAP if _value_labels else 1.05)
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
            # Properties → Overlays, the same overlay the on-tab Line figure
            # draws. The batch twin of an on-tab figure is exactly where a
            # half-applied option hides: an exported PNG without the labels
            # the user put on the plot looks like a complete figure.
            value_labels=_value_labels, value_label_format=_value_label_fmt,
            # Properties → Colours, resolved from the same state the on-tab
            # figure resolves it from. The batch twin of an on-tab figure is
            # where a half-applied mode hides: an export in plate colours
            # beside a screen figure in greys, drawn from one setting.
            color_mode=_line_color_mode(self._ctx.state.plot),
        )
        return fig

