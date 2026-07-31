"""Runs a Merge Plates merge off the UI thread.

Same shape as ``datasets.concatenate.worker.ConcatenateWorker``.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from util.aw_safe import swallowed
from datasets.merge_plates.apply import execute_plan
from datasets.merge_plates.merge_plates_compute import MergePlatesError, build_plate_merge_plan


class MergePlatesWorker(QThread):
    """Plans and executes a plate merge, streaming progress to the UI."""

    logLine = Signal(str)
    wellsPlanned = Signal(list)
    wellStage = Signal(str, str)   # (dest well, "merging"|"merged")
    succeeded = Signal(str, str)   # (destination root, summary line)
    failed = Signal(str)

    def __init__(self, ds1_root: str, ds2_root: str, dest_root: str, well_map: dict, parent=None) -> None:
        super().__init__(parent)
        self._ds1_root = ds1_root
        self._ds2_root = ds2_root
        self._dest_root = dest_root
        self._well_map = dict(well_map)

    def run(self) -> None:  # noqa: D401 — QThread entry point
        try:
            self.logLine.emit("Validating…")
            plan = build_plate_merge_plan(self._ds1_root, self._ds2_root, self._dest_root, self._well_map)
        except MergePlatesError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # pragma: no cover — defensive
            swallowed("merge plates planning")
            self.failed.emit(f"Unexpected error while planning: {exc}")
            return

        all_wells = sorted(set(plan.wells_ds1) | set(plan.wells_ds2))
        self.wellsPlanned.emit(all_wells)

        try:
            summary = execute_plan(
                plan, self.logLine.emit, self.isInterruptionRequested,
                progress=self.wellStage.emit,
            )
        except MergePlatesError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # pragma: no cover — defensive
            swallowed("merge plates execute")
            self.failed.emit(f"Merge failed: {exc}")
        else:
            self.succeeded.emit(str(plan.dest.root), summary)
