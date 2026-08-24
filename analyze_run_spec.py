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

from PySide6.QtWidgets import QPushButton, QWidget

from analyze_job_edit import AnalyzeJobEditMixin
from services.pipeline_service import find_pipeline_script
from services.run_spec import RunSpec, RunSpecError
from widgets.flow_layout import FlowLayout


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
        failure (pipeline missing, remote no longer resolvable, a run already
        in flight), or ``None`` once the run has been launched.

        Every refusal is checked *before* any UI state is touched. This used to
        reset the progress tracker, dashboard, ETA and last-run card first and
        then let the runner's re-entrancy refusal escape as a `RuntimeError` —
        so a queue dispatch that raced a manual run wiped that run's readout on
        its way out, and `start_from_queue` never reached its `jobFinished`.
        """
        if pipeline is None:
            pipeline = find_pipeline_script()
        if pipeline is None:
            return "process_microscopy.py not found next to allwell.py."
        # Every entry point answers the same way. This was wired into
        # `start_from_queue` alone, so the Run button — the way almost everyone
        # restarts a plate — never asked, and a well that was mid-flight when
        # the previous run was killed stayed skipped as complete for ever.
        spec = self.resume_aware_spec(spec)
        if self._runner.is_busy:
            return ("A pipeline is already running on the Analyze pane. Wait "
                    "for it to finish, or stop it, before starting another.")
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
            # The run journal's resume payload. This is the one seam that has
            # the whole spec — the Run button, the resume banner and the job
            # queue all pass through here — while the output directory the
            # journal lives in is not resolved until inside the runner's own
            # thread. The same ``to_json`` the queue and the suspend record
            # already persist, so there is no second serialisation to keep in
            # step with the form.
            journal_spec=spec.to_json(),
            # Who owns offering this run back if it does not finish. A run the
            # queue started is already on the queue's own list, and the Analyze
            # banner offering it too let a user resume from the banner while
            # the queue still held the job — the same plate running twice,
            # concurrently, into one output directory.
            queued=bool(getattr(self, "_starting_from_queue", False)),
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

    def _build_run_row(self, parent: QWidget) -> QWidget:
        """The whole run-controls row: Run, Suspend, + Queue, Stop.

        Built here rather than in ``analyze_tab_form.py`` because that file is
        at its line budget and the ratchet only moves down — moving the row out
        wholesale lowers it.

        The layout is a :class:`~widgets.flow_layout.FlowLayout`, not a
        ``QHBoxLayout``. The four buttons want 398px and the panel they sit in
        is ``RAIL_W`` (329) wide and fixed, so a box layout — which cannot wrap
        — would compress them until their labels were unreadable. FlowLayout
        also watches its children for show/hide, which matters here: "+ Queue"
        appears only once the Datasets pane injects a sink, and the row has to
        re-wrap when it does.
        """
        row = QWidget(parent)
        layout = FlowLayout(row, margin=0)
        layout.setContentsMargins(12, 10, 12, 10)

        self._run_btn = QPushButton("▶  Run Pipeline", row)
        self._run_btn.setProperty("variant", "primary")
        self._run_btn.clicked.connect(self._run)
        layout.addWidget(self._run_btn)

        layout.addWidget(self._build_suspend_button(row))
        layout.addWidget(self._build_queue_button(row))

        self._stop_btn = QPushButton("■  Stop", row)
        self._stop_btn.setProperty("variant", "danger")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop)
        layout.addWidget(self._stop_btn)
        return row

    def _build_queue_button(self, parent) -> QPushButton:
        self._queue_btn = QPushButton("+ Queue", parent)
        self._queue_btn.setToolTip(
            "Add this run to the Job Queue instead of running it now. It waits "
            "its turn in Datasets → Job Queue, which runs one job at a time.")
        self._queue_btn.clicked.connect(self._on_queue_requested)
        self._queue_btn.setVisible(getattr(self, "_queue_sink", None) is not None)
        return self._queue_btn

    def _on_queue_requested(self) -> None:
        """Queue the current form, through the same gate as Run.

        "+ Queue" used to hand the sink whatever ``collect_spec`` produced,
        skipping ``_validate_run_request`` entirely — so a run with no input
        folder, an unparseable schema, no fluorescent channels or an
        out-of-range numeric field was accepted without a word and sat in the
        queue looking identical to a valid job. It failed at dequeue time, in
        ``start_from_queue``, which reports through ``jobFinished`` because
        there is no dialog to show it to: the user came back to a queue entry
        marked failed and no statement of what was wrong with it.

        Validating here is not a duplicate of Run's check — it is the same
        check, and it has to be the same one, or the two buttons disagree about
        what a runnable form is.
        """
        sink = getattr(self, "_queue_sink", None)
        if sink is None:
            return
        if self._validate_run_request() is None:
            return              # already reported in a dialog naming the field
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

    #: Set for the duration of :meth:`start_from_queue`, and read by
    #: :meth:`start_run` when it opens the journal. An attribute rather than a
    #: parameter because ``start_run`` is the seam every entry point shares and
    #: threading a flag through it would put the queue's vocabulary into the
    #: live form's call.
    _starting_from_queue = False

    def start_from_queue(self, spec: dict) -> None:
        """Build a RunSpec from the persisted JSON and launch it exactly as
        start_run() would from the live form. A remote that no longer
        resolves, or a missing pipeline script, reports failure through
        jobFinished rather than raising — there is no dialog to show it to."""
        # `start_run` applies `resume_aware_spec` itself now, for every caller.
        self._starting_from_queue = True
        try:
            error = self.start_run(RunSpec.from_json(spec))
        finally:
            self._starting_from_queue = False
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
        input field from a clicked queued row, and by the queue's own dispatch
        gate before it starts the next job.

        Reads the runner's worker thread rather than the pipeline subprocess.
        The subprocess does not exist while a run is resolving directories and
        grouping input, so the narrower answer let the queue dispatch into a
        live run: `start_run` reset the running run's progress UI and then
        raised, leaving `_current` set with no `jobFinished` to come.
        """
        return self._runner.is_busy

    def was_cancelled(self) -> bool:
        """Whether the most recent run's failure was a user-requested Stop
        rather than a genuine failure — both surface as the same kind of
        ``error`` event, so a queue bridge needs this to tell a cancel apart
        from a failure when deciding whether to cascade-block successors."""
        return self._runner.was_stopped
