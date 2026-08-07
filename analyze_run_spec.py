"""Analyze-pane run splitting, mirroring ``analyze_input.py``'s precedent.

``AnalyzeTab`` mixes in :class:`AnalyzeRunMixin` for two members that split
"what would be run" from "actually run it": :meth:`_collect_run_spec` turns
the live form into a :class:`services.run_spec.RunSpec`, and :meth:`start_run`
launches that spec on this tab's runner. The split exists so a job queue can
persist a Analyze run's configuration as a plain, JSON-round-trippable
``RunSpec`` and start it later, without depending on the live form having the
same values it had at enqueue time.

The queue's *edit* flow needs the opposite direction too — a spec written back
into the form — which is :class:`analyze_job_edit.AnalyzeJobEditMixin`, mixed
in here because it is part of the same queue-facing surface. ``show_job`` below
stays a shallow preview; ``hydrate_job`` is the full inverse.

``start_run`` never raises and never shows a dialog — it returns an error
string on failure so it stays callable headless (from a queue worker with no
form on screen); the caller decides how to surface the message.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QPushButton

from analyze_job_edit import AnalyzeJobEditMixin
from services.pipeline_service import find_pipeline_script
from services.run_spec import RunSpec, RunSpecError


class AnalyzeRunMixin(AnalyzeJobEditMixin):
    """Splits run-collection (``_collect_run_spec``) from run-starting
    (``start_run``) so a :class:`RunSpec` can be persisted and started later,
    independent of the form state at collection time."""

    def _collect_run_spec(self) -> RunSpec:
        return RunSpec(
            opts=self._collect_run_options(),
            remote_name=self._remote_cb.currentData(),
            cleanup_remote="all" if self._cleanup_remote_cb.isChecked() else "none",
            dry_run_cleanup=self._dry_run_cleanup_cb.isChecked(),
            output_filter="full",
            selective_channels=True,
        )

    def start_run(self, spec: RunSpec, *, pipeline: Path | None = None) -> str | None:
        """Start ``spec`` on this tab's runner. Returns an error message on
        failure (pipeline missing, remote no longer resolvable), or ``None``
        once the run has been launched."""
        if pipeline is None:
            pipeline = find_pipeline_script()
        if pipeline is None:
            return "process_microscopy.py not found next to allwell.py."
        try:
            remote = spec.resolve_remote()
        except RunSpecError as exc:
            return str(exc)
        self._run_ok = False
        self._run_error = None
        self._set_running_ui_state(remote=remote)
        self._runner.start(
            pipeline,
            spec.runner_opts(),
            resolve_dirs=self._resolve_run_dirs_for_runner,
            remote=remote,
            output_filter=spec.output_filter,
            cleanup_remote=spec.cleanup_remote,
            dry_run_cleanup=spec.dry_run_cleanup,
            selective_channels=spec.selective_channels,
        )
        return None

    # ── job queue (QueueableController hooks) ───────────────────────────────
    def set_queue_sink(self, sink) -> None:
        """Where "+ Queue" sends a run. Injected by the Datasets pane, which
        owns the queue — so this module never imports it, exactly as the queue
        never imports ``analyze_tab``. Left unset when the pane is not in the
        build, and the button hides itself rather than failing on click."""
        self._queue_sink = sink
        btn = getattr(self, "_queue_btn", None)
        if btn is not None:
            btn.setVisible(sink is not None)

    def _build_run_row_extras(self, layout, parent) -> None:
        """Suspend and "+ Queue", added to the run-controls row.

        Both live outside ``analyze_tab_form.py`` because that file is at its
        line budget and the ratchet only moves down; this takes the one line
        the suspend button already had, so the budgeted file does not grow.
        """
        layout.addWidget(self._build_suspend_button(parent))
        self._queue_btn = QPushButton("+ Queue", parent)
        self._queue_btn.setToolTip(
            "Add this run to the Job Queue instead of running it now. It waits "
            "its turn in Datasets → Job Queue, which runs one job at a time.")
        self._queue_btn.clicked.connect(self._on_queue_requested)
        self._queue_btn.setVisible(getattr(self, "_queue_sink", None) is not None)
        layout.addWidget(self._queue_btn)

    def _on_queue_requested(self) -> None:
        sink = getattr(self, "_queue_sink", None)
        if sink is not None:
            sink(self.collect_spec())

    def collect_spec(self) -> dict:
        """The current form's run, as a job queue's JSON spec."""
        return self._collect_run_spec().to_json()

    def show_job(self, spec: dict) -> None:
        """Populate the input field so switching to this tab shows what a
        queued job will run against, without starting it. Doesn't hydrate
        every form field — the queue re-derives a full RunSpec from the
        persisted JSON at dequeue time regardless, so the live form only
        needs to look right, not be authoritative."""
        raw = (spec.get("opts") or {}).get("raw")
        if raw and hasattr(self, "_input_edit"):
            self._input_edit.setText(str(raw))

    def start_from_queue(self, spec: dict) -> None:
        """Build a RunSpec from the persisted JSON and launch it exactly as
        start_run() would from the live form. A remote that no longer
        resolves, or a missing pipeline script, reports failure through
        jobFinished rather than raising — there is no dialog to show it to."""
        run_spec = self.resume_aware_spec(RunSpec.from_json(spec))
        error = self.start_run(run_spec)
        if error is not None:
            self.jobFinished.emit(False, error, "")

    def stop(self) -> None:
        """QueueableController hook: cancel without waiting — callable from
        a queue bridge, which isn't a widget event."""
        self._runner.stop()

    def suspend(self) -> None:
        """QueueableController hook: wind down rather than cancel.

        Optional on the protocol — the queue probes for it and falls back to
        ``stop`` for the Datasets tools, which have no equivalent boundary to
        stop at. ``AnalyzeSuspendMixin._suspend`` is the button's path and also
        writes the resume record; this is the queue's, where the record is not
        needed because the queue is itself the record.
        """
        self._runner.suspend()

    def was_suspended(self) -> bool:
        """Whether the most recent run was wound down rather than cancelled.

        The queue reads this to tell a suspend apart from a Stop when the user
        clicked Suspend on the Analyze page rather than in the queue.
        """
        return self._runner.was_suspended

    def is_busy(self) -> bool:
        """QueueableController hook: whether a pipeline run is already in
        flight — checked by QueueBridge.show_job before it repopulates the
        input field from a clicked queued row."""
        return self._runner.is_running

    def was_cancelled(self) -> bool:
        """Whether the most recent run's failure was a user-requested Stop
        rather than a genuine failure — both surface as the same kind of
        ``error`` event, so a queue bridge needs this to tell a cancel apart
        from a failure when deciding whether to cascade-block successors."""
        return self._runner.was_stopped
