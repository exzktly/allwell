"""Wires the Subset / Split page to its background worker.

Same shape as ``datasets.concatenate.controller.ConcatenateController``, with
one difference: Subset's page already has an interactive well-plate selector
for choosing the input wells, so this controller paints run progress directly
onto that same widget (``page._plate``) instead of driving a second, separate
status plate — ``SubsetDashboard`` (``PlateProgress(show_plate=False)``)
carries only the headline, overall bar and counts rail.

That difference reaches every kind of per-well progress, not just colour. The
sibling controllers connect their worker's ``wellFraction`` signal straight to
``dashboard.set_well_progress``; here that call would land on a dashboard with
no plate behind it and silently do nothing, so the fractions are routed to
``page._plate.setWellFraction`` instead. Same dial, same rule for what a
non-positive total means (``widgets.plate_progress.fraction_or_none``), drawn
on the plate this page actually shows.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox

from util.aw_safe import swallowed
from util.well_token import canonical_well_label
from widgets.plate_progress import canon_well, fraction_or_none
from datasets._common.dataset_paths import shorten_headline
from datasets.subset.dashboard import well_color, well_tooltip_label
from datasets.subset.subset_compute import (
    DatasetPaths,
    SubsetFilter,
    destination_has_marker,
)
from datasets.subset.view import SubsetPage
from datasets.subset.worker import SubsetWorker


class SubsetController(QObject):
    """Behaviour for the Subset / Split Dataset feature."""

    # QueueableController hook: (ok, message, dest_root — "" for none).
    jobFinished = Signal(bool, str, str)

    def __init__(
        self,
        *,
        on_open_in_review: Callable[[str], None] | None = None,
        on_add_to_queue: Callable[[str, dict], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_open_in_review = on_open_in_review
        self._on_add_to_queue = on_add_to_queue
        self._worker: SubsetWorker | None = None
        self._last_result: tuple[bool, str, str] = (False, "", "")
        self._well_stage: dict[str, str] = {}

        self.page = SubsetPage()
        self.page._plate.setColoredWellsLit(True)
        self.page._plate.setWellTooltipProvider(self._well_tooltip)
        self.page.runRequested.connect(self._on_run)
        self.page.queueRequested.connect(self._on_queue_requested)
        self.page.openInReviewRequested.connect(self._on_open_in_review_requested)
        self.page.sourceChanged.connect(self._on_source_changed)

    def _on_run(self) -> None:
        dest_path = self.page.destination_path()
        if dest_path and self._destination_is_restart(dest_path) and not self._confirm_restart(dest_path):
            return
        self._launch()

    def _launch(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        dest_path = self.page.destination_path()
        wells = frozenset(w for w in (canonical_well_label(w) for w in self.page.selected_wells()) if w)
        tps = None if self.page.all_timepoints_selected() else frozenset(self.page.selected_timepoints())
        fovs = None if self.page.all_fovs_selected() else frozenset(self.page.selected_fovs())
        filt = SubsetFilter(wells=wells or None, timepoint_labels=tps, fovs=fovs)

        self.page.clear_log()
        self.page.dashboard.reset()
        self.page.dashboard.set_headline("Validating…")
        self.page.dashboard.setVisible(True)
        self._well_stage = {}
        self.page._plate.clearWellColors()
        self.page._plate.clearWellMarkers()
        # Dials too, not just colours and markers: a second run that kept the
        # first one's fractions would start with every well already part-full.
        self.page._plate.clearWellFractions()
        # Lock the picker for the duration of the run — it's about to become
        # the progress display for exactly the selection it holds right now.
        self.page._plate.setEnabled(False)
        self.page.set_running(True)

        worker = SubsetWorker(self.page.source_path(), dest_path, filt)
        worker.logLine.connect(self.page.append_log)
        worker.wellsPlanned.connect(self._on_wells_planned)
        worker.wellStage.connect(self._on_well_stage)
        worker.wellFraction.connect(self._on_well_fraction)
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
            "wells": self.page.selected_wells() or None,
            "timepoint_labels": (
                None if self.page.all_timepoints_selected() else self.page.selected_timepoints()
            ),
            "fovs": None if self.page.all_fovs_selected() else self.page.selected_fovs(),
        }

    def _on_queue_requested(self) -> None:
        if self._on_add_to_queue is not None:
            self._on_add_to_queue("subset", self.collect_spec())

    def show_job(self, spec: dict) -> None:
        self.page.set_source_path(spec.get("source", ""))
        self.page.set_destination_path(spec.get("destination", ""))
        self.page.set_selected_wells(spec.get("wells"))
        self.page.set_selected_timepoints(spec.get("timepoint_labels"))
        self.page.set_selected_fovs(spec.get("fovs"))

    def start_from_queue(self, spec: dict) -> None:
        self.show_job(spec)
        self._launch()

    def stop(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()

    def is_busy(self) -> bool:
        """QueueableController hook: whether a run is already in flight —
        checked by QueueBridge.show_job before it repopulates this page from
        a clicked queued row, since show_job() writes fields directly
        (bypassing set_running's field locks, which only block user clicks)
        and this controller is actively painting run progress onto the
        picker plate those fields feed."""
        return self._worker is not None and self._worker.isRunning()

    def _on_source_changed(self) -> None:
        self._well_stage = {}
        self.page._plate.clearWellColors()
        self.page._plate.clearWellMarkers()
        self.page._plate.clearWellFractions()

    def _on_wells_planned(self, wells: list) -> None:
        self.page.dashboard.set_wells(wells)
        self.page.dashboard.set_headline(f"Copying {len(wells)} well(s)…")
        self._well_stage = dict.fromkeys((canon_well(w) for w in wells), "pending")
        for w in self._well_stage:
            self.page._plate.setWellColor(w, well_color("pending"))

    def _on_well_stage(self, well_id: str, stage: str) -> None:
        self.page.dashboard.set_well_stage(well_id, stage)
        w = canon_well(well_id)
        if not w:
            return
        self._well_stage[w] = stage
        self.page._plate.setWellColor(w, well_color(stage))
        # Terminal states carry a marker too, not just colour (accessibility
        # — colour alone is unreadable to anyone who can't distinguish
        # success-green from accent-lilac). A *copying* well carries none: it
        # is drawn as a dial, and a glyph would sit in the middle of exactly
        # the area the dial fills while the fill and the colour already say
        # the well is working. Every plate map in the app follows that rule.
        marker = {"done": "✓", "empty": "!"}.get(stage)
        self.page._plate.setWellMarker(w, marker)
        # Make the terminal states definite rather than trusting the last
        # sub-progress tick to have arrived: a copied well reads as a full
        # circle, and a well nothing matched has no copy progress to describe
        # — a part-filled dial beside its "!" would suggest it is still going.
        if stage == "done":
            self.page._plate.setWellFraction(w, 1.0)
        elif stage == "empty":
            self.page._plate.setWellFraction(w, None)

    def _on_well_fraction(self, well_id: str, done: int, total: int) -> None:
        """Fill *well_id*'s dial on the picker plate to ``done / total``.

        The sibling Datasets controllers hand this signal to their dashboard's
        ``set_well_progress``; ``SubsetDashboard`` has no plate of its own, so
        the fraction goes to the page's picker — the plate this page actually
        shows. See this module's docstring.
        """
        w = canon_well(well_id)
        if not w:
            return
        self.page._plate.setWellFraction(w, fraction_or_none(done, total))

    def _well_tooltip(self, well_id: str) -> str:
        w = canon_well(well_id)
        stage = self._well_stage.get(w)
        if not w or stage is None:
            return ""
        return f"{w}: {well_tooltip_label(stage)}"

    def _on_succeeded(self, dest_root: str, summary: str) -> None:
        self.page.append_log(f"✓ {summary}")
        self.page.dashboard.set_done(shorten_headline(summary, dest_root), tooltip=summary)
        self.page.show_open_in_review(dest_root)
        self._last_result = (True, summary, dest_root)

    def _on_failed(self, message: str) -> None:
        self.page.append_log(f"✗ {message}")
        if message.startswith("Cancelled"):
            self.page.dashboard.set_cancelled(message)
        else:
            self.page.dashboard.set_failed(message)
        self._last_result = (False, message, "")

    def _on_finished(self) -> None:
        self.page.set_running(False)
        self.page._plate.setEnabled(True)
        self.page.revalidate_fields()
        ok, message, dest_root = self._last_result
        try:
            self.jobFinished.emit(ok, message, dest_root)
        except Exception:
            swallowed()

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
