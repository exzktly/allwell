"""Wires the Merge Plates page to its background worker.

Same shape as ``datasets.concatenate.controller.ConcatenateController``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox

from util.aw_safe import swallowed
from datasets.merge_plates.merge_plates_compute import (
    DatasetPaths,
    MergePlatesError,
    block_shift_well_map,
    dataset_wells,
    destination_has_marker,
    resolve_dataset,
)
from datasets.merge_plates.view import MergePlatesPage
from datasets.merge_plates.worker import MergePlatesWorker


class MergePlatesController(QObject):
    """Behaviour for the Merge Plates feature."""

    def __init__(
        self,
        *,
        on_open_in_review: Callable[[str], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_open_in_review = on_open_in_review
        self._worker: MergePlatesWorker | None = None
        self._last_well_map: dict[str, str] = {}

        self.page = MergePlatesPage()
        self.page.runRequested.connect(self._on_run)
        self.page.openInReviewRequested.connect(self._on_open_in_review_requested)

    def _on_run(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        dest_path = self.page.destination_path()
        if dest_path and self._destination_is_restart(dest_path) and not self._confirm_restart(dest_path):
            return

        well_map: dict[str, str] = {}
        if self.page.remap_enabled():
            try:
                ds2 = resolve_dataset(self.page.dataset2_path(), label="dataset 2")
                wells2 = dataset_wells(ds2)
                well_map = block_shift_well_map(wells2, target_origin=self.page.remap_origin())
            except MergePlatesError as exc:
                self.page.append_log(f"✗ {exc}")
                self.page.dashboard.set_failed(str(exc))
                return

        self.page.clear_log()
        self.page.dashboard.reset()
        self.page.dashboard.set_headline("Validating…")
        self.page.set_running(True)

        worker = MergePlatesWorker(
            self.page.dataset1_path(), self.page.dataset2_path(), dest_path, well_map,
        )
        worker.logLine.connect(self.page.append_log)
        worker.wellsPlanned.connect(self._on_wells_planned)
        worker.wellStage.connect(self.page.dashboard.set_well_stage)
        worker.succeeded.connect(self._on_succeeded)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        self._worker = worker
        self._last_well_map = well_map
        worker.start()

    def _on_wells_planned(self, wells: list) -> None:
        # Distinguish origin using the well map applied for this run: any
        # well that is a remap *target* came from dataset 2, as did any
        # dataset-2 well kept at its own (unmapped) position — the dashboard
        # only needs the two source lists, which the worker doesn't expose
        # directly, so re-derive them from the datasets on disk.
        try:
            ds1 = resolve_dataset(self.page.dataset1_path(), label="dataset 1")
            ds2 = resolve_dataset(self.page.dataset2_path(), label="dataset 2")
            wells1 = dataset_wells(ds1)
            wells2_src = dataset_wells(ds2)
            wells2_dest = {self._last_well_map.get(w, w) for w in wells2_src}
        except MergePlatesError:
            wells1, wells2_dest = set(wells), set()
        self.page.dashboard.set_wells(wells1, wells2_dest)
        self.page.dashboard.set_headline(f"Merging {len(wells)} well(s)…")

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
            swallowed("merge plates worker shutdown")
