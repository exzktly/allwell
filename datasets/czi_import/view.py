"""Qt builder for the Convert CZI page (the main viewer area).

A source ``.czi`` picker and a destination-folder picker, the well-source
choice (the file's own scene names, or the plate grid), the grid controls that
choice reveals, a per-channel role picker, a live readout of how the file
resolves into wells × FOVs × timepoints, and the Datasets status section — a
per-well plate map (:class:`~datasets.czi_import.dashboard.CziImportDashboard`)
with the raw log demoted to a collapsible checkbox, the same shape every other
tool page uses.

The plate is filled in from the *current* settings before a run starts, so the
mapping is on screen while it is still cheap to correct. That matters more here
than on the OME-TIFF page: a CZI's wells usually come out of the file, and a
user has no other way to see what the file said.

Pure view: it owns widgets and emits intent (``runRequested``,
``queueRequested``, ``openInAnalyzeRequested``); the worker wiring lives in
:mod:`~datasets.czi_import.controller`. Colours and spacing come from the app
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
from datasets.core.channel_roles_row import ChannelRolesMixin
from datasets.core.dataset_fields import promote_open_button
from datasets.core.log_panel import LogPanelMixin, hrule as _hrule
from datasets.czi_import.dashboard import CziImportDashboard
from datasets.czi_import.czi_import_compute import (
    ConversionParams,
    CziImportError,
    RawFields,
    default_dest,
    destination_problem,
    peek_source,
    plan_conversion,
    read_source,
    resume_state,
)
from core.worker_bridge import run_off_thread
from pipeline.czi_explode import WELL_SOURCES
from pipeline.czi_set import CZI_SUFFIXES, CziSet, siblings_of
from ui import theme
from widgets._support import set_variant
from widgets.path_field import PathField, folder_picker


#: Widths of the small numeric boxes. Named rather than written at the call
#: site, both because ARCHITECTURE §8 asks for it and because a literal passed
#: through a helper's parameter is invisible to the pixel guard that enforces it.
_FIELD_W = 48
_ORIGIN_W = 56

_WELL_SOURCE_LABELS = {
    "file": "The file's own scene names",
    "grid": "A plate grid I set below",
}


def _is_destination(root) -> str:
    """The destination rule with no run in view — see `CziImportPage._dest_rule`.

    :class:`~widgets.path_field.PathField` validates a path on its own, so this
    cannot see the source or the settings and therefore cannot see a resume.
    The page rebinds it to ``_dest_rule`` at build time, which can; this stays
    as the honest answer for a ``PathField`` used without one.
    """
    return destination_problem(root)


class CziImportPage(ChannelRolesMixin, LogPanelMixin, QWidget):
    """The Convert CZI feature body."""

    runRequested = Signal()
    queueRequested = Signal()
    openInAnalyzeRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dest_root = ""
        self._dest_user_set = False
        self._running = False
        #: The last layout read, keyed on the source path and its mtime, so
        #: typing in a grid field does not re-read a multi-gigabyte file on
        #: every keystroke. Keyed on mtime as well as path because a re-export
        #: saved over the same name is a different file.
        #: What the last scan was of — the source path and whether the folder
        #: was widened. Deliberately **not** the resolved file list or their
        #: mtimes: building that key is itself a stat of every file, and this
        #: is consulted on every keystroke in the grid and experiment fields.
        #: Those keystrokes cannot change which files a run reads, so they must
        #: not cost a round trip each on a network share.
        self._scan_key: tuple | None = None
        #: The last completed full read, rendered from until the key changes.
        self._czi_set: CziSet | None = None
        #: Bumped by every new scan and by teardown. A scan that lands holding
        #: a stale number drops its result without touching a widget — which is
        #: what makes a slow share safe to type over, and what stops a result
        #: arriving after the page has gone.
        self._scan_generation = 0
        #: Every scan bridge still owed a delivery, keyed ``(generation,
        #: stage)``. A bridge that is dropped between its worker's queued emit
        #: and the GUI thread's handling of it is collected, and the queued
        #: event then fires on a freed ``QObject`` — a segfault with no Python
        #: traceback. The worker thread keeps the bridge alive only until it
        #: emits, so the reference that covers the rest of that gap has to be
        #: this one. Entries are released by :meth:`_begin_scan`, which runs on
        #: the GUI thread outside any handler, and never by a handler itself
        #: (releasing one from inside its own callback frees it underneath the
        #: ``disconnect`` that follows).
        self._scan_bridges: dict[tuple[int, str], object] = {}
        self._scan_delivered: set[tuple[int, str]] = set()
        self._scanning = False
        #: Whether the user has touched the "convert the whole folder" box.
        #: Until they do it follows the folder; after, their answer stands.
        self._folder_touched = False
        #: What the selected path turned out to be, and how many ``.czi`` files
        #: sit beside it. Cached because establishing it is a stat of the path
        #: and a listing of its folder — one round trip per file on a network
        #: share — and only a change of *source* can change the answer. It was
        #: recomputed on every keystroke in the grid and experiment fields,
        #: which is three listings of a 96-file folder per character typed.
        self._source_is_dir = False
        self._sibling_count = 0
        #: The source field's own verdict, and the path it was reached for.
        #: The validator runs on every ``revalidate``, which the readout calls
        #: on every keystroke, and answering it means listing the folder.
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

        heading = QLabel("Convert CZI", self)
        heading.setObjectName("Heading")
        outer.addWidget(heading)
        outer.addWidget(_hrule(self))

        blurb = QLabel(
            "Turn a Zeiss <tt>.czi</tt> plate scan into a dataset folder: one "
            "TIF per channel, well, field and timepoint, grouped into one "
            "archive per well — the layout Analyze reads. Z stacks are "
            "max-projected and mosaic tiles are kept as separate fields, never "
            "stitched. Wells normally come from the scene names inside the "
            "file; a file that does not name its scenes can be laid over a "
            "plate grid instead. ZEN saves a plate either as one file holding "
            "every scene or as one file per scene — pick either the file or "
            "the folder.",
            self,
        )
        blurb.setTextFormat(Qt.RichText)
        blurb.setObjectName("Muted")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        self._src_field = PathField("CZI file or folder", pick=self._pick_source,
                                    validator=self._source_rule)
        self._src_field.changed.connect(self._on_source_changed)
        outer.addWidget(self._src_field)

        # The second way in. ZEN writes a plate as one file or as a folder of
        # them, and one dialog cannot accept both kinds — see ``_pick_source``.
        folder_row = QWidget(self)
        folder_layout = QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        self._pick_folder_btn = QPushButton(
            "Choose a folder of .czi files instead…", folder_row)
        set_variant(self._pick_folder_btn, "secondary")
        self._pick_folder_btn.setToolTip(
            "For ZEN's Split Scenes export, which writes one .czi per scene")
        self._pick_folder_btn.clicked.connect(self._pick_source_folder)
        folder_layout.addWidget(self._pick_folder_btn)
        folder_layout.addStretch(1)
        outer.addWidget(folder_row)

        self._dest_field = PathField(
            "Destination folder", pick=folder_picker("Select destination folder"),
            validator=self._dest_rule)
        self._dest_field.changed.connect(self._on_dest_changed)
        outer.addWidget(self._dest_field)

        self._folder_cb = QCheckBox("", self)
        self._folder_cb.setToolTip(
            "ZEN's Split Scenes export writes one .czi per scene. Ticked, "
            "every .czi in this folder is converted as a single plate.")
        self._folder_cb.setVisible(False)
        self._folder_cb.toggled.connect(self._on_folder_box_toggled)
        outer.addWidget(self._folder_cb)

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
            "Leave blank to use the tiles the file holds per scene. Only a "
            "grid-mapped file needs this set, and then only when its positions "
            "do not divide evenly by themselves.")
        options_layout.addWidget(self._fovs_edit)
        options_layout.addWidget(QLabel("Keep every Nth timepoint", options))
        self._stride_edit = self._small("", placeholder="all")
        self._stride_edit.setToolTip(
            "Leave blank to convert every timepoint. Set 3 to convert the "
            "first and then every third one after it, which is how a "
            "densely-sampled timelapse is converted at a coarser resolution "
            "without re-exporting it. The kept timepoints keep their own "
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
            "dataset there; the CZI's channel names are always what goes into "
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
        self.build_log_header(outer, "Convert CZI Output")
        self._dashboard = CziImportDashboard(self)
        # Picking wells on the plate is how a subset conversion is chosen. It
        # already shows the mapping the settings resolve to, so a selection
        # cannot name a well the source does not have. Nothing selected is the
        # whole plate.
        self._dashboard.wellsSelected.connect(self._on_readout_input_changed)
        outer.addWidget(self._dashboard)
        self.build_log_tail(outer, placeholder="Status will appear here.")

    # ── pickers ──────────────────────────────────────────────────────────────
    def _pick_source(self, current: str, parent: QWidget | None = None) -> str:
        """Pick a ``.czi`` file — the Browse button beside the source field.

        A plain file dialog, and a **second button** picks a folder, because
        one dialog cannot do both. ``QFileDialog`` in ``Directory`` mode shows
        files when asked but will not *accept* one — ``accept()`` leaves
        ``result() == 0`` with a ``.czi`` selected — and ``PathField``'s edit is
        read-only, so that was the only way in and the single-file workflow was
        unreachable through the UI while the docstring and the manual both said
        otherwise. Nothing in thirty-three page tests pressed Browse
        (CLAUDE.md §7.6: asserting a control is offered is not asserting it
        works).
        """
        chosen, _ = QFileDialog.getOpenFileName(
            parent or self, "Select CZI file", current or "",
            "Zeiss CZI (*.czi);;All files (*)",
        )
        return chosen or ""

    def _pick_source_folder(self) -> None:
        """Pick the folder a split-scene export was written to, into the same field."""
        chosen = folder_picker("Select a folder of CZI files")(
            self.source_path(), self)
        if chosen:
            self._src_field.set_path(chosen)
            self._on_source_changed()

    # ── field plumbing ───────────────────────────────────────────────────────
    def _on_source_changed(self) -> None:
        src = self._src_field.path()
        # Always rescan, even onto the same path. The scan key is
        # ``(source, use_folder)`` and carries no mtime on purpose — building
        # one is a stat per file, on the path a keystroke takes — so re-picking
        # the source is the one moment a plate re-exported in place gets
        # noticed. Without this the readout could describe 48 wells while the
        # run converted 96, which the key it replaced (path + mtime) could not
        # do.
        self._scan_key = None
        # A new source is a new question about the folder, so the box goes
        # back to following it. Without this, unticking it once made every
        # later plate convert one file of its folder.
        self._folder_touched = False
        self._refresh_folder_box()
        # Fill the destination in beside the source unless the user has
        # already chosen one — a default that overwrote their answer would be
        # worse than no default at all.
        if src and not self._dest_user_set:
            self._dest_field.set_path(str(default_dest(src, self.params())))
        self.refresh_readout()

    def _on_dest_changed(self) -> None:
        self._dest_user_set = bool(self._dest_field.path().strip())
        self.refresh_readout()

    def _on_readout_input_changed(self, *_args) -> None:
        self.refresh_readout()

    def _on_folder_box_toggled(self, _checked: bool) -> None:
        self._folder_touched = True
        self.refresh_readout()

    def _on_well_source_changed(self, *_args) -> None:
        self._grid_host.setVisible(self.well_source() == "grid")
        self.refresh_readout()

    def well_source(self) -> str:
        return str(self._well_source_cb.currentData() or "file")

    def use_folder(self) -> bool:
        """Whether the run converts every ``.czi`` beside the selected one.

        A folder selected outright always converts all of them — the folder
        *is* the set — whatever the box says, so this answers True for one
        without consulting it. The box only ever widens a single **file** to
        its folder, which is the case where widening is a choice.

        Reads the cached verdict on what the source is rather than asking the
        filesystem: this is called from ``params()``, which the readout calls
        several times per keystroke.
        """
        if self._source_is_dir:
            return True
        return self._folder_cb.isChecked()

    def _source_rule(self, root) -> str:
        """The source field's verdict, from facts gathered when it last changed.

        :func:`_czi_source_problem` is still the rule; this is the page's cache
        of its answer. The field revalidates on every readout refresh, and the
        rule has to list the folder to answer — so unbound, a keystroke in the
        grid box listed a 96-file plate over the wire.
        """
        if str(root) == str(self._source_facts_for):
            return self._source_problem
        return _czi_source_problem(root)

    def _refresh_folder_box(self) -> None:
        """Offer — and by default take — the rest of the folder, when there is one.

        Shown only for a lone **file** with ``.czi`` siblings. Ticked by
        default, because a folder of per-scene files is a plate and converting
        one well of ninety-six while reporting success is the worse answer; the
        label names the count so the default is never silent. Once the user
        touches it, their answer stands — a default that re-asserted itself on
        the next keystroke is not a default, it is a setting that cannot be
        changed.
        """
        source = Path(self.source_path())
        self._source_facts_for = str(source)
        self._source_problem = _czi_source_problem(source)
        self._source_is_dir = source.is_dir()
        if self._source_is_dir:
            self._sibling_count = 0
            self._folder_cb.setVisible(False)
            return
        siblings = siblings_of(source) if source.is_file() else []
        self._sibling_count = len(siblings)
        if len(siblings) < 2:
            self._folder_cb.setVisible(False)
            if not self._folder_touched:
                self._folder_cb.setChecked(False)
            return
        self._folder_cb.setText(
            f"Convert all {len(siblings)} .czi files in this folder as one "
            f"plate (ZEN writes one file per scene)")
        self._folder_cb.setVisible(True)
        if not self._folder_touched:
            with QSignalBlocker(self._folder_cb):
                self._folder_cb.setChecked(True)

    def source_path(self) -> str:
        return self._src_field.path().strip()

    def destination_path(self) -> str:
        return self._dest_field.path().strip()

    def set_source_path(self, path: str) -> None:
        self._src_field.set_path(path or "")
        self._scan_key = None
        # What the source *is* can only change here and in the browse handler,
        # which is why the readout no longer asks.
        self._refresh_folder_box()
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
            use_folder=self.use_folder(),
        )

    def params(self) -> ConversionParams:
        return self.raw_fields().to_params()

    def set_params(self, params: ConversionParams) -> None:
        """Repopulate every field from *params* — the Job Queue's edit path."""
        index = self._well_source_cb.findData(params.well_source)
        if index >= 0:
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
        # A spec's answer is an answer, so it sticks the way a click does.
        self._folder_touched = True
        with QSignalBlocker(self._folder_cb):
            self._folder_cb.setChecked(bool(params.use_folder))
        self._refresh_folder_box()
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
        writers with two denominators on one widget — it read "2 / 4" at the
        end of a two-well run that wrote four images, which is either number
        being wrong depending on which label you assumed. Both numbers are worth
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
        for widget in (self._well_source_cb, self._grid_host, self._folder_cb,
                       self._experiment_edit, self._fovs_edit,
                       self._stride_edit, self._workers_edit):
            widget.setEnabled(not running)
        # Path fields lock rather than disable: ``setEnabled(False)`` dims the
        # path text too, which is the one piece of context someone watching a
        # long run most wants to read (see ``widgets/path_field.py``).
        self._pick_folder_btn.setEnabled(not running)
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
        """Read the plate off the UI thread, drawing what the headers say first.

        Two stages, because they cost very different amounts and only the first
        is needed to draw a plate. The **peek** reads each file's header and
        metadata — a few hundred bytes to a few kilobytes — and names the wells.
        The **full read** walks every file's subblock directory, about a
        megabyte for a 96-well plate however large the images are, and is what
        can say the plate is sound and convertible.

        Both run on a worker thread. They are file reads, and on a network
        share a 96-file plate is ninety-six round trips: doing that in
        ``refresh_readout`` froze the window for as long as it took, on a page
        whose whole job is to be looked at before a long run.
        """
        self._scan_generation += 1
        generation = self._scan_generation
        self._scanning = True
        # Release what has already been delivered. Safe here and nowhere else:
        # this runs from a GUI event, never from inside a handler.
        for key in list(self._scan_delivered):
            self._scan_bridges.pop(key, None)
            self._scan_delivered.discard(key)
        params = self.params()
        self._readout.setText("Reading the file headers…")
        # Convert stays available while the plate is read. The scan describes
        # the plate; it does not permit it — see ``_ready_before_the_scan``.
        self._set_verdict(_UNREAD_NOTE if self._ready_before_the_scan() else "",
                          "muted")
        self._update_run_gate(self._ready_before_the_scan())

        def _peek(report):
            return peek_source(src, params, report=report)

        self._scan_bridges[(generation, "peek")] = run_off_thread(
            _peek,
            on_done=lambda peek: self._on_peeked(generation, src, params, peek),
            on_failed=lambda message: self._on_scan_failed(
                generation, "peek", message),
            on_progress=lambda done, total: self._on_scan_progress(
                generation, "Reading the file headers", done, total),
        )

    def _on_scan_progress(self, generation: int, stage: str,
                          done: int, total: int) -> None:
        """Count the files as the scan reaches them.

        A split export is up to ninety-six files, and over a share the read
        takes long enough that one unchanging sentence reads as a page that
        has hung. The count arrives on the GUI thread through the bridge's
        queued signal, so the scan thread never touches a widget.

        Guarded on the generation like every other scan callback: a stale
        scan's count landing on the readout would describe a file set the page
        has already moved off.
        """
        if generation != self._scan_generation or not self._scanning:
            return
        if total > 1:
            self._readout.setText(f"{stage} ({done + 1} of {total})…")
        else:
            self._readout.setText(f"{stage}…")

    def _delivered(self, generation: int, stage: str) -> None:
        """Note that this bridge has called back, so it may be released later.

        Recorded before the staleness check, because delivery is a fact about
        the bridge and happens whether or not the page still wants the answer.
        """
        self._scan_delivered.add((generation, stage))

    def _on_peeked(self, generation: int, src: str, params, peek) -> None:
        self._delivered(generation, "peek")
        if generation != self._scan_generation:
            return
        # The plate, from the headers alone and said to be provisional. A well
        # the file names wrongly is visible here, which is the whole reason the
        # plate is drawn before the run rather than during it.
        if peek.wells and not self._running:
            self._dashboard.set_wells(peek.wells)
        self._readout.setText("Reading the rest of the plate…")
        self._set_verdict(peek.note(), "muted")
        # Still available, and now with the wells drawn to judge it by. The
        # peek's own note ends "not yet checked", which is the honest label
        # for a plate the page has described from headers alone.
        self._update_run_gate(self._ready_before_the_scan())

        def _full(report):
            return read_source(src, params, report=report)

        # Its own key: this line runs inside the peek bridge's completion
        # handler, and reusing that bridge's slot would free it underneath the
        # ``disconnect`` that follows this call's return.
        self._scan_bridges[(generation, "full")] = run_off_thread(
            _full,
            on_done=lambda czi_set: self._on_scanned(generation, czi_set),
            on_failed=lambda message: self._on_scan_failed(
                generation, "full", message),
            on_progress=lambda done, total: self._on_scan_progress(
                generation, "Reading the rest of the plate", done, total),
        )

    def _on_scanned(self, generation: int, czi_set: CziSet) -> None:
        self._delivered(generation, "full")
        if generation != self._scan_generation:
            return
        self._scanning = False
        self._czi_set = czi_set
        self._render(czi_set)

    def _on_scan_failed(self, generation: int, stage: str,
                        message: str) -> None:
        """A scan that raised. ``read_source`` reports rather than raises, so
        reaching here means something below it did — an unreadable share, a
        permission error — and answering "nothing to report" would be the
        fail-open the OME guard was fixed against."""
        self._delivered(generation, stage)
        if generation != self._scan_generation:
            return
        self._scanning = False
        self._czi_set = None
        # The key is deliberately **kept**. Clearing it here looks like the fix
        # for "a share that blinked leaves the page stuck", and it is a worse
        # bug: with no key, the next ``refresh_readout`` — which a keystroke in
        # the grid or experiment box triggers — starts another scan, which
        # fails, which clears it again. Measured against a dead share: one scan
        # per character typed. Recovery is re-picking the source, which always
        # rescans (see ``_on_source_changed``); a keystroke is not a request to
        # go back to a filer that just refused.
        self._readout.setText("")
        self._set_verdict(f"Could not read the source: {message}", "error")
        self._dashboard.set_wells([])
        self._update_run_gate(False)

    def _set_verdict(self, text: str, tone: str) -> None:
        self._verdict.setText(text)
        self._verdict.setObjectName("Error" if tone == "error" else "Muted")
        self._verdict.setVisible(bool(text))
        # Re-polish: an object name set after the widget is shown does not
        # repaint on its own, so the second verdict would wear the first's tone.
        with swallow("czi verdict restyle"):
            self._verdict.style().unpolish(self._verdict)
            self._verdict.style().polish(self._verdict)

    def refresh_readout(self) -> None:
        """Recompute the readout, the plate preview and the Run gate.

        Reads no files itself. A new source (or a widened folder) starts a
        background scan; everything else renders from the scan already in hand,
        which is what lets a keystroke in the grid fields cost nothing on a
        network share.
        """
        self._src_field.revalidate()
        self._dest_field.revalidate()
        src = self.source_path()
        if not src:
            self._scan_key = None
            self._czi_set = None
            self.stop_scanning()
            self._readout.setText(
                "Choose a CZI file, or a folder of them, to see what it holds.")
            self._set_verdict("", "muted")
            self._rebuild_channels([])
            self._dashboard.set_wells([])
            self._update_run_gate(False)
            return

        key = (src, self.use_folder())
        if key != self._scan_key:
            self._scan_key = key
            self._czi_set = None
            self._begin_scan(src)
            return
        if self._czi_set is None:
            # A scan is in flight; it will render when it lands. The gate is
            # still re-evaluated, because Convert is now available during the
            # scan and the fields it depends on are live: a grid typed into
            # while the plate is being read would otherwise keep whatever
            # verdict the gate had when the scan started.
            self._update_run_gate(self._ready_before_the_scan())
            return
        self._render(self._czi_set)

    def _render(self, czi_set: CziSet) -> None:
        """Draw everything that follows from a plate already read."""
        src = self.source_path()
        problems = self.raw_fields().problems()
        if czi_set.problem:
            self._readout.setText("")
            self._set_verdict(czi_set.problem, "error")
            self._rebuild_channels([])
            self._dashboard.set_wells([])
            self._update_run_gate(False)
            return

        self._readout.setText(czi_set.describe())
        self._rebuild_channels(list(czi_set.layout.channel_names))
        if problems:
            self._set_verdict(" ".join(problems), "error")
            self._dashboard.set_wells([])
            self._update_run_gate(False)
            return
        try:
            plan = plan_conversion(czi_set, self.params())
        except CziImportError as exc:
            self._set_verdict(str(exc), "error")
            self._dashboard.set_wells([])
            self._update_run_gate(False)
            return

        note = ""
        dest = self.destination_path()
        if dest:
            state = resume_state(dest, src, self.params())
            note = state.hint()
        # The timepoint warning goes in the same line rather than a log the
        # user reads afterwards: a plate that lost its elapsed hours has no
        # time axis in Review, and now is when the source can still be
        # re-exported.
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

    def _ready_before_the_scan(self) -> bool:
        """Whether **Convert** may be pressed before the plate has been read.

        It may. The run reads and checks the plate itself and writes nothing
        until it has: ``PackedConverterWorker.run`` plans before converting,
        and ``explode_czi`` raises for an unsound source or an unresolvable
        mapping with no archive created. So the page's scan is what
        *describes* the plate, not what permits it, and gating the button on a
        description is what made a 661-file export on a network share an hour
        of a window that could not be used for anything.

        What is checked here is what can be checked without reading a file:
        that a source is named and resolves to at least one ``.czi``, that the
        form's own fields parse, and — inside ``_update_run_gate`` — that the
        destination is usable. All three are answered from a directory listing
        and the widgets.

        The plate is still read, and what it finds still lands: an unsound
        plate takes the button back and says why. The difference is that the
        wait is now the user's to spend rather than the page's to impose.
        """
        if not self.source_path() or self._source_problem:
            return False
        return not self.raw_fields().problems()

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


#: What the verdict says while the plate is being read and Convert is already
#: available. The plate has not been checked, and a page that offered the
#: button without saying so would be offering a judgement it has not made.
_UNREAD_NOTE = ("The plate has not been read yet — Convert reads and checks "
                "it before writing anything.")


def _czi_source_problem(path) -> str:
    """``""`` if *path* is a ``.czi`` or a folder holding some, else a reason."""
    candidate = Path(str(path or "").strip())
    if not str(candidate).strip():
        return ""
    if not candidate.exists():
        return "No such file or folder."
    if candidate.is_dir():
        return ("" if siblings_of(candidate)
                else "This folder holds no .czi files.")
    if candidate.suffix.lower() not in CZI_SUFFIXES:
        return "Not a .czi file."
    return ""
