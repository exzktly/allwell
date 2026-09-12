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

from PySide6.QtCore import QObject

from core.safe import swallowed
from datasets.ome_import.ome_import_compute import ConversionParams
from datasets.ome_import.view import OmeImportPage
from datasets.ome_import.worker import OmeImportWorker
from datasets._common.tool_controller import DatasetToolController


class OmeImportController(DatasetToolController):
    """Behaviour for the Convert OME-TIFF feature."""

    _shutdown_label = "ome-import"


    def __init__(
        self,
        *,
        on_open_in_analyze: Callable[[str, dict], None] | None = None,
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
            missing = "source file" if not src else "destination folder"
            message = f"No {missing} selected."
            self.page.append_log(f"✗ {message}")
            self.page.set_outcome("failed", message)
            self._last_result = (False, message, "")
            # No worker gets started on this path, so ``_on_finished`` never
            # fires — emit directly, or a queued run would sit marked "running"
            # waiting for a ``jobFinished`` that was never coming, and the whole
            # queue would stop behind it. Validate and Merge Plates already
            # answer this the same way on their own no-worker paths.
            try:
                self.jobFinished.emit(False, message, "")
            except Exception:
                swallowed()
            return
        self.page.clear_log()
        self.page.clear_outcome()
        self.page.set_progress(0, 0)
        # A fresh run starts on an empty plate — a second conversion must not
        # begin wearing the first one's dials.
        self.page.clear_well_fractions()
        self.page.set_running(True)

        worker = OmeImportWorker(src, dest, self.page.params())
        worker.logLine.connect(self.page.append_log)
        worker.progress.connect(self.page.set_progress)
        worker.wellFraction.connect(self.page.set_well_fraction)
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

    def _on_succeeded(self, dest_root: str, summary: str) -> None:
        self.page.append_log("")
        self.page.append_log(f"✓ {summary}")
        self.page.set_outcome("done", summary)
        self.page.show_open_in_analyze(dest_root)
        self._last_result = (True, summary, dest_root)

    def _on_failed(self, message: str) -> None:
        self.page.append_log("")
        self.page.append_log(f"✗ {message}")
        # On the page as well as in the log: the raw log is hidden by default,
        # so a log line on its own is a failure the user never sees. Same split
        # every other Datasets tool makes on its dashboard headline.
        self.page.set_outcome(
            "cancelled" if message.startswith("Cancelled") else "failed", message)
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
            # Hand the channel-role assignment across so Analyze's channel tokens
            # arrive pre-filled instead of retyped.
            self._on_open_in_analyze(dest_root, self.page.channel_tokens_dict())

    # ── lifecycle ────────────────────────────────────────────────────────────
