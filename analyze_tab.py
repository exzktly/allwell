"""
analyze_tab.py — AnalyzeTab widget (PySide6 port).

Input-folder resolution rules (applied when Run is pressed, and previewed under
the input field the moment a folder is chosen):
  1. Folder named "in"            → input = folder,     output = parent/"out"
  2. Folder contains sub-dir "in" → input = folder/"in", output = folder/"out"
  3. Folder has >3 TIF files      → group them into folder/"in" first
  4. Otherwise                    → error

Both the preview and the run take that decision from
``services.input_resolution_service.plan_input_layout``; this module holds no
layout rules of its own.

process_microscopy.py must live alongside this file.
"""

from __future__ import annotations

import queue
import time as _time
from pathlib import Path
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLineEdit, QMessageBox, QPushButton, QScrollArea,
    QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

from services.input_resolution_service import plan_input_layout
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
from analyze_preflight import AnalyzePreflightMixin
from analyze_progress import AnalyzeProgressMixin
from analyze_run_spec import AnalyzeRunMixin
from analyze_suspend import AnalyzeSuspendMixin
from analyze_tab_log import AnalyzeLogPaneMixin
from analyze_tab_form import (  # form builders (split out)
    AnalyzeTabFormMixin,
    # Entry-boundary numeric validation lives with the fields it reads; re-exported
    # so ``from analyze_tab import _numeric_option_errors`` still resolves.
    _numeric_option_errors,  # noqa: F401 (re-export)
)


# Numeric fields are plain QLineEdits with no validators, and bad values used to
# be swallowed deep in the pipeline (a non-integer radius silently dropped its
# flag; mesh_ratio=0 caused a downstream division-by-zero). Validate them at the
# entry boundary instead (audit X4/X6). Pure + module-level so it's unit-tested.

# How long one drain slice may hold the GUI thread. Short enough that a click
# always lands within a frame or two, long enough that the per-slice overhead
# (one text insert, one timer hop) stays negligible against the work.
_POLL_BUDGET_S = 0.015

# …and how many log lines one slice may carry. The budget above bounds the
# *collecting*; writing them costs about four times as much again per line, so
# a slice that collected for its whole budget would spend four more on the
# insert. Whichever limit is reached first ends the slice.
_POLL_MAX_LINES = 500

_PREVIEW_TOKENS = {
    "experiment": "Exp01",
    "well":       "B03",
    "fov":        "F001",
    "timepoint":  "02d04h30m",
    "ignore":     "X",
}


