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
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import theme
from util.plate_grid import serpentine_well_labels
from widgets.path_field import PathField
from widgets.plate_order_editor import PlateOrderEditor
from datasets.ome_import.ome_import_compute import (
    ConversionParams,
    OmeImportError,
    default_dest,
    plan_conversion,
    read_layout,
)


def _hrule(parent: QWidget) -> QFrame:
    rule = QFrame(parent)
    rule.setFrameShape(QFrame.HLine)
    rule.setObjectName("HRule")
    return rule


class OmeImportPage(QWidget):
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
        # Capped like every other Datasets/Analyze plate map (PlateProgress's
        # own 330x210 convention) -- PlateOrderEditor's QSizePolicy.Expanding
        # otherwise grows to fill the scroll area's full width/height, which
        # used to push the Convert button (now above it) far below the fold.
        self._plate.setMaximumWidth(330)
        self._plate.setMaximumHeight(210)
        outer.addWidget(self._plate)

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

    def _build_status_section(self, outer: QVBoxLayout) -> None:
        hdr = QWidget(self)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(12, 6, 12, 6)
        hl.addWidget(QLabel("Conversion Output", hdr))
        hl.addStretch(1)
        self._raw_log_cb = QCheckBox("Show raw log", hdr)
        self._raw_log_cb.toggled.connect(self._on_toggle_raw_log)
        hl.addWidget(self._raw_log_cb)
        clear_btn = QPushButton("Clear", hdr)
        clear_btn.setProperty("variant", "secondary")
        clear_btn.clicked.connect(self.clear_log)
        hl.addWidget(clear_btn)
        outer.addWidget(hdr)
        outer.addWidget(_hrule(self))

        self._progress = QProgressBar(self)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        outer.addWidget(self._progress)

        self._log_sep = _hrule(self)
        outer.addWidget(self._log_sep)
        self._status = QPlainTextEdit(self)
        self._status.setReadOnly(True)
        self._status.document().setMaximumBlockCount(10000)
        self._status.setFont(QFont("monospace"))
        self._status.setPlaceholderText("Status will appear here.")
        outer.addWidget(self._status, 1)
        self._on_toggle_raw_log(False)

    def _on_toggle_raw_log(self, show: bool) -> None:
        for w in (getattr(self, "_status", None), getattr(self, "_log_sep", None)):
            if w is not None:
                w.setVisible(bool(show))

    # ── pickers ──────────────────────────────────────────────────────────────
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

    def append_log(self, text: str) -> None:
        self._status.appendPlainText(text)

    def clear_log(self) -> None:
        self._status.clear()

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
        if running:
            self._open_btn.setVisible(False)

    def show_open_in_analyze(self, dest_root: str) -> None:
        self._dest_root = dest_root
        self._open_btn.setVisible(True)

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
        if src:
            layout = self._read_layout(src)
            if layout is None:
                message = "⚠ Could not read this file as an OME-TIFF."
            else:
                single = layout.single_image
                head = (
                    f"single image: {layout.n_frames} frame(s)"
                    if single else f"{layout.n_positions} position(s)"
                ) + f" · {layout.size_c} channel(s)"
                try:
                    plan = plan_conversion(layout, self.params())
                    message = f"{head}\n→ {plan.summary()}"
                    can = bool(self._dest_field.path())
                except OmeImportError as exc:
                    message = f"{head}\n⚠ {exc}"
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
