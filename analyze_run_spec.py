"""Analyze-pane run splitting, mirroring ``analyze_input.py``'s precedent.

``AnalyzeTab`` mixes in :class:`AnalyzeRunMixin` for two members that split
"what would be run" from "actually run it": :meth:`_collect_run_spec` turns
the live form into a :class:`services.run_spec.RunSpec`, and :meth:`start_run`
launches that spec on this tab's runner. The split exists so a job queue can
persist a Analyze run's configuration as a plain, JSON-round-trippable
``RunSpec`` and start it later, without depending on the live form having the
same values it had at enqueue time.

``start_run`` never raises and never shows a dialog — it returns an error
string on failure so it stays callable headless (from a queue worker with no
form on screen); the caller decides how to surface the message.
"""

from __future__ import annotations

from pathlib import Path

from services.pipeline_service import find_pipeline_script
from services.run_spec import RunSpec, RunSpecError


class AnalyzeRunMixin:
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
        run_spec = RunSpec.from_json(spec)
        error = self.start_run(run_spec)
        if error is not None:
            self.jobFinished.emit(False, error, "")

    def stop(self) -> None:
        """QueueableController hook: cancel without waiting — callable from
        a queue bridge, which isn't a widget event."""
        self._runner.stop()

    def was_cancelled(self) -> bool:
        """Whether the most recent run's failure was a user-requested Stop
        rather than a genuine failure — both surface as the same kind of
        ``error`` event, so a queue bridge needs this to tell a cancel apart
        from a failure when deciding whether to cascade-block successors."""
        return self._runner.was_stopped
