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
import time as _time
from pathlib import Path
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLineEdit, QMessageBox, QPushButton, QScrollArea,
    QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

from services.input_resolution_service import (
    tif_files_in,
)
from util.well_token import is_valid_well_name
from util.schema_infer import infer_from_dir
from services.pipeline_runner import (
    PipelineRunner,
    ProgressTracker,
    classify_log_line,
    is_transient_progress,
)
from services.pipeline_service import find_pipeline_script
from services.remote_config import default_remote_index
import theme
from well_viewer.core.app_shell_view import RAIL_W
from well_viewer.core.safe import swallowed
from analyze_input import AnalyzeInputMixin
from analyze_progress import AnalyzeProgressMixin
from analyze_run_spec import AnalyzeRunMixin
from analyze_suspend import AnalyzeSuspendMixin
from analyze_tab_form import (  # form builders + schema vocab (split out)
    AnalyzeTabFormMixin,
    DEFAULT_SEP, SCHEMA_FIELDS,
    _FIELD_TO_LABEL, _LABEL_TO_FIELD, _SCHEMA_LABELS,
    # Entry-boundary numeric validation lives with the fields it reads; re-exported
    # so ``from analyze_tab import _numeric_option_errors`` still resolves.
    _numeric_option_errors,  # noqa: F401 (re-export)
)



# Numeric fields are plain QLineEdits with no validators, and bad values used to
# be swallowed deep in the pipeline (a non-integer radius silently dropped its
# flag; mesh_ratio=0 caused a downstream division-by-zero). Validate them at the
# entry boundary instead (audit X4/X6). Pure + module-level so it's unit-tested.
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



