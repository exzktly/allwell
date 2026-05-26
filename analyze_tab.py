"""
analyze_tab.py — AnalyzeTab widget (PySide6 port).

Input-folder resolution rules (applied when Run is pressed):
  1. Folder named "in"           → input = folder,     output = parent/"out"
  2. Folder contains sub-dir "in"→ input = folder/"in", output = folder/"out"
  3. Folder has >10 TIF files    → move TIFs to folder/"in"
  4. Otherwise                   → error

process_microscopy.py must live alongside this file.
"""

from __future__ import annotations

import queue
import re as _re_well
import sys as _sys
import time as _time
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from services.input_resolution_service import resolve_input_output, tif_files_in
from services.pipeline_runner import (
    PipelineRunner,
    ProgressTracker,
    classify_log_line,
)
from services.pipeline_service import find_pipeline_script
from services.remote_config import load_remotes
from ui.theme import (
    CLR_DANGER, CLR_SUCCESS, TXT_MUT, TXT_PRI, WARN,
)

# ---------------------------------------------------------------------------
# Schema vocabulary — kept in sync with process_microscopy.py
# ---------------------------------------------------------------------------
SCHEMA_FIELDS  = ("experiment", "channel", "well", "fov", "timepoint", "ignore")
DEFAULT_SCHEMA = "experiment:channel:well:fov:timepoint"
DEFAULT_SEP    = "_"

_SCHEMA_LABELS = ("Experiment", "Channel", "Well", "FOV", "Timepoint", "— ignore —")
_LABEL_TO_FIELD = dict(zip(_SCHEMA_LABELS, SCHEMA_FIELDS))
_FIELD_TO_LABEL = dict(zip(SCHEMA_FIELDS, _SCHEMA_LABELS))


def _validate_schema_mappings() -> None:
    if len(_SCHEMA_LABELS) != len(SCHEMA_FIELDS):
        raise RuntimeError("Schema label/field length mismatch.")
    for label in _SCHEMA_LABELS:
        field = _LABEL_TO_FIELD.get(label)
        if field is None:
            raise RuntimeError(f"Missing mapping for schema label: {label!r}")
        if _FIELD_TO_LABEL.get(field) != label:
            raise RuntimeError(f"Schema mapping not invertible for label: {label!r}")


_validate_schema_mappings()

_PREVIEW_TOKENS = {
    "experiment": "Exp01",
    "well":       "B03",
    "fov":        "F001",
    "timepoint":  "02d04h30m",
    "ignore":     "X",
}

_WELL_NAME_RE = _re_well.compile(r"^[A-Ha-h]\d{1,2}$")
_tif_files_in = tif_files_in


def _has_well_content(folder: Path) -> bool:
    if any(folder.glob("*.zip")):
        return True
    try:
        return any(_WELL_NAME_RE.match(p.name) for p in folder.iterdir() if p.is_dir())
    except OSError:
        return False


def _count_well_content(folder: Path) -> int:
    zips = list(folder.glob("*.zip"))
    folders = [p for p in folder.iterdir() if p.is_dir() and _WELL_NAME_RE.match(p.name)]
    return len(zips) or len(folders)


