"""Runs a Validate & Repair scan (and optional repair) off the UI thread.

Same shape as ``datasets.concatenate.worker.ConcatenateWorker`` — a thin
``QThread`` wrapper whose progress/result/errors are marshalled to the UI
through queued signals, so the actual scan/repair logic
(:mod:`~datasets.validate.validate_compute` /
:mod:`~datasets.validate.apply`) stays Qt-free and unit-testable without a Qt
event loop.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from util.aw_safe import swallowed
from datasets.validate.apply import RepairResult, apply_repairs
from datasets.validate.validate_compute import ValidateError, build_report


class ValidateWorker(QThread):
    """Scans a dataset, optionally applies repairs, streaming progress to the UI."""

    logLine = Signal(str)
    succeeded = Signal(object, object)   # (DatasetReport, RepairResult | None)
    failed = Signal(str)

    def __init__(self, root: str, *, apply_repair: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._root = root
        self._apply_repair = apply_repair

    def run(self) -> None:  # noqa: D401 — QThread entry point
        self.logLine.emit("Scanning dataset…")
        try:
            report = build_report(self._root)
        except ValidateError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # pragma: no cover — defensive
            swallowed("validate scan")
            self.failed.emit(f"Unexpected error while scanning: {exc}")
            return

        result: RepairResult | None = None
        if self._apply_repair and report.repairable:
            self.logLine.emit("Applying repairs…")
            try:
                result = apply_repairs(self._root, report)
            except Exception as exc:  # pragma: no cover — defensive
                swallowed("validate repair")
                self.failed.emit(f"Repair failed: {exc}")
                return
            self.logLine.emit(result.summary)
            # Repairing changes the sidecar the report was built from — build
            # a fresh report so the UI reflects what's actually on disk now.
            try:
                report = build_report(self._root)
            except ValidateError as exc:
                self.failed.emit(str(exc))
                return

        self.succeeded.emit(report, result)
