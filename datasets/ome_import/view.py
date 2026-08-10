"""Qt builder for the Convert OME-TIFF page (the main viewer area).

A source OME-TIFF picker + a destination-folder picker, the de-interleave
controls (well grid, FOVs per well, origin, serpentine direction, and — for a
single-image packed file — the frame order), a live readout of how the file will
split into wells × FOVs × timepoints, and an Analyze-style status view (a
progress bar + a collapsible raw log). On success an "Open dataset in Analyze"
button appears.

Pure view: it owns widgets and emits intent (``runRequested``,
``openInAnalyzeRequested``); the worker wiring lives in
:mod:`~datasets.ome_import.controller`. Colours/spacing come from the app QSS and
theme tokens — nothing hardcoded (CLAUDE.md §5).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import theme
from datasets._common.log_panel import LogPanelMixin, hrule as _hrule
from util.plate_grid import serpentine_well_labels
from widgets._support import set_variant
from widgets.path_field import PathField
from widgets.plate_geometry import PLATE_ALIGNMENT, apply_plate_geometry
from widgets.plate_order_editor import PlateOrderEditor
from widgets.plate_progress import apply_outcome, fraction_or_none
from datasets.ome_import.ome_import_compute import (
    CHANNEL_ROLES,
    ConversionParams,
    OmeImportError,
    channel_tokens_from_roles,
    default_channel_roles,
    default_dest,
    plan_conversion,
    read_layout,
    selection_target,
)
from datasets._common.dataset_fields import promote_open_button

# Channel-name column in the per-channel role picker.
_ROLE_LABEL_MIN_W = 120

_ROLE_LABELS = {
    "nuclear": "Nuclear / segmentation",
    "fluor": "Fluorescent",
    "phase": "Phase / brightfield",
    "skip": "Skip",
}




class OmeImportPage(LogPanelMixin, QWidget):
    """The Convert OME-TIFF feature body."""

    runRequested = Signal()
    queueRequested = Signal()
    openInAnalyzeRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dest_root = ""
        self._layout_cache: dict[tuple[str, float], object] = {}
        self._dest_user_set = False
        self._build_ui()
        self.refresh_readout()

    # ── build ────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        # Wrap in a scroll area — see datasets/concatenate/view.py's
        # _build_ui for why (the acquisition-order plate above the status
        # section used to get crushed into whatever space the growing
        # progress/log area left it, with no way to scroll down to see
        # both). Qt's layout.addWidget() reparents regardless of any
        # constructor-passed parent, so the widgets below can keep passing
        # ``self`` without needing a bulk rewrite.
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

        heading = QLabel("Convert OME-TIFF", self)
        heading.setObjectName("Heading")
        outer.addWidget(heading)
        outer.addWidget(_hrule(self))

        blurb = QLabel(
            "De-interleave a single packed OME-TIFF (every well, channel, "
            "Z-plane and timepoint in one file) into a normal dataset folder of "
            "per-well images. Enter the imaged well grid so each serpentine stage "
            "position gets its well label, then open the result in Analyze to "
            "process it.",
            self,
        )
        blurb.setObjectName("Muted")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        self._src_field = PathField("OME-TIFF source", pick=self._pick_source)
        self._src_field.changed.connect(self._on_source_changed)
        outer.addWidget(self._src_field)
        self._dest_field = PathField("Destination folder", pick=self._pick_dest)
        self._dest_field.changed.connect(self._on_dest_changed)
        outer.addWidget(self._dest_field)

        self._build_params(outer)
        self._build_channels(outer)

        self._readout = QLabel("", self)
        self._readout.setObjectName("Muted")
        self._readout.setWordWrap(True)
        outer.addWidget(self._readout)

        run_row = QWidget(self)
        run_row_l = QHBoxLayout(run_row)
        run_row_l.setContentsMargins(0, 0, 0, 0)
        run_row_l.setSpacing(theme.Spacing.sm)
        self._run_btn = QPushButton("▶  Convert", self)
        self._run_btn.setProperty("variant", "primary")
        self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self.runRequested)
        run_row_l.addWidget(self._run_btn)
        self._queue_btn = QPushButton("+ Queue", self)
        self._queue_btn.setProperty("variant", "secondary")
        self._queue_btn.setToolTip("Add this conversion to the Job Queue instead of running it now.")
        self._queue_btn.setEnabled(False)
        self._queue_btn.clicked.connect(self.queueRequested)
        run_row_l.addWidget(self._queue_btn)
        run_row_l.addStretch(1)
        outer.addWidget(run_row, 0, Qt.AlignLeft)

        # The readout and Convert/Queue buttons sit above the acquisition plate, not
        # below it -- every other Datasets page puts its primary action above
        # a capped plate map, and an uncapped PlateOrderEditor (see _build_plate)
        # used to push both off the bottom of the fold.
        self._build_plate(outer)

        self._build_status_section(outer)

        self._open_btn = QPushButton("Open dataset in Analyze", self)
        self._open_btn.setProperty("variant", "secondary")
        self._open_btn.setVisible(False)
        self._open_btn.clicked.connect(
            lambda: self.openInAnalyzeRequested.emit(self._dest_root))
        outer.addWidget(self._open_btn, 0, Qt.AlignLeft)
        outer.addStretch(1)

    def _build_params(self, outer: QVBoxLayout) -> None:
        row = QWidget(self)
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(theme.Spacing.md)

        def _small(default: str, width: int = 48, placeholder: str = "") -> QLineEdit:
            e = QLineEdit(default, row)
            e.setFixedWidth(width)
            if placeholder:
                e.setPlaceholderText(placeholder)
            e.textChanged.connect(lambda _=None: self.refresh_readout())
            return e

        hl.addWidget(QLabel("Grid (rows × cols):", row))
        self._rows_edit = _small("1")
        hl.addWidget(self._rows_edit)
        hl.addWidget(QLabel("×", row))
        self._cols_edit = _small("1")
        hl.addWidget(self._cols_edit)
        hl.addWidget(QLabel("FOVs/well:", row))
        self._fovs_edit = _small("", placeholder="auto")
        hl.addWidget(self._fovs_edit)
        hl.addWidget(QLabel("Origin:", row))
        self._origin_edit = _small("A01", width=56, placeholder="A01")
        hl.addWidget(self._origin_edit)
        hl.addWidget(QLabel("Workers:", row))
        self._workers_edit = _small("", placeholder="auto")
        self._workers_edit.setToolTip(
            "Parallel conversion threads (blank = auto). Wells are converted in "
            "parallel; lower this if a single spinning disk thrashes."
        )
        hl.addWidget(self._workers_edit)
        hl.addStretch(1)
        outer.addWidget(row)

        row2 = QWidget(self)
        hl2 = QHBoxLayout(row2)
        hl2.setContentsMargins(0, 0, 0, 0)
        hl2.setSpacing(theme.Spacing.md)
        hl2.addWidget(QLabel("Serpentine:", row2))
        self._direction_cb = QComboBox(row2)
        self._direction_cb.addItem("Snake by row", userData="row")
        self._direction_cb.addItem("Snake by column", userData="col")
        self._direction_cb.currentIndexChanged.connect(lambda _=None: self.refresh_readout())
        hl2.addWidget(self._direction_cb)
        self._order_label = QLabel("Frame order:", row2)
        hl2.addWidget(self._order_label)
        self._frame_order_cb = QComboBox(row2)
        self._frame_order_cb.addItem("Single timepoint", userData="single")
        self._frame_order_cb.addItem("Positions within timepoint", userData="pos_in_time")
        self._frame_order_cb.addItem("Timepoints within position", userData="time_in_pos")
        self._frame_order_cb.setToolTip(
            "How a single packed image's frame sequence is ordered: every frame a "
            "distinct position, or positions looped inside each timepoint, or "
            "timepoints inside each position."
        )
        self._frame_order_cb.currentIndexChanged.connect(lambda _=None: self.refresh_readout())
        hl2.addWidget(self._frame_order_cb)
        hl2.addStretch(1)
        outer.addWidget(row2)

    def _build_plate(self, outer: QVBoxLayout) -> None:
        caption = QLabel(
            "Acquisition path — the numbered wells and arrow show the order stage "
            "positions map onto the plate. Enter a grid above to seed a serpentine "
            "path, or click wells here, in visit order, to define an arbitrary "
            "(even non-rectangular) ordering.",
            self,
        )
        caption.setObjectName("Muted")
        caption.setWordWrap(True)
        outer.addWidget(caption)

        self._plate = PlateOrderEditor(self)
        self._plate.orderChanged.connect(lambda _=None: self.refresh_readout())
        # The one sizing and placement rule every plate map in the app goes
        # through. The "editor" role hides the built-in action row (this page
        # carries its own Generate/Clear buttons below the plate), keeps the
        # plate interactive, and takes the shared size caps rather than this
        # page restating them as literals — which is how it ended up the one
        # plate whose numbers had to be kept in step with plate_geometry.py by
        # hand.
        apply_plate_geometry(self._plate, role="editor")
        outer.addWidget(self._plate, 0, PLATE_ALIGNMENT)

        btn_row = QWidget(self)
        bl = QHBoxLayout(btn_row)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(theme.Spacing.sm)
        self._seed_btn = QPushButton("Generate path from grid", btn_row)
        self._seed_btn.setProperty("variant", "secondary")
        self._seed_btn.setToolTip(
            "Fill the plate with the serpentine path of the grid above, as an "
            "editable starting point."
        )
        self._seed_btn.clicked.connect(self._generate_path)
        bl.addWidget(self._seed_btn)
        self._clear_path_btn = QPushButton("Clear path", btn_row)
        self._clear_path_btn.setProperty("variant", "secondary")
        self._clear_path_btn.setToolTip(
            "Clear the clicked ordering and return to the live grid preview."
        )
        self._clear_path_btn.clicked.connect(self._plate.clear)
        bl.addWidget(self._clear_path_btn)
        bl.addStretch(1)
        outer.addWidget(btn_row)

    def _generate_path(self) -> None:
        labels = self._serpentine_seed()
        if labels:
            self._plate.set_order(labels)
            self.refresh_readout()

    def _serpentine_seed(self) -> list[str]:
        """The grid's serpentine well labels, or ``[]`` if the grid is invalid."""
        rows = _as_int(self._rows_edit.text())
        cols = _as_int(self._cols_edit.text())
        if rows <= 0 or cols <= 0:
            return []
        try:
            return serpentine_well_labels(
                rows, cols,
                origin=self._origin_edit.text().strip() or "A01",
                direction=str(self._direction_cb.currentData() or "row"),
            )
        except ValueError:
            return []

    def _build_channels(self, outer: QVBoxLayout) -> None:
        self._channel_rows: list[tuple[str, QComboBox]] = []
        self._channel_names_shown: list[str] = []
        self._channels_caption = QLabel(
            "Channel roles — assign each channel a role (seeded from its name). "
            "These pre-fill Analyze's channel tokens when you open the dataset "
            "there; the OME channel names are always written into the filenames.",
            self,
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
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._channel_rows = []
        defaults = default_channel_roles(names)
        for name in names:
            row = QWidget(self._channels_host)
            hl = QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(theme.Spacing.sm)
            lbl = QLabel(name, row)
            lbl.setMinimumWidth(_ROLE_LABEL_MIN_W)
            hl.addWidget(lbl)
            cb = QComboBox(row)
            for role in CHANNEL_ROLES:
                cb.addItem(_ROLE_LABELS[role], userData=role)
            cb.setCurrentIndex(CHANNEL_ROLES.index(defaults.get(name, "fluor")))
            hl.addWidget(cb)
            hl.addStretch(1)
            self._channels_lay.addWidget(row)
            self._channel_rows.append((name, cb))
        self._channels_caption.setVisible(bool(names))

    def channel_tokens_dict(self) -> dict:
        """The channel role assignment as a ``pipeline_info``-shaped dict."""
        pairs = [(name, str(cb.currentData() or "fluor"))
                 for name, cb in self._channel_rows]
        return channel_tokens_from_roles(pairs).as_pipeline_info()

    def _build_status_section(self, outer: QVBoxLayout) -> None:
        self.build_log_header(outer, "Convert Output")

        self._progress = QProgressBar(self)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        # Hidden until a conversion actually starts. An idle bar reading 0%
        # from the moment the page loads describes a run that has not been
        # asked for — the same "reads as a stuck state" problem every other
        # Datasets page's status strip was fixed against.
        self._progress.setVisible(False)
        outer.addWidget(self._progress)

        # How a finished run says how it went. Every other Datasets tool has a
        # dashboard whose headline carries this; this page reports on the
        # acquisition plate it already draws, so it had a progress bar and
        # nothing else — a failed conversion changed nothing on screen and left
        # its only account in the raw log, which is hidden by default. That is a
        # run that failed looking exactly like a run that did not.
        self._outcome_label = QLabel("", self)
        self._outcome_label.setWordWrap(True)
        self._outcome_label.setVisible(False)
        outer.addWidget(self._outcome_label)

        self.build_log_tail(outer, placeholder="Status will appear here.")

    def _pick_source(self, current: str, parent: QWidget | None = None) -> str:
        f, _ = QFileDialog.getOpenFileName(
            parent or self, "Select OME-TIFF file", current or "",
            "OME-TIFF (*.ome.tif *.ome.tiff);;All files (*)",
        )
        return f

    def _pick_dest(self, current: str, parent: QWidget | None = None) -> str:
        return QFileDialog.getExistingDirectory(
            parent or self, "Select destination folder", current or "",
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )

    def _on_source_changed(self) -> None:
        set_variant(self._run_btn, "primary")
        # Auto-fill the destination the first time (unless the user set one).
        if self._src_field.path() and not self._dest_user_set:
            self._dest_field.set_path(str(default_dest(self._src_field.path())))
        self.refresh_readout()

    def _on_dest_changed(self) -> None:
        self._dest_user_set = True
        self.refresh_readout()

    # ── public API used by the controller ────────────────────────────────────
    def source_path(self) -> str:
        return self._src_field.path()

    def destination_path(self) -> str:
        return self._dest_field.path()

    def set_source_path(self, path: str) -> None:
        """Programmatic set (job-queue ``show_job``) — unlike ``_browse()``,
        does not flip ``_dest_user_set``, so a later change still auto-fills
        the destination exactly as an interactive pick would."""
        self._src_field.set_path(path)
        self.refresh_readout()

    def set_destination_path(self, path: str) -> None:
        self._dest_user_set = True
        self._dest_field.set_path(path)
        self.refresh_readout()

    def params(self) -> ConversionParams:
        return ConversionParams(
            grid_rows=_as_int(self._rows_edit.text()),
            grid_cols=_as_int(self._cols_edit.text()),
            origin=self._origin_edit.text().strip() or "A01",
            direction=str(self._direction_cb.currentData() or "row"),
            fovs_per_well=_as_int(self._fovs_edit.text()) or None,
            frame_order=str(self._frame_order_cb.currentData() or "single"),
            well_order=tuple(self._plate.order()) if hasattr(self, "_plate") else (),
            workers=max(0, _as_int(self._workers_edit.text())),
        )

    def set_params(self, params: ConversionParams) -> None:
        """Restores every field ``params()`` reads from — the job-queue
        counterpart to ``params()``, used by ``show_job`` to repopulate a
        queued conversion's settings."""
        self._rows_edit.setText(str(params.grid_rows))
        self._cols_edit.setText(str(params.grid_cols))
        self._origin_edit.setText(params.origin or "A01")
        idx = self._direction_cb.findData(params.direction)
        if idx >= 0:
            self._direction_cb.setCurrentIndex(idx)
        self._fovs_edit.setText(str(params.fovs_per_well) if params.fovs_per_well else "")
        idx = self._frame_order_cb.findData(params.frame_order)
        if idx >= 0:
            self._frame_order_cb.setCurrentIndex(idx)
        self._workers_edit.setText(str(params.workers) if params.workers else "")
        if params.well_order:
            self._plate.set_order(list(params.well_order))
        else:
            self._plate.clear()
        self.refresh_readout()

    def set_well_fraction(self, well_id: str, done: int, total: int) -> None:
        """Fill *well_id*'s dial on the acquisition plate.

        The plate that defines the visit order doubles as the progress view
        while a conversion runs, the same way Subset's well picker does — the
        wells on it are exactly the wells being written, so a second plate map
        would be a copy of this one.
        """
        self._plate.setWellFraction(well_id, fraction_or_none(done, total))

    def clear_well_fractions(self) -> None:
        self._plate.clearWellFractions()

    def set_outcome(self, outcome: str, message: str = "") -> None:
        """Show how the run ended: ``done`` / ``failed`` / ``cancelled``.

        Shares its wording and tone with every Datasets dashboard's headline
        (``widgets.plate_progress.apply_outcome``) so the same conversion
        reported here and in the Job Queue reads the same way.
        """
        apply_outcome(self._outcome_label, outcome, message)
        self._outcome_label.setVisible(True)
        if outcome != "done":
            # A run that never counted its planes left the bar indeterminate, so
            # it goes on sweeping after the run is over — the one thing on the
            # page still claiming work is happening. Land it empty and still.
            self._progress.setRange(0, 1)
            self._progress.setValue(0)

    def clear_outcome(self) -> None:
        """Drop the previous run's verdict — a new run has not ended yet."""
        self._outcome_label.setText("")
        self._outcome_label.setToolTip("")
        self._outcome_label.setStyleSheet("")
        self._outcome_label.setVisible(False)

    def set_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self._progress.setRange(0, 0)  # indeterminate
            return
        self._progress.setRange(0, total)
        self._progress.setValue(done)

    def set_running(self, running: bool) -> None:
        self._run_btn.setText("Converting…" if running else "▶  Convert")
        self._run_btn.setEnabled(not running and self._can_convert())
        self._queue_btn.setEnabled(not running and self._can_convert())
        # The bar appears with the run and stays afterwards to show the result;
        # what it must not do is sit at 0% before anything has been asked for.
        if running:
            self._progress.setVisible(True)
            self._open_btn.setVisible(False)
            set_variant(self._run_btn, "primary")
        # Locked for the run's duration — the plate is the progress display
        # now, and a click would edit the very order being converted.
        self._plate.setEnabled(not running)

    def show_open_in_analyze(self, dest_root: str) -> None:
        self._dest_root = dest_root
        promote_open_button(self._open_btn, self._run_btn)

    def refresh_readout(self) -> None:
        """Recompute the split readout and the frame-order visibility / Run gate."""
        # Keep the plate a live serpentine preview of the grid until the user
        # hand-draws an order (dirty); ``set_preview`` is a no-op once dirty, so
        # a clicked ordering is never clobbered by a grid edit.
        if hasattr(self, "_plate") and not self._plate.is_dirty():
            self._plate.set_preview(self._serpentine_seed())
        src = self._src_field.path() if hasattr(self, "_src_field") else ""
        single = False
        message = ""
        can = False
        layout = self._read_layout(src) if src else None
        if hasattr(self, "_channels_host"):
            self._rebuild_channels(layout.channel_names if layout else [])
        if src:
            if layout is None:
                message = "⚠ Could not read this file as an OME-TIFF."
            else:
                single = layout.single_image
                params = self.params()
                head = (
                    f"single image: {layout.n_frames} frame(s)"
                    if single else f"{layout.n_positions} position(s)"
                ) + f" · {layout.size_c} channel(s)"
                # Metadata-derived target: how many wells to place (read on load,
                # before any grid is entered) so the user is guided, not just
                # error-checked at the end.
                guide = selection_target(layout, params).hint()
                try:
                    plan = plan_conversion(layout, params)
                    message = f"{head}\n{guide}\n✓ {plan.summary()}"
                    can = bool(self._dest_field.path())
                except OmeImportError as exc:
                    message = f"{head}\n{guide}\n⚠ {exc}"
        self._order_label.setVisible(single)
        self._frame_order_cb.setVisible(single)
        self._readout.setText(message)
        self._run_btn.setEnabled(can)
        self._queue_btn.setEnabled(can)

    # ── internals ────────────────────────────────────────────────────────────
    def _can_convert(self) -> bool:
        src = self._src_field.path()
        if not src or not self._dest_field.path():
            return False
        layout = self._read_layout(src)
        if layout is None:
            return False
        try:
            plan_conversion(layout, self.params())
        except OmeImportError:
            return False
        return True

    def _read_layout(self, src: str):
        try:
            key = (src, Path(src).stat().st_mtime)
        except OSError:
            return None
        if key in self._layout_cache:
            return self._layout_cache[key]
        try:
            layout = read_layout(src)
        except OmeImportError:
            layout = None
        self._layout_cache[key] = layout
        return layout


def _as_int(text: str) -> int:
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return 0
