"""
analyze/tab.py — AnalyzeTab widget (PySide6 port).

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
    QCheckBox, QFileDialog, QLineEdit, QMessageBox, QPushButton,
    QSplitter, QVBoxLayout, QWidget,
)

from analyze.phone_view import AnalyzePhoneView
from services.pipeline_runner import (
    PipelineRunner,
    ProgressTracker,
    classify_log_line,
    is_transient_progress,
)
from ui import theme
from widgets.footer_scroll_area import FooterScrollArea
from core.safe import swallowed
from analyze.bg_map import AnalyzeBackgroundMapMixin
from analyze.channel_rows import AnalyzeChannelRowsMixin
from analyze.domain.input import AnalyzeInputMixin
from analyze.domain.run_log import RunLogFile
from analyze.preflight import AnalyzePreflightMixin
from analyze.progress import AnalyzeProgressMixin
from analyze.run_spec import AnalyzeRunMixin
from analyze.suspend import AnalyzeSuspendMixin
from analyze.log_pane import AnalyzeLogPaneMixin
from analyze.form import (  # form builders (split out)
    AnalyzeTabFormMixin,
    # Entry-boundary numeric validation lives with the fields it reads; re-exported
    # so ``from analyze.tab import _numeric_option_errors`` still resolves.
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

# ---------------------------------------------------------------------------
# AnalyzeTab
# ---------------------------------------------------------------------------
class AnalyzeTab(AnalyzeBackgroundMapMixin, AnalyzeChannelRowsMixin, AnalyzeInputMixin,
                 AnalyzeLogPaneMixin, AnalyzePreflightMixin,
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

    #: Set while a caller is about to change the input path *and* the schema
    #: that reads it, so the readout is computed once, from both. See
    #: :meth:`_browse_input` — every recomputation walks the selected folder.
    _suspend_output_refresh = False

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        on_pipeline_complete: Callable[[Path], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._runner = PipelineRunner()
        self._log_q: queue.Queue[tuple[str, object]] = self._runner.log_q
        # Every line the log pane is given, written to the run's output folder too (analyze.domain.run_log).
        self._run_log = RunLogFile()
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
        scroll = FooterScrollArea()  # the run footer pins under it (analyze/run_footer.py)
        scroll.setWidgetResizable(True)
        # Fixed, not just a minimum: theme.Metrics.rail_w (review/core/app_shell_view.py)
        # is the one width shared by Review's rail, Datasets' sidebar, and this
        # panel, so switching modes never visibly resizes the window. The form
        # is laid out to fit it — the narrow label column and the capped combo
        # widths in analyze/form_chrome.py are that fit, and
        # tests/test_analyze_narrow_sidebar_fit.py holds it with every section
        # open — so a change to either side has to re-check the other. A
        # QSplitter child still respects setFixedWidth's equal min/max, so the
        # handle can't be dragged past it either.
        scroll.setFixedWidth(theme.Metrics.rail_w)
        form_host = QWidget()
        self._form_layout = QVBoxLayout(form_host)
        self._form_layout.setContentsMargins(0, 8, 0, 8)
        self._form_layout.setSpacing(0)
        scroll.setWidget(form_host)
        self._left_sidebar = scroll  # shell left-rail toggle hides/shows this
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 0)

        self._build_form(form_host)
        self._mount_run_footer(scroll)

        # Right — log panel
        right = QWidget()
        right.setMinimumWidth(self._RIGHT_MIN_W)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        self._build_log(right)
        splitter.setSizes([theme.Metrics.rail_w, 600])
        self._phone_view = AnalyzePhoneView(self)  # follows phone layout on its own

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
        """Show the remote-only parameter group only while a remote is selected,
        and enable the dry-run-cleanup checkbox only while auto-delete is on.

        It changes what is *reachable* and never what is *set*. Forcing
        auto-delete back on for Local, and clearing dry-run whenever it was
        disabled, meant a glance at Local silently dropped both: a user who
        ticked "Dry-run cleanup" to see what auto-delete would remove, flicked
        to Local and back, and pressed Run had the per-run remote output
        directory and the cached input deleted for real. The mirror case
        re-armed an auto-delete they had deliberately turned off. The group is
        hidden for a local run anyway, and neither value is read on one —
        ``PipelineRunner`` passes both only down the remote branch, where
        ``verify_and_cleanup`` returns before touching anything for
        ``cleanup_mode="none"`` — so resetting them bought nothing.
        """
        is_remote = self._remote_cb.currentData() is not None
        self._remote_params_widget.setVisible(is_remote)
        self._dry_run_cleanup_cb.setEnabled(
            is_remote and self._cleanup_remote_cb.isChecked())

    def _open_manage_remotes(self) -> None:
        """Open the Manage Remotes dialog and rebuild "Run on" from disk after.

        The dialog is deleted once it closes. Left alive it stayed a child of
        this tab, holding its ``remotes_changed`` connection and every child
        dialog opened inside it — five opens measured five live dialogs.
        """
        # Lazy import to avoid pulling the dialog module into the
        # AnalyzeTab import graph on startup — the dialog imports
        # services.remote_runner which has heavier-weight dependencies.
        from analyze.remote_dialog import ManageRemotesDialog
        dlg = ManageRemotesDialog(self)
        dlg.remotes_changed.connect(self._reload_remotes_combo)
        try:
            dlg.exec()
        finally:
            dlg.deleteLater()

    def _reload_remotes_combo(self) -> None:
        """Refresh the "Run on" combobox from remotes.toml.

        Called after every add / edit / delete in the Manage Remotes dialog —
        and after a *failed* bootstrap, which emits ``remotes_changed`` too.

        The target on screen is what is kept. A named remote that still exists
        is re-selected; "Local", and a remote that has just been deleted, both
        land on Local — which is what this method's own docstring has always
        promised and did not do.

        **The default is a build-time decision and is not re-applied here.**
        ``default_remote_index`` picks the first configured remote when the form
        is built, which is a sensible opening state; a rebuild is not an opening
        state. Re-applying it meant ``None`` — the userData of the ``Local``
        item — was read as "nothing chosen yet" rather than as the target on
        screen, so *any* Manage-Remotes change moved a local run onto an SSH
        host: adding a remote, deleting a different one, or a bootstrap that
        failed, all of which emit ``remotes_changed``. It happened under
        ``blockSignals(True)``, so nothing was emitted and nothing was logged,
        and the next Run rsync'd the whole plate to the cluster.
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
            index = 0           # Local, and the fallback for a deleted remote
            for i in range(1, self._remote_cb.count()):
                if self._remote_cb.itemData(i) == prev:
                    index = i
                    break
            self._remote_cb.setCurrentIndex(index)
        finally:
            self._remote_cb.blockSignals(False)
        # Manually re-fire the dependent-control refresh since we
        # silenced the signal during the rebuild.
        self._refresh_remote_controls()

    # Segmentation hints (_segmentation_validation_errors /
    # _collapse_seg_param_stack / _refresh_segmentation_hints) live in
    # analyze.seg_params.AnalyzeSegParamsMixin, beside the parameter panel
    # they drive.

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
        if not d:
            return
        # The readout is held while the path is set and the folder scan fills
        # the form, and computed once at the end: one walk of the selected
        # folder per click rather than two (ARCHITECTURE, the input-layout
        # section).
        #
        # Both the release and the recomputation are in the `finally`:
        # `_autofill_from_input_scan` returns early on names that do not parse
        # and can raise out of `_apply_pipeline_info`, and either way the
        # readout must be recomputed or the path field names the new folder
        # while every label beside it describes the previous one.
        self._suspend_output_refresh = True
        try:
            self._input_edit.setText(d)
            self._input_edit.setEnabled(True)
            self._autofill_from_input_scan(d)
        finally:
            self._suspend_output_refresh = False
            self._refresh_output()

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

    def _set_running_ui_state(self, *, remote=None) -> None:
        self._running = True
        self._well_total = 0
        self._well_done  = 0
        self._well_repeats = 0
        self._progress_tracker.reset()
        self._dashboard.reset(remote=remote is not None)
        self._reset_run_phase(remote=remote is not None)
        if getattr(self, "_last_run_card", None) is not None:
            self._last_run_card.setVisible(False)  # C4: hide stale summary while running
        # Hand back the raw-log toggle if the previous run's failure opened it
        # and the user has not since made that choice their own.
        self.restore_raw_log()
        self._eta_deadline = None
        self._dashboard.set_eta("ETA")
        self._pipeline_started_at = _time.monotonic()
        self._show_run_buttons(running=True)
        self.refresh_resume_banner()      # hidden while a run is going
        self.suspend_preflight()          # ...and so is the pre-run report
        self._dashboard.set_status("Running…")

    def _expected_well_count(self, opts: dict) -> int:
        # Same layout detection as resolve_input_output (no GUI/service drift).
        from services.input_resolution_service import count_expected_wells
        return count_expected_wells(opts["raw"])

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
        # The headline below is the run's last word; the phase machine stands
        # down so a straggling progress event cannot write over it.
        self._end_run_phase()
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
                f"well(s) processed; {missing} never reported completion{self._partial_note()}")
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
            self._dashboard.set_headline(f"Complete — {n} well(s) processed{self._partial_note()}")
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

    def _close_run_log(self) -> None:
        """The run has ended: name its log file, as the file's last line too, and close it."""
        path = self._run_log.path
        if path is not None:
            self._log_line(f"[info] This run's log is saved as {path}\n", "INFO")
        self._run_log.end()

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
                        if ev_kind == "workers":   # inline: a blocking put from the queue's only reader can deadlock
                            pending.append((f"[info] Workers: {ev_payload} parallel well(s).\n", "INFO"))
                        else:
                            self._apply_progress_event(ev_kind, ev_payload)
                    if not is_transient_progress(payload):
                        pending.append((payload, classify_log_line(payload)))
                elif kind in ("zipper_start", "zipper_well", "zipper_done",
                              "stage_total"):
                    self._apply_grouping_event(kind, payload)
                elif kind == "workers":
                    pending.append(
                        (f"[info] Workers: {payload} parallel well(s).\n", "INFO"))
                elif kind == "remote_xfer":       # ("up"|"down", fraction): see _on_remote_xfer
                    self._on_remote_xfer(payload)
                elif kind in ("done", "suspended"):      # the two clean endings
                    (self._on_run_done if kind == "done" else self._on_run_suspended)(payload, pending)
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
                    self._log_lines(pending)
                    pending = []
                    self._close_run_log()
                    self._running = False
                    self._show_run_buttons(running=False)
                    self._on_run_settled()
                    self._dashboard.finish_run()  # wells still in flight are unfinished; status/ETA cleared
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
                        self.jobFinished.emit(ok, message if not ok else self._detection_note, out_dir)
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
