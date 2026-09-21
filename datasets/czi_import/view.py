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
from datasets.core.dataset_fields import promote_open_button
from datasets.core.log_panel import LogPanelMixin, hrule as _hrule
from datasets.czi_import.dashboard import CziImportDashboard
from datasets.czi_import.czi_import_compute import (
    CHANNEL_ROLES,
    ConversionParams,
    CziImportError,
    RawFields,
    channel_tokens_from_roles,
    default_channel_roles,
    default_dest,
    destination_problem,
    plan_conversion,
    read_source,
    resume_state,
    source_files,
)
from pipeline.czi_explode import WELL_SOURCES
from pipeline.czi_set import CZI_SUFFIXES, CziSet, siblings_of
from ui import theme
from widgets._support import set_variant
from widgets.path_field import PathField, folder_picker

#: Channel-name column in the per-channel role picker.
_ROLE_LABEL_MIN_W = 120

#: Widths of the small numeric boxes. Named rather than written at the call
#: site, both because ARCHITECTURE §8 asks for it and because a literal passed
#: through a helper's parameter is invisible to the pixel guard that enforces it.
_FIELD_W = 48
_ORIGIN_W = 56

_ROLE_LABELS = {
    "nuclear": "Nuclear / segmentation",
    "fluor": "Fluorescent",
    "phase": "Phase / brightfield",
    "skip": "Skip",
}

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


