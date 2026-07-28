"""Wires the Concatenate page to its background worker.

Owns the :class:`~datasets.concatenate.view.ConcatenatePage` and the running
:class:`~datasets.concatenate.worker.ConcatenateWorker`, translating the page's
``runRequested`` into a worker launch and the worker's queued signals back into
status-window lines. Kept separate from the view so the page stays a pure Qt
builder (CLAUDE.md §3).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox

from well_viewer.core.safe import swallowed
from datasets.concatenate.concatenate_compute import DatasetPaths, destination_has_marker
from datasets.concatenate.view import ConcatenatePage
from datasets.concatenate.worker import ConcatenateWorker


class ConcatenateController(QObject):
    """Behaviour for the Concatenate Datasets feature."""

    def __init__(
        self,
        *,
        on_open_in_review: Callable[[str], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_open_in_review = on_open_in_review
        self._worker: ConcatenateWorker | None = None

        self.page = ConcatenatePage()
        self.page.runRequested.connect(self._on_run)
        self.page.openInReviewRequested.connect(self._on_open_in_review_requested)

    # ── run ──────────────────────────────────────────────────────────────────
    def _on_run(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        dest_path = self.page.destination_path()
        if dest_path and self._destination_is_restart(dest_path) and not self._confirm_restart(dest_path):
            return
        self.page.clear_log()
        self.page.dashboard.reset()
        self.page.dashboard.set_phase("validate")
        self.page.dashboard.set_headline("Validating datasets…")
        self.page.set_running(True)

        worker = ConcatenateWorker(
            self.page.dataset1_path(),
            self.page.dataset2_path(),
            self.page.destination_path(),
        )
        worker.logLine.connect(self.page.append_log)
        worker.wellsPlanned.connect(self._on_wells_planned)
        worker.wellStage.connect(self.page.dashboard.set_well_stage)
        worker.succeeded.connect(self._on_succeeded)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        self._worker = worker
        worker.start()

    def _on_wells_planned(self, wells: list) -> None:
        self.page.dashboard.set_wells(wells)
        self.page.dashboard.set_phase("merge")
        self.page.dashboard.set_headline(f"Merging {len(wells)} well(s)…")

    def _on_succeeded(self, dest_root: str, summary: str) -> None:
        self.page.append_log("")
        self.page.append_log(f"✓ {summary}")
        self.page.dashboard.set_phase("finalize")
        self.page.dashboard.set_done(summary)
        self.page.show_open_in_review(dest_root)

    def _on_failed(self, message: str) -> None:
        self.page.append_log("")
        self.page.append_log(f"✗ {message}")
        if message.startswith("Cancelled"):
            self.page.dashboard.set_cancelled(message)
        else:
            self.page.dashboard.set_failed(message)

    def _on_finished(self) -> None:
        self.page.set_running(False)
        # Disk state under the destination (and possibly the marker file)
        # just changed — refresh the fields' inline hints so a destination
        # that's now "already holds a finished dataset" explains itself
        # instead of just quietly disabling the button.
        self.page.revalidate_fields()

    def _on_open_in_review_requested(self, dest_root: str) -> None:
        if self._on_open_in_review is not None:
            self._on_open_in_review(dest_root)

    # ── restart confirmation ─────────────────────────────────────────────────
    @staticmethod
    def _destination_is_restart(dest_path: str) -> bool:
        root = Path(dest_path)
        return destination_has_marker(DatasetPaths(root=root, in_dir=root / "in", out_dir=root / "out"))

    def _confirm_restart(self, dest_path: str) -> bool:
        """Destructive-clear confirmation before resuming into a destination
        that holds leftovers from an interrupted attempt (see
        ``concatenate_compute.destination_has_marker`` / ``apply.clear_destination``)."""
        answer = QMessageBox.question(
            self.page,
            "Resume interrupted run?",
            "The destination already has partial output from an earlier "
            f"attempt that didn't finish:\n\n{dest_path}\n\n"
            "Continuing will delete that partial content and rebuild it from "
            "scratch. Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

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
            swallowed("concatenate worker shutdown")
