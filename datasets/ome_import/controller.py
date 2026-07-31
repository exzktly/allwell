"""Wires the Convert OME-TIFF page to its background worker.

Owns the :class:`~datasets.ome_import.view.OmeImportPage` and the running
:class:`~datasets.ome_import.worker.OmeImportWorker`, translating the page's
``runRequested`` into a worker launch and the worker's queued signals back into
progress + log. Kept separate from the view so the page stays a pure Qt builder
(CLAUDE.md §3).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict

from PySide6.QtCore import QObject, Signal

from well_viewer.core.safe import swallowed
from datasets.ome_import.ome_import_compute import ConversionParams
from datasets.ome_import.view import OmeImportPage
from datasets.ome_import.worker import OmeImportWorker


class OmeImportController(QObject):
    """Behaviour for the Convert OME-TIFF feature."""

    # QueueableController hook: (ok, message, dest_root — "" for none).
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
        self._worker: OmeImportWorker | None = None
        self._last_result: tuple[bool, str, str] = (False, "", "")

        self.page = OmeImportPage()
        self.page.runRequested.connect(self._on_run)
        self.page.queueRequested.connect(self._on_queue_requested)
        self.page.openInAnalyzeRequested.connect(self._on_open_in_analyze_requested)

    # ── run ──────────────────────────────────────────────────────────────────
    def _on_run(self) -> None:
        self._launch()

    def _launch(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        src, dest = self.page.source_path(), self.page.destination_path()
        if not src or not dest:
            return
        self.page.clear_log()
        self.page.set_progress(0, 0)
        self.page.set_running(True)

        worker = OmeImportWorker(src, dest, self.page.params())
        worker.logLine.connect(self.page.append_log)
        worker.progress.connect(self.page.set_progress)
        worker.succeeded.connect(self._on_succeeded)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        self._worker = worker
        worker.start()

    # ── job queue ────────────────────────────────────────────────────────────
    def collect_spec(self) -> dict:
        params = asdict(self.page.params())
        params["well_order"] = list(params["well_order"])
        return {
            "source": self.page.source_path(),
            "destination": self.page.destination_path(),
            "params": params,
        }

    def _on_queue_requested(self) -> None:
        if self._on_add_to_queue is not None:
            self._on_add_to_queue("ome_import", self.collect_spec())

    def show_job(self, spec: dict) -> None:
        self.page.set_source_path(spec.get("source", ""))
        self.page.set_destination_path(spec.get("destination", ""))
        params = spec.get("params") or {}
        if params:
            self.page.set_params(ConversionParams(**{
                **params, "well_order": tuple(params.get("well_order") or ()),
            }))

    def start_from_queue(self, spec: dict) -> None:
        self.show_job(spec)
        self._launch()

    def stop(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()

    def is_busy(self) -> bool:
        """QueueableController hook — see SubsetController.is_busy."""
        return self._worker is not None and self._worker.isRunning()

    def _on_succeeded(self, dest_root: str, summary: str) -> None:
        self.page.append_log("")
        self.page.append_log(f"✓ {summary}")
        self.page.show_open_in_analyze(dest_root)
        self._last_result = (True, summary, dest_root)

    def _on_failed(self, message: str) -> None:
        self.page.append_log("")
        self.page.append_log(f"✗ {message}")
        self._last_result = (False, message, "")

    def _on_finished(self) -> None:
        self.page.set_running(False)
        self.page.refresh_readout()
        ok, message, dest_root = self._last_result
        try:
            self.jobFinished.emit(ok, message, dest_root)
        except Exception:
            swallowed()

    def _on_open_in_analyze_requested(self, dest_root: str) -> None:
        if self._on_open_in_analyze is not None:
            self._on_open_in_analyze(dest_root)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def shutdown(self) -> None:
        """Stop a running worker before teardown (called on app close)."""
        worker = self._worker
        if worker is None or not worker.isRunning():
            return
        try:
            worker.requestInterruption()
            worker.wait(2000)
        except RuntimeError:
            swallowed("ome-import worker shutdown")
