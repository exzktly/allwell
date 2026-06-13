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
import sys as _sys
import time as _time
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea,
    QSizePolicy, QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from services.input_resolution_service import resolve_input_output, tif_files_in
from core.well_token import is_valid_well_name
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
from widgets.checkable_dropdown import CheckableDropdown
from widgets.run_dashboard import RunDashboard
from widgets.wrap_checkbox import WrapCheckBox
import core.shape_features as _sfc
from well_viewer.core.safe import swallowed
from analyze_tab_form import (  # form builders + schema vocab (split out)
    AnalyzeTabFormMixin,
    DEFAULT_SCHEMA, DEFAULT_SEP, SCHEMA_FIELDS,
    _FIELD_TO_LABEL, _LABEL_TO_FIELD, _SCHEMA_LABELS,
)


def _safe_qc_float(text: str) -> float:
    """Parse a QC bound from a text field; 0.0 (= no bound) on empty/invalid."""
    try:
        return float(str(text).strip() or "0")
    except (TypeError, ValueError):
        return 0.0

# ---------------------------------------------------------------------------
# Schema vocabulary — kept in sync with process_microscopy.py
# ---------------------------------------------------------------------------


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

_tif_files_in = tif_files_in


def _has_well_content(folder: Path) -> bool:
    if any(folder.glob("*.zip")):
        return True
    try:
        return any(is_valid_well_name(p.name) for p in folder.iterdir() if p.is_dir())
    except OSError:
        return False


def _count_well_content(folder: Path) -> int:
    zips = list(folder.glob("*.zip"))
    folders = [p for p in folder.iterdir() if p.is_dir() and is_valid_well_name(p.name)]
    return len(zips) or len(folders)


def _safe_seg_float(text: str, default: float) -> float:
    """Parse a segmentation-parameter field; *default* on empty/invalid."""
    try:
        return float(str(text).strip() or default)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# AnalyzeTab
