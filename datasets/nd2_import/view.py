"""Qt builder for the Convert ND2 page (the main viewer area).

A source ``.nd2`` picker and a destination-folder picker, the well-source
choice (the file's own position names, or the plate grid), the grid controls
that choice reveals, a per-channel role picker, a live readout of how the file
resolves into wells × FOVs × timepoints, and the Datasets status section — a
per-well plate map (:class:`~datasets.nd2_import.dashboard.Nd2ImportDashboard`)
with the raw log demoted to a collapsible checkbox, the same shape every other
tool page uses.

The plate is filled in from the *current* settings before a run starts, so the
mapping is on screen while it is still cheap to correct. That matters here for
the same reason it does on the CZI page and not on the OME-TIFF one: an ND2's
wells usually come out of the file, and a user has no other way to see what the
file said.

**Simpler than the CZI page in exactly one way**, and it is worth naming
because the two look alike otherwise: an ND2 is always one file. Nikon has no
equivalent of ZEN's *Split Scenes* export, so there is no folder of per-scene
files to discover, no second Browse button, and no "convert the whole folder"
tick-box — and the scan is a single stage rather than a cheap peek followed by
an expensive full read, because reading an ND2's layout means reading its
metadata chunks and its first position's frame times, which is bounded by the
timepoint count rather than by the plate size.

Pure view: it owns widgets and emits intent (``runRequested``,
``queueRequested``, ``openInAnalyzeRequested``); the worker wiring lives in
:mod:`~datasets.nd2_import.controller`. Colours and spacing come from the app
QSS and theme tokens — nothing hardcoded (CLAUDE.md §5).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.safe import swallow
from core.worker_bridge import run_off_thread
from datasets.core.channel_roles_row import ChannelRolesMixin
from datasets.core.dataset_fields import promote_open_button
from datasets.core.log_panel import LogPanelMixin, hrule as _hrule
from datasets.nd2_import.dashboard import Nd2ImportDashboard
from datasets.nd2_import.nd2_import_compute import (
    ConversionParams,
    Nd2ImportError,
    RawFields,
    default_dest,
    destination_problem,
    plan_conversion,
    read_source,
    resume_state,
)
from pipeline.nd2_explode import WELL_SOURCES
from pipeline.nd2_read import Nd2Layout
from ui import theme
from widgets._support import set_variant
from widgets.path_field import PathField, folder_picker

#: Widths of the small numeric boxes. Named rather than written at the call
#: site, both because ARCHITECTURE §8 asks for it and because a literal passed
#: through a helper's parameter is invisible to the pixel guard that enforces it.
_FIELD_W = 48
_ORIGIN_W = 56

#: What an ND2 is called on disk. A tuple rather than one string so the
#: extension test reads the same as the CZI page's, which really does have two.
_ND2_SUFFIXES = (".nd2",)

_WELL_SOURCE_LABELS = {
    "file": "The file's own position names",
    "grid": "A plate grid I set below",
}


def _nd2_source_problem(path) -> str:
    """``""`` if *path* is an ``.nd2`` file, else the reason it is not."""
    candidate = Path(str(path or "").strip())
    if not str(candidate).strip():
        return ""
    if not candidate.exists():
        return "No such file."
    if candidate.is_dir():
        return "Choose the .nd2 file itself, not a folder."
    if candidate.suffix.lower() not in _ND2_SUFFIXES:
        return "Not a .nd2 file."
    return ""


class Nd2ImportPage(ChannelRolesMixin, LogPanelMixin, QWidget):
    """The Convert ND2 feature body."""

    runRequested = Signal()
    queueRequested = Signal()
    openInAnalyzeRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dest_root = ""
        self._dest_user_set = False
        self._running = False
        #: What the last scan was of. Just the source path: an ND2 is one file
        #: and no other field on this page changes which bytes a scan reads, so
        #: typing in the grid or experiment boxes must not cost a re-read of a
        #: multi-gigabyte file on a network share.
        self._scan_key: str | None = None
        #: The last completed read, rendered from until the key changes.
        self._layout: Nd2Layout | None = None
        #: Bumped by every new scan and by teardown. A scan that lands holding
        #: a stale number drops its result without touching a widget — which is
        #: what makes a slow share safe to type over, and what stops a result
        #: arriving after the page has gone.
        self._scan_generation = 0
        #: Every scan bridge still owed a delivery, keyed by generation. A
        #: bridge dropped between its worker's queued emit and the GUI thread's
        #: handling of it is collected, and the queued event then fires on a
        #: freed ``QObject`` — a segfault with no Python traceback. The worker
        #: thread keeps the bridge alive only until it emits, so the reference
        #: covering the rest of that gap has to be this one. Entries are
        #: released by :meth:`_begin_scan`, which runs on the GUI thread
        #: outside any handler, and never by a handler itself (releasing one
        #: from inside its own callback frees it underneath the ``disconnect``
        #: that follows).
        self._scan_bridges: dict[int, object] = {}
        self._scan_delivered: set[int] = set()
        self._scanning = False
        #: The source field's own verdict, and the path it was reached for.
        #: The validator runs on every ``revalidate``, which the readout calls
        #: on every keystroke, and answering it means stat-ing the path.
        self._source_problem = ""
        self._source_facts_for = None
        self.init_channel_roles()
        self._build_ui()
        self.refresh_readout()

    # ── construction ─────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        # Wrapped in a scroll area for the same reason every other Datasets
        # page is: the dashboard's plate map renders illegibly small at the
        # app's default window height otherwise.
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

        heading = QLabel("Convert ND2", self)
        heading.setObjectName("Heading")
        outer.addWidget(heading)
        outer.addWidget(_hrule(self))

        blurb = QLabel(
            "Turn a Nikon <tt>.nd2</tt> plate scan into a dataset folder: one "
            "TIF per channel, well, field and timepoint, grouped into one "
            "archive per well — the layout Analyze reads. Z stacks are "
            "max-projected and each stage position is kept as its own field, "
            "never stitched. Wells normally come from the position names "
            "NIS-Elements saved with the acquisition; a file whose positions "
            "are unnamed can be laid over a plate grid instead.",
            self,
        )
        blurb.setTextFormat(Qt.RichText)
        blurb.setObjectName("Muted")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        self._src_field = PathField("ND2 file", pick=self._pick_source,
                                    validator=self._source_rule)
        self._src_field.changed.connect(self._on_source_changed)
        outer.addWidget(self._src_field)

        self._dest_field = PathField(
            "Destination folder", pick=folder_picker("Select destination folder"),
            validator=self._dest_rule)
        self._dest_field.changed.connect(self._on_dest_changed)
        outer.addWidget(self._dest_field)

        self._build_params(outer)
        self._build_channels(outer)
        self._build_actions(outer)
        self._build_status_section(outer)

    def _sub_heading(self, text: str, outer: QVBoxLayout) -> QLabel:
        label = QLabel(text, self)
        label.setObjectName("Heading")
        outer.addWidget(label)
        return label

    def _small(self, default: str, width: int = _FIELD_W,
               placeholder: str = "") -> QLineEdit:
        edit = QLineEdit(default, self)
        edit.setFixedWidth(width)
        if placeholder:
            edit.setPlaceholderText(placeholder)
        edit.textChanged.connect(self._on_readout_input_changed)
        return edit

    def _build_params(self, outer: QVBoxLayout) -> None:
        self._sub_heading("Which well is which?", outer)

        source_row = QWidget(self)
        source_layout = QHBoxLayout(source_row)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.setSpacing(theme.Spacing.sm)
        source_layout.addWidget(QLabel("Well labels from", source_row))
        self._well_source_cb = QComboBox(source_row)
        for source in WELL_SOURCES:
            self._well_source_cb.addItem(_WELL_SOURCE_LABELS[source],
                                         userData=source)
        self._well_source_cb.currentIndexChanged.connect(
            self._on_well_source_changed)
        source_layout.addWidget(self._well_source_cb)
        source_layout.addStretch(1)
        outer.addWidget(source_row)

        # Everything below this point is the grid fallback, hidden while the
        # wells are coming out of the file. Hidden rather than merely disabled:
        # a row of greyed-out grid boxes reads as settings that apply and are
        # locked, which is the opposite of what is true.
        self._grid_host = QWidget(self)
        grid_outer = QVBoxLayout(self._grid_host)
        grid_outer.setContentsMargins(0, 0, 0, 0)
        grid_outer.setSpacing(theme.Spacing.sm)

        grid_hint = QLabel(
            "The acquisition order is laid over this block of the plate: the "
            "first stage position becomes the origin well, and the scan snakes "
            "or rasters from there.", self._grid_host)
        grid_hint.setObjectName("Muted")
        grid_hint.setWordWrap(True)
        grid_outer.addWidget(grid_hint)

        self._grid_row = QWidget(self._grid_host)
        grid = QHBoxLayout(self._grid_row)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(theme.Spacing.sm)
        grid.addWidget(QLabel("Rows", self._grid_row))
        self._rows_edit = self._small("")
        grid.addWidget(self._rows_edit)
        grid.addWidget(QLabel("Columns", self._grid_row))
        self._cols_edit = self._small("")
        grid.addWidget(self._cols_edit)
        grid.addWidget(QLabel("Origin well", self._grid_row))
        self._origin_edit = self._small("A01", _ORIGIN_W)
        grid.addWidget(self._origin_edit)
        grid.addWidget(QLabel("Order", self._grid_row))
        self._direction_cb = QComboBox(self._grid_row)
        self._direction_cb.addItem("Along rows", userData="row")
        self._direction_cb.addItem("Down columns", userData="col")
        self._direction_cb.currentIndexChanged.connect(
            self._on_readout_input_changed)
        grid.addWidget(self._direction_cb)
        self._serpentine_cb = QCheckBox("Serpentine", self._grid_row)
        self._serpentine_cb.setChecked(True)
        self._serpentine_cb.setToolTip(
            "Ticked, the stage snakes: each line is swept the opposite way to "
            "the last. Unticked, it rasters: every line is swept the same way. "
            "A raster read as a snake labels every second line backwards, with "
            "the well count still correct.")
        self._serpentine_cb.toggled.connect(self._on_readout_input_changed)
        grid.addWidget(self._serpentine_cb)
        grid.addStretch(1)
        grid_outer.addWidget(self._grid_row)
        outer.addWidget(self._grid_host)
        # Hidden from the first paint, not only once the choice changes:
        # the well source starts at "file", so a visible grid on a fresh
        # page offers settings that do not apply to the run it would start.
        self._grid_host.setVisible(self.well_source() == "grid")

        self._sub_heading("Conversion options", outer)
        options = QWidget(self)
        options_layout = QHBoxLayout(options)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(theme.Spacing.sm)
        options_layout.addWidget(QLabel("Experiment name", options))
        self._experiment_edit = QLineEdit("", options)
        self._experiment_edit.setPlaceholderText("from the filename")
        self._experiment_edit.textChanged.connect(self._on_readout_input_changed)
        options_layout.addWidget(self._experiment_edit)
        options_layout.addWidget(QLabel("FOVs per well", options))
        self._fovs_edit = self._small("", placeholder="auto")
        self._fovs_edit.setToolTip(
            "Leave blank to use the positions the file holds per well. Only a "
            "grid-mapped file needs this set, and then only when its positions "
            "do not divide evenly by themselves.")
        options_layout.addWidget(self._fovs_edit)
        options_layout.addWidget(QLabel("Keep every Nth timepoint", options))
        self._stride_edit = self._small("", placeholder="all")
        self._stride_edit.setToolTip(
            "Leave blank to convert every timepoint. Set 3 to convert the "
            "first and then every third one after it, which is how a "
            "densely-sampled timelapse is converted at a coarser resolution "
            "without re-acquiring it. The kept timepoints keep their own "
            "names, so t0, t3, t6 stay three hours apart in Review.")
        options_layout.addWidget(self._stride_edit)
        options_layout.addWidget(QLabel("Worker threads", options))
        self._workers_edit = self._small("", placeholder="auto")
        options_layout.addWidget(self._workers_edit)
        options_layout.addStretch(1)
        outer.addWidget(options)

        self._readout = QLabel("", self)
        self._readout.setObjectName("Muted")
        self._readout.setWordWrap(True)
        outer.addWidget(self._readout)

        self._verdict = QLabel("", self)
        self._verdict.setWordWrap(True)
        self._verdict.setVisible(False)
        outer.addWidget(self._verdict)

    def _build_channels(self, outer: QVBoxLayout) -> None:
        self._channels_caption = QLabel(
            "Channel roles — assign each channel a role (seeded from its "
            "name). These pre-fill Analyze's channel tokens when you open the "
            "dataset there; the ND2's channel names are always what goes into "
            "the filenames.", self,
        )
        self._channels_caption.setObjectName("Muted")
        self._channels_caption.setWordWrap(True)
        self._channels_caption.setVisible(False)
        outer.addWidget(self._channels_caption)
        self._channels_host = QWidget(self)
        self._channels_lay = QVBoxLayout(self._channels_host)
        self._channels_lay.setContentsMargins(0, 0, 0, 0)
        self._channels_lay.setSpacing(theme.Spacing.xs)
        outer.addWidget(self._channels_host)

    def _build_actions(self, outer: QVBoxLayout) -> None:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.Spacing.sm)
        self._run_btn = QPushButton("▶  Convert", row)
        set_variant(self._run_btn, "primary")
        self._run_btn.clicked.connect(self.runRequested.emit)
        layout.addWidget(self._run_btn)
        self._queue_btn = QPushButton("+ Queue", row)
        set_variant(self._queue_btn, "secondary")
        self._queue_btn.setToolTip("Add this conversion to the Job Queue "
                                   "instead of running it now")
        self._queue_btn.clicked.connect(self.queueRequested.emit)
        layout.addWidget(self._queue_btn)
        self._open_btn = QPushButton("Open dataset in Analyze", row)
        set_variant(self._open_btn, "secondary")
        self._open_btn.setVisible(False)
        self._open_btn.clicked.connect(
            lambda: self.openInAnalyzeRequested.emit(self._dest_root))
        layout.addWidget(self._open_btn)
        layout.addStretch(1)
        outer.addWidget(row)

    def _build_status_section(self, outer: QVBoxLayout) -> None:
        self.build_log_header(outer, "Convert ND2 Output")
        self._dashboard = Nd2ImportDashboard(self)
        # Picking wells on the plate is how a subset conversion is chosen. It
        # already shows the mapping the settings resolve to, so a selection
        # cannot name a well the source does not have. Nothing selected is the
        # whole plate.
        self._dashboard.wellsSelected.connect(self._on_readout_input_changed)
        outer.addWidget(self._dashboard)
        self.build_log_tail(outer, placeholder="Status will appear here.")

    # ── pickers ──────────────────────────────────────────────────────────────
    def _pick_source(self, current: str, parent: QWidget | None = None) -> str:
        """Pick an ``.nd2`` file — the Browse button beside the source field.

        A plain file dialog, and only that: unlike the CZI page there is no
        second folder button, because NIS-Elements writes a plate as one file.
        """
        chosen, _ = QFileDialog.getOpenFileName(
            parent or self, "Select ND2 file", current or "",
            "Nikon ND2 (*.nd2);;All files (*)",
        )
        return chosen or ""

    # ── field plumbing ───────────────────────────────────────────────────────
    def _on_source_changed(self) -> None:
        src = self._src_field.path()
        # Always rescan, even onto the same path. The scan key carries no mtime
        # on purpose, so re-picking the source is the one moment a file
        # re-exported in place gets noticed. Without this the readout could
        # describe 48 wells while the run converted 96.
        self._scan_key = None
        self._refresh_source_facts()
        # Fill the destination in beside the source unless the user has
        # already chosen one — a default that overwrote their answer would be
        # worse than no default at all.
        if src and not self._dest_user_set:
            self._dest_field.set_path(str(default_dest(src)))
        self.refresh_readout()

    def _on_dest_changed(self) -> None:
        self._dest_user_set = bool(self._dest_field.path().strip())
        self.refresh_readout()

    def _on_readout_input_changed(self, *_args) -> None:
        self.refresh_readout()

    def _on_well_source_changed(self, *_args) -> None:
        self._grid_host.setVisible(self.well_source() == "grid")
        self.refresh_readout()

    def well_source(self) -> str:
        return str(self._well_source_cb.currentData() or "file")

    def _source_rule(self, root) -> str:
        """The source field's verdict, from facts gathered when it last changed.

        :func:`_nd2_source_problem` is still the rule; this is the page's cache
        of its answer. The field revalidates on every readout refresh, which a
        keystroke in the grid box triggers, and the rule has to touch the
        filesystem to answer.
        """
        if str(root) == str(self._source_facts_for):
            return self._source_problem
        return _nd2_source_problem(root)

    def _refresh_source_facts(self) -> None:
        """Re-answer what the selected path is, for the cached validator."""
        source = Path(self.source_path())
        self._source_facts_for = str(source)
        self._source_problem = _nd2_source_problem(source)

    def source_path(self) -> str:
        return self._src_field.path().strip()

    def destination_path(self) -> str:
        return self._dest_field.path().strip()

    def set_source_path(self, path: str) -> None:
        self._src_field.set_path(path or "")
        self._scan_key = None
        self._refresh_source_facts()
        self.refresh_readout()

    def set_destination_path(self, path: str) -> None:
        self._dest_field.set_path(path or "")
        self._dest_user_set = bool((path or "").strip())
        self.refresh_readout()

    def raw_fields(self) -> RawFields:
        """The fields exactly as typed, for validation that can say what is missing."""
        return RawFields(
            well_source=self.well_source(),
            rows=self._rows_edit.text(),
            cols=self._cols_edit.text(),
            fovs=self._fovs_edit.text(),
            origin=self._origin_edit.text(),
            workers=self._workers_edit.text(),
            timepoint_stride=self._stride_edit.text(),
            direction=str(self._direction_cb.currentData() or "row"),
            serpentine=self._serpentine_cb.isChecked(),
            experiment=self._experiment_edit.text(),
            well_subset=tuple(self._dashboard.selected_wells()),
        )

    def params(self) -> ConversionParams:
        return self.raw_fields().to_params()

    def set_params(self, params: ConversionParams) -> None:
        """Repopulate every field from *params* — the Job Queue's edit path."""
        index = self._well_source_cb.findData(params.well_source)
        if index >= 0:
            with QSignalBlocker(self._well_source_cb):
                self._well_source_cb.setCurrentIndex(index)
        self._rows_edit.setText(str(params.grid_rows or ""))
        self._cols_edit.setText(str(params.grid_cols or ""))
        self._origin_edit.setText(params.origin or "A01")
        self._fovs_edit.setText("" if params.fovs_per_well is None
                                else str(params.fovs_per_well))
        self._workers_edit.setText(str(params.workers or ""))
        self._stride_edit.setText(
            "" if params.timepoint_stride in (0, 1)
            else str(params.timepoint_stride))
        direction = self._direction_cb.findData(params.direction)
        if direction >= 0:
            self._direction_cb.setCurrentIndex(direction)
        self._serpentine_cb.setChecked(bool(params.serpentine))
        self._experiment_edit.setText(params.experiment or "")
        self._dashboard.set_selected_wells(params.well_subset)
        self._grid_host.setVisible(self.well_source() == "grid")
        self.refresh_readout()

    # ── run-state display ────────────────────────────────────────────────────
    def set_well_fraction(self, well_id: str, done: int, total: int) -> None:
        self._dashboard.set_well_progress(well_id, done, total)

    def clear_well_fractions(self) -> None:
        self._dashboard.reset()

    def set_outcome(self, outcome: str, message: str = "") -> None:
        """Show how the run ended: ``done`` / ``failed`` / ``cancelled``."""
        if outcome == "done":
            self._dashboard.set_done(message)
        elif outcome == "cancelled":
            self._dashboard.set_cancelled(message)
        else:
            self._dashboard.set_failed(message)

    def clear_outcome(self) -> None:
        self._dashboard.clear_outcome()

    def set_progress(self, done: int, total: int) -> None:
        """Report the run's plane count — as text, deliberately not as the bar.

        The dashboard's bar counts **wells**: that is what its plate draws and
        what its rail tallies, and it is what every other Datasets dashboard
        puts there. The worker reports *planes*, which is a finer and more
        useful number but a different unit, and writing both to one bar is two
        writers with two denominators on one widget. Both numbers are worth
        having, so each goes where it means something.
        """
        if total <= 0:
            self._dashboard.set_status("")
            return
        self._dashboard.set_status(f"{done:,} of {total:,} image(s)")

    def set_running(self, running: bool) -> None:
        self._running = bool(running)
        self._run_btn.setText("Converting…" if running else "▶  Convert")
        self._run_btn.setEnabled(not running and self._can_convert())
        self._queue_btn.setEnabled(not running and self._can_convert())
        if running:
            self._open_btn.setVisible(False)
            set_variant(self._run_btn, "primary")
        for widget in (self._well_source_cb, self._grid_host,
                       self._experiment_edit, self._fovs_edit,
                       self._stride_edit, self._workers_edit):
            widget.setEnabled(not running)
        # Path fields lock rather than disable: ``setEnabled(False)`` dims the
        # path text too, which is the one piece of context someone watching a
        # long run most wants to read (see ``widgets/path_field.py``).
        self._dashboard.set_selection_enabled(not running)
        self._src_field.setLocked(running)
        self._dest_field.setLocked(running)

    def show_open_in_analyze(self, dest_root: str) -> None:
        self._dest_root = dest_root
        promote_open_button(self._open_btn, self._run_btn)

    # ── readout ──────────────────────────────────────────────────────────────
    def _dest_rule(self, root) -> str:
        """The destination rule *with* the run in view, so a resume is not a problem."""
        src = self.source_path()
        if not src:
            return destination_problem(root)
        return destination_problem(root, src, self.params())

    def is_scanning(self) -> bool:
        """True while a background source scan has yet to land.

        Public because a caller that drives this page — a test, or anything
        that wants to know whether the readout is settled — otherwise has to
        guess at a duration, and a guess is either a flake or a delay.
        """
        return self._scanning

    def stop_scanning(self) -> None:
        """Make any scan in flight land on the floor instead of on a widget.

        Bumps the generation, which every handler checks before it touches
        anything, and **keeps** the bridges: dropping them here is what the
        dict above exists to prevent, and a result that arrives after teardown
        must be free to arrive and be ignored rather than free the object it
        arrives on.
        """
        self._scan_generation += 1
        self._scanning = False

    def _begin_scan(self, src: str) -> None:
        """Read the file's layout off the UI thread.

        One stage, not the CZI page's two. An ND2 states its whole layout in
        its metadata chunks, and the elapsed hours come from the *first*
        position's frame times, so the cost is bounded by the timepoint count
        rather than by the number of wells. There is no cheap half worth
        drawing a provisional plate from.

        It still goes to a worker thread: it is a file read, and on a network
        share a large file's chunk map is not free. Doing it in
        ``refresh_readout`` would freeze the window on a page whose whole job
        is to be looked at before a long run.
        """
        self._scan_generation += 1
        generation = self._scan_generation
        self._scanning = True
        self._scan_stage = "Reading the file…"
        # Release what has already been delivered. Safe here and nowhere else:
        # this runs from a GUI event, never from inside a handler.
        for key in list(self._scan_delivered):
            self._scan_bridges.pop(key, None)
            self._scan_delivered.discard(key)
        self._readout.setText("Reading the file…")
        self._set_verdict("", "muted")
        self._update_run_gate(False)

        def _read(report):
            return read_source(src, report=report)

        self._scan_bridges[generation] = run_off_thread(
            _read,
            on_done=lambda layout: self._on_scanned(generation, layout),
            on_failed=lambda message: self._on_scan_failed(generation, message),
            on_stage=lambda name: self._on_scan_stage(generation, name),
            on_progress=lambda done, total: self._on_scan_progress(
                generation, done, total),
        )

    def _on_scan_stage(self, generation: int, name: str) -> None:
        """Say which part of the read is under way.

        One unchanging sentence for the whole read is indistinguishable from a
        page that has hung, and on a large file over a share the read is long
        enough for that to be the natural reading. These arrive on the GUI
        thread through the bridge's queued signal, so the worker never touches
        a widget.
        """
        if generation == self._scan_generation and self._scanning:
            self._scan_stage = name
            self._readout.setText(name)

    def _on_scan_progress(self, generation: int, done: int, total: int) -> None:
        """Add a count to the stage that has one, rather than replacing it."""
        if generation != self._scan_generation or not self._scanning:
            return
        stage = getattr(self, "_scan_stage", "Reading the file…")
        if total > 0:
            self._readout.setText(
                f"{stage.rstrip('… ')} ({done + 1} of {total})…")

    def _delivered(self, generation: int) -> None:
        """Note that this bridge has called back, so it may be released later.

        Recorded before the staleness check, because delivery is a fact about
        the bridge and happens whether or not the page still wants the answer.
        """
        self._scan_delivered.add(generation)

    def _on_scanned(self, generation: int, layout: Nd2Layout) -> None:
        self._delivered(generation)
        if generation != self._scan_generation:
            return
        self._scanning = False
        self._layout = layout
        self._render(layout)

    def _on_scan_failed(self, generation: int, message: str) -> None:
        """A scan that raised.

        ``read_source`` reports rather than raises, so reaching here means
        something below it did — an unreadable share, a permission error — and
        answering "nothing to report" would be the fail-open the OME guard was
        fixed against.
        """
        self._delivered(generation)
        if generation != self._scan_generation:
            return
        self._scanning = False
        self._layout = None
        # The key is deliberately **kept**. Clearing it here looks like the fix
        # for "a share that blinked leaves the page stuck", and it is a worse
        # bug: with no key, the next ``refresh_readout`` — which a keystroke in
        # the grid or experiment box triggers — starts another scan, which
        # fails, which clears it again: one scan per character typed. Recovery
        # is re-picking the source, which always rescans.
        self._readout.setText("")
        self._set_verdict(f"Could not read the file: {message}", "error")
        self._dashboard.set_wells([])
        self._update_run_gate(False)

    def _set_verdict(self, text: str, tone: str) -> None:
        self._verdict.setText(text)
        self._verdict.setObjectName("Error" if tone == "error" else "Muted")
        self._verdict.setVisible(bool(text))
        # Re-polish: an object name set after the widget is shown does not
        # repaint on its own, so the second verdict would wear the first's tone.
        with swallow("nd2 verdict restyle"):
            self._verdict.style().unpolish(self._verdict)
            self._verdict.style().polish(self._verdict)

    def refresh_readout(self) -> None:
        """Recompute the readout, the plate preview and the Run gate.

        Reads no files itself. A new source starts a background scan;
        everything else renders from the scan already in hand, which is what
        lets a keystroke in the grid fields cost nothing on a network share.
        """
        self._src_field.revalidate()
        self._dest_field.revalidate()
        src = self.source_path()
        if not src:
            self._scan_key = None
            self._layout = None
            self.stop_scanning()
            self._readout.setText("Choose an ND2 file to see what it holds.")
            self._set_verdict("", "muted")
            self._rebuild_channels([])
            self._dashboard.set_wells([])
            self._update_run_gate(False)
            return

        if src != self._scan_key:
            self._scan_key = src
            self._layout = None
            self._begin_scan(src)
            return
        if self._layout is None:
            return          # a scan is in flight; it will render when it lands
        self._render(self._layout)

    def _render(self, layout: Nd2Layout) -> None:
        """Draw everything that follows from a file already read."""
        src = self.source_path()
        problems = self.raw_fields().problems()
        unreadable = layout.warnings and not layout.n_positions
        if unreadable:
            self._readout.setText("")
            self._set_verdict(layout.warnings[0], "error")
            self._rebuild_channels([])
            self._dashboard.set_wells([])
            self._update_run_gate(False)
            return

        self._readout.setText(_describe(layout))
        self._rebuild_channels(list(layout.channel_names))
        if problems:
            self._set_verdict(" ".join(problems), "error")
            self._dashboard.set_wells([])
            self._update_run_gate(False)
            return
        try:
            plan = plan_conversion(layout, self.params())
        except Nd2ImportError as exc:
            self._set_verdict(str(exc), "error")
            self._dashboard.set_wells([])
            self._update_run_gate(False)
            return

        note = ""
        dest = self.destination_path()
        if dest:
            note = resume_state(dest, src, self.params()).hint()
        # The timepoint warning goes in the same line rather than a log the
        # user reads afterwards: a plate that lost its elapsed hours has no
        # time axis in Review, and now is when the source can still be
        # re-acquired or re-exported.
        parts = [plan.summary(), plan.timepoint_warning, note]
        self._set_verdict("  ".join(p for p in parts if p), "muted")
        # The plate shows the mapping *before* the run, which is the whole
        # point of drawing it: a well the file named wrongly is visible here
        # and nowhere else.
        #
        # Seeded mid-run too, but only onto a plate that is still **empty**.
        # A job started from the Job Queue calls ``show_job`` and ``_launch``
        # back to back while the source scan is still on its worker thread, so
        # the run begins with no wells on the plate — and every well-progress
        # signal for the whole conversion is then dropped by the dashboard,
        # which ignores wells it does not know. The run wrote its archives and
        # reported "done" above a plate reading "Pending: 0" with the bar at
        # zero. Bounded to the empty case because a mid-run reseed of a
        # *populated* plate would wipe the dials of the run in flight; settings
        # are disabled while running, so an empty plate is the only way the
        # mapping can arrive late.
        if not self._running or not self._dashboard.well_ids():
            self._dashboard.set_wells(plan.well_labels)
        self._update_run_gate(True)

    def _update_run_gate(self, ready: bool) -> None:
        # The destination is part of "can this run", not merely a hint under
        # its own field. Without this the buttons stayed live for a folder
        # already holding a converted plate, for a finished dataset, and for a
        # resume whose settings had changed — the run then refused on the
        # worker thread, and **+ Queue** put a job in the queue that was
        # guaranteed to fail. The OME-TIFF page has always gated on it.
        if ready and self._dest_rule(self.destination_path()):
            ready = False
        self._ready = bool(ready)
        enabled = ready and not self._running
        self._run_btn.setEnabled(enabled)
        self._queue_btn.setEnabled(enabled)

    def _can_convert(self) -> bool:
        return bool(getattr(self, "_ready", False))


def _describe(layout: Nd2Layout) -> str:
    """What the file holds, in the page's own words.

    The ND2 reader has no equivalent of :meth:`pipeline.czi_set.CziSet.describe`
    because there is no set to describe — one file, one layout — so the
    sentence is assembled here, where it is only ever shown.
    """
    shape = " × ".join([
        f"{layout.n_positions} position(s)",
        f"{max(1, layout.size_t)} timepoint(s)",
        f"{max(1, layout.size_z)} Z-plane(s)",
        f"{max(1, layout.size_c)} channel(s)",
    ])
    line = f"{shape} — {layout.width}×{layout.height} px, {layout.dtype}."
    if layout.warnings:
        line += "  " + "  ".join(layout.warnings)
    return line
