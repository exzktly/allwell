"""Suspend and resume for an Analyze run.

Stop kills a run; Suspend winds it down. The difference the user sees is what
survives: after a Stop, whichever wells were in flight are gone and the form
state goes with the window; after a Suspend, every well that had done any work
has written its CSV and its ``<well>_out.zip``, and the app remembers the spec
that produced them. Close All-Well, reopen it tomorrow, and the Analyze pane
offers to carry on.

The resume itself needs no new machinery. ``--incremental`` already reads a
half-finished output directory as its done-set of ``(fov, timepoint)`` groups
and recomputes only what is missing; :class:`services.run_spec.RunSpec` already
round-trips every form field, because the job queue needed exactly that. So this
module is the seam between them: a button that asks the run to wind down and
writes the spec down, and a banner that offers the spec back with incremental
forced on.

Held as a mixin rather than added to ``analyze_tab.py`` for the same reason
``AnalyzeRunMixin`` is — that file sits against its line budget, and this is a
self-contained feature rather than something the tab's other behaviour reaches
into.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QWidget,
)

from services.run_spec import RunSpec, RunSpecError
from services.suspend_record import SuspendedRun, clear, latest, save
from well_viewer.core.safe import swallowed


class AnalyzeSuspendMixin:
    """Suspend / resume behaviour for ``AnalyzeTab``.

    Expects the host to provide ``_runner``, ``_collect_run_spec``,
    ``_log_line``, ``_input_edit``, ``_well_done`` / ``_well_total``,
    ``start_run``, and ``_suspend_btn`` / ``_resume_banner`` widgets.
    """

    # ── suspending ───────────────────────────────────────────────────────
    def _suspend(self) -> None:
        """Record the run, then ask it to wind down.

        The record is written **first**. If the app is killed between the two
        steps the worst case is a stale offer to resume a run that never
        stopped, which the user can decline; the other order risks a run that
        winds down having left nothing behind to resume it with.

        Never lets an exception escape — PySide6 ≥ 6.5 aborts the process on an
        unhandled exception out of a Qt slot, and this one is reached from a
        button the user presses precisely when they want the work kept.
        """
        try:
            if not self._runner.is_running:
                return
            out_dir = self._runner.last_output_dir
            if out_dir is None:
                QMessageBox.warning(
                    self, "Suspend",
                    "This run has not resolved its output folder yet — there is "
                    "nothing to resume from. Use Stop to cancel it.")
                return
            self._write_suspend_record(out_dir)
            self._runner.suspend()
            self._log_line(
                "\n[Suspending] Finishing the FOV-group(s) already in flight, "
                "then writing each well's CSV and out.zip. This can take a "
                "minute. Press Stop to give up on the in-flight work.\n",
                "WARNING",
            )
        except Exception:
            swallowed()  # a failed suspend must not take the window down

    def _write_suspend_record(self, out_dir: Path) -> None:
        """Persist the spec behind the running job, best-effort."""
        try:
            spec = self._collect_run_spec()
        except Exception:
            swallowed()  # a form that cannot be read cannot be resumed
            return
        run = SuspendedRun(
            spec=spec.to_json(),
            input_dir=self._input_edit.text().strip(),
            output_dir=str(out_dir),
            suspended_at=time.time(),
            wells_done=int(getattr(self, "_well_done", 0) or 0),
            wells_total=int(getattr(self, "_well_total", 0) or 0),
        )
        if not save(run):
            self._log_line(
                "[warn] Could not write the resume record; the output folder is "
                "still complete, so re-running this input with 'incremental' "
                "checked will pick up where it left off.\n",
                "WARNING",
            )

    def _build_suspend_button(self, parent: QWidget) -> QPushButton:
        """The Suspend button, built here rather than in the form monolith.

        It sits *before* Stop in the run-controls row because it is the one you
        want in the common case — the run is fine, you just need the machine
        back — and Stop is the escape hatch beside it. Disabled until a run is
        going, in step with Stop.
        """
        self._suspend_btn = QPushButton("⏸  Suspend", parent)
        self._suspend_btn.setEnabled(False)
        self._suspend_btn.setToolTip(
            "Wind the run down at the next field boundary and remember where it "
            "got to. Every well that has done any work keeps its results, and "
            "All-Well offers to carry on — after a restart if you like. Stop, by "
            "contrast, discards whatever is in flight.")
        self._suspend_btn.clicked.connect(self._suspend)
        return self._suspend_btn

    # ── the banner ───────────────────────────────────────────────────────
    def _build_resume_banner(self, parent: QWidget) -> QWidget:
        """A strip above the form offering the most recent suspended run.

        Hidden whenever there is nothing to resume, which is the normal state —
        it costs one row of chrome only when it has something to say.
        """
        banner = QWidget(parent)
        banner.setObjectName("ResumeBanner")
        row = QHBoxLayout(banner)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)
        self._resume_label = QLabel("", banner)
        self._resume_label.setWordWrap(True)
        row.addWidget(self._resume_label, 1)
        resume_btn = QPushButton("Resume", banner)
        resume_btn.setProperty("variant", "primary")
        resume_btn.clicked.connect(self._resume_suspended)
        row.addWidget(resume_btn)
        discard_btn = QPushButton("Discard", banner)
        discard_btn.clicked.connect(self._discard_suspended)
        row.addWidget(discard_btn)
        self._resume_banner = banner
        banner.setVisible(False)
        return banner

    def refresh_resume_banner(self) -> None:
        """Show or hide the banner against what is on disk right now.

        Called at construction and after every run finishes, so a completed
        plate stops offering to resume itself and a freshly suspended one starts.
        """
        banner = getattr(self, "_resume_banner", None)
        if banner is None:
            return
        run = latest()
        if run is None or getattr(self, "_running", False):
            banner.setVisible(False)
            return
        self._resume_label.setText(
            f"A suspended run is waiting: {run.describe()}. Resuming continues "
            f"it — only the field/timepoint groups still missing are computed.")
        banner.setVisible(True)

    def _on_run_settled(self) -> None:
        """Called once a run reaches ``finished``, however it got there.

        A run that completed normally must stop offering to resume itself, so
        its record is dropped. A suspended or stopped one keeps its record: the
        stop case matters as much as the suspend case, because a hard Stop still
        leaves every finished well's CSV on disk and the user may well want to
        carry on from it.
        """
        try:
            out_dir = self._runner.last_output_dir
            completed = bool(getattr(self, "_run_ok", False)) and not (
                self._runner.was_stopped)
            if completed and out_dir is not None:
                clear(str(out_dir))
            self.refresh_resume_banner()
        except Exception:
            swallowed()

    # ── resuming ─────────────────────────────────────────────────────────
    def _resume_suspended(self) -> None:
        """Restart the recorded spec with incremental forced on.

        Incremental is not a preference here, it is what makes the resume a
        resume: without it the pipeline skips any well whose CSV already exists
        and reprocesses nothing, so a plate that was half done would come back
        still half done, with the run reporting success.
        """
        try:
            run = latest()
            if run is None:
                self.refresh_resume_banner()
                return
            spec = RunSpec.from_json(run.spec)
            opts = dict(spec.opts)
            opts["incremental"] = True
            spec = RunSpec(
                opts=opts,
                remote_name=spec.remote_name,
                cleanup_remote=spec.cleanup_remote,
                dry_run_cleanup=spec.dry_run_cleanup,
                output_filter=spec.output_filter,
                selective_channels=spec.selective_channels,
            )
            error = self.start_run(spec)
            if error is not None:
                QMessageBox.critical(self, "Resume Error", error)
                return
            # Only once the run is actually going: a record cleared on a resume
            # that then failed to start would strand the half-finished output.
            clear(run.output_dir)
            self.refresh_resume_banner()
            self._log_line(
                f"\n[Resuming] {run.describe()} — incremental, so only the "
                f"field/timepoint groups missing from the output are computed.\n",
                "INFO",
            )
        except RunSpecError as exc:
            QMessageBox.critical(self, "Resume Error", str(exc))
        except Exception:
            swallowed()

    def _discard_suspended(self) -> None:
        """Forget the record. The output folder itself is left alone."""
        try:
            run = latest()
            if run is None:
                return
            choice = QMessageBox.question(
                self, "Discard suspended run",
                f"Forget the suspended run?\n\n{run.describe()}\n\n"
                f"Nothing is deleted — the results already written to\n"
                f"{run.output_dir}\nstay exactly as they are. You can still "
                f"finish it later by re-running this input with 'incremental' "
                f"checked.",
            )
            if choice == QMessageBox.Yes:
                clear(run.output_dir)
                self.refresh_resume_banner()
        except Exception:
            swallowed()