# ---------------------------------------------------------------------------
# AnalyzeTab
# ---------------------------------------------------------------------------
class AnalyzeTab(AnalyzeInputMixin, AnalyzeProgressMixin, AnalyzeRunMixin,
                 AnalyzeSuspendMixin, AnalyzeTabFormMixin, QWidget):
    """Left: pipeline options form.  Right: live subprocess log."""

    # Positive-latch completion signal for a job queue: emitted exactly once
    # per run, as the final statement of _poll_log's "finished" branch. Never
    # inferred from "no error seen" — see the _run_ok / _run_error latch below.
    runFinished = Signal(bool, str)

    # QueueableController hook (ok, message, out_dir — "" for none). Kept
    # distinct from runFinished (whose (bool, str) shape is pinned by
    # tests/test_analyze_run_finished.py) rather than changed in place, and
    # emitted strictly after it — see _poll_log's "finished" branch, where
    # this is truly the last statement so a queue bridge's slot starting the
    # next job can't race anything after its own emit.
    jobFinished = Signal(bool, str, str)

    @staticmethod
    def _log_color(tag: str) -> str:
        return {
            "INFO":    theme.Colors.text_primary,
            "WARNING": theme.Colors.warn,
            "ERROR":   theme.Colors.danger,
            "DONE":    theme.Colors.success,
            "CMD":     theme.Colors.text_muted,
        }.get(tag, theme.Colors.text_primary)

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
        # Cleared on app close so the self-rescheduling log poller stops
        # re-arming its QTimer (audit L2) — AnalyzeTab is a child widget whose
        # own closeEvent never fires.
        self._alive = True
        # runFinished latch: set by the "done"/"error" branches of _poll_log,
        # read (and reset) by start_run. Initialised here, not just in
        # start_run, since _poll_log self-arms below and can drain a
        # "finished" event before any run has ever started.
        self._run_ok = False
        self._run_error: str | None = None
        self._well_total = 0
        self._well_done  = 0
        # Wells that announced completion more than once (batched remote runs
        # can re-visit a well whose input zip outlived its batch). Counted and
        # surfaced rather than folded into _well_done, which would let the
        # headline read 100% with wells still outstanding.
        self._well_repeats = 0
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

        # No rule across the top — the shell already draws the hairline under
        # the topbar (app_shell_view's ``_top_sep``) and this pane mounts
        # straight beneath it. Handle width is likewise the theme's
        # (``QSplitter::handle``), so this divider matches Review's.
        splitter = QSplitter(Qt.Horizontal, self)
        outer.addWidget(splitter, 1)

        # Left — scrollable form
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        # Fixed, not just a minimum: RAIL_W (well_viewer/core/app_shell_view.py)
        # is the one width shared by Review's rail, Datasets' sidebar, and this
        # panel, so switching modes never visibly resizes the window. It was
        # widened here twice before landing at its current value (280→350→438)
        # because the schema string and per-channel rows clipped below it — that
        # history is *why* this is the value the other two modes' panels match,
        # not the other way around; don't narrow it without re-checking those
        # rows. A QSplitter child still respects setFixedWidth's equal min/max,
        # so the handle can't be dragged past it either.
        scroll.setFixedWidth(RAIL_W)
        form_host = QWidget()
        self._form_layout = QVBoxLayout(form_host)
        self._form_layout.setContentsMargins(0, 8, 0, 8)
        self._form_layout.setSpacing(0)
        scroll.setWidget(form_host)
        self._left_sidebar = scroll  # shell left-rail toggle hides/shows this
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 0)

        self._build_form(form_host)

        # Right — log panel
        right = QWidget()
        right.setMinimumWidth(300)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        self._build_log(right)

        splitter.setSizes([RAIL_W, 600])

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
        checked. Switching back to Local restores the toggles to their defaults
        so a stale value can't be submitted for the next remote run."""
        is_remote = self._remote_cb.currentData() is not None
        self._remote_params_widget.setVisible(is_remote)
        if not is_remote:
            # Back to its default rather than to off: auto-delete is on by
            # default, so resetting to off here would quietly hand the next
            # remote run the opposite setting from a fresh one.
            self._cleanup_remote_cb.setChecked(True)
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
            # Re-select an explicit prior remote when it still exists; else use
            # the default (first configured remote if any, else Local).
            _default = default_remote_index(self._remote_cb.count())
            for i in range(self._remote_cb.count()):
                if prev is not None and self._remote_cb.itemData(i) == prev:
                    self._remote_cb.setCurrentIndex(i)
                    break
            else:
                self._remote_cb.setCurrentIndex(_default)
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
        remove_btn.setFixedWidth(36)   # wide enough that the button padding doesn't clip ✕
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
        phase_cb.setToolTip(
            "Mark this channel as phase/brightfield: it skips all background "
            "correction and is resaved from the original image. With the "
            "Label-free method it is also the Cellpose segmentation input."
        )
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
        # Each hint leads with *when to use this* so a first-time user can pick.
        hint = {
            "stardist_nuclei": "Best default for a nuclear stain (DAPI/Hoechst/NIR): StarDist segments the 'Segmentation channel' above into nuclei.",
            "stardist_seeded_watershed_cell": "Use for whole-cell (cytoplasm) measurements: StarDist seeds + a cytoplasm mask; also quantifies the cytoplasm channel.",
            "cellpose": "Use for non-nuclear or tricky morphologies: Cellpose on the 'Segmentation channel' above (fluorescent or brightfield).",
            "cellpose_labelfree": "Use when there's no stain at all: Cellpose (cyto3) on the channel marked 'Phase' — label-free.",
            "classical_threshold": "Fast model-free fallback for clean, separated objects: Otsu threshold + watershed on the 'Segmentation channel' above.",
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
            self._autofill_from_input_scan(d)

    def _autofill_from_input_scan(self, folder: str) -> None:
        """Pre-fill the schema + channel fields by briefly scanning *folder*.

        Samples the filenames on disk (loose TIFs, a zip, or the per-well-zip
        layout) and guesses the separator, slot layout, channel tokens and their
        roles, applying them like a loaded ``pipeline_info``. Best-effort: a
        folder whose names don't parse (or an unreadable one) leaves the form as
        the user set it. Only the interactive picker calls this — the converter's
        "Open in Analyze" handoff supplies its own channel roles.
        """
        try:
            guess = infer_from_dir(folder)
        except Exception:
            swallowed()  # a scan must never break folder selection
            return
        if guess is None:
            return
        info = {"filename_sep": guess.separator, "filename_schema": guess.schema_str}
        info.update(guess.channel_tokens().as_pipeline_info())
        self._apply_pipeline_info(info)

    def _refresh_output(self) -> None:
        raw_str = self._input_edit.text().strip()
        if not raw_str:
            self._output_lbl.setText("—"); self._layout_lbl.setText(""); return
        # A raw packed OME-TIFF must be converted to a dataset folder first
        # (Datasets → Convert OME-TIFF); Analyze only processes folders.
        ome_hint = self._ome_input_hint()
        if ome_hint is not None:
            self._output_lbl.setText("—"); self._layout_lbl.setText(f"⚠ {ome_hint}")
            return
        raw = Path(raw_str)
        if not raw.is_dir():
            self._output_lbl.setText("—"); self._layout_lbl.setText("Not a directory."); return
        if raw.name.lower() == "in":
            self._output_lbl.setText(str(raw.parent / "out"))
            loose = _tif_files_in(raw)
            if not _has_well_content(raw) and len(loose) > 3:
                self._layout_lbl.setText(
                    f"✓ Will group {len(loose)} loose TIFs inside this in/ folder")
            else:
                self._layout_lbl.setText("✓ Using selected folder as input")
            return
        in_sub = raw / "in"
        if (in_sub.is_dir() and not _has_well_content(in_sub)
                and len(_tif_files_in(in_sub)) > 3):
            self._output_lbl.setText(str(raw / "out"))
            self._layout_lbl.setText(
                f"✓ Will group {len(_tif_files_in(in_sub))} loose TIFs in in/")
            return
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
        spec = self._collect_run_spec()
        error = self.start_run(spec, pipeline=pipeline)
        if error is not None:
            QMessageBox.critical(self, "Run Error", error)

    def _validate_run_request(self) -> Path | None:
        if not self._input_edit.text().strip():
            QMessageBox.critical(self, "Input Error", "No input folder selected.")
            return None
        ome_hint = self._ome_input_hint()
        if ome_hint is not None:
            QMessageBox.critical(self, "OME-TIFF Input", ome_hint)
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
        # Numeric fields — block a bad value at the boundary rather than
        # silently dropping the flag or dividing by zero downstream.
        num_errors = _numeric_option_errors(self._collect_run_options())
        if num_errors:
            QMessageBox.critical(self, "Input Error", "\n".join(num_errors))
            return None
        # Empty-well flat-field / background reads its references from a named
        # background well — require it to exist in the input before starting,
        # rather than letting the run no-op or fail mid-pipeline.
        bw_err = self._background_well_validation_error()
        if bw_err:
            QMessageBox.critical(self, "Background Well Error", bw_err)
            return None
        at_err = self._auto_threshold_validation_error()
        if at_err:
            QMessageBox.critical(self, "Control Well Error", at_err)
            return None
        pipeline = find_pipeline_script()
        if pipeline is None:
            QMessageBox.critical(self, "Configuration Error",
                "process_microscopy.py not found next to allwell.py.")
            return None
        return pipeline

    def _set_running_ui_state(self, *, remote=None) -> None:
        self._running = True
        self._well_total = 0
        self._well_done  = 0
        self._well_repeats = 0
        self._progress_tracker.reset()
        self._dashboard.reset(remote=remote is not None)
        if getattr(self, "_last_run_card", None) is not None:
            self._last_run_card.setVisible(False)  # C4: hide stale summary while running
        self._eta_deadline = None
        self._dashboard.set_eta("ETA")
        self._pipeline_started_at = _time.monotonic()
        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._suspend_btn.setEnabled(True)
        self.refresh_resume_banner()      # hidden while a run is going
        self._dashboard.set_status("Running…")

    def _expected_well_count(self, opts: dict) -> int:
        # Same layout detection as resolve_input_output (no GUI/service drift).
        from services.input_resolution_service import count_expected_wells
        return count_expected_wells(
            opts["raw"],
            filename_schema=opts["filename_schema"],
            filename_sep=opts["filename_sep"],
        )

    def _stop(self) -> None:
        # Never let an exception escape this Qt slot (PySide6≥6.5 aborts the app).
        try:
            self._runner.stop()
            self._log_line("\n[User stopped the pipeline]\n", "WARNING")
        except Exception:
            swallowed()

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
        fmt.setForeground(QColor(self._log_color(tag)))
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
                    if not is_transient_progress(payload):
                        self._log_line(payload, classify_log_line(payload))
                elif kind in ("zipper_start", "stage_total"):
                    self._apply_grouping_event(kind, payload)
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
                    self._dashboard.set_headline("Grouped — pipeline (segmentation + zipping)…")
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
                    # Remote upload/download progress in [0,1] — show on the
                    # status label, not the (not-yet-accurate) well bar.
                    try:
                        pct = float(payload)
                    except (TypeError, ValueError):
                        pct = 0.0
                    self._dashboard.set_status(f"Transferring… {int(pct * 100)}%")
                elif kind == "done":
                    self._run_ok = True
                    n = self._well_done or getattr(self, "_zipper_done", 0)
                    missing = max(0, self._well_total - self._well_done)
                    if missing:
                        # Fewer wells finished than the run announced. Leave the
                        # bar short and name the shortfall: a full bar over a
                        # partial plate is the one thing this display must never
                        # show, because nothing else in the window says so.
                        self._dashboard.set_overall(self._well_done)
                        self._dashboard.set_headline(
                            f"Complete — {self._well_done} of {self._well_total} "
                            f"well(s) processed; {missing} never reported completion")
                        self._log_line(
                            f"[warn] Run finished with {missing} of "
                            f"{self._well_total} well(s) never reporting "
                            f"completion. Check the log above for FAILED / "
                            f"PARTIAL lines and the output dir's errors/ "
                            f"folder.\n",
                            "WARNING",
                        )
                    else:
                        self._dashboard.set_overall(self._dashboard.overall_maximum() or 100)
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
                    if self._run_error is None:  # keep the first message only
                        self._run_error = str(payload)
                elif kind == "finished":
                    self._running = False
                    self._run_btn.setEnabled(True)
                    self._stop_btn.setEnabled(False)
                    self._suspend_btn.setEnabled(False)
                    self._on_run_settled()
                    self._dashboard.set_status("")
                    self._dashboard.set_eta("")
                    self._stop_eta_timer()
                    self._refresh_last_run_summary()  # C4: show the just-finished run
                                # A directly-connected slot on either signal below may
                    # start the next queued run re-entrantly, so nothing after
                    # jobFinished's emit may touch run state.
                    ok, message = self._run_ok, (self._run_error or "")
                    out_dir = str(self._last_output_dir) if ok and self._last_output_dir else ""
                    try:
                        self.runFinished.emit(ok, "" if ok else message)
                    except Exception:
                        swallowed()
                    try:
                        self.jobFinished.emit(ok, "" if ok else message, out_dir)
                    except Exception:
                        swallowed()
        except queue.Empty:
            pass
        except Exception:  # a bad event must not kill the poller
            swallowed()    # (would freeze the live log/dashboard permanently)
        if getattr(self, "_alive", True):
            QTimer.singleShot(80, self._poll_log)

    def closeEvent(self, event) -> None:
        if self._runner.is_running:
            self._runner.stop()
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
