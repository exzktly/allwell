"""Wires the Validate & Repair page to its background worker.

Same shape as ``datasets.concatenate.controller.ConcatenateController``: owns
the page and the running worker, translates the page's requests into a
worker launch and the worker's signals back into dashboard/log updates.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from util.aw_safe import swallowed
from datasets._common.dataset_paths import preview_wells
from datasets.validate.validate_compute import (
    DatasetReport,
    WellReport,
    read_dataset_settings,
    well_health,
    wells_needing_force,
)
from datasets.validate.view import ValidatePage
from datasets.validate.worker import ValidateWorker


class ValidateController(QObject):
    """Behaviour for the Validate & Repair Dataset feature."""

    # QueueableController hook: (ok, message, dataset_root — "" for none).
    jobFinished = Signal(bool, str, str)

    def __init__(
        self,
        *,
        on_open_in_analyze: Callable[..., None] | None = None,
        on_add_to_queue: Callable[[str, dict], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_open_in_analyze = on_open_in_analyze
        self._on_add_to_queue = on_add_to_queue
        self._worker: ValidateWorker | None = None
        self._last_result: tuple[bool, str, str] = (False, "", "")
        # Wells the last report found with images but no measurements CSV —
        # the ones an incremental run skips forever. Carried to the completion
        # handoff as its per-well force list.
        self._force_wells: tuple[str, ...] = ()

        self.page = ValidatePage()
        self.page.checkRequested.connect(self._on_check)
        self.page.applyRepairsRequested.connect(self._on_apply_repairs)
        self.page.completeRunRequested.connect(self._on_complete_run)
        self.page.queueRequested.connect(self._on_queue_requested)

    # ── run ──────────────────────────────────────────────────────────────────
    def _on_check(self) -> None:
        self._run(apply_repair=False)

    def _on_apply_repairs(self) -> None:
        self._run(apply_repair=True)

    def _run(self, *, apply_repair: bool) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        root = self.page.dataset_path()
        if not root:
            # No worker gets started on this path, so _on_finished never
            # fires — emit directly (matching MergePlatesController's
            # remap-failure branch), or a queued job with an empty/corrupted
            # "dataset" spec value would stall the queue's _current forever,
            # with no jobFinished ever arriving to clear it.
            self._last_result = (False, "No dataset folder selected.", "")
            try:
                self.jobFinished.emit(False, "No dataset folder selected.", "")
            except Exception:
                swallowed()
            return
        self.page.clear_log()
        self.page.dashboard.reset()
        # Seeded before the worker starts, not left blank until it finishes.
        # reset() clears the plate's enabled wells, so a scan that takes long
        # enough to watch (every well's input archive opened, every
        # measurements CSV read) used to blank the very plate map
        # _update_status_preview had just painted and leave it empty for the
        # whole run — the page looked like the Check had done nothing. These
        # are the wells the folder holds; the worker's own wellsDiscovered
        # replaces them with the wells the scan actually covers as soon as it
        # knows, which is before it reads a single one.
        self.page.dashboard.set_wells(preview_wells(root))
        self.page.dashboard.set_headline("Scanning…" if not apply_repair else "Repairing…")
        self.page.dashboard.setVisible(True)
        self.page.set_running(True)

        worker = ValidateWorker(root, apply_repair=apply_repair)
        worker.logLine.connect(self.page.append_log)
        worker.wellsDiscovered.connect(self._on_wells_discovered)
        worker.wellScanned.connect(self._on_well_scanned)
        worker.succeeded.connect(self._on_succeeded)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        self._worker = worker
        worker.start()

    # ── job queue ────────────────────────────────────────────────────────────
    def collect_spec(self) -> dict:
        return {"dataset": self.page.dataset_path(), "apply_repair": False}

    def _on_queue_requested(self) -> None:
        if self._on_add_to_queue is not None:
            self._on_add_to_queue("validate", self.collect_spec())

    def show_job(self, spec: dict) -> None:
        self.page.set_dataset_path(spec.get("dataset", ""))

    def start_from_queue(self, spec: dict) -> None:
        self.show_job(spec)
        self._run(apply_repair=bool(spec.get("apply_repair", False)))

    def stop(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()

    def is_busy(self) -> bool:
        """QueueableController hook — see SubsetController.is_busy."""
        return self._worker is not None and self._worker.isRunning()

    # ── live scan progress ───────────────────────────────────────────────────
    def _on_wells_discovered(self, wells) -> None:
        self.page.dashboard.set_wells(list(wells))

    def _on_well_scanned(self, well_report: WellReport) -> None:
        self._paint_well(well_report)

    def _paint_well(self, w: WellReport) -> None:
        self.page.dashboard.set_well_state(
            w.well, well_health(w), missing_count=len(w.missing))

    def _on_succeeded(self, report: DatasetReport, result) -> None:
        # Repainted from the finished report even though the live callbacks
        # have already painted each well: the report is the authority, and a
        # plate that disagreed with the findings list below it would be the
        # exact silent substitution this feature exists to catch.
        self.page.dashboard.set_wells([w.well for w in report.wells])
        for w in report.wells:
            self._paint_well(w)
        if result is not None:
            self.page.append_log(f"✓ {result.summary}")
        # Built from the finding count directly, not report.is_clean --
        # is_clean ignores warning-severity findings (a "no_csv" warning on a
        # well that legitimately segmented zero cells at every timepoint), so
        # a dataset with exactly that one warning used to show a green
        # "Clean — no issues found." headline above the very well the plate
        # painted red and the findings list explained. "Clean" now means what
        # the page's own findings list shows: zero findings, full stop.
        summary = (
            "Clean — no issues found." if not report.findings
            else f"{len(report.findings)} finding(s)."
        )
        # Logged for every run, not only a repair. A plain Check used to write
        # exactly one line ("Scanning dataset…") and nothing else, so the raw
        # log never recorded that the scan had even finished, let alone what
        # it concluded.
        self._force_wells = wells_needing_force(report)
        self.page.append_log(f"✓ {summary}")
        self.page.dashboard.set_done(summary)
        self.page.show_report(report)
        self._last_result = (True, summary, str(report.root))

    def _on_failed(self, message: str) -> None:
        self.page.append_log(f"✗ {message}")
        self.page.dashboard.set_failed(message)
        # A failed re-check would otherwise leave the previous run's findings
        # on screen under the new red "Failed" headline — nothing between two
        # back-to-back runs on the same page clears them. show_failure (not
        # clear_findings) puts the reason where the findings were and hides the
        # stale "Complete this run in Analyze" offer, which otherwise survived
        # from the earlier successful check and pointed at a dataset whose
        # sidecar had just failed to read.
        self.page.show_failure(message)
        self._force_wells = ()
        self._last_result = (False, message, "")

    def _on_finished(self) -> None:
        self.page.set_running(False)
        ok, message, dataset_root = self._last_result
        try:
            self.jobFinished.emit(ok, message, dataset_root)
        except Exception:
            swallowed()

    def _on_complete_run(self, dataset_root: str) -> None:
        """Hand the dataset to Analyze configured to finish what it started.

        Handing over only the folder path was not enough to complete a run, in
        two independent ways. The form kept whatever settings happened to be in
        it, so the schema, separator, channel roles, QC options and — the one
        that silently breaks the done-set lookup — the ``csv_prefix`` all had to
        be re-entered by hand and matched exactly. And a plain re-run *skips*
        any well whose ``<well>_out.zip`` and CSV both exist, however few of its
        FOV-groups actually landed, which is exactly the state this page exists
        to report: pressing Run again did nothing for the very wells it named.

        So the dataset's own recorded sidecar goes with it, and the run is asked
        for FOV-level incremental mode.
        """
        if self._on_open_in_analyze is None or not dataset_root:
            return
        info, problem = read_dataset_settings(dataset_root)
        if problem:
            # Never a silent handoff: Analyze still opens on the folder, but the
            # form keeps whatever was already in it, and a run configured by
            # hand against the wrong csv_prefix or schema reads its done-set
            # from files that don't exist and reprocesses everything. Say so —
            # in the findings list as well as the log, because the log is behind
            # a checkbox that is off by default, so a log-only warning about a
            # half-configured handoff is a warning nobody reads.
            warning = (
                f"⚠ {problem} Analyze opened on the folder, but its settings "
                "were NOT pre-filled — check the schema, channel tokens and CSV "
                "prefix against the original run before pressing Run."
            )
            self.page.append_log(warning)
            self.page.show_note(warning)
        self._on_open_in_analyze(
            dataset_root, info, incremental=True, force_wells=list(self._force_wells))

    # ── lifecycle ────────────────────────────────────────────────────────────
    def shutdown(self) -> None:
        worker = self._worker
        if worker is None or not worker.isRunning():
            return
        try:
            worker.requestInterruption()
            worker.wait(2000)
        except RuntimeError:
            swallowed("validate worker shutdown")
