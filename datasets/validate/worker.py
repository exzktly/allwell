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

from pipeline import completeness
from util.aw_safe import swallowed
from datasets.validate.apply import RepairResult, apply_repairs
from datasets.validate.record import write_record
from datasets.validate.validate_compute import (
    ScanProgress,
    ValidateError,
    WellReport,
    build_report,
    well_health,
)

# What each dataset-health state says in the raw log, per well.
_STATE_WORD = {
    "complete": "complete",
    "missing": "no measurements CSV",
    "no_output": "no output",
}


class ValidateWorker(QThread):
    """Scans a dataset, optionally applies repairs, streaming progress to the UI."""

    logLine = Signal(str)
    # Emitted once per scan, before any well is read, carrying the wells the
    # scan will actually cover — the host seeds its plate map from this so the
    # map shows the run's real scope from the first moment rather than staying
    # blank until the whole scan lands.
    wellsDiscovered = Signal(object)     # tuple[str, ...]
    wellScanned = Signal(object)         # WellReport, as each well finishes
    succeeded = Signal(object, object)   # (DatasetReport, RepairResult | None)
    failed = Signal(str)

    def __init__(self, root: str, *, apply_repair: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._root = root
        self._apply_repair = apply_repair

    def _progress(self) -> ScanProgress:
        """The scan's progress channel.

        ``on_well_done`` is called from `build_report`'s own pool threads (see
        :class:`ScanProgress`), so it must do nothing but emit — a Qt signal
        emitted from a non-GUI thread is delivered as a queued call on the
        receiver's thread, which is exactly the marshalling needed here.
        """
        return ScanProgress(
            on_wells=self.wellsDiscovered.emit,
            on_well_done=self._emit_well_scanned,
        )

    def _emit_well_scanned(self, well_report: WellReport) -> None:
        self.wellScanned.emit(well_report)
        state = well_health(well_report)
        detail = (
            f"{len(well_report.missing)} (fov, timepoint) pair(s) missing"
            if state == "partial" else _STATE_WORD[state]
        )
        self.logLine.emit(f"  {well_report.well}: {detail}")

    def run(self) -> None:  # noqa: D401 — QThread entry point
        self.logLine.emit("Scanning dataset…")
        try:
            report = build_report(self._root, progress=self._progress())
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
            self.logLine.emit("Re-scanning after repair…")
            try:
                report = build_report(self._root, progress=self._progress())
            except ValidateError as exc:
                self.failed.emit(str(exc))
                return

        self._record(report)
        self.succeeded.emit(report, result)

    def _record(self, report) -> None:
        """Write the finished scan back as the dataset's completeness record.

        This is what lets a dataset produced before the end-of-run check
        existed — or by a run launched with the check turned off — populate
        Review's Home page: Home reports a record rather than scanning, and
        without this the tool Home sends the user to would measure the answer
        and then discard it.

        A failure to write is reported in the log and does not fail the scan.
        The scan's own findings are the deliverable and they are already in
        hand; losing the record costs a Home page that still says the dataset
        has not been checked, which is true. Saying so is what makes the two
        outcomes distinguishable — a silent failure here would leave the user
        re-running a scan that appears to do nothing.
        """
        try:
            block = write_record(report)
        except Exception as exc:
            swallowed("validate record")
            self.logLine.emit(
                f"Could not record this scan into pipeline_info.json ({exc}). "
                "The findings below are unaffected, but Review's Home page will "
                "still report this dataset as unchecked."
            )
            return
        totals = block.get("totals") or {}
        self.logLine.emit(
            f"Recorded into pipeline_info.json — {totals.get('complete', 0)} of "
            f"{completeness.plate_total(totals)} well(s) complete. Review's "
            "Home page reports this."
        )