# ---------------------------------------------------------------------------
class AnalyzeTab(AnalyzeTabFormMixin, QWidget):
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
        self._fluor_rows: list[tuple[QLineEdit, QCheckBox, QCheckBox, QPushButton]] = []
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
        # Further +25% (350→438) per user feedback — the per-fluor rows
        # were still tight when multiple channels were configured.
        scroll.setMinimumWidth(438)
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

        splitter.setSizes([450, 600])

    # ------------------------------------------------------------------
    # Layout helpers
    # ------------------------------------------------------------------



    # ------------------------------------------------------------------
    # Form orchestration
    # ------------------------------------------------------------------


    # Segmentation routines: (CLI value, friendly label). The combo stores the
    # value in userData so the label can read naturally without changing argv.
    _SEG_METHODS = (
        ("stardist_nuclei", "StarDist — nuclei"),
        ("stardist_seeded_watershed_cell", "StarDist + watershed — cell"),
        ("cellpose", "Cellpose (general)"),
        ("cellpose_labelfree", "Label-free (Cellpose, brightfield)"),
        ("classical_threshold", "Classical (Otsu + watershed)"),
    )

    def _seg_method(self) -> str:
        """The canonical segmentation-method value behind the combo selection."""
        return self._seg_method_cb.currentData() or "stardist_nuclei"




    # Sensible default morphology features (centroids are always emitted by the
    # pipeline regardless of selection, so they are not offered as chips).
    _SHAPE_DEFAULTS = ("area", "eccentricity", "solidity", "perimeter")






    def _refresh_remote_controls(self) -> None:
        """Show the remote-only parameter group only when a remote is selected,
        and within it gate the dry-run-cleanup checkbox on auto-delete being
        checked. Switching back to Local resets the toggles so a stale value
        can't be submitted for the next remote run."""
        is_remote = self._remote_cb.currentData() is not None
        self._remote_params_widget.setVisible(is_remote)
        if not is_remote:
            self._cleanup_remote_cb.setChecked(False)
        dry_run_enabled = is_remote and self._cleanup_remote_cb.isChecked()
        self._dry_run_cleanup_cb.setEnabled(dry_run_enabled)
        if not dry_run_enabled:
            self._dry_run_cleanup_cb.setChecked(False)

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
        rl.setSpacing(8)

        edit = QLineEdit(default_token, row)
        edit.setFixedWidth(80)
        edit.textChanged.connect(lambda _: self._refresh_schema_preview())
        rl.addWidget(edit)

        remove_btn = QPushButton("✕", row)
        remove_btn.setFixedWidth(24)
        remove_btn.setProperty("variant", "secondary")
        rl.addWidget(remove_btn)

        # Pin each checkbox to a width derived from font metrics + a fixed
        # indicator/spacing allowance, NOT sizeHint(): sizeHint() underreports
        # the label width before the global stylesheet is polished, so the
        # layout reserved too little and the "Phase" indicator painted on top
        # of the "smFISH" label. Minimum policy floors the width at this value.
        def _pin_checkbox(text: str) -> QCheckBox:
            cb = QCheckBox(text, row)
            # indicator (14px + 1px border ×2 = 16) + QSS spacing + padding.
            cb.setMinimumWidth(cb.fontMetrics().horizontalAdvance(text) + 34)
            cb.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            rl.addWidget(cb)
            return cb

        smfish_cb = _pin_checkbox("smFISH")
        phase_cb = _pin_checkbox("Phase")
        rl.addStretch(1)

        self._fluor_rows.append((edit, smfish_cb, phase_cb, remove_btn))

        # Phase is single across the experiment (mutually exclusive across rows)
        # and cannot coexist with smFISH on the same channel.
        def _on_phase_toggled(checked: bool, this_cb=phase_cb, this_sm=smfish_cb) -> None:
            if checked:
                this_sm.setChecked(False)
                for _e, _s, _p, _b in self._fluor_rows:
                    if _p is not this_cb:
                        _p.setChecked(False)
            self._refresh_segmentation_hints()

        def _on_smfish_toggled(checked: bool, this_ph=phase_cb) -> None:
            if checked:
                this_ph.setChecked(False)

        phase_cb.toggled.connect(_on_phase_toggled)
        smfish_cb.toggled.connect(_on_smfish_toggled)
        remove_btn.clicked.connect(
            lambda _=False, r=row, b=remove_btn: self._fluor_remove_row(r, b)
        )
        self._fluor_frame_layout.addWidget(row)
        self._fluor_refresh_remove_buttons()
        self._refresh_schema_preview()

    def _fluor_remove_row(self, row: QWidget, remove_btn: QPushButton) -> None:
        if len(self._fluor_rows) <= 1:
            return
        self._fluor_rows = [(e, s, p, b) for e, s, p, b in self._fluor_rows if b is not remove_btn]
        row.deleteLater()
        self._fluor_refresh_remove_buttons()
        self._refresh_schema_preview()

    def _fluor_refresh_remove_buttons(self) -> None:
        only_one = len(self._fluor_rows) == 1
        for _, _, _, btn in self._fluor_rows:
            btn.setEnabled(not only_one)

    def _fluor_tokens_list(self) -> list[str]:
        return [e.text().strip() for e, _, _, _ in self._fluor_rows if e.text().strip()]

    def _smfish_tokens_list(self) -> list[str]:
        return [e.text().strip() for e, s, _, _ in self._fluor_rows if s.isChecked() and e.text().strip()]

    def _phase_token(self) -> str:
        """The single channel token flagged as the phase/brightfield image (or '')."""
        for e, _s, p, _b in self._fluor_rows:
            if p.isChecked() and e.text().strip():
                return e.text().strip()
        return ""

    # ------------------------------------------------------------------
    # Segmentation hints
    # ------------------------------------------------------------------
    def _segmentation_validation_errors(self) -> list[str]:
        errors: list[str] = []
        method = self._seg_method()
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
        elif method == "cellpose_labelfree":
            if not self._phase_token():
                errors.append(
                    "Label-free segmentation requires exactly one channel marked "
                    "'Phase' (the brightfield image to segment).")
        return errors

    def _background_well_validation_error(self) -> Optional[str]:
        """When empty_well drives flat-field and/or background, the background
        well must be specified and must exist in the input. Returns an error
        message to block the run, or None when the request is valid (or empty_well
        isn't selected)."""
        uses = []
        if self._flatfield_cb.currentText() == "empty_well":
            uses.append("flat-field correction")
        if self._background_cb.currentText() == "empty_well":
            uses.append("background subtraction")
        if not uses:
            return None
        which = " and ".join(uses)
        # One or several comma-separated wells (e.g. "H12, G12"), pooled.
        raw = self._background_well_edit.text().strip()
        seen, wells = set(), []
        for tok in raw.replace(";", ",").split(","):
            w = tok.strip()
            if w and w.lower() not in seen:
                seen.add(w.lower())
                wells.append(w)
        if not wells:
            return (f"Empty-well {which} requires a Background well.\n\n"
                    "Enter one empty/background well (e.g. H12) — or several "
                    "comma-separated (e.g. H12, G12) — so its measured "
                    "background can be sampled.")
        input_dir = Path(self._input_edit.text().strip())
        sep = self._filename_sep_edit.text() or DEFAULT_SEP
        schema = [f.strip().lower() for f in self._build_schema_arg().split(":") if f.strip()]
        from services.input_resolution_service import well_present_in_input
        missing = [w for w in wells
                   if not well_present_in_input(input_dir, w, schema=schema, sep=sep)]
        if missing:
            label = ", ".join(f"'{w}'" for w in missing)
            return (f"Background well {label} was not found in the input folder.\n\n"
                    f"Empty-well {which} reads its reference images from the named "
                    f"well(s) — confirm they exist in:\n{input_dir}")
        return None

    def _collapse_seg_param_stack(self) -> None:
        """Size the per-method parameter stack to its *current* page only.

        A ``QStackedWidget`` reports the max sizeHint over all pages, so without
        this the empty StarDist page reserves the tall Cellpose page's height
        (the gap the user saw under channel tokens). Giving every non-current
        page a vertically-Ignored policy drops it from the height calculation.
        """
        stack = getattr(self, "_seg_param_stack", None)
        if stack is None:
            return
        cur = stack.currentIndex()
        for i in range(stack.count()):
            page = stack.widget(i)
            pol = page.sizePolicy()
            pol.setVerticalPolicy(
                QSizePolicy.Preferred if i == cur else QSizePolicy.Ignored)
            page.setSizePolicy(pol)
        stack.adjustSize()
        stack.updateGeometry()

    def _refresh_segmentation_hints(self) -> None:
        if not hasattr(self, "_segmentation_hint_lbl"):
            return
        method = self._seg_method()
        # Show only the selected routine's parameter page.
        if hasattr(self, "_seg_param_stack"):
            self._seg_param_stack.setCurrentByName(method)
            self._collapse_seg_param_stack()
        # The Label-free preset segments the Phase channel — the explicit
        # Segmentation-channel field doesn't apply there (it's taken at runtime).
        phase_locked = method == "cellpose_labelfree"
        if hasattr(self, "_nuclear_row_widget"):
            self._nuclear_row_widget.setVisible(not phase_locked)
        errors = self._segmentation_validation_errors()
        if errors:
            self._segmentation_hint_lbl.setText("  ".join(errors))
            return
        hint = {
            "stardist_seeded_watershed_cell":
                "Watershed mode uses StarDist seeds + cytoplasm mask and also "
                "quantifies the cytoplasm channel.",
            "cellpose":
                "Cellpose segments the 'Segmentation channel' above — works on a "
                "fluorescent or brightfield channel.",
            "cellpose_labelfree":
                "Label-free preset: Cellpose (cyto3) on the channel marked 'Phase' "
                "— no nuclear stain required.",
            "classical_threshold":
                "Otsu threshold + watershed on the 'Segmentation channel' above.",
        }.get(method, "")
        self._segmentation_hint_lbl.setText(hint)

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

    # ------------------------------------------------------------------
    # Folder resolution
    # ------------------------------------------------------------------
    def _browse_input(self) -> None:
        # ``DontResolveSymlinks`` keeps a Windows mapped drive (e.g.
        # ``Z:\plate`` for a ``net use Z: \\server\share`` mount)
        # rendered as its drive letter in the returned string instead
        # of being canonicalized to the underlying UNC path. The
        # remote-execution path (rsync over Cygwin ssh) rejects UNC
        # paths via ``to_rsync_path`` — users who carefully mapped
        # their network share and picked it via the drive letter
        # would otherwise hit "UNC paths aren't supported by rsync on
        # Windows" because Qt resolved the mapping out from under
        # them. ``ShowDirsOnly`` is implied by ``getExistingDirectory``
        # but spelled explicitly here so the option set is obvious.
        d = QFileDialog.getExistingDirectory(
            self,
            "Select input folder",
            "",
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )
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
                self._dashboard.set_headline("Idle")
                self._dashboard.set_status("")
                return
        # v5.2.11: unchecked = "none" (safe default for shared
        # machines). Checked = "all" (delete per-run output AND the
        # dataset cache after a verified-successful run).
        cleanup_mode = "all" if self._cleanup_remote_cb.isChecked() else "none"
        dry_run_cleanup = self._dry_run_cleanup_cb.isChecked()
        self._runner.start(
            pipeline,
            opts,
            resolve_dirs=self._resolve_run_dirs_for_runner,
            remote=remote,
            output_filter="full",
            cleanup_remote=cleanup_mode,
            dry_run_cleanup=dry_run_cleanup,
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
        # Empty-well flat-field / background reads its reference images from a
        # named background well — require it to be specified AND to exist in the
        # input before starting, rather than letting the run no-op (empty model)
        # or fail mid-pipeline.
        bw_err = self._background_well_validation_error()
        if bw_err:
            QMessageBox.critical(self, "Background Well Error", bw_err)
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
        self._dashboard.reset(remote=self._remote_cb.currentData() is not None)
        self._eta_deadline = None
        self._dashboard.set_eta("ETA")
        self._pipeline_started_at = _time.monotonic()
        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._dashboard.set_status("Running…")
        # Status light goes yellow (warn) for the lifetime of the pipeline
        # run. ``finished`` (in ``_poll_log``) pops the count.
        try:
            from well_viewer import status_signal as _status_signal
            _status_signal.warn_push()
            self._status_signal_pushed = True
        except Exception:
            self._status_signal_pushed = False

    def _collect_run_options(self) -> dict:
        method = self._seg_method()
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
            csv_prefix=self._csv_prefix_edit.text().strip() or "measurements",
            tophat_radius_nir=self._tophat_radius_nir_edit.text(),
            tophat_radius_fluor=self._tophat_radius_fluor_edit.text(),
            fft_small_nir=self._fft_small_nir_edit.text(),
            fft_small_fluor=self._fft_small_fluor_edit.text(),
            mesh_ratio=self._mesh_ratio_edit.text(),
            mesh_sigma=self._mesh_sigma_edit.text(),
            no_tophat_nir=self._no_tophat_nir_cb.isChecked(),
            no_tophat_fluor=self._no_tophat_fluor_cb.isChecked(),
            compress_input_well_folders=self._compress_input_cb.isChecked(),
            compress_output_well_folders=self._compress_output_cb.isChecked(),
            move_source_tifs=self._move_source_tifs_cb.isChecked(),
            remote_batch_size=self._remote_batch_size_edit.text(),
            force=self._force_cb.isChecked(),
            cpu_only=self._cpu_only_cb.isChecked(),
            tf_threads=self._tf_threads_edit.text(),
            workers=self._workers_edit.text(),
            filename_schema=self._build_schema_arg(),
            filename_sep=self._filename_sep_edit.text() or DEFAULT_SEP,
            smfish_tokens=self._smfish_tokens_list(),
            shape_features=list(self._shape_chips.checkedData()),
            flatfield=self._flatfield_cb.currentText(),
            background=self._background_cb.currentText(),
            background_well=self._background_well_edit.text().strip(),
            qc_remove_debris=self._qc_remove_debris_cb.isChecked(),
            qc_min_area=_safe_qc_float(self._qc_min_area_edit.text()),
            qc_max_area=_safe_qc_float(self._qc_max_area_edit.text()),
            qc_min_solidity=_safe_qc_float(self._qc_min_solidity_edit.text()),
            phase_token=self._phase_token(),
            segmentation_method=method,
            cytoplasm_token=cyto,
            min_nucleus_area_px=min_area,
            cellpose_model=self._cellpose_model_cb.currentText(),
            cellpose_diameter=_safe_seg_float(self._cellpose_diameter_edit.text(), 0.0),
            cellpose_flow_threshold=_safe_seg_float(self._cellpose_flow_thr_edit.text(), 0.4),
            cellpose_cellprob_threshold=_safe_seg_float(self._cellpose_cellprob_thr_edit.text(), 0.0),
            cellpose_nuclei_channel=self._cellpose_nuclei_channel_edit.text().strip(),
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
            grouped = {"n": 0}

            def _on_group(tok: str) -> None:
                grouped["n"] += 1
                log_q.put(("zipper_well", tok))

            input_dir, output_dir, on_success = resolve_input_output(
                opts["raw"],
                log_fn=lambda msg: log_q.put(("line", msg)),
                progress_fn=_on_group,
                filename_schema=opts["filename_schema"],
                filename_sep=opts["filename_sep"],
                proc_hook=proc_hook,
                move_source_tifs=bool(opts.get("move_source_tifs", False)),
            )
            # When the inputs are already grouped/zipped the zipper is skipped
            # and fires no per-well progress — surface the existing wells so the
            # Group stage still shows progress and the plate marks each well
            # queued (pending) instead of jumping from empty straight to
            # processing.
            if grouped["n"] == 0:
                from services.input_resolution_service import iter_well_tokens
                for tok in iter_well_tokens(input_dir):
                    log_q.put(("zipper_well", tok))
            log_q.put(("zipper_done", None))
            # v5.x.x: forward the optional success callback as a
            # 3-tuple. PipelineRunner unpacks it and runs it after
            # a clean pipeline completion so loose TIFs at raw/ get
            # cleaned up once raw/in/ has the zipped equivalents.
            return input_dir, output_dir, on_success
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

        # v5.4.7: compensate for ``setMaximumBlockCount(10000)``-driven
        # trimming. When a new line pushes the block count over the
        # cap, Qt removes blocks from the TOP. From the scrollbar's
        # view that shifts every following pixel up — a user scrolled
        # to read an earlier line still LOOKS like they're at the
        # same value() but the content under that pixel has moved.
        # Without an explicit compensation the viewport drifts toward
        # the bottom (toward newer lines) every time trimming fires,
        # which felt extra-broken once the rsync progress2 stream
        # made trimming common during a remote run. Solution:
        # snapshot block count before insert, count lines added,
        # derive how many blocks were trimmed, subtract that many
        # line-heights from the saved scroll value.
        old_value = vsb.value()
        doc = self._log.document()
        old_block_count = doc.blockCount()
        lines_added = text.count("\n") if text else 0
        # An ``insertText`` of "foo\nbar" appends 1 hard newline +
        # bar (no trailing newline) → blockCount goes up by 1.
        # "foo\nbar\n" → up by 1. "foo" (no newline) → up by 0.
        # The text we receive always has a trailing "\n" (see
        # callers), so ``\n``-count == new blocks added.

        # Use a *fresh* QTextCursor for the insert — setting the active
        # text cursor (the old code) is what forced the viewport to
        # follow the caret. The doc-bound cursor leaves the visible
        # cursor / selection alone.
        cursor = QTextCursor(doc)
        cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self._LOG_COLORS.get(tag, self._LOG_COLORS["INFO"])))
        cursor.insertText(text, fmt)

        if at_bottom:
            vsb.setValue(vsb.maximum())
            return

        # User was scrolled up. Did setMaximumBlockCount trim
        # blocks from the top? If so, shift the saved scroll value
        # up by the corresponding pixel height so the user stays
        # anchored to the SAME content they were reading instead
        # of drifting toward the new lines at the bottom.
        new_block_count = doc.blockCount()
        blocks_trimmed = max(0, old_block_count + lines_added - new_block_count)
        if blocks_trimmed > 0:
            line_height = max(1, self._log.fontMetrics().lineSpacing())
            vsb.setValue(max(0, old_value - blocks_trimmed * line_height))
        else:
            # No trimming — explicitly restore the pre-insert value
            # in case Qt did anything implicit.
            vsb.setValue(old_value)

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
            self._dashboard.set_eta("")
            return
        if total > 0 and done >= total:
            # All wells finished. The pipeline still has post-well work
            # (write pipeline_info.json, run the auto-threshold pass)
            # before the "finished" event fires; surface that state
            # explicitly so the user doesn't see a stale ETA / blank
            # while the progress bar sits at 100%.
            self._dashboard.set_eta("Finalizing…")
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
            self._dashboard.set_eta("ETA")
            return
        remaining = deadline - _time.monotonic()
        self._dashboard.set_eta(f"ETA {_fmt(remaining)}")

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
            self._dashboard.set_total(total)
            self._dashboard.set_overall_range(total)
            self._dashboard.set_overall(0)
            self._dashboard.set_headline(f"Processing 0 / {total} wells")
        elif kind == "well_done":
            done, total = payload  # type: ignore[misc]
            self._well_done = int(done)
            self._dashboard.set_overall(self._well_done)
            pct = int(self._well_done / max(1, total) * 100)
            self._update_eta(self._well_done, total)
            self._dashboard.set_headline(
                f"Processing {self._well_done} / {total} wells  ({pct}%)")
            # The auto-threshold pass runs *after* the last well — show
            # an explicit finalizing state so the user doesn't see the
            # progress bar pinned at 100% with nothing happening.
            if total > 0 and self._well_done >= total:
                self._dashboard.set_status("Finalizing…")
        elif kind == "stage":
            self._dashboard.set_active_stage(str(payload))
        elif kind == "well_stage":
            well, stage = payload  # type: ignore[misc]
            self._dashboard.set_well_stage(str(well), str(stage))
        elif kind == "well_fov":
            well, done, total = payload  # type: ignore[misc]
            self._dashboard.set_well_fov(str(well), int(done), int(total))
        elif kind == "status":
            # Descriptive finalize-phase status (auto-threshold / remote drain
            # / cleanup) so the post-well work doesn't read as a hang.
            self._dashboard.set_status(str(payload))

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
                    self._dashboard.set_total(n)
                    self._dashboard.set_overall_range(n)
                    self._dashboard.set_overall(0)
                    self._dashboard.set_active_stage("group")
                    self._dashboard.set_headline(f"Grouping 0 / {n} wells")
                elif kind == "zipper_well":
                    n_total = self._dashboard.overall_maximum() or 96
                    self._zipper_done = getattr(self, "_zipper_done", 0) + 1
                    self._dashboard.set_overall(self._zipper_done)
                    pct = int(self._zipper_done / n_total * 100)
                    self._update_eta(self._zipper_done, n_total)
                    self._dashboard.set_active_stage("group")
                    if payload:
                        self._dashboard.set_well_stage(str(payload), "group")
                    self._dashboard.set_headline(
                        f"Grouping {self._zipper_done} / {n_total} wells  ({pct}%)")
                elif kind == "zipper_done":
                    self._dashboard.set_overall_range(100)
                    self._dashboard.set_overall(0)
                    self._well_total = 0
                    self._well_done  = 0
                    self._dashboard.set_headline("Grouping complete — starting pipeline…")
                    self._stop_eta_timer()
                    self._dashboard.set_eta("ETA")
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
                    self._dashboard.set_status(f"Transferring… {int(pct * 100)}%")
                elif kind == "done":
                    self._dashboard.set_overall(self._dashboard.overall_maximum() or 100)
                    n = self._well_done or getattr(self, "_zipper_done", 0)
                    self._dashboard.set_headline(f"Complete — {n} well(s) processed")
                    self._dashboard.set_status("")
                    self._dashboard.set_eta("")
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
                    self._dashboard.set_status("")
                    self._dashboard.set_eta("")
                    self._stop_eta_timer()
                    if getattr(self, "_status_signal_pushed", False):
                        try:
                            from well_viewer import status_signal as _status_signal
                            _status_signal.warn_pop()
                        except Exception:
                            swallowed()  # was: pass (silent except)
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
                swallowed()  # was: pass (silent except)
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
