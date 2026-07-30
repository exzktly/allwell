"""Wires the Validate & Repair page to its background worker.

Same shape as ``datasets.concatenate.controller.ConcatenateController``: owns
the page and the running worker, translates the page's requests into a
worker launch and the worker's signals back into dashboard/log updates.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from util.aw_safe import swallowed
from datasets.validate.validate_compute import DatasetReport
from datasets.validate.view import ValidatePage
from datasets.validate.worker import ValidateWorker


class ValidateController(QObject):
    """Behaviour for the Validate & Repair Dataset feature."""

    # QueueableController hook: (ok, message, dataset_root — "" for none).
    jobFinished = Signal(bool, str, str)

    def __init__(
        self,
        *,
        on_open_in_analyze: Callable[[str], None] | None = None,
        on_add_to_queue: Callable[[str, dict], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_open_in_analyze = on_open_in_analyze
        self._on_add_to_queue = on_add_to_queue
        self._worker: ValidateWorker | None = None
        self._last_result: tuple[bool, str, str] = (False, "", "")

        self.page = ValidatePage()
        self.page.checkRequested.connect(self._on_check)
        self.page.applyRepairsRequested.connect(self._on_apply_repairs)
        self.page.completeRunRequested.connect(self._on_complete_run)
        self.page.queueRequested.connect(self._on_queue_requested)

    # ── run ──────────────────────────────────────────────────────────────────
    def _on_check(self) -> None:
        self._run(apply_repair=False)

    def _on_apply_repairs(self) -> None:
        self._run(apply_repair=True)

    def _run(self, *, apply_repair: bool) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        root = self.page.dataset_path()
        if not root:
            return
        self.page.clear_log()
        self.page.dashboard.reset()
        self.page.dashboard.set_headline("Scanning…" if not apply_repair else "Repairing…")
        self.page.dashboard.setVisible(True)
        self.page.set_running(True)

        worker = ValidateWorker(root, apply_repair=apply_repair)
        worker.logLine.connect(self.page.append_log)
        worker.succeeded.connect(self._on_succeeded)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        self._worker = worker
        worker.start()

    # ── job queue ────────────────────────────────────────────────────────────
    def collect_spec(self) -> dict:
        return {"dataset": self.page.dataset_path(), "apply_repair": False}

    def _on_queue_requested(self) -> None:
        if self._on_add_to_queue is not None:
            self._on_add_to_queue("validate", self.collect_spec())

    def show_job(self, spec: dict) -> None:
        self.page.set_dataset_path(spec.get("dataset", ""))

    def start_from_queue(self, spec: dict) -> None:
        self.show_job(spec)
        self._run(apply_repair=bool(spec.get("apply_repair", False)))

    def stop(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()

    def _on_succeeded(self, report: DatasetReport, result) -> None:
        self.page.dashboard.set_wells([w.well for w in report.wells])
        for w in report.wells:
            if w.missing:
                state = "partial"
            elif w.csv_state == "ok":
                state = "complete"
            elif w.has_in_container or w.has_out_zip:
                state = "missing"
            else:
                state = "no_output"
            self.page.dashboard.set_well_state(w.well, state, missing_count=len(w.missing))
        if result is not None:
            self.page.append_log(f"✓ {result.summary}")
        summary = "Clean — no issues found." if report.is_clean else (
            f"{len(report.findings)} finding(s)."
        )
        self.page.dashboard.set_done(summary)
        self.page.show_report(report)
        self._last_result = (True, summary, self.page.dataset_path())

    def _on_failed(self, message: str) -> None:
        self.page.append_log(f"✗ {message}")
        self.page.dashboard.set_failed(message)
        self._last_result = (False, message, "")

    def _on_finished(self) -> None:
        self.page.set_running(False)
        ok, message, dataset_root = self._last_result
        try:
            self.jobFinished.emit(ok, message, dataset_root)
        except Exception:
            swallowed()

    def _on_complete_run(self, dataset_root: str) -> None:
        if self._on_open_in_analyze is not None and dataset_root:
            self._on_open_in_analyze(dataset_root)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def shutdown(self) -> None:
        worker = self._worker
        if worker is None or not worker.isRunning():
            return
        try:
            worker.requestInterruption()
            worker.wait(2000)
        except RuntimeError:
            swallowed("validate worker shutdown")
