"""Wires the Subset / Split page to its background worker.

Same shape as ``datasets.concatenate.controller.ConcatenateController``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox

from util.aw_safe import swallowed
from util.well_token import canonical_well_label
from datasets.subset.subset_compute import (
    DatasetPaths,
    SubsetFilter,
    destination_has_marker,
)
from datasets.subset.view import SubsetPage
from datasets.subset.worker import SubsetWorker


class SubsetController(QObject):
    """Behaviour for the Subset / Split Dataset feature."""

    def __init__(
        self,
        *,
        on_open_in_review: Callable[[str], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_open_in_review = on_open_in_review
        self._worker: SubsetWorker | None = None

        self.page = SubsetPage()
        self.page.runRequested.connect(self._on_run)
        self.page.openInReviewRequested.connect(self._on_open_in_review_requested)

    def _on_run(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        dest_path = self.page.destination_path()
        if dest_path and self._destination_is_restart(dest_path) and not self._confirm_restart(dest_path):
            return

        wells = frozenset(w for w in (canonical_well_label(w) for w in self.page.selected_wells()) if w)
        tps = None if self.page.all_timepoints_selected() else frozenset(self.page.selected_timepoints())
        fovs = None if self.page.all_fovs_selected() else frozenset(self.page.selected_fovs())
        filt = SubsetFilter(wells=wells or None, timepoint_labels=tps, fovs=fovs)

        self.page.clear_log()
        self.page.dashboard.reset()
        self.page.dashboard.set_headline("Validating…")
        self.page.set_running(True)

        worker = SubsetWorker(self.page.source_path(), dest_path, filt)
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
        self.page.dashboard.set_headline(f"Copying {len(wells)} well(s)…")

    def _on_succeeded(self, dest_root: str, summary: str) -> None:
        self.page.append_log(f"✓ {summary}")
        self.page.dashboard.set_done(summary)
        self.page.show_open_in_review(dest_root)

    def _on_failed(self, message: str) -> None:
        self.page.append_log(f"✗ {message}")
        if message.startswith("Cancelled"):
            self.page.dashboard.set_cancelled(message)
        else:
            self.page.dashboard.set_failed(message)

    def _on_finished(self) -> None:
        self.page.set_running(False)
        self.page.revalidate_fields()

    def _on_open_in_review_requested(self, dest_root: str) -> None:
        if self._on_open_in_review is not None:
            self._on_open_in_review(dest_root)

    @staticmethod
    def _destination_is_restart(dest_path: str) -> bool:
        root = Path(dest_path)
        return destination_has_marker(DatasetPaths(root=root, in_dir=root / "in", out_dir=root / "out"))

    def _confirm_restart(self, dest_path: str) -> bool:
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

    def shutdown(self) -> None:
        worker = self._worker
        if worker is None or not worker.isRunning():
            return
        try:
            worker.requestInterruption()
            worker.wait(2000)
        except RuntimeError:
            swallowed("subset worker shutdown")
