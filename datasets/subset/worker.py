"""Runs a Subset / Split off the UI thread.

Same shape as ``datasets.concatenate.worker.ConcatenateWorker``.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from util.aw_safe import swallowed
from datasets.subset.apply import execute_plan
from datasets.subset.subset_compute import SubsetError, SubsetFilter, build_subset_plan


class SubsetWorker(QThread):
    """Plans and executes a subset, streaming progress to the UI."""

    logLine = Signal(str)
    wellsPlanned = Signal(list)
    wellStage = Signal(str, str)   # (well, "pending"|"copying"|"done"|"empty")
    succeeded = Signal(str, str)   # (destination root, summary line)
    failed = Signal(str)

    def __init__(self, src_root: str, dest_root: str, filt: SubsetFilter, parent=None) -> None:
        super().__init__(parent)
        self._src_root = src_root
        self._dest_root = dest_root
        self._filt = filt

    def run(self) -> None:  # noqa: D401 — QThread entry point
        try:
            self.logLine.emit("Validating…")
            plan = build_subset_plan(self._src_root, self._dest_root, self._filt)
        except SubsetError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # pragma: no cover — defensive
            swallowed("subset planning")
            self.failed.emit(f"Unexpected error while planning: {exc}")
            return

        self.wellsPlanned.emit(list(plan.wells_kept))

        try:
            summary = execute_plan(
                plan, self.logLine.emit, self.isInterruptionRequested,
                progress=self.wellStage.emit,
            )
        except SubsetError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # pragma: no cover — defensive
            swallowed("subset execute")
            self.failed.emit(f"Subset failed: {exc}")
        else:
            self.succeeded.emit(str(plan.dest.root), summary)
