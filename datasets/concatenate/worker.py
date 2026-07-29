"""Runs a Concatenate Datasets merge off the UI thread.

A thin ``QThread`` wrapper: planning (:func:`build_merge_plan`) and execution
(:func:`~datasets.concatenate.apply.execute_plan`) both run inside :meth:`run`,
and their progress / result / errors are marshalled back to the UI through queued
signals — the only safe way to reach the UI thread from a worker, which has no
event loop of its own (CLAUDE.md §5). All the actual file-merging logic lives in
the Qt-free :mod:`~datasets.concatenate.apply` module so it can be unit-tested
without a Qt event loop.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from util.aw_safe import swallowed
from datasets.concatenate.apply import execute_plan
from datasets.concatenate.concatenate_compute import ConcatError, build_merge_plan


class ConcatenateWorker(QThread):
    """Plans and executes a concatenation, streaming progress to the UI."""

    logLine = Signal(str)          # one line for the status window
    wellsPlanned = Signal(list)    # the wells this merge will actually touch
    wellStage = Signal(str, str)   # (well, "merging"|"merged"|"warned")
    succeeded = Signal(str, str)   # (destination root, summary line)
    failed = Signal(str)           # user-facing error message

    def __init__(self, ds1_root: str, ds2_root: str, dest_root: str, parent=None) -> None:
        super().__init__(parent)
        self._ds1_root = ds1_root
        self._ds2_root = ds2_root
        self._dest_root = dest_root

    def run(self) -> None:  # noqa: D401 — QThread entry point
        try:
            self.logLine.emit("Validating datasets…")
            plan = build_merge_plan(self._ds1_root, self._ds2_root, self._dest_root)
        except ConcatError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # pragma: no cover — defensive
            swallowed("concatenate planning")
            self.failed.emit(f"Unexpected error while planning: {exc}")
            return

        all_wells = sorted(set(plan.wells_ds1_only) | set(plan.wells_ds2_only) | set(plan.wells_both))
        self.wellsPlanned.emit(all_wells)

        try:
            summary = execute_plan(
                plan, self.logLine.emit, self.isInterruptionRequested,
                progress=self.wellStage.emit,
            )
        except ConcatError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # pragma: no cover — defensive
            swallowed("concatenate execute")
            self.failed.emit(f"Concatenation failed: {exc}")
        else:
            self.succeeded.emit(str(plan.dest.root), summary)
