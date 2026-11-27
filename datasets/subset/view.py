"""Qt builder for the Subset / Split Dataset page.

Source + destination folder pickers, a well-plate selector, timepoint and FOV
checkbox rows seeded from the source dataset's own ``available_timepoints``/
``available_fovs``, and a compact status strip
(:class:`~datasets.subset.dashboard.SubsetDashboard` — headline, overall bar,
a per-well-state counts rail, and a colour legend, but **no plate map of its
own**) with the raw log demoted to a collapsible checkbox. Run progress is
painted directly onto the well-plate selector above instead of a second
plate map — see :mod:`~datasets.subset.controller`.

A source holding only ``in/`` (a converted plate not yet analysed) adds three controls, shown only for one:
the filename schema (read from the acquisition record, typed, or inferred — see
:mod:`datasets.subset.raw_compute`, which also fills the pickers), a Channels row, and **Edit in place**,
which removes what is unticked from the source's own archives instead of copying the rest.

Pure view: it owns widgets and emits intent (``runRequested``,
``openInReviewRequested``, ``sourceChanged``); the worker wiring lives in
:mod:`~datasets.subset.controller`.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui import theme
from datasets.core.log_panel import LogPanelMixin, hrule as _hrule
from util.timepoint import sort_key as tp_sort_key
from widgets._support import set_variant
from widgets.flow_layout import FlowLayout
from widgets.path_field import PathField, folder_picker
from datasets.core.pending_inputs import RUN_WAITS, any_pending, apply_pending
from widgets.plate_geometry import PLATE_ALIGNMENT, apply_plate_geometry
from widgets.well_plate_selector import WellPlateSelector
from datasets.subset.dashboard import SubsetDashboard
from datasets.subset.subset_compute import (
    SubsetFilter,
    describe_in_place_marker,
    destination_has_unrecognized_content,
    destination_is_complete_dataset,
    read_in_place_marker,
)
from datasets.core.dataset_fields import is_free_destination
from datasets.core.dataset_fields import promote_open_button
from datasets.core.raw_dataset import is_raw_dataset, raw_paths, unusable_reason
from datasets.subset.raw_compute import RawSchemaError, resolve_raw_source
from ui import text_roles
from util.schema import DEFAULT_SEP
from ui import buttons


def _is_dataset(root: Path) -> str:
    """The source rule: an analysed dataset, or one holding only ``in/``."""
    return unusable_reason(root, tool="Subset")


def _is_destination(root: Path) -> str:
    """This page's destination rule: the shared one, bound to this tool's
    own in-progress marker through its compute module's predicates."""
    return is_free_destination(
        root,
        is_complete=destination_is_complete_dataset,
        has_unrecognized=destination_has_unrecognized_content,
    )


_TP_PICK_SOURCE = "Pick a source dataset to preview timepoints."
_FOV_PICK_SOURCE = "Pick a source dataset to preview FOVs."
_WELLS_UNKNOWN = (
    "Which wells the earlier job writes isn't known until it runs. Leave the "
    "plate empty to take every well, or pick the wells to keep.")
_TP_UNKNOWN = ("All timepoints — which ones the earlier job writes is known "
               "only when it runs.")
_ONLY_THESE_TIMEPOINTS = (
    "The timepoints this job keeps. Which others the earlier job writes is "
    "known only when it runs; untick all to keep every timepoint.")
_ONLY_THESE_FOVS = (
    "The FOVs this job keeps. Which others the earlier job writes is known "
    "only when it runs; untick all to keep every FOV.")
_FOV_UNKNOWN = ("All FOVs — which ones the earlier job writes is known only "
                "when it runs.")
_IN_PLACE_WARNING = (
    "Edit in place removes every unticked well, timepoint, FOV and channel from this dataset's own "
    "archives. Nothing removed is kept anywhere, and it cannot be undone. Each well is rewritten on its "
    "own, so it needs only that well's room on disk.")
_RUN_LABEL = "▶  Subset"
_RUN_IN_PLACE_LABEL = "▶  Remove from dataset"
_RUN_FINISH_LABEL = "▶  Finish the interrupted edit"


