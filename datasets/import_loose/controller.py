"""Wires the Import Loose Folder page to its background worker.

Same shape as ``datasets.ome_import.controller.OmeImportController``.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox

from util.aw_safe import swallowed
from datasets.import_loose.view import ImportLoosePage
from datasets.import_loose.worker import ImportLooseWorker


class ImportLooseController(QObject):
    """Behaviour for the Import Loose Folder feature."""

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
        self._worker: ImportLooseWorker | None = None
        self._last_result: tuple[bool, str, str] = (False, "", "")

        self.page = ImportLoosePage()
        self.page.runRequested.connect(self._on_run)
        self.page.queueRequested.connect(self._on_queue_requested)
        self.page.openInAnalyzeRequested.connect(self._on_open_in_analyze_requested)

    def _on_run(self) -> None:
        if self.page.in_place() and not self._confirm_in_place():
            return
        self._launch()

    def _launch(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self.page.clear_log()
        self.page.set_progress(0, 0)
        self.page.set_running(True)

        worker = ImportLooseWorker(
            self.page.source_path(), self.page.destination_path(),
            filename_schema=self.page.filename_schema(),
            filename_sep=self.page.filename_sep(),
            in_place=self.page.in_place(),
            move_source_tifs=self.page.in_place(),
        )
        worker.logLine.connect(self.page.append_log)
        worker.progress.connect(self.page.set_progress)
        worker.succeeded.connect(self._on_succeeded)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        self._worker = worker
        worker.start()

    # ── job queue ────────────────────────────────────────────────────────────
    def collect_spec(self) -> dict:
        return {
            "source": self.page.source_path(),
            "destination": self.page.destination_path(),
            "in_place": self.page.in_place(),
        }

    def _on_queue_requested(self) -> None:
        if self._on_add_to_queue is not None:
            self._on_add_to_queue("import_loose", self.collect_spec())

    def show_job(self, spec: dict) -> None:
        self.page.set_source_path(spec.get("source", ""))
        self.page.set_destination_path(spec.get("destination", ""))
        self.page.set_in_place(bool(spec.get("in_place", False)))

    def start_from_queue(self, spec: dict) -> None:
        """A queued in-place import proceeds without the interactive
        "this deletes your loose TIFs" confirmation — unattended by
        definition, same trade-off as the other tools' restart confirms."""
        self.show_job(spec)
        self._launch()

    def stop(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()

    def _on_succeeded(self, dest_root: str, summary: str) -> None:
        self.page.append_log(f"✓ {summary}")
        produced = dest_root
        if dest_root:
            self.page.show_open_in_analyze(dest_root)
        elif self.page.in_place():
            produced = self.page.source_path()
            self.page.show_open_in_analyze(produced)
        self._last_result = (True, summary, produced)

    def _on_failed(self, message: str) -> None:
        self.page.append_log(f"✗ {message}")
        self._last_result = (False, message, "")

    def _on_finished(self) -> None:
        self.page.set_running(False)
        self.page.refresh_readout()
        ok, message, dataset_root = self._last_result
        try:
            self.jobFinished.emit(ok, message, dataset_root)
        except Exception:
            swallowed()

    def _on_open_in_analyze_requested(self, dataset_root: str) -> None:
        if self._on_open_in_analyze is not None and dataset_root:
            self._on_open_in_analyze(dataset_root)

    def _confirm_in_place(self) -> bool:
        answer = QMessageBox.question(
            self.page,
            "Group in place?",
            "This will group the loose TIFs directly in "
            f"{self.page.source_path()!r} and delete them once each well is "
            "safely zipped. This can't be undone. Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def shutdown(self) -> None:
        worker = self._worker
        if worker is None or not worker.isRunning():
            return
        try:
            worker.requestInterruption()
            worker.wait(2000)
        except RuntimeError:
            swallowed("import-loose worker shutdown")