class CziImportPage(LogPanelMixin, QWidget):
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
        #: The last set read, keyed on the exact files and their mtimes, so
        #: typing in a grid field does not re-read a multi-gigabyte plate on
        #: every keystroke. Keyed on mtimes as well as paths because a
        #: re-export saved over the same name is a different file, and on the
        #: whole list because a sibling appearing changes the plate.
        self._source_cache: dict[tuple, CziSet] = {}
        #: Whether the user has touched the "convert the whole folder" box.
        #: Until they do it follows the folder; after, their answer stands.
        self._folder_touched = False
        self._channel_rows: list[tuple[str, QComboBox]] = []
        self._channel_names_shown: list[str] = []
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
                                    validator=_czi_source_problem)
        self._src_field.changed.connect(self._on_source_changed)
        outer.addWidget(self._src_field)

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

    def _rebuild_channels(self, names) -> None:
        """Rebuild the per-channel role rows when the channel set changes."""
        names = list(names)
        if names == self._channel_names_shown:
            return
        self._channel_names_shown = names
        while self._channels_lay.count():
            item = self._channels_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._channel_rows = []
        defaults = default_channel_roles(names)
        for name in names:
            row = QWidget(self._channels_host)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(theme.Spacing.sm)
            label = QLabel(name, row)
            label.setMinimumWidth(_ROLE_LABEL_MIN_W)
            row_layout.addWidget(label)
            combo = QComboBox(row)
            for role in CHANNEL_ROLES:
                combo.addItem(_ROLE_LABELS[role], userData=role)
            combo.setCurrentIndex(
                CHANNEL_ROLES.index(defaults.get(name, "fluor")))
            row_layout.addWidget(combo)
            row_layout.addStretch(1)
            self._channels_lay.addWidget(row)
            self._channel_rows.append((name, combo))
        self._channels_caption.setVisible(bool(names))

    def channel_tokens_dict(self) -> dict:
        """The channel role assignment as a ``pipeline_info``-shaped dict."""
        pairs = [(name, str(combo.currentData() or "fluor"))
                 for name, combo in self._channel_rows]
        return channel_tokens_from_roles(pairs).as_pipeline_info()

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
        outer.addWidget(self._dashboard)
        self.build_log_tail(outer, placeholder="Status will appear here.")

    # ── pickers ──────────────────────────────────────────────────────────────
    def _pick_source(self, current: str, parent: QWidget | None = None) -> str:
        """Pick a ``.czi``, or the folder a split-scene export was written to.

        One dialog rather than two buttons, in ``Directory`` mode with files
        shown: a folder is chosen by opening it and accepting, a file by
        selecting it. ZEN writes a plate either way and the user should not
        have to know which kind of thing they are looking for before they open
        the dialog.

        Qt's own file dialog is forced here (``DontUseNativeDialog``). The
        native macOS panel in Directory mode hides files outright, so a folder
        of ninety-six ``.czi`` files looked empty and there was no way to pick
        one of them.
        """
        dialog = QFileDialog(parent or self, "Select a CZI file, or a folder of them",
                             current or "")
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        dialog.setOption(QFileDialog.ShowDirsOnly, False)
        if dialog.exec() != QFileDialog.Accepted:
            return ""
        chosen = dialog.selectedFiles()
        return chosen[0] if chosen else ""

    # ── field plumbing ───────────────────────────────────────────────────────
    def _on_source_changed(self) -> None:
        src = self._src_field.path()
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
        """
        source = Path(self.source_path())
        if source.is_dir():
            return True
        return self._folder_cb.isChecked()

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
        if source.is_dir():
            self._folder_cb.setVisible(False)
            return
        siblings = siblings_of(source) if source.is_file() else []
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
            direction=str(self._direction_cb.currentData() or "row"),
            serpentine=self._serpentine_cb.isChecked(),
            experiment=self._experiment_edit.text(),
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
        direction = self._direction_cb.findData(params.direction)
        if direction >= 0:
            self._direction_cb.setCurrentIndex(direction)
        self._serpentine_cb.setChecked(bool(params.serpentine))
        self._experiment_edit.setText(params.experiment or "")
        # A spec's answer is an answer, so it sticks the way a click does.
        self._folder_touched = True
        with QSignalBlocker(self._folder_cb):
            self._folder_cb.setChecked(bool(params.use_folder))
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
                       self._workers_edit):
            widget.setEnabled(not running)
        # Path fields lock rather than disable: ``setEnabled(False)`` dims the
        # path text too, which is the one piece of context someone watching a
        # long run most wants to read (see ``widgets/path_field.py``).
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

    def _read_source(self, src: str) -> CziSet:
        """The selected files as one plate, cached on their paths and mtimes."""
        params = self.params()
        try:
            key = tuple((str(p), p.stat().st_mtime)
                        for p in source_files(src, params))
        except OSError:
            key = ()
        if key and key in self._source_cache:
            return self._source_cache[key]
        czi_set = read_source(src, params)
        if key and not czi_set.problem:
            # Only a readable set is cached. A problem is usually something the
            # user is in the middle of fixing, and a cached one keeps being
            # reported after the fix.
            self._source_cache.clear()
            self._source_cache[key] = czi_set
        return czi_set

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
        """Recompute the readout, the plate preview and the Run gate."""
        self._src_field.revalidate()
        self._dest_field.revalidate()
        self._refresh_folder_box()
        src = self.source_path()
        if not src:
            self._readout.setText(
                "Choose a CZI file, or a folder of them, to see what it holds.")
            self._set_verdict("", "muted")
            self._rebuild_channels([])
            self._dashboard.set_wells([])
            self._update_run_gate(False)
            return

        problems = self.raw_fields().problems()
        czi_set = self._read_source(src)
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
        self._set_verdict(f"{plan.summary()}{f'  {note}' if note else ''}",
                          "muted")
        # The plate shows the mapping *before* the run, which is the whole
        # point of drawing it: a well the file named wrongly is visible here
        # and nowhere else.
        if not self._running:
            self._dashboard.set_wells(plan.well_labels)
        self._update_run_gate(True)

    def _update_run_gate(self, ready: bool) -> None:
        self._ready = bool(ready)
        enabled = ready and not self._running
        self._run_btn.setEnabled(enabled)
        self._queue_btn.setEnabled(enabled)

    def _can_convert(self) -> bool:
        return bool(getattr(self, "_ready", False))


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