# ---------------------------------------------------------------------------
# AnalyzeTab
# ---------------------------------------------------------------------------
class AnalyzeTab(AnalyzeInputMixin, AnalyzeLogPaneMixin, AnalyzePreflightMixin,
                 AnalyzeProgressMixin, AnalyzeRunMixin, AnalyzeSuspendMixin,
                 AnalyzeTabFormMixin, QWidget):
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

        self._build_ui()
        # Both strips are built hidden and were only ever refreshed by a run
        # ending or the folder changing, so a record written in a previous
        # session had nothing to reveal it: "close All-Well, reopen it
        # tomorrow, and it offers to carry on" never survived the restart it
        # names. Ask once, here, with the form still empty.
        self._refresh_folder_banners(None, immediate=True)
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
        right.setMinimumWidth(self._RIGHT_MIN_W)
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
        edit.setFixedWidth(theme.Metrics.num_field_w_lg)
        edit.textChanged.connect(lambda _: self._refresh_schema_preview())
        rl.addWidget(edit)

        remove_btn = QPushButton("✕", row)
        remove_btn.setFixedWidth(self._REMOVE_BTN_W)   # wide enough that the button padding doesn't clip ✕
        remove_btn.setProperty("variant", "secondary")
        rl.addWidget(remove_btn)

        # Pin each checkbox to a width derived from font metrics + a fixed
        # indicator/spacing allowance, NOT sizeHint(): sizeHint() underreports
        # the label width before the global stylesheet is polished, so the
        # layout reserved too little and the "Phase" indicator painted on top
        # of the "smFISH" label. Minimum policy floors the width at this value.
        def _pin_checkbox(text: str) -> QCheckBox:
            cb = QCheckBox(text, row)
            cb.setMinimumWidth(cb.fontMetrics().horizontalAdvance(text)
                               + theme.Metrics.checkbox_chrome_w)
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

    # Segmentation hints (_segmentation_validation_errors /
    # _collapse_seg_param_stack / _refresh_segmentation_hints) live in
    # analyze_tab_seg_params.AnalyzeSegParamsMixin, beside the parameter panel
    # they drive.

    # ------------------------------------------------------------------
    # Schema helpers
    # ------------------------------------------------------------------
    # The separator / dropdowns / schema-string control is
    # ``widgets.schema_picker.SchemaPicker``, shared with the Datasets pane's
    # Zip Loose Folder page. These read through to it; the preview below is the
    # part that is genuinely Analyze's, since it is drawn from the channel
    # tokens entered further down this same form.
    def _schema_field_list(self) -> list[str]:
        return self._schema_picker.fields()

    def _schema_errors(self) -> list[str]:
        return self._schema_picker.errors()

    def _build_schema_arg(self) -> str:
        return self._schema_picker.schema()

    def _filename_sep(self) -> str:
        return self._schema_picker.separator()

    def _refresh_schema_preview(self) -> None:
        if not hasattr(self, "_schema_picker") or not hasattr(self, "_schema_err_lbl"):
            return
        errors = self._schema_errors()
        fields = self._schema_field_list()
        sep = self._filename_sep()
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
            # A valid schema is necessary, not sufficient. This runs from four
            # controls that stay live during a run — the segmentation-channel
            # edit, every fluorescent row's edit, add/remove row, and the
            # schema picker — so enabling unconditionally re-armed Run while a
            # plate was still going, and pressing it started a second pipeline
            # into the same output directory. `_set_running_ui_state` owns the
            # other half of this flag; both must agree.
            self._run_btn.setEnabled(not self._running)

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

    def _refresh_output(self, *, immediate: bool = False) -> None:
        """Show where the run will read from and write to, and what it will do.

        Every word of this comes from ``plan_input_layout`` — the same function
        ``resolve_input_output`` executes when Run is pressed. The readout used
        to re-implement the layout rules with a looser idea of what counts as
        well content, so a folder holding ``in/Plate1_A1.zip`` displayed
        "using as input" and then failed the run outright.
        """
        raw_str = self._input_edit.text().strip()
        if not raw_str:
            self._output_lbl.setText("—"); self._layout_lbl.setText("")
            self._refresh_folder_banners(None, immediate=immediate)
            return
        # The form's schema goes with it, so the fold-loose readout counts the
        # files the run would actually fold rather than everything with a TIF
        # suffix — the two disagree on a duplicate, a calibration frame, and
        # anything whose well token will not parse.
        plan = plan_input_layout(
            Path(raw_str),
            filename_schema=self._build_schema_arg(),
            filename_sep=self._filename_sep(),
        )
        self._output_lbl.setText(
            str(plan.output_dir) if plan.output_dir is not None else "—")
        self._layout_lbl.setText(plan.message)
        # The same planner that names the output folder is what makes a pre-run
        # report possible at all: it resolves that path without writing
        # anything, so what an earlier run left there can be read while the
        # form is still being filled in.
        self._refresh_folder_banners(plan, immediate=immediate)

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
        # Hand back the raw-log toggle if the previous run's failure opened it
        # and the user has not since made that choice their own.
        self.restore_raw_log()
        self._eta_deadline = None
        self._dashboard.set_eta("ETA")
        self._pipeline_started_at = _time.monotonic()
        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._suspend_btn.setEnabled(True)
        self.refresh_resume_banner()      # hidden while a run is going
        self.suspend_preflight()          # ...and so is the pre-run report
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
    def _clear_log(self) -> None:
        self._log.clear()

    def _on_run_done(self, payload, pending) -> None:
        """The run ended well. Split out of ``_poll_log`` to keep that
        loop under the complexity ratchet — it is the one branch long
        enough to be a paragraph rather than a line."""
        # Everything collected so far goes down before the ending
        # does. A run's last words are written straight through
        # (they are one line each, once per run), so the buffer has
        # to be emptied first or the summary lands above the output
        # it summarises.
        self._log_lines(pending)
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

    def _poll_log(self) -> None:
        """Drain what the run has said, in bounded slices.

        Bounded because this runs on the GUI thread and the queue is fed by a
        subprocess that can talk faster than any painter. The loop used to run
        until the queue reported empty, which on a remote run's segmentation
        phase it does not do for minutes at a time — and while it spins the
        window answers nothing, which is how "Show raw log" came to be
        unclickable exactly when there was most to read. It also feeds *itself*
        (a parsed line puts more events back), so "until empty" was never a
        bound the producer alone could satisfy.

        A slice ends on ``_POLL_BUDGET_S`` and re-arms immediately when there is
        more, so throughput is unchanged while the event loop gets a turn
        between slices. Log lines are collected and written once per slice —
        see :meth:`_log_lines` for why one insert per line is the expensive
        part.
        """
        deadline = _time.monotonic() + _POLL_BUDGET_S
        pending: list[tuple[str, str]] = []
        try:
            while True:
                if _time.monotonic() >= deadline or len(pending) >= _POLL_MAX_LINES:
                    break
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
                        pending.append((payload, classify_log_line(payload)))
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
                    pending.append(
                        (f"[info] Workers: {payload} parallel well(s).\n", "INFO"))
                elif kind == "remote_xfer":
                    # Remote upload/download progress in [0,1]. The dashboard
                    # owns the label so the compute phase can retract it.
                    try:
                        pct = float(payload)
                    except (TypeError, ValueError):
                        pct = 0.0
                    self._dashboard.set_transfer(pct)
                elif kind == "done":
                    self._on_run_done(payload, pending)
                    pending = []
                elif kind == "error":
                    self._log_lines(pending)      # see the "done" branch
                    pending = []
                    self._log_line(payload, "ERROR")
                    # First message only, on both surfaces. The first is the
                    # cause and anything after it is that cause's fallout, so
                    # latching one and painting another would leave the
                    # dashboard headline naming something other than the
                    # message runFinished carries.
                    if self._run_error is None:
                        self._run_error = str(payload)
                        self._surface_run_failure(str(payload))
                elif kind == "finished":
                    self._running = False
                    self._run_btn.setEnabled(True)
                    self._stop_btn.setEnabled(False)
                    self._suspend_btn.setEnabled(False)
                    self._on_run_settled()
                    self._dashboard.set_status("")
                    self._dashboard.set_eta("")
                    self._stop_eta_timer()
                    # C4: show the just-finished run — but only when there IS
                    # one. The card's only test is that pipeline_info.json
                    # exists, and the pipeline writes that before any image
                    # work, so a run that died seconds in rendered a summary
                    # indistinguishable from a successful plate.
                    if self._run_ok:
                        self._refresh_last_run_summary()
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
        if pending:
            self._log_lines(pending)
        if getattr(self, "_alive", True):
            # Straight back round while the queue still holds work — through the
            # event loop, so a click lands between slices rather than after the
            # flood — and at the idle cadence once it is drained.
            more = not self._log_q.empty()
            QTimer.singleShot(0 if more else 80, self._poll_log)

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
