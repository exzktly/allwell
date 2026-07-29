"""Qt builder for the Convert OME-TIFF page (the main viewer area).

A source OME-TIFF picker + a destination-folder picker, the de-interleave
controls (well grid, FOVs per well, origin, serpentine direction, and — for a
single-image packed file — the frame order), a live readout of how the file will
split into wells × FOVs × timepoints, and an Analyze-style status view (a
progress bar + a collapsible raw log). On success an "Open in Analyze" button
appears.

Pure view: it owns widgets and emits intent (``runRequested``,
``openInAnalyzeRequested``); the worker wiring lives in
:mod:`~datasets.ome_import.controller`. Colours/spacing come from the app QSS and
theme tokens — nothing hardcoded (CLAUDE.md §5).
"""

from __future__ import annotations

from collections.abc import Callable
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
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import theme
from widgets.icons import make_icon
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


class _PathField(QWidget):
    """Caption + a compact read-only path box + an icon Browse button.

    *pick* returns the chosen path string (a file or a folder dialog); the field
    stores it, shows the basename with the full path on hover, and emits
    ``changed``.
    """

    changed = Signal()

    def __init__(
        self, title: str, *, pick: Callable[[str], str], parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._pick = pick
        self._path = ""

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(theme.Spacing.xs)
        label = QLabel(title, self)
        label.setObjectName("Muted")
        col.addWidget(label)

        row = QWidget(self)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(theme.Spacing.xs)
        self._edit = QLineEdit(row)
        self._edit.setReadOnly(True)
        self._edit.setPlaceholderText("(not set)")
        self._edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        rl.addWidget(self._edit, 1)
        browse = QToolButton(row)
        browse.setIcon(make_icon("folder-open"))
        browse.setAutoRaise(True)
        browse.setToolTip("Browse…")
        browse.clicked.connect(self._browse)
        rl.addWidget(browse)
        col.addWidget(row)

    def _browse(self) -> None:
        chosen = self._pick(self._path)
        if chosen:
            self.set_path(chosen)
            self.changed.emit()

    def set_path(self, path: str) -> None:
        self._path = path
        self._edit.setText(Path(path).name or path)
        self._edit.setToolTip(path)

    def path(self) -> str:
        return self._path


class OmeImportPage(QWidget):
    """The Convert OME-TIFF feature body."""

    runRequested = Signal()
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
        outer = QVBoxLayout(self)
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

        self._src_field = _PathField("OME-TIFF source", pick=self._pick_source)
        self._src_field.changed.connect(self._on_source_changed)
        outer.addWidget(self._src_field)
        self._dest_field = _PathField("Destination folder", pick=self._pick_dest)
        self._dest_field.changed.connect(self._on_dest_changed)
        outer.addWidget(self._dest_field)

        self._build_params(outer)

        self._readout = QLabel("", self)
        self._readout.setObjectName("Muted")
        self._readout.setWordWrap(True)
        outer.addWidget(self._readout)

        self._run_btn = QPushButton("▶  Convert", self)
        self._run_btn.setProperty("variant", "primary")
        self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self.runRequested)
        outer.addWidget(self._run_btn, 0, Qt.AlignLeft)

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
    def _pick_source(self, current: str) -> str:
        f, _ = QFileDialog.getOpenFileName(
            self, "Select OME-TIFF file", current or "",
            "OME-TIFF (*.ome.tif *.ome.tiff);;All files (*)",
        )
        return f

    def _pick_dest(self, current: str) -> str:
        return QFileDialog.getExistingDirectory(
            self, "Select destination folder", current or "",
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

    def params(self) -> ConversionParams:
        return ConversionParams(
            grid_rows=_as_int(self._rows_edit.text()),
            grid_cols=_as_int(self._cols_edit.text()),
            origin=self._origin_edit.text().strip() or "A01",
            direction=str(self._direction_cb.currentData() or "row"),
            fovs_per_well=_as_int(self._fovs_edit.text()) or None,
            frame_order=str(self._frame_order_cb.currentData() or "single"),
        )

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
        self._run_btn.setText("Converting…" if running else "Convert")
        self._run_btn.setEnabled(not running and self._can_convert())
        if running:
            self._open_btn.setVisible(False)

    def show_open_in_analyze(self, dest_root: str) -> None:
        self._dest_root = dest_root
        self._open_btn.setVisible(True)

    def refresh_readout(self) -> None:
        """Recompute the split readout and the frame-order visibility / Run gate."""
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