# ---------------------------------------------------------------------------
# AnalyzeTab
# ---------------------------------------------------------------------------
class AnalyzeTab(QWidget):
    """Left: pipeline options form.  Right: live subprocess log."""

    _LOG_COLORS = {
        "INFO":    TXT_PRI,
        "WARNING": WARN,
        "ERROR":   CLR_DANGER,
        "DONE":    CLR_SUCCESS,
        "CMD":     TXT_MUT,
    }

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        on_pipeline_complete: Callable[[Path], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._runner = PipelineRunner()
        self._log_q: queue.Queue[tuple[str, object]] = self._runner.log_q
        self._progress_tracker = ProgressTracker()
        self._running = False
        self._well_total = 0
        self._well_done  = 0
        self._zipper_done = 0
        self._on_pipeline_complete = on_pipeline_complete
        self._fluor_rows: list[tuple[QLineEdit, QCheckBox, QPushButton]] = []
        self._schema_cbs: list[QComboBox] = []

        self._build_ui()
        self._poll_log()

    @property
    def _last_output_dir(self) -> Path | None:
        return self._runner.last_output_dir

    # ------------------------------------------------------------------
    # Top-level layout
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        rule = QFrame(self)
        rule.setFrameShape(QFrame.HLine)
        rule.setObjectName("HRule")
        outer.addWidget(rule)

        splitter = QSplitter(Qt.Horizontal, self)
        splitter.setHandleWidth(5)
        outer.addWidget(splitter, 1)

        # Left — scrollable form
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll
        # Analyze form sidebar expanded 25% (280→350) per redesign-bug-batch-2:
        # the schema string + per-channel rows often clip at 280 px.
        scroll.setMinimumWidth(350)
        form_host = QWidget()
        form_host
        self._form_layout = QVBoxLayout(form_host)
        self._form_layout.setContentsMargins(0, 8, 0, 8)
        self._form_layout.setSpacing(0)
        scroll.setWidget(form_host)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 0)

        self._build_form(form_host)

        # Right — log panel
        right = QWidget()
        right.setMinimumWidth(300)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        self._build_log(right)

        splitter.setSizes([360, 600])

    # ------------------------------------------------------------------
    # Layout helpers
    # ------------------------------------------------------------------
    def _section(self, parent: QWidget, title: str) -> QWidget:
        """Add a labelled section to *parent* and return its body widget."""
        container = QWidget(parent)
        container
        cl = QVBoxLayout(container)
        cl.setContentsMargins(0, 4, 0, 4)
        cl.setSpacing(2)

        title_lbl = QLabel(title, container)
        title_lbl.setObjectName("SectionTitle")
        title_lbl.setContentsMargins(12, 6, 12, 2)
        cl.addWidget(title_lbl)

        rule = QFrame(container)
        rule.setFrameShape(QFrame.HLine)
        rule.setObjectName("HRule")
        cl.addWidget(rule)

        body = QWidget(container)
        body
        bl = QVBoxLayout(body)
        bl.setContentsMargins(12, 4, 12, 8)
        bl.setSpacing(3)
        cl.addWidget(body)

        parent.layout().addWidget(container)
        return body

    def _row(self, parent: QWidget, label: str) -> QWidget:
        """Append a two-column label+widget row to *parent*; return the right cell."""
        row = QWidget(parent)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 1, 0, 1)
        rl.setSpacing(4)

        lbl = QLabel(label, row)
        lbl.setObjectName("Muted")
        lbl.setFixedWidth(150)
        rl.addWidget(lbl)

        right = QWidget(row)
        right.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        rr = QHBoxLayout(right)
        rr.setContentsMargins(0, 0, 0, 0)
        rr.setSpacing(4)
        rl.addWidget(right)

        parent.layout().addWidget(row)
        return right

    def _entry(self, parent: QWidget, default: str = "", width: int = 80) -> QLineEdit:
        """Create a QLineEdit, add it to *parent*'s layout, and return it."""
        e = QLineEdit(default, parent)
        e.setFixedWidth(width)
        parent.layout().addWidget(e)
        return e

    # ------------------------------------------------------------------
    # Form orchestration
    # ------------------------------------------------------------------
    def _build_form(self, parent: QWidget) -> None:
        self._build_schema_section(parent)
        self._build_channel_tokens_section(parent)
        self._build_folders_section(parent)
        self._refresh_schema_preview()
        self._build_tophat_section(parent)
        self._build_output_options_section(parent)
        self._build_compute_options_section(parent)
        self._build_run_controls(parent)
        parent.layout().addStretch(1)

    def _build_schema_section(self, parent: QWidget) -> None:
        sec = self._section(parent, "Filename Schema")

        sep_row = self._row(sec, "Separator char")
        self._filename_sep_edit = self._entry(sep_row, DEFAULT_SEP, width=30)
        sep_row.layout().addStretch(1)
        self._filename_sep_edit.textChanged.connect(lambda _: self._refresh_schema_preview())

        default_fields = DEFAULT_SCHEMA.split(":")
        for pos_idx in range(5):
            row = self._row(sec, f"Position {pos_idx + 1}")
            cb = QComboBox(row)
            cb.addItems(list(_SCHEMA_LABELS))
            cb.setCurrentText(_FIELD_TO_LABEL.get(default_fields[pos_idx], "— ignore —"))
            cb.setFixedWidth(160)
            row.layout().addWidget(cb)
            row.layout().addStretch(1)
            self._schema_cbs.append(cb)
            cb.currentIndexChanged.connect(lambda _i, c=cb: self._on_combobox_selected(c))

        schema_str_row = self._row(sec, "Schema string")
        self._schema_str_edit = QLineEdit(DEFAULT_SCHEMA, schema_str_row)
        self._schema_str_edit.setFixedWidth(170)
        schema_str_row.layout().addWidget(self._schema_str_edit)
        schema_str_row.layout().addStretch(1)
        self._schema_str_edit.editingFinished.connect(self._sync_dropdowns_from_string)

        self._schema_err_lbl = QLabel("", sec)
        self._schema_err_lbl.setObjectName("Error")
        self._schema_err_lbl.setWordWrap(True)
        sec.layout().addWidget(self._schema_err_lbl)

        self._schema_preview_lbl = QLabel("", sec)
        self._schema_preview_lbl.setObjectName("Muted")
        self._schema_preview_lbl.setWordWrap(True)
        sec.layout().addWidget(self._schema_preview_lbl)

    def _build_channel_tokens_section(self, parent: QWidget) -> None:
        sec = self._section(parent, "Channel Tokens")

        seg_row = self._row(sec, "Segmentation")
        self._seg_method_cb = QComboBox(seg_row)
        self._seg_method_cb.addItems(["stardist_nuclei", "stardist_seeded_watershed_cell"])
        self._seg_method_cb.setFixedWidth(170)
        seg_row.layout().addWidget(self._seg_method_cb)
        seg_row.layout().addStretch(1)
        self._seg_method_cb.currentIndexChanged.connect(lambda _: self._refresh_segmentation_hints())

        nuc_row = self._row(sec, "Nuclear (seg)")
        self._nuclear_token_edit = self._entry(nuc_row, "NIR")
        nuc_row.layout().addStretch(1)
        self._nuclear_token_edit.textChanged.connect(lambda _: self._refresh_schema_preview())
        self._nuclear_token_edit.textChanged.connect(lambda _: self._refresh_segmentation_hints())

        fl_title = QLabel("Fluorescent channels", sec)
        fl_title.setContentsMargins(0, 6, 0, 2)
        sec.layout().addWidget(fl_title)
        fl_hint = QLabel("Mark smFISH channels with the checkbox on each row.", sec)
        fl_hint.setObjectName("Muted")
        sec.layout().addWidget(fl_hint)

        self._fluor_frame = QWidget(sec)
        self._fluor_frame
        self._fluor_frame_layout = QVBoxLayout(self._fluor_frame)
        self._fluor_frame_layout.setContentsMargins(0, 0, 0, 0)
        self._fluor_frame_layout.setSpacing(2)
        sec.layout().addWidget(self._fluor_frame)
        self._fluor_add_row("GFP")

        add_btn = QPushButton("+ Add channel", sec)
        add_btn.setProperty("variant", "secondary")
        add_btn.setFixedWidth(130)
        add_btn.clicked.connect(lambda _=False: self._fluor_add_row())
        sec.layout().addWidget(add_btn)

        # Watershed-only rows (hidden until method selected)
        self._cyto_row_widget = QWidget(sec)
        cr = QHBoxLayout(self._cyto_row_widget)
        cr.setContentsMargins(0, 1, 0, 1)
        clbl = QLabel("Cytoplasm token", self._cyto_row_widget)
        clbl.setObjectName("Muted")
        clbl.setFixedWidth(150)
        cr.addWidget(clbl)
        self._cytoplasm_edit = QLineEdit(self._cyto_row_widget)
        self._cytoplasm_edit.setFixedWidth(80)
        cr.addWidget(self._cytoplasm_edit)
        cr.addStretch(1)
        self._cytoplasm_edit.textChanged.connect(lambda _: self._refresh_segmentation_hints())
        sec.layout().addWidget(self._cyto_row_widget)
        self._cyto_row_widget.setVisible(False)

        self._area_row_widget = QWidget(sec)
        ar = QHBoxLayout(self._area_row_widget)
        ar.setContentsMargins(0, 1, 0, 1)
        albl = QLabel("Min nucleus area", self._area_row_widget)
        albl.setObjectName("Muted")
        albl.setFixedWidth(150)
        ar.addWidget(albl)
        self._min_nucleus_area_edit = QLineEdit("50", self._area_row_widget)
        self._min_nucleus_area_edit.setFixedWidth(60)
        ar.addWidget(self._min_nucleus_area_edit)
        px_lbl = QLabel("pixels (watershed)", self._area_row_widget)
        px_lbl.setObjectName("Muted")
        ar.addWidget(px_lbl)
        ar.addStretch(1)
        self._min_nucleus_area_edit.textChanged.connect(lambda _: self._refresh_segmentation_hints())
        sec.layout().addWidget(self._area_row_widget)
        self._area_row_widget.setVisible(False)

        self._segmentation_hint_lbl = QLabel("", sec)
        self._segmentation_hint_lbl.setObjectName("Muted")
        self._segmentation_hint_lbl.setWordWrap(True)
        sec.layout().addWidget(self._segmentation_hint_lbl)
        self._refresh_segmentation_hints()

    def _build_folders_section(self, parent: QWidget) -> None:
        sec = self._section(parent, "Folders")

        in_lbl = QLabel("Input folder", sec)
        sec.layout().addWidget(in_lbl)

        in_pick = QWidget(sec)
        ipl = QHBoxLayout(in_pick)
        ipl.setContentsMargins(0, 0, 0, 4)
        ipl.setSpacing(4)
        self._input_edit = QLineEdit(in_pick)
        self._input_edit.setReadOnly(True)
        self._input_edit.setPlaceholderText("(not set)")
        self._input_edit.setEnabled(False)
        self._input_edit.setMinimumWidth(0)
        self._input_edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        ipl.addWidget(self._input_edit, 1)
        self._browse_btn = QPushButton("Browse…", in_pick)
        self._browse_btn.setProperty("variant", "secondary")
        self._browse_btn.setEnabled(False)
        self._browse_btn.clicked.connect(self._browse_input)
        ipl.addWidget(self._browse_btn)
        sec.layout().addWidget(in_pick)

        self._folder_lock_lbl = QLabel("Define the filename schema above first.", sec)
        self._folder_lock_lbl.setObjectName("Muted")
        sec.layout().addWidget(self._folder_lock_lbl)

        out_lbl = QLabel("Output folder (auto)", sec)
        out_lbl.setContentsMargins(0, 6, 0, 0)
        sec.layout().addWidget(out_lbl)
        self._output_lbl = QLabel("—", sec)
        self._output_lbl.setObjectName("Muted")
        self._output_lbl.setWordWrap(True)
        sec.layout().addWidget(self._output_lbl)

        self._layout_lbl = QLabel("", sec)
        self._layout_lbl.setWordWrap(True)
        sec.layout().addWidget(self._layout_lbl)

        self._input_edit.textChanged.connect(lambda _: self._refresh_output())

    def _build_tophat_section(self, parent: QWidget) -> None:
        sec = self._section(parent, "Top-Hat Background Subtraction")

        nir_row = self._row(sec, "Nuclear radius")
        self._tophat_radius_nir_edit = self._entry(nir_row, "100", width=60)
        self._no_tophat_nir_cb = QCheckBox("Disable", nir_row)
        nir_row.layout().addWidget(self._no_tophat_nir_cb)
        nir_row.layout().addStretch(1)

        fluor_row = self._row(sec, "Fluor radius")
        self._tophat_radius_fluor_edit = self._entry(fluor_row, "100", width=60)
        self._no_tophat_fluor_cb = QCheckBox("Disable", fluor_row)
        fluor_row.layout().addWidget(self._no_tophat_fluor_cb)
        fluor_row.layout().addStretch(1)

        hint = QLabel("Fluor radius applies to all fluorescent channels.", sec)
        hint.setObjectName("Muted")
        sec.layout().addWidget(hint)

    def _build_output_options_section(self, parent: QWidget) -> None:
        sec = self._section(parent, "Output Options")

        self._compress_input_cb = QCheckBox(
            "Folder mode only: Compress input well folders to .zip", sec)
        self._compress_input_cb.setChecked(True)
        sec.layout().addWidget(self._compress_input_cb)

        self._compress_output_cb = QCheckBox(
            "Folder mode only: Compress output well folders to .zip", sec)
        self._compress_output_cb.setChecked(True)
        sec.layout().addWidget(self._compress_output_cb)

        r = self._row(sec, "CSV prefix")
        self._csv_prefix_edit = self._entry(r, "gfp_measurements", width=160)
        r.layout().addStretch(1)

    def _build_compute_options_section(self, parent: QWidget) -> None:
        sec = self._section(parent, "Compute Options")

        tf_row = self._row(sec, "TF threads (0=auto)")
        self._tf_threads_edit = self._entry(tf_row, "0", width=40)
        tf_hint = QLabel("(0 → auto-select 4)", tf_row)
        tf_hint.setObjectName("Muted")
        tf_row.layout().addWidget(tf_hint)
        tf_row.layout().addStretch(1)

        w_row = self._row(sec, "Workers (0=auto)")
        self._workers_edit = self._entry(w_row, "0", width=40)
        w_hint = QLabel("(process count override)", w_row)
        w_hint.setObjectName("Muted")
        w_row.layout().addWidget(w_hint)
        w_row.layout().addStretch(1)

        self._cpu_only_cb = QCheckBox("CPU only (disable GPU)", sec)
        sec.layout().addWidget(self._cpu_only_cb)
        self._force_cb = QCheckBox("Force reprocess all wells", sec)
        sec.layout().addWidget(self._force_cb)

    def _build_run_controls(self, parent: QWidget) -> None:
        # Remote-execution selector. Populated from ~/.config/allwell/remotes.toml
        # so users can flip between Local and any configured SSH host
        # without restarting the GUI.
        remote_widget = QWidget(parent)
        rl = QHBoxLayout(remote_widget)
        rl.setContentsMargins(12, 6, 12, 0)
        rl.setSpacing(8)
        rl.addWidget(QLabel("Run on:", remote_widget))
        self._remote_cb = QComboBox(remote_widget)
        self._remote_cb.addItem("Local", userData=None)
        try:
            for name in load_remotes():
                self._remote_cb.addItem(name, userData=name)
        except (ValueError, OSError) as exc:
            # Malformed remotes.toml shouldn't bring the GUI down — just
            # surface the error in the log and stick with Local.
            self._log_q.put((
                "line",
                f"[warn] could not load remotes.toml: {exc}\n",
            ))
        rl.addWidget(self._remote_cb)
        self._manage_remotes_btn = QPushButton("Manage…", remote_widget)
        self._manage_remotes_btn.setProperty("variant", "secondary")
        self._manage_remotes_btn.setToolTip(
            "Add, edit, delete, test, or bootstrap remote-execution hosts."
        )
        self._manage_remotes_btn.clicked.connect(self._open_manage_remotes)
        rl.addWidget(self._manage_remotes_btn)
        self._cleanup_remote_cb = QCheckBox(
            "Remove from remote when done", remote_widget,
        )
        self._cleanup_remote_cb.setToolTip(
            "After a verified-successful run, delete both the per-run output "
            "dir AND the cached input on the remote. Unchecked = keep the "
            "input cache so the next run on the same data skips re-upload."
        )
        rl.addWidget(self._cleanup_remote_cb)
        rl.addStretch(1)
        parent.layout().addWidget(remote_widget)
        # Disable the cleanup checkbox until the user picks a remote.
        self._remote_cb.currentIndexChanged.connect(self._refresh_remote_controls)
        self._refresh_remote_controls()

        btn_widget = QWidget(parent)
        btn_widget
        bl = QHBoxLayout(btn_widget)
        bl.setContentsMargins(12, 10, 12, 10)
        bl.setSpacing(8)

        self._run_btn = QPushButton("▶  Run Pipeline", btn_widget)
        self._run_btn.setProperty("variant", "primary")
        self._run_btn.clicked.connect(self._run)
        bl.addWidget(self._run_btn)

        self._stop_btn = QPushButton("■  Stop", btn_widget)
        self._stop_btn.setProperty("variant", "danger")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop)
        bl.addWidget(self._stop_btn)
        bl.addStretch(1)

        parent.layout().addWidget(btn_widget)

    def _refresh_remote_controls(self) -> None:
        """Enable the cleanup checkbox only when a remote is selected."""
        is_remote = self._remote_cb.currentData() is not None
        self._cleanup_remote_cb.setEnabled(is_remote)
        if not is_remote:
            self._cleanup_remote_cb.setChecked(False)

    def _open_manage_remotes(self) -> None:
        """Open the v5.1.0 Manage Remotes dialog.

        After the dialog closes (or any time remotes_changed fires), the
        "Run on:" combobox is rebuilt from disk. The previous selection
        is preserved if it still exists; otherwise we fall back to
        "Local" so a user who just deleted their currently-selected
        remote can't end up with a stale selection.
        """
        # Lazy import to avoid pulling the dialog module into the
        # AnalyzeTab import graph on startup — the dialog imports
        # services.remote_runner which has heavier-weight dependencies.
        from analyze_remote_dialog import ManageRemotesDialog
        dlg = ManageRemotesDialog(self)
        dlg.remotes_changed.connect(self._reload_remotes_combo)
        dlg.exec()

    def _reload_remotes_combo(self) -> None:
        """Refresh the 'Run on:' combobox from remotes.toml.

        Called after every add / edit / delete in the Manage Remotes
        dialog. Preserves the user's current selection when possible.
        """
        prev = self._remote_cb.currentData()
        self._remote_cb.blockSignals(True)
        try:
            self._remote_cb.clear()
            self._remote_cb.addItem("Local", userData=None)
            try:
                from services.remote_config import load_remotes
                for name in load_remotes():
                    self._remote_cb.addItem(name, userData=name)
            except (ValueError, OSError) as exc:
                self._log_q.put((
                    "line",
                    f"[warn] could not reload remotes.toml: {exc}\n",
                ))
            # Try to re-select the previous remote; otherwise fall back
            # to Local (index 0) which is always present.
            if prev is not None:
                for i in range(self._remote_cb.count()):
                    if self._remote_cb.itemData(i) == prev:
                        self._remote_cb.setCurrentIndex(i)
                        break
                else:
                    self._remote_cb.setCurrentIndex(0)
        finally:
            self._remote_cb.blockSignals(False)
        # Manually re-fire the dependent-control refresh since we
        # silenced the signal during the rebuild.
        self._refresh_remote_controls()

    # ------------------------------------------------------------------
    # Fluorescent channel helpers
    # ------------------------------------------------------------------
    def _fluor_add_row(self, default_token: str = "") -> None:
        row = QWidget(self._fluor_frame)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)

        edit = QLineEdit(default_token, row)
        edit.setFixedWidth(80)
        edit.textChanged.connect(lambda _: self._refresh_schema_preview())
        rl.addWidget(edit)

        remove_btn = QPushButton("✕", row)
        remove_btn.setFixedWidth(24)
        remove_btn.setProperty("variant", "secondary")
        rl.addWidget(remove_btn)

        smfish_cb = QCheckBox("smFISH", row)
        rl.addWidget(smfish_cb)
        rl.addStretch(1)

        self._fluor_rows.append((edit, smfish_cb, remove_btn))
        remove_btn.clicked.connect(
            lambda _=False, r=row, b=remove_btn: self._fluor_remove_row(r, b)
        )
        self._fluor_frame_layout.addWidget(row)
        self._fluor_refresh_remove_buttons()
        self._refresh_schema_preview()

    def _fluor_remove_row(self, row: QWidget, remove_btn: QPushButton) -> None:
        if len(self._fluor_rows) <= 1:
            return
        self._fluor_rows = [(e, s, b) for e, s, b in self._fluor_rows if b is not remove_btn]
        row.deleteLater()
        self._fluor_refresh_remove_buttons()
        self._refresh_schema_preview()

    def _fluor_refresh_remove_buttons(self) -> None:
        only_one = len(self._fluor_rows) == 1
        for _, _, btn in self._fluor_rows:
            btn.setEnabled(not only_one)

    def _fluor_tokens_list(self) -> list[str]:
        return [e.text().strip() for e, _, _ in self._fluor_rows if e.text().strip()]

    def _smfish_tokens_list(self) -> list[str]:
        return [e.text().strip() for e, s, _ in self._fluor_rows if s.isChecked() and e.text().strip()]

    # ------------------------------------------------------------------
    # Segmentation hints
    # ------------------------------------------------------------------
    def _segmentation_validation_errors(self) -> list[str]:
        errors: list[str] = []
        method = self._seg_method_cb.currentText()
        if method == "stardist_seeded_watershed_cell":
            cyto = self._cytoplasm_edit.text().strip()
            nuc  = self._nuclear_token_edit.text().strip()
            if not cyto:
                errors.append("Watershed mode requires a cytoplasm token.")
            if cyto and cyto == nuc:
                errors.append("Cytoplasm token must differ from nuclear token.")
            try:
                area = int(self._min_nucleus_area_edit.text().strip())
                if area <= 0:
                    errors.append("Minimum nucleus area must be a positive integer.")
            except ValueError:
                errors.append("Minimum nucleus area must be a positive integer.")
        return errors

    def _refresh_segmentation_hints(self) -> None:
        if not hasattr(self, "_segmentation_hint_lbl"):
            return
        method = self._seg_method_cb.currentText()
        watershed = method == "stardist_seeded_watershed_cell"
        self._cyto_row_widget.setVisible(watershed)
        self._area_row_widget.setVisible(watershed)
        if watershed:
            errors = self._segmentation_validation_errors()
            if errors:
                self._segmentation_hint_lbl.setText("  ".join(errors))
            else:
                self._segmentation_hint_lbl.setText(
                    "Watershed mode uses StarDist seeds + cytoplasm mask "
                    "and also quantifies the cytoplasm channel."
                )
        else:
            self._segmentation_hint_lbl.setText("")

    # ------------------------------------------------------------------
    # Schema helpers
    # ------------------------------------------------------------------
    def _schema_field_list(self) -> list[str]:
        return [_LABEL_TO_FIELD.get(cb.currentText(), "ignore") for cb in self._schema_cbs]

    def _on_combobox_selected(self, changed_cb: QComboBox) -> None:
        if hasattr(self, "_schema_str_edit"):
            new_schema = ":".join(
                _LABEL_TO_FIELD.get(c.currentText(), "ignore") for c in self._schema_cbs
            )
            self._schema_str_edit.blockSignals(True)
            self._schema_str_edit.setText(new_schema)
            self._schema_str_edit.blockSignals(False)
        self._refresh_schema_preview()

    def _sync_dropdowns_from_string(self) -> None:
        if not self._schema_cbs or not hasattr(self, "_schema_str_edit"):
            return
        raw = self._schema_str_edit.text().strip()
        if not raw:
            return
        parts = [p.strip().lower() for p in raw.split(":")]
        parts = (parts + ["ignore"] * 5)[:5]
        if parts == self._schema_field_list():
            return
        for cb, field in zip(self._schema_cbs, parts):
            label = _FIELD_TO_LABEL.get(field, "— ignore —")
            if cb.currentText() != label:
                cb.blockSignals(True)
                cb.setCurrentText(label)
                cb.blockSignals(False)
        self._refresh_schema_preview()

    def _schema_errors(self) -> list[str]:
        schema_str = self._build_schema_arg()
        fields = [f.strip().lower() for f in schema_str.split(":") if f.strip()]
        errors: list[str] = []
        if fields.count("channel") != 1:
            errors.append(f'"Channel" must appear exactly once (found {fields.count("channel")}).')
        if fields.count("well") != 1:
            errors.append(f'"Well" must appear exactly once (found {fields.count("well")}).')
        return errors

    def _build_schema_arg(self) -> str:
        if hasattr(self, "_schema_str_edit"):
            raw = self._schema_str_edit.text().strip()
            if raw:
                return raw
        return ":".join(self._schema_field_list())

    def _refresh_schema_preview(self) -> None:
        if not self._schema_cbs or not hasattr(self, "_schema_err_lbl"):
            return
        errors = self._schema_errors()
        fields = self._schema_field_list()
        sep = (self._filename_sep_edit.text() if hasattr(self, "_filename_sep_edit") else DEFAULT_SEP) or DEFAULT_SEP
        schema_valid = not errors

        if hasattr(self, "_input_edit"):
            self._input_edit.setEnabled(schema_valid)
            self._browse_btn.setEnabled(schema_valid)
        if hasattr(self, "_folder_lock_lbl"):
            self._folder_lock_lbl.setText("" if schema_valid else "Define the filename schema above first.")

        if errors:
            self._schema_err_lbl.setText("  ".join(errors))
            self._schema_preview_lbl.setText("")
            if hasattr(self, "_run_btn"):
                self._run_btn.setEnabled(False)
            return

        self._schema_err_lbl.setText("")
        if hasattr(self, "_run_btn"):
            self._run_btn.setEnabled(True)

        nuclear_tok = (self._nuclear_token_edit.text().strip() if hasattr(self, "_nuclear_token_edit") else "NIR") or "NIR"
        fluor_toks  = self._fluor_tokens_list() if self._fluor_rows else ["GFP"]
        fluor_toks  = fluor_toks or ["GFP"]

        def _make_example(chan_tok: str) -> str:
            parts = [chan_tok if f == "channel" else _PREVIEW_TOKENS.get(f, "X") for f in fields]
            return sep.join(parts) + ".tif"

        lines = [f"e.g. {_make_example(nuclear_tok)}"] + [f"     {_make_example(t)}" for t in fluor_toks[:2]]
        self._schema_preview_lbl.setText("\n".join(lines))

    # ------------------------------------------------------------------
    # Log panel
    # ------------------------------------------------------------------
    def _build_log(self, parent: QWidget) -> None:
        rl = QVBoxLayout(parent)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        hdr = QWidget(parent)
        hdr
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(12, 6, 12, 6)
        hl.addWidget(QLabel("Pipeline Output", hdr))
        self._status_lbl = QLabel("Idle", hdr)
        self._status_lbl.setObjectName("Muted")
        hl.addWidget(self._status_lbl)
        hl.addStretch(1)
        clear_btn = QPushButton("Clear", hdr)
        clear_btn.setProperty("variant", "secondary")
        clear_btn.clicked.connect(self._clear_log)
        hl.addWidget(clear_btn)
        rl.addWidget(hdr)

        r1 = QFrame(parent); r1.setFrameShape(QFrame.HLine); r1.setObjectName("HRule")
        rl.addWidget(r1)

        prog_widget = QWidget(parent)
        prog_widget
        pl = QHBoxLayout(prog_widget)
        pl.setContentsMargins(12, 6, 12, 6)
        self._prog_lbl = QLabel("", prog_widget)
        self._prog_lbl.setObjectName("Muted")
        pl.addWidget(self._prog_lbl)
        self._progress = QProgressBar(prog_widget)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        pl.addWidget(self._progress, 1)
        self._eta_lbl = QLabel("", prog_widget)
        self._eta_lbl.setObjectName("Muted")
        pl.addWidget(self._eta_lbl)
        rl.addWidget(prog_widget)

        r2 = QFrame(parent); r2.setFrameShape(QFrame.HLine); r2.setObjectName("HRule")
        rl.addWidget(r2)

        self._log = QTextEdit(parent)
        self._log.setReadOnly(True)
        self._log.setLineWrapMode(QTextEdit.NoWrap)
        # Cap the QTextDocument so a multi-hour pipeline can't grow it to
        # hundreds of megabytes. Older lines drop off the top once we hit
        # the cap; the ring buffer in all_well captures everything for
        # the help-drawer log tab anyway.
        self._log.document().setMaximumBlockCount(10000)
        font = self._log.font()
        font.setFamily("Menlo" if _sys.platform == "darwin" else "Consolas")
        font.setPointSize(9)
        self._log.setFont(font)
        rl.addWidget(self._log, 1)

    # ------------------------------------------------------------------
    # Folder resolution
    # ------------------------------------------------------------------
    def _browse_input(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select input folder")
        if d:
            self._input_edit.setText(d)
            self._input_edit.setEnabled(True)

    def _refresh_output(self) -> None:
        raw_str = self._input_edit.text().strip()
        if not raw_str:
            self._output_lbl.setText("—"); self._layout_lbl.setText(""); return
        raw = Path(raw_str)
        if not raw.is_dir():
            self._output_lbl.setText("—"); self._layout_lbl.setText("Not a directory."); return
        if raw.name.lower() == "in":
            self._output_lbl.setText(str(raw.parent / "out"))
            self._layout_lbl.setText("✓ Using selected folder as input"); return
        in_sub = raw / "in"
        if in_sub.is_dir() and _has_well_content(in_sub):
            self._output_lbl.setText(str(raw / "out"))
            self._layout_lbl.setText("✓ Found in/ subfolder — using as input"); return
        tifs = _tif_files_in(raw)
        if len(tifs) > 3:
            self._output_lbl.setText(str(raw / "out"))
            self._layout_lbl.setText(f"✓ Will run WellPlateZipper on {len(tifs)} TIF files → in/"); return
        self._output_lbl.setText("—")
        self._layout_lbl.setText("No TIF files or in/ folder found in selected directory.")

    # ------------------------------------------------------------------
    # Run / Stop
    # ------------------------------------------------------------------
    def _run(self) -> None:
        pipeline = self._validate_run_request()
        if pipeline is None:
            return
        self._set_running_ui_state()
        opts = self._collect_run_options()
        remote_name = self._remote_cb.currentData()
        remote = None
        if remote_name is not None:
            try:
                from services.remote_config import get_remote
                remote = get_remote(remote_name)
            except (ValueError, OSError) as exc:
                QMessageBox.critical(
                    self,
                    "Remote Config Error",
                    f"Could not load remote '{remote_name}':\n{exc}",
                )
                # Revert the running UI state set above so the user can
                # try again. Mirrors the inline cleanup in _poll_log's
                # "finished" branch.
                self._running = False
                self._run_btn.setEnabled(True)
                self._stop_btn.setEnabled(False)
                self._status_lbl.setText("Idle")
                return
        cleanup_mode = "all" if self._cleanup_remote_cb.isChecked() else "run"
        self._runner.start(
            pipeline,
            opts,
            resolve_dirs=self._resolve_run_dirs_for_runner,
            remote=remote,
            output_filter="full",
            cleanup_remote=cleanup_mode,
            selective_channels=True,
        )

    def _validate_run_request(self) -> Optional[Path]:
        if not self._input_edit.text().strip():
            QMessageBox.critical(self, "Input Error", "No input folder selected.")
            return None
        schema_errors = self._schema_errors()
        if schema_errors:
            QMessageBox.critical(self, "Schema Error", "Invalid filename schema:\n\n" + "\n".join(schema_errors))
            return None
        if not self._fluor_tokens_list():
            QMessageBox.critical(self, "Channel Error", "At least one fluorescent channel token is required.")
            return None
        seg_errors = self._segmentation_validation_errors()
        if seg_errors:
            QMessageBox.critical(self, "Segmentation Error", "\n".join(seg_errors))
            return None
        pipeline = find_pipeline_script()
        if pipeline is None:
            QMessageBox.critical(self, "Configuration Error",
                "process_microscopy.py not found next to all_well.py.")
            return None
        return pipeline

    def _set_running_ui_state(self) -> None:
        self._running = True
        self._well_total = 0
        self._well_done  = 0
        self._progress_tracker.reset()
        self._progress.setValue(0)
        self._prog_lbl.setText("Preparing…")
        self._eta_deadline = None
        self._eta_lbl.setText("ETA")
        self._pipeline_started_at = _time.monotonic()
        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._status_lbl.setText("Running…")
        # Status light goes yellow (warn) for the lifetime of the pipeline
        # run. ``finished`` (in ``_poll_log``) pops the count.
        try:
            from well_viewer import status_signal as _status_signal
            _status_signal.warn_push()
            self._status_signal_pushed = True
        except Exception:
            self._status_signal_pushed = False

    def _collect_run_options(self) -> dict:
        method = self._seg_method_cb.currentText() or "stardist_nuclei"
        try:
            min_area = int(self._min_nucleus_area_edit.text().strip() or "50")
        except ValueError:
            min_area = 50
        cyto = self._cytoplasm_edit.text().strip()
        if method != "stardist_seeded_watershed_cell":
            cyto = ""
        return dict(
            raw=Path(self._input_edit.text().strip()),
            nuclear_token=self._nuclear_token_edit.text().strip() or "NIR",
            fluor_tokens=self._fluor_tokens_list(),
            csv_prefix=self._csv_prefix_edit.text().strip() or "gfp_measurements",
            tophat_radius_nir=self._tophat_radius_nir_edit.text(),
            tophat_radius_fluor=self._tophat_radius_fluor_edit.text(),
            no_tophat_nir=self._no_tophat_nir_cb.isChecked(),
            no_tophat_fluor=self._no_tophat_fluor_cb.isChecked(),
            compress_input_well_folders=self._compress_input_cb.isChecked(),
            compress_output_well_folders=self._compress_output_cb.isChecked(),
            force=self._force_cb.isChecked(),
            cpu_only=self._cpu_only_cb.isChecked(),
            tf_threads=self._tf_threads_edit.text(),
            workers=self._workers_edit.text(),
            filename_schema=self._build_schema_arg(),
            filename_sep=self._filename_sep_edit.text() or DEFAULT_SEP,
            smfish_tokens=self._smfish_tokens_list(),
            segmentation_method=method,
            cytoplasm_token=cyto,
            min_nucleus_area_px=min_area,
        )

    def _expected_well_count(self, opts: dict) -> int:
        import re as _re2
        raw = opts["raw"]
        in_sub = raw / "in"
        if raw.name.lower() == "in":
            return _count_well_content(raw) or 1
        if in_sub.is_dir() and _has_well_content(in_sub):
            return _count_well_content(in_sub) or 1
        well_re = _re2.compile(r"[A-Ha-h]\d{1,2}", _re2.I)
        tifs = _tif_files_in(raw)
        sep = opts["filename_sep"]
        fields = [f.strip() for f in opts["filename_schema"].split(":")]
        try:
            well_idx = fields.index("well")
        except ValueError:
            well_idx = -1
        wells: set[str] = set()
        for tif_path in tifs:
            parts = tif_path.stem.split(sep)
            token = parts[well_idx] if 0 <= well_idx < len(parts) else ""
            if token and well_re.fullmatch(token):
                wells.add(token.upper())
        return len(wells) or len(tifs) or 1

    def _resolve_run_dirs_for_runner(self, opts: dict, log_q: queue.Queue,
                                     *, proc_hook=None):
        """Adapter passed to ``PipelineRunner.start``.

        Lives on the tab because expected-well-count + grouping progress are
        UI-side helpers that depend on Analyze form state. ``proc_hook`` is
        forwarded to ``resolve_input_output`` so the grouping subprocess can
        be tracked by the runner and reached by Stop.
        """
        try:
            log_q.put(("zipper_start", self._expected_well_count(opts)))
            input_dir, output_dir = resolve_input_output(
                opts["raw"],
                log_fn=lambda msg: log_q.put(("line", msg)),
                progress_fn=lambda tok: log_q.put(("zipper_well", tok)),
                filename_schema=opts["filename_schema"],
                filename_sep=opts["filename_sep"],
                proc_hook=proc_hook,
            )
            log_q.put(("zipper_done", None))
            return input_dir, output_dir
        except (ValueError, RuntimeError) as exc:
            log_q.put(("error", f"Input error: {exc}\n"))
            return None

    def _stop(self) -> None:
        # Stop is meaningful whenever the runner has *any* in-flight work
        # — including the grouping phase, when self._runner._proc is
        # still None. Calling stop() unconditionally is safe (it no-ops
        # when nothing is alive) and lets it reach the zipper.
        self._runner.stop()
        self._log_line("\n[User stopped the pipeline]\n", "WARNING")

    # ------------------------------------------------------------------
    # Log helpers
    # ------------------------------------------------------------------
    def _log_line(self, text: str, tag: str = "INFO") -> None:
        # Snapshot the user's scroll position before appending. Auto-
        # scroll-to-bottom only if they were already pinned there;
        # otherwise leave the viewport alone so a user scrolled up to
        # read an earlier line isn't yanked back every time a new line
        # arrives.
        vsb = self._log.verticalScrollBar()
        at_bottom = vsb.value() >= vsb.maximum() - 4  # 4-px tolerance
        # Use a *fresh* QTextCursor for the insert — setting the active
        # text cursor (the old code) is what forced the viewport to
        # follow the caret. The doc-bound cursor leaves the visible
        # cursor / selection alone.
        cursor = QTextCursor(self._log.document())
        cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self._LOG_COLORS.get(tag, self._LOG_COLORS["INFO"])))
        cursor.insertText(text, fmt)
        if at_bottom:
            vsb.setValue(vsb.maximum())

    def _clear_log(self) -> None:
        self._log.clear()

    def _update_eta(self, done: int, total: int) -> None:
        # Remember the latest progress snapshot so the tick timer can
        # re-render the ETA against live ``_time.monotonic()`` between
        # well_done events.
        self._eta_done = int(done)
        self._eta_total = int(total)
        # Lock in a deadline at this event using the cumulative per-well
        # wall-clock rate. The tick timer between events just renders
        # ``deadline - now`` — never recomputing from elapsed, which would
        # make the displayed remaining grow while waiting for the next
        # well to complete.
        started = getattr(self, "_pipeline_started_at", None)
        if (
            started is not None
            and self._eta_done > 0
            and self._eta_total > 0
            and self._eta_done < self._eta_total
        ):
            elapsed = _time.monotonic() - started
            if elapsed > 0.0:
                per_well_rate = elapsed / self._eta_done
                remaining_wells = self._eta_total - self._eta_done
                self._eta_deadline = _time.monotonic() + remaining_wells * per_well_rate
        self._render_eta()
        # Start the 1-Hz tick timer the first time we get usable progress
        # so the label counts down even when no new well_done arrives.
        timer = getattr(self, "_eta_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setInterval(1000)
            timer.timeout.connect(self._render_eta)
            self._eta_timer = timer
        if done > 0 and total > 0 and done < total and not timer.isActive():
            timer.start()

    def _render_eta(self) -> None:
        started = getattr(self, "_pipeline_started_at", None)
        done = int(getattr(self, "_eta_done", 0))
        total = int(getattr(self, "_eta_total", 0))
        if started is None:
            self._eta_lbl.setText("")
            return
        if total > 0 and done >= total:
            # All wells finished. The pipeline still has post-well work
            # (write pipeline_info.json, run the auto-threshold pass)
            # before the "finished" event fires; surface that state
            # explicitly so the user doesn't see a stale ETA / blank
            # while the progress bar sits at 100%.
            self._eta_lbl.setText("Finalizing…")
            return
        deadline = getattr(self, "_eta_deadline", None)

        def _fmt(secs: float) -> str:
            secs = int(round(max(0.0, secs)))
            if secs >= 3600:
                return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"
            if secs >= 60:
                return f"{secs // 60}m{secs % 60:02d}s"
            return f"{secs}s"

        if deadline is None:
            # Pipeline is running but no well has completed yet — show a
            # placeholder so the user sees the timer is alive.
            self._eta_lbl.setText("ETA")
            return
        remaining = deadline - _time.monotonic()
        self._eta_lbl.setText(f"ETA {_fmt(remaining)}")

    def _stop_eta_timer(self) -> None:
        timer = getattr(self, "_eta_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()
        self._eta_done = 0
        self._eta_total = 0
        self._eta_deadline = None

    def _apply_progress_event(self, kind: str, payload: object) -> None:
        """Render a progress event from :class:`ProgressTracker` to the UI."""
        if kind == "well_total":
            total = int(payload)  # type: ignore[arg-type]
            self._well_total = total
            self._progress.setRange(0, total)
            self._progress.setValue(0)
            self._prog_lbl.setText(f"Pipeline: 0 / {total} wells")
        elif kind == "well_done":
            done, total = payload  # type: ignore[misc]
            self._well_done = int(done)
            self._progress.setValue(self._well_done)
            pct = int(self._well_done / max(1, total) * 100)
            self._prog_lbl.setText(
                f"Pipeline: {self._well_done} / {total} wells  ({pct}%)"
            )
            self._update_eta(self._well_done, total)
            # The auto-threshold pass runs *after* the last well — show
            # an explicit finalizing state so the user doesn't see the
            # progress bar pinned at 100% with nothing happening.
            if total > 0 and self._well_done >= total:
                self._status_lbl.setText("Finalizing…")

    def _poll_log(self) -> None:
        try:
            while True:
                kind, payload = self._log_q.get_nowait()
                if kind == "line":
                    for ev_kind, ev_payload in self._progress_tracker.parse(payload):
                        if ev_kind == "line":
                            self._log_q.put((ev_kind, ev_payload))
                        elif ev_kind == "workers":
                            self._log_q.put((ev_kind, ev_payload))
                        else:
                            self._apply_progress_event(ev_kind, ev_payload)
                    self._log_line(payload, classify_log_line(payload))
                elif kind == "zipper_start":
                    n = payload or 96
                    self._zipper_done = 0
                    self._progress.setRange(0, n)
                    self._progress.setValue(0)
                    self._prog_lbl.setText(f"Grouping: 0 / {n} wells")
                elif kind == "zipper_well":
                    n_total = self._progress.maximum() or 96
                    self._zipper_done = getattr(self, "_zipper_done", 0) + 1
                    self._progress.setValue(self._zipper_done)
                    pct = int(self._zipper_done / n_total * 100)
                    self._prog_lbl.setText(f"Grouping: {self._zipper_done} / {n_total} wells  ({pct}%)")
                    self._update_eta(self._zipper_done, n_total)
                elif kind == "zipper_done":
                    self._progress.setRange(0, 100)
                    self._progress.setValue(0)
                    self._well_total = 0
                    self._well_done  = 0
                    self._prog_lbl.setText("Grouping complete — starting pipeline…")
                    self._stop_eta_timer()
                    self._eta_lbl.setText("ETA")
                    # Reset the per-phase clock so the pipeline-phase ETA
                    # isn't biased by the (typically short) grouping phase.
                    self._pipeline_started_at = _time.monotonic()
                elif kind == "workers":
                    try:
                        self._pipeline_workers = max(1, int(payload))
                    except (TypeError, ValueError):
                        self._pipeline_workers = 1
                    self._log_line(f"[info] Workers: {payload} parallel well(s).\n", "INFO")
                elif kind == "remote_xfer":
                    # Remote upload / download progress in [0, 1]. The
                    # well-progress bar isn't accurate yet (no wells have
                    # finished on the remote), so surface the transfer
                    # progress on the status label instead of clobbering
                    # the well bar.
                    try:
                        pct = float(payload)
                    except (TypeError, ValueError):
                        pct = 0.0
                    self._status_lbl.setText(f"Transferring… {int(pct * 100)}%")
                elif kind == "done":
                    self._progress.setValue(self._progress.maximum() or 100)
                    n = self._well_done or getattr(self, "_zipper_done", 0)
                    self._prog_lbl.setText(f"Complete — {n} well(s) processed")
                    self._log_line(payload, "DONE")
                    self._log_line(
                        "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        "  Processing Complete\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
                        "DONE",
                    )
                    if self._on_pipeline_complete is not None and self._last_output_dir is not None:
                        try:
                            self._on_pipeline_complete(self._last_output_dir)
                        except Exception as exc:
                            self._log_line(f"[warn] Could not open Review tab: {exc}\n", "WARNING")
                elif kind == "error":
                    self._log_line(payload, "ERROR")
                elif kind == "finished":
                    self._running = False
                    self._run_btn.setEnabled(True)
                    self._stop_btn.setEnabled(False)
                    self._status_lbl.setText("Idle")
                    self._eta_lbl.setText("")
                    self._stop_eta_timer()
                    if getattr(self, "_status_signal_pushed", False):
                        try:
                            from well_viewer import status_signal as _status_signal
                            _status_signal.warn_pop()
                        except Exception:
                            pass
                        self._status_signal_pushed = False
        except queue.Empty:
            pass
        QTimer.singleShot(80, self._poll_log)

    def closeEvent(self, event) -> None:
        if self._runner.is_running:
            self._runner.stop()
        # Mirror the cleanup branch in _poll_log so closing the app
        # mid-run doesn't leave the status_signal warn-scope
        # unbalanced (the in-memory ref count would otherwise carry
        # across a re-build of the tab without a fresh QApplication).
        if getattr(self, "_status_signal_pushed", False):
            try:
                from well_viewer import status_signal as _status_signal
                _status_signal.warn_pop()
            except Exception:
                pass
            self._status_signal_pushed = False
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Standalone test harness
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    win = QWidget()
    win.setWindowTitle("Analyze Tab — standalone test")
    win.resize(1100, 700)
    layout = QVBoxLayout(win)
    layout.setContentsMargins(0, 0, 0, 0)
    tab = AnalyzeTab(win)
    layout.addWidget(tab)
    win.show()
    sys.exit(app.exec())