class SubsetPage(LogPanelMixin, QWidget):
    """The Subset / Split Dataset feature body."""

    runRequested = Signal()
    queueRequested = Signal()
    openInReviewRequested = Signal(str)
    sourceChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dest_root = ""
        self._running = False
        self._tp_checks: dict[str, QCheckBox] = {}
        self._fov_checks: dict[str, QCheckBox] = {}
        self._ch_checks: dict[str, QCheckBox] = {}
        # Whether the source holds only in/ — the controls for one are shown only then.
        self._raw = False
        self._source_wells: list[str] = []
        # What the queued job writing a pending source is expected to hold
        # (``services.job_projection``); read instead of the disk while the
        # source does not exist yet. Empty when the source is a real folder.
        self._projection: dict = {}
        # An edited job's explicit timepoints / FOVs, kept while its pending
        # source's projection does not know that dimension — the picker then
        # offers exactly these rather than nothing (see _load_projected_metadata).
        self._job_only_choices: dict = {}
        # What set_selected_timepoints / set_selected_fovs were last asked for,
        # since the current source was set (see set_selected_timepoints).
        self._requested: dict = {}
        # The interrupted in-place edit the source holds (``.aw_subset_in_place``'s contents), or ``None``.
        # While there is one the page offers only to finish it, with the filter it recorded: its archives are
        # partly cut, so they no longer match the acquisition record the pickers would be read from, and a
        # filter rebuilt from the pickers could delete images the interrupted edit meant to keep.
        self._interrupted: dict | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        # The page's content (fields, plate selector, timepoints/FOVs,
        # status strip, log) can exceed the window's default height
        # (1640x980 — allwell.py). A plain QVBoxLayout with no scroll area
        # just crushes whatever doesn't fit into whatever's left, which is
        # exactly what made the well-plate selector above render illegibly
        # small before this wrap existed — its cell size is computed from
        # its actual allotted width/height (well_plate_selector's
        # _metrics()), and a squeezed few dozen pixels of height produces a
        # squeezed cell size regardless of how much width is available. Wrap
        # everything in a scroll area instead, mirroring the pattern
        # analyze/tab.py already uses for its own form panel.
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        page_layout.addWidget(scroll)

        content = QWidget(scroll)
        scroll.setWidget(content)
        outer = QVBoxLayout(content)
        outer.setContentsMargins(theme.Spacing.lg, theme.Spacing.lg,
                                 theme.Spacing.lg, theme.Spacing.lg)
        outer.setSpacing(theme.Spacing.md)

        heading = QLabel("Subset / Split", self)
        heading.setProperty("textRole", "sectionHeading")
        outer.addWidget(heading)
        outer.addWidget(_hrule(self))

        blurb = QLabel(
            "Copy a filtered slice of a dataset — chosen wells, timepoints "
            "and/or FOVs — into a new folder, leaving the source untouched. "
            "Timepoints keep their original hours; nothing is relabelled.",
            self,
        )
        blurb.setProperty("textRole", "help")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        self._src_field = PathField(
            "Source dataset", pick=folder_picker("Select source dataset folder"),
            validator=_is_dataset)
        self._src_field.changed.connect(self._on_source_changed)
        outer.addWidget(self._src_field)
        self._build_raw_controls(outer)
        self._dest_field = PathField(
            "Destination folder", pick=folder_picker("Select destination folder"),
            validator=_is_destination)
        self._dest_field.changed.connect(self._on_input_changed)
        outer.addWidget(self._dest_field)

        outer.addWidget(QLabel("Wells", self))
        # Shown only while the source is a queued job's output whose wells
        # cannot be known before it runs.
        self._wells_hint = QLabel(_WELLS_UNKNOWN, self)
        self._wells_hint.setProperty("textRole", "help")
        self._wells_hint.setWordWrap(True)
        self._wells_hint.setVisible(False)
        outer.addWidget(self._wells_hint)
        self._plate = WellPlateSelector(self)
        # The app-wide plate geometry (widgets/plate_geometry.py): the "picker"
        # role keeps this plate's own All / Invert / Clear row — this page has no
        # other well-selection buttons — and the helper adds the room that row
        # needs on top of the shared height cap, so the grid itself still renders
        # at exactly the cell scale every other plate map in the app uses.
        apply_plate_geometry(self._plate, role="picker")
        self._plate.selectionChanged.connect(self._on_plate_selection_changed)
        outer.addWidget(self._plate, 0, PLATE_ALIGNMENT)

        outer.addWidget(QLabel("Timepoints", self))
        # Matches Import Loose's own "Pick a folder to preview." -- an empty
        # checkbox row under a heading, with nothing said about why, read as
        # broken rather than "pick a source first".
        self._tp_hint = QLabel(_TP_PICK_SOURCE, self)
        self._tp_hint.setProperty("textRole", "help")
        outer.addWidget(self._tp_hint)
        self._tp_host = QWidget(self)
        self._tp_flow = FlowLayout(self._tp_host)
        outer.addWidget(self._tp_host)

        outer.addWidget(QLabel("FOVs", self))
        self._fov_hint = QLabel(_FOV_PICK_SOURCE, self)
        self._fov_hint.setProperty("textRole", "help")
        outer.addWidget(self._fov_hint)
        self._fov_host = QWidget(self)
        self._fov_flow = FlowLayout(self._fov_host)
        outer.addWidget(self._fov_host)

        # Only an in-only source can be cut by channel: an analysed one's tables are per channel.
        self._ch_heading = QLabel("Channels", self)
        outer.addWidget(self._ch_heading)
        self._ch_hint = QLabel("This dataset's filename schema has no channel field.", self)
        self._ch_hint.setProperty("textRole", "help")
        outer.addWidget(self._ch_hint)
        self._ch_host = QWidget(self)
        self._ch_flow = FlowLayout(self._ch_host)
        outer.addWidget(self._ch_host)
        for widget in (self._ch_heading, self._ch_hint, self._ch_host):
            widget.setVisible(False)

        run_row = QWidget(self)
        run_row_l = QHBoxLayout(run_row)
        run_row_l.setContentsMargins(0, 0, 0, 0)
        run_row_l.setSpacing(theme.Spacing.sm)
        self._run_btn = buttons.primary(_RUN_LABEL, self)
        self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self.runRequested)
        run_row_l.addWidget(self._run_btn)
        self._queue_btn = buttons.default("Add to queue", self)
        self._queue_btn.setToolTip("Add this run to the Job Queue instead of running it now.")
        self._queue_btn.setEnabled(False)
        self._queue_btn.clicked.connect(self.queueRequested)
        run_row_l.addWidget(self._queue_btn)
        run_row_l.addStretch(1)
        outer.addWidget(run_row, 0, Qt.AlignLeft)

        self._build_status_section(outer)

        self._open_btn = buttons.default("Open subset in Review", self)
        self._open_btn.setVisible(False)
        self._open_btn.clicked.connect(lambda: self.openInReviewRequested.emit(self._dest_root))
        outer.addWidget(self._open_btn, 0, Qt.AlignLeft)

        # A trailing stretch outranks every 0-stretch item above it for any
        # leftover space the QScrollArea's content widget picks up filling
        # the viewport, so slack collects as blank page background below
        # everything instead of inflating the dashboard's own plate map (the
        # dashboard has no maximumHeight of its own, and with the raw log
        # hidden by default there was nothing else to absorb the growth).
        outer.addStretch(1)

    def _build_raw_controls(self, outer: QVBoxLayout) -> None:
        """The filename schema and Edit in place, for a source holding only ``in/``. Hidden otherwise."""
        self._raw_box = QWidget(self)
        box = QVBoxLayout(self._raw_box)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.Spacing.sm)
        row = QHBoxLayout()
        row.setSpacing(theme.Spacing.sm)
        row.addWidget(QLabel("Filename schema", self._raw_box))
        self._schema_edit = QLineEdit(self._raw_box)
        self._schema_edit.setPlaceholderText("from the acquisition record, or e.g. experiment:channel:well:fov:timepoint")
        self._schema_edit.setToolTip(
            "Which part of each image name is which field, colon-separated. Leave empty to use the dataset's "
            "acquisition record (in/acquisition.json), or a reading of the names when it has none.")
        self._schema_edit.editingFinished.connect(self._on_schema_edited)
        row.addWidget(self._schema_edit, 1)
        row.addWidget(QLabel("Separator", self._raw_box))
        self._sep_edit = QLineEdit(DEFAULT_SEP, self._raw_box)
        self._sep_edit.setMaxLength(3)
        self._sep_edit.editingFinished.connect(self._on_schema_edited)
        row.addWidget(self._sep_edit)
        box.addLayout(row)
        self._schema_note = QLabel("", self._raw_box)
        self._schema_note.setProperty("textRole", "help")
        self._schema_note.setWordWrap(True)
        box.addWidget(self._schema_note)
        self._in_place_cb = QCheckBox("Edit in place — remove what is unticked from this dataset", self._raw_box)
        self._in_place_cb.toggled.connect(self._on_in_place_toggled)
        box.addWidget(self._in_place_cb)
        self._in_place_warning = QLabel(_IN_PLACE_WARNING, self._raw_box)
        text_roles.set_role(self._in_place_warning, text_roles.HELP, tone=text_roles.ATTENTION)
        self._in_place_warning.setWordWrap(True)
        self._in_place_warning.setVisible(False)
        box.addWidget(self._in_place_warning)
        self._interrupted_note = QLabel("", self._raw_box)
        text_roles.set_role(self._interrupted_note, text_roles.HELP, tone=text_roles.ATTENTION)
        self._interrupted_note.setWordWrap(True)
        self._interrupted_note.setVisible(False)
        box.addWidget(self._interrupted_note)
        self._raw_box.setVisible(False)
        outer.addWidget(self._raw_box)

    def _build_status_section(self, outer: QVBoxLayout) -> None:
        self.build_log_header(outer, "Subset output")

        self.dashboard = SubsetDashboard(self)
        # Hidden until the inputs are valid enough to answer "what would
        # this subset?" — _update_status_preview() reveals it seeded with
        # the current picker selection, and SubsetController.launch() takes
        # over from there once a run actually starts.
        self.dashboard.setVisible(False)
        outer.addWidget(self.dashboard)

        self.build_log_tail(outer, object_name="SubsetStatus", placeholder="Status will appear here.")

    # ── public API used by the controller ──────────────────────────────
    def source_path(self) -> str:
        return self._src_field.path()

    def destination_path(self) -> str:
        return self._dest_field.path()

    def selected_wells(self) -> list[str]:
        return list(self._plate.selectedWellIds())

    def selected_timepoints(self) -> list[str]:
        return [label for label, cb in self._tp_checks.items() if cb.isChecked()]

    def selected_fovs(self) -> list[str]:
        return [label for label, cb in self._fov_checks.items() if cb.isChecked()]

    def available_timepoints(self) -> list[str]:
        """Every timepoint the page offers, ticked or not."""
        return list(self._tp_checks)

    def available_fovs(self) -> list[str]:
        """Every FOV the page offers, ticked or not."""
        return list(self._fov_checks)

    def selected_channels(self) -> list[str]:
        return [label for label, cb in self._ch_checks.items() if cb.isChecked()]

    def available_channels(self) -> list[str]:
        return list(self._ch_checks)

    def all_channels_selected(self) -> bool:
        return len(self.selected_channels()) == len(self._ch_checks)

    def is_raw_source(self) -> bool:
        """Whether the source holds only ``in/`` (a converted plate not yet analysed)."""
        return self._raw

    def in_place(self) -> bool:
        return self._raw and self._in_place_cb.isChecked()

    def interrupted_edit(self) -> dict | None:
        """The interrupted in-place edit the source holds, or ``None``: its marker's contents, ``{}`` when the
        marker cannot be read (:func:`~datasets.subset.subset_compute.read_in_place_marker`)."""
        return self._interrupted if self._raw else None

    def interrupted_filter(self) -> SubsetFilter | None:
        """The filter the interrupted edit recorded — what finishing it keeps — or ``None`` when there is no
        interrupted edit or its record cannot be read."""
        marker = self.interrupted_edit()
        return None if not marker else SubsetFilter.from_json(marker.get("filter"))

    def refresh_interrupted_edit(self) -> None:
        """Re-read whether the source holds an interrupted in-place edit, and show the page for it. For the
        controller once a run ends: a cancelled in-place run leaves one, a finished one removes it."""
        src = self.source_path()
        self._interrupted = read_in_place_marker(src) if self._raw and src else None
        self._apply_interrupted_state()
        if not self._running:
            self._run_btn.setText(self._run_label())
            set_variant(self._run_btn, "destructive" if self.in_place() else "primary")
        self._refresh_run_enabled()

    def typed_schema(self) -> str:
        return self._schema_edit.text().strip() if self._raw else ""

    def typed_separator(self) -> str:
        return self._sep_edit.text() or DEFAULT_SEP

    def set_schema(self, schema: str, separator: str | None) -> None:
        if schema:
            self._requested["raw"] = True
        self._schema_edit.setText(schema or "")
        self._sep_edit.setText(separator or DEFAULT_SEP)
        self._reload_source_metadata()
        self._refresh_run_enabled()

    def set_in_place(self, on: bool) -> None:
        if on:
            self._requested["raw"] = True
        self._in_place_cb.setChecked(bool(on))

    def set_selected_channels(self, labels: list[str] | None) -> None:
        self._requested["channels"] = list(labels) if labels is not None else None
        if labels is None:
            return
        self._requested["raw"] = True
        wanted = {label.lower() for label in labels}
        for label, cb in self._ch_checks.items():
            cb.setChecked(label.lower() in wanted)

    def wells_for_spec(self) -> list[str] | None:
        """The selected wells, or ``None`` for "every well".

        For a pending source every projected well left ticked is "every
        well", not a list of them: the projection is what the earlier job was
        expected to write, and a well it writes that the projection did not
        know about must not be dropped from a subset that kept everything.
        """
        wells = self.selected_wells()
        if not wells:
            return None
        if self._requested.get("wells"):
            # An edited job's explicit list stays a list: collapsing it to
            # "every well" because it happens to equal the projection widens
            # the job to wells it never named.
            return wells
        offered = (self._projection or {}).get("wells")
        if self.has_pending_inputs() and offered and set(wells) >= set(offered):
            return None
        return wells

    def all_timepoints_selected(self) -> bool:
        if self._only_from_job("timepoints"):
            return not self.selected_timepoints()    # unticking them all is "every one"
        return len(self.selected_timepoints()) == len(self._tp_checks)

    def all_fovs_selected(self) -> bool:
        if self._only_from_job("fovs"):
            return not self.selected_fovs()    # unticking them all is "every one"
        return len(self.selected_fovs()) == len(self._fov_checks)

    def _only_from_job(self, dimension: str) -> bool:
        """Whether this picker lists only the job's own choices, because the
        pending source's full list is not known. Every box ticked then means
        "these", not "all": the source may hold more."""
        return (self.has_pending_inputs()
                and (self._projection or {}).get(dimension) is None
                and bool(self._job_only_choices.get(dimension)))

    def set_source_path(self, path: str) -> None:
        self._requested = {}
        self._src_field.set_path(path)
        self._reload_source_metadata()
        self._refresh_run_enabled()

    def set_destination_path(self, path: str) -> None:
        self._dest_field.set_path(path)

    def set_selected_wells(self, wells: list[str] | None) -> None:
        """``None`` keeps whatever _reload_source_metadata already selected
        (everything) — a queued job with no explicit well filter subsets
        nothing away rather than silently selecting zero wells.

        Remembered as asked (see set_selected_timepoints): an edited job's
        explicit wells stay explicit, and stay offered, over a projection
        that does not list them."""
        self._requested["wells"] = list(wells) if wells else None
        if wells:
            self._plate.setSelectedWellIds(list(wells))

    def set_selected_timepoints(self, labels: list[str] | None) -> None:
        # Remembered as asked, not only as ticked: a source that does not
        # exist yet has no boxes to tick, and an edit must not lose the job's
        # choice before set_pending_inputs can offer it.
        self._requested["timepoints"] = list(labels) if labels is not None else None
        if labels is None:
            return
        wanted = set(labels)
        for label, cb in self._tp_checks.items():
            cb.setChecked(label in wanted)

    def set_selected_fovs(self, labels: list[str] | None) -> None:
        self._requested["fovs"] = list(labels) if labels is not None else None
        if labels is None:
            return
        wanted = set(labels)
        for label, cb in self._fov_checks.items():
            cb.setChecked(label in wanted)

    def set_running(self, running: bool) -> None:
        self._running = running
        self._run_btn.setEnabled(not running and self._inputs_ready()
                                 and not self.has_pending_inputs())
        self._queue_btn.setEnabled(not running and self._inputs_ready() and self.interrupted_edit() is None)
        self._run_btn.setText("Subsetting…" if running else self._run_label())
        # Locked alongside the plate (SubsetController._launch) — picking a
        # new source mid-run fires sourceChanged, which repaints the plate
        # for the new dataset while the old run's worker keeps executing in
        # the background; its still-arriving wellStage signals would then
        # paint the *previous* dataset's progress onto wells the user now
        # believes belong to the newly-selected one. setLocked(), not
        # setEnabled(False) — the source path is exactly what a user
        # watching a long run most wants to check, and the latter dims it
        # to the lowest-contrast text on the page for no functional reason
        # (the path display is already read-only; only Browse needs locking).
        self._src_field.setLocked(running)
        self._dest_field.setLocked(running)
        self._raw_box.setEnabled(not running)
        if running:
            self._open_btn.setVisible(False)
            set_variant(self._run_btn, "primary")

    def show_open_in_review(self, dest_root: str) -> None:
        self._dest_root = dest_root
        if self._raw:
            # An unanalysed plate has nothing for Review to show until Analyze runs on it.
            return
        promote_open_button(self._open_btn, self._run_btn)

    def revalidate_fields(self) -> None:
        self._src_field.revalidate()
        self._dest_field.revalidate()
        self._refresh_run_enabled()

    # ── internals ────────────────────────────────────────────────────────────
    def _on_plate_selection_changed(self, wells: list[str]) -> None:
        # A dedicated pre-run tint, not the picker's own default multi-select
        # look -- WellPlateSelector's default "selected" rendering cycles a
        # rank-based rainbow trace colour per selection order, and this same
        # widget is also where SubsetController paints post-run state
        # colours (pending/copying/done/empty — the last of which is
        # theme.Colors.accent, the exact hue a plain unselected-but-enabled
        # well already renders in a faint tint by default). accent_dim reads
        # as "chosen, not yet run" without colliding with either.
        # Dials as well as colours: after a run every copied well is a full
        # circle, and the plate becomes interactive again when the run ends, so
        # without this the pre-run tint would be painted underneath a plate
        # still wearing the last run's progress.
        self._plate.clearWellColors()
        self._plate.clearWellFractions()
        for w in wells:
            self._plate.setWellColor(w, theme.Colors.selected)
        self._refresh_run_enabled()
        self._update_status_preview()

    def _on_source_changed(self) -> None:
        self._open_btn.setVisible(False)
        set_variant(self._run_btn, "primary")
        self._reload_source_metadata()
        self._refresh_run_enabled()
        self._update_status_preview()
        self.sourceChanged.emit()

    def set_pending_inputs(self, pending: dict, projection: dict | None = None) -> None:
        """Show the source as the output of a queued job not yet run.

        *pending* maps ``"source"`` to the note the field shows
        (``datasets.core.pending_inputs``); an empty dict clears it.
        *projection* is what that job is expected to write — ``wells``,
        ``timepoints`` and ``fovs``, each a list or ``None`` for "not known
        until it runs" — and the pickers are built from it instead of from
        the disk. Choices already made on the page (an edited job's own) are
        kept where the projection still offers them.
        """
        wells = self.selected_wells()
        tps = self._current_choice("timepoints", self._tp_checks,
                                   self.all_timepoints_selected, self.selected_timepoints)
        fovs = self._current_choice("fovs", self._fov_checks,
                                    self.all_fovs_selected, self.selected_fovs)
        apply_pending({"source": self._src_field}, pending)
        self._projection = dict(projection or {}) if self.has_pending_inputs() else {}
        self._job_only_choices = (
            {"timepoints": list(tps or []), "fovs": list(fovs or [])}
            if self.has_pending_inputs() else {})
        self._reload_source_metadata()
        if wells:
            # Re-ticked on the plate directly: the page's own selection, not
            # a new request (set_selected_wells would record it as one).
            self._plate.setSelectedWellIds(list(wells))
        self.set_selected_timepoints(tps)
        self.set_selected_fovs(fovs)
        self._refresh_run_enabled()
        self._update_status_preview()

    def has_pending_inputs(self) -> bool:
        return any_pending({"source": self._src_field})

    def input_fields(self) -> dict:
        """The source field an earlier queued job's output can fill."""
        return {"source": self._src_field}

    def _current_choice(self, dimension, checks, all_selected, selected) -> list[str] | None:
        """The page's choice for one dimension, ``None`` for "all" — or what it
        was last asked to select when it had no boxes to tick it in."""
        asked = self._requested.get(dimension)
        if asked is not None and not checks:
            return asked
        return None if all_selected() else selected()

    def _reload_source_metadata(self) -> None:
        if self.has_pending_inputs():
            # Whether a source not written yet will hold only in/ is not known, so the raw controls show only
            # for an edited job that already uses them — keeping its schema, channels and in-place choice — and
            # the run checks them against the real folder when it starts.
            asked = self._requested.get("channels")
            self._raw = bool(self._requested.get("raw"))
            self._interrupted = None                    # a folder not written yet holds no interrupted edit
            self._raw_box.setVisible(self._raw)
            self._schema_note.setText("")
            self._show_channels(list(asked or []) if self._raw else None)
            self._load_projected_metadata()
            self._on_in_place_toggled(self._in_place_cb.isChecked())
            return
        self._wells_hint.setVisible(False)
        self._tp_hint.setText(_TP_PICK_SOURCE)
        self._fov_hint.setText(_FOV_PICK_SOURCE)
        src = self.source_path()
        info = {}
        self._raw = bool(src) and is_raw_dataset(src)
        self._raw_box.setVisible(self._raw)
        self._interrupted = read_in_place_marker(src) if self._raw else None
        if self._raw:
            info = self._raw_info(src)
        elif src:
            info_path = Path(src) / "out" / "pipeline_info.json"
            if info_path.exists():
                try:
                    info = json.loads(info_path.read_text())
                except (OSError, json.JSONDecodeError):
                    info = {}
        self._show_channels(info.get("available_channels") or [] if self._raw else None)

        wells = []
        if src:
            in_dir = Path(src) / "in"
            if in_dir.is_dir():
                from util.well_token import canonical_well_label
                for entry in sorted(in_dir.iterdir()):
                    name = entry.stem if entry.suffix.lower() == ".zip" else entry.name
                    token = canonical_well_label(name)
                    if token:
                        wells.append(token)
        self._source_wells = list(wells)
        self._plate.setEnabledWells(wells or None)
        self._plate.setSelectedWellIds(wells)

        timepoints = sorted(
            {str(t).strip() for t in info.get("available_timepoints", []) or [] if str(t).strip()},
            key=tp_sort_key,
        )
        self._rebuild_checks(self._tp_flow, self._tp_checks, timepoints, self._tp_hint)

        fovs = sorted({str(f).strip() for f in info.get("available_fovs", []) or [] if str(f).strip()})
        self._rebuild_checks(self._fov_flow, self._fov_checks, fovs, self._fov_hint)
        self._apply_interrupted_state()
        self._on_in_place_toggled(self._in_place_cb.isChecked())

    def _apply_interrupted_state(self) -> None:
        """Show the page for an interrupted in-place edit, or give the controls back once there is none.

        The run then finishes that edit with the filter it recorded (``SubsetController._launch`` reads
        :meth:`interrupted_filter`), so every control that would choose a different one is shown at the recorded
        choice and locked: the wells, timepoints, FOVs and channels, the schema, and Edit in place itself.
        """
        marker = self.interrupted_edit()
        finishing = marker is not None
        filt = self.interrupted_filter()
        if finishing:
            if filt is None:
                text = ("This dataset holds an interrupted in-place edit whose record (.aw_subset_in_place) can't "
                        "be read, so Subset can't tell what it was keeping. Some archives may already be cut. "
                        "Nothing can be run on it here until that is settled by hand.")
            else:
                text = (f"This dataset holds an interrupted in-place edit that keeps "
                        f"{describe_in_place_marker(marker)}. Some of its archives are already cut and others "
                        "are not. Run it again to finish it; nothing else can be done with this dataset until "
                        "then.")
                self._show_recorded_choice(filt)
            self._interrupted_note.setText(text)
            self._in_place_cb.setChecked(True)
        self._interrupted_note.setVisible(finishing)
        self._in_place_cb.setEnabled(not finishing)
        self._schema_edit.setEnabled(not finishing)
        self._sep_edit.setEnabled(not finishing)
        if not self._running:
            self._plate.setEnabled(not finishing)
        for checks in (self._tp_checks, self._fov_checks, self._ch_checks):
            for cb in checks.values():
                cb.setEnabled(not finishing)

    def _show_recorded_choice(self, filt: SubsetFilter) -> None:
        """Tick what the interrupted edit keeps. Shown only: the run reads the recorded filter, not these."""
        if filt.wells is not None:
            self._plate.setSelectedWellIds([w for w in self._source_wells if w in filt.wells])
        for checks, kept, norm in ((self._tp_checks, filt.timepoint_labels, str),
                                   (self._fov_checks, filt.fovs, str),
                                   (self._ch_checks, filt.channel_tokens, str.lower)):
            for label, cb in checks.items():
                cb.setChecked(kept is None or norm(label) in kept)

    def _raw_info(self, src: str) -> dict:
        """The pickers' values for an in-only source, and the schema note saying where they came from.

        With an interrupted in-place edit the schema is the one the edit recorded and the record is not checked
        (the partly cut archives no longer match it) — the same reading ``build_subset_plan`` resumes with.
        """
        marker = self._interrupted
        try:
            if marker:
                source = resolve_raw_source(raw_paths(src), typed_schema=str(marker.get("schema") or ""),
                                            typed_sep=str(marker.get("separator") or ""), record_override={})
            else:
                source = resolve_raw_source(raw_paths(src), typed_schema=self._schema_edit.text().strip(),
                                            typed_sep=self.typed_separator())
        except RawSchemaError as exc:
            text_roles.set_role(self._schema_note, text_roles.HELP, tone=text_roles.FAILURE)
            self._schema_note.setText(f"{exc} Only wells can be chosen until the schema is settled.")
            self._schema_note.style().polish(self._schema_note)
            return {}
        self._schema_note.setProperty("textRole", "help")
        self._schema_note.setText(source.describe())
        self._schema_note.style().polish(self._schema_note)
        return source.info

    def _show_channels(self, channels: list[str] | None) -> None:
        """The Channels row: shown for an in-only source (*channels* a list), hidden otherwise (``None``)."""
        shown = channels is not None
        self._ch_heading.setVisible(shown)
        self._rebuild_checks(self._ch_flow, self._ch_checks, list(channels or []), self._ch_hint)
        self._ch_hint.setVisible(shown and not channels)
        self._ch_host.setVisible(shown)

    def _on_schema_edited(self) -> None:
        if not self._raw:
            return
        self._reload_source_metadata()
        self._refresh_run_enabled()
        self._update_status_preview()

    def _run_label(self) -> str:
        if self.interrupted_edit() is not None:
            return _RUN_FINISH_LABEL
        return _RUN_IN_PLACE_LABEL if self.in_place() else _RUN_LABEL

    def _on_in_place_toggled(self, _on: bool) -> None:
        in_place = self.in_place()
        self._in_place_warning.setVisible(in_place)
        self._dest_field.setVisible(not in_place)
        if not self._running:
            self._run_btn.setText(self._run_label())
            set_variant(self._run_btn, "destructive" if in_place else "primary")
        self._refresh_run_enabled()
        self._update_status_preview()

    def _load_projected_metadata(self) -> None:
        """The pickers for a source a queued job has still to write.

        A dimension the projection knows is offered exactly as the disk would
        offer it. One it does not is left empty with a sentence saying the job
        will take all of it — which is what an empty picker already queues as
        (``SubsetController.collect_spec`` sends ``None`` for "all").
        """
        proj = self._projection
        wells = proj.get("wells")
        own = self._requested.get("wells") or []
        self._wells_hint.setVisible(wells is None)
        # The job's own wells are offered whether or not the projection lists
        # them: enabling only the projection's dropped a well the job names
        # on a no-op save, and the run's shortfall check never saw it.
        offered = sorted(set(wells) | set(own)) if wells else None
        self._plate.setEnabledWells(offered)
        self._plate.setSelectedWellIds(list(wells or []))
        # An edited job's own explicit choices are offered whether or not the
        # projection lists them: built from the projection alone, a picker
        # that does not know a dimension came up empty and saved the job as
        # "all", and one that knows a different list dropped the job's
        # choices it did not contain. The run checks them against the real
        # source (SubsetController.start_from_queue).
        own = self._job_only_choices
        tps = set(proj.get("timepoints") or []) | set(own.get("timepoints") or [])
        self._tp_hint.setText(_TP_UNKNOWN)
        self._rebuild_checks(self._tp_flow, self._tp_checks,
                             sorted(tps, key=tp_sort_key), self._tp_hint)
        fovs = set(proj.get("fovs") or []) | set(own.get("fovs") or [])
        self._fov_hint.setText(_FOV_UNKNOWN)
        self._rebuild_checks(self._fov_flow, self._fov_checks, sorted(fovs), self._fov_hint)
        if self._only_from_job("timepoints"):
            self._tp_hint.setText(_ONLY_THESE_TIMEPOINTS)
            self._tp_hint.setVisible(True)
        if self._only_from_job("fovs"):
            self._fov_hint.setText(_ONLY_THESE_FOVS)
            self._fov_hint.setVisible(True)

    def _rebuild_checks(
        self, flow: FlowLayout, store: dict, labels: list[str], hint: QLabel,
    ) -> None:
        while flow.count():
            item = flow.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        store.clear()
        for label in labels:
            cb = QCheckBox(label, flow.parentWidget())
            cb.setChecked(True)
            cb.toggled.connect(self._on_input_changed)
            flow.addWidget(cb)
            store[label] = cb
        hint.setVisible(not labels)

    def _inputs_ready(self) -> bool:
        if self.interrupted_edit() is not None:
            # Finishing reads the recorded filter, not the pickers; an unreadable record cannot be finished.
            return self._src_field.is_valid() and self.interrupted_filter() is not None
        destination_ok = self.in_place() or (
            self._dest_field.is_valid() and self.destination_path() != self.source_path())
        return (
            self._src_field.is_valid()
            and destination_ok
            and bool(self.selected_wells())
            and bool(self.selected_timepoints() or not self._tp_checks)
            and bool(self.selected_fovs() or not self._fov_checks)
            and bool(self.selected_channels() or not self._ch_checks)
        )

    def _on_input_changed(self, *_args) -> None:
        self._refresh_run_enabled()
        self._update_status_preview()

    def _refresh_run_enabled(self) -> None:
        pending = self.has_pending_inputs()
        self._run_btn.setEnabled(self._inputs_ready() and not pending)
        self._run_btn.setToolTip(RUN_WAITS if pending else "")
        # Not queued: a queued job is rebuilt from the pickers, and finishing must use the recorded filter.
        self._queue_btn.setEnabled(self._inputs_ready() and self.interrupted_edit() is None)

    def _update_status_preview(self) -> None:
        # The status strip (no plate map of its own — see SubsetDashboard's
        # own docstring) used to stay hidden until Run was pressed; reveal it
        # as soon as there's a real answer to "what would this subset?" —
        # the current picker selection — instead of gating it on the run
        # having actually started. Deliberately NOT called from
        # _refresh_run_enabled(): revalidate_fields() (called after a run
        # finishes, to refresh the fields' inline validity hints) also goes
        # through that function, and by then self._running is already False
        # -- calling this from there would immediately overwrite the just-
        # finished run's own result colours/headline with a plain
        # pending-coloured "Ready" preview. Wired instead to the specific
        # interactive-change handlers below, none of which fire mid-run
        # (source/dest fields and the plate are all locked for the run's
        # duration) or as part of run completion.
        if self._running:
            return
        if not self._inputs_ready() or self.has_pending_inputs():
            self.dashboard.setVisible(False)
            return
        wells = self.selected_wells()
        self.dashboard.reset()
        self.dashboard.set_wells(wells)
        if self.interrupted_edit() is not None:
            self.dashboard.set_headline("Ready to finish the interrupted in-place edit.")
        elif self.in_place():
            dropped = len(self._source_wells) - len(wells)
            self.dashboard.set_headline(
                f"Ready to edit in place — {len(wells)} well(s) kept"
                + (f", {dropped} deleted." if dropped > 0 else "."))
        else:
            self.dashboard.set_headline(f"Ready — {len(wells)} well(s) selected.")
        self.dashboard.setVisible(True)
