"""Wires the Concatenate page to its background worker.

Owns the :class:`~datasets.concatenate.view.ConcatenatePage` and the running
:class:`~datasets.concatenate.worker.ConcatenateWorker`, translating the page's
``runRequested`` into a worker launch and the worker's queued signals back into
status-window lines. Kept separate from the view so the page stays a pure Qt
builder (CLAUDE.md §3).
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject

from core.safe import swallowed
from datasets.core.dataset_paths import shorten_headline
from datasets.concatenate.concatenate_compute import destination_has_marker
from datasets.concatenate.view import ConcatenatePage
from datasets.concatenate.worker import ConcatenateWorker
from datasets.concatenate.raw_compute import is_raw_dataset
from datasets.core.tool_controller import DestinationToolController


class ConcatenateController(DestinationToolController):
    """Behaviour for the Concatenate Datasets feature."""

    _shutdown_label = "concatenate"
    _destination_has_marker = staticmethod(destination_has_marker)


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
        self._worker: ConcatenateWorker | None = None
        self._last_result: tuple[bool, str, str] = (False, "", "")

        self.page = ConcatenatePage()
        self.page.runRequested.connect(self._on_run)
        self.page.queueRequested.connect(self._on_queue_requested)
        self.page.openInReviewRequested.connect(self._on_open_in_review_requested)

    # ── run ──────────────────────────────────────────────────────────────────
    def _launch(self) -> None:
        if self._worker is not None and self._worker.isRunning():
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
            gap_text=self.page.gap_text_if_entered(),
            in_place=self.page.in_place(),
        )
        worker.logLine.connect(self.page.append_log)
        worker.wellsPlanned.connect(self._on_wells_planned)
        worker.wellStage.connect(self.page.dashboard.set_well_stage)
        worker.wellFraction.connect(self.page.dashboard.set_well_progress)
        worker.succeeded.connect(self._on_succeeded)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        self._worker = worker
        worker.start()

    def _on_run(self) -> None:
        # In place there is no destination folder, so nothing to resume into
        # and nothing to confirm: dataset 1 itself is the target, and the run
        # is journalled rather than staged (datasets.concatenate.in_place).
        if self.page.in_place():
            self._launch()
            return
        super()._on_run()

    # ── job queue ────────────────────────────────────────────────────────────
    def collect_spec(self) -> dict:
        return {
            "dataset1": self.page.dataset1_path(),
            "dataset2": self.page.dataset2_path(),
            "destination": self.page.destination_path(),
            # The text only when the user changed it: a queued run that left
            # the pre-filled default alone should take whatever the default
            # is when it runs, as a run started now would.
            "gap": self.page.gap_text_if_entered(),
            # Add dataset 2 to dataset 1 in place rather than writing the
            # destination; absent in a spec queued before the option existed.
            "in_place": self.page.in_place(),
        }

    def _on_queue_requested(self) -> None:
        if self._on_add_to_queue is not None:
            self._on_add_to_queue("concatenate", self.collect_spec())

    def show_job(self, spec: dict) -> None:
        self.page.set_dataset1_path(spec.get("dataset1", ""))
        self.page.set_dataset2_path(spec.get("dataset2", ""))
        self.page.set_destination_path(spec.get("destination", ""))
        self.page.set_gap_text(spec.get("gap", ""))
        self.page.set_in_place(bool(spec.get("in_place", False)))

    def start_from_queue(self, spec: dict) -> None:
        """A queued run is unattended by definition, so a destination that
        needs the restart confirmation proceeds without asking — there is
        no user to ask."""
        self.show_job(spec)
        self._launch()

    def _on_wells_planned(self, wells: list) -> None:
        self.page.dashboard.set_wells(wells)
        self.page.dashboard.set_phase("merge")
        self.page.dashboard.set_headline(f"Merging {len(wells)} well(s)…")

    def _on_succeeded(self, dest_root: str, summary: str) -> None:
        self.page.append_log("")
        self.page.append_log(f"✓ {summary}")
        self.page.dashboard.set_phase("finalize")
        # The dashboard headline gets a basename, not the full destination
        # path buried in a sentence — the log line above (and the tooltip)
        # keeps the untouched original.
        self.page.dashboard.set_done(shorten_headline(summary, dest_root), tooltip=summary)
        if is_raw_dataset(dest_root):
            # Nothing for Review to open yet: a raw plate is analysed first.
            self.page.append_log("Raw images only — analyse this folder in the Analyze tab.")
        else:
            self.page.show_open_in_review(dest_root)
        self._last_result = (True, summary, dest_root)

    def _on_failed(self, message: str) -> None:
        self.page.append_log("")
        self.page.append_log(f"✗ {message}")
        if message.startswith("Cancelled"):
            self.page.dashboard.set_cancelled(message)
        else:
            self.page.dashboard.set_failed(message)
        self._last_result = (False, message, "")

    def _on_finished(self) -> None:
        self.page.set_running(False)
        ok, message, dest_root = self._last_result
        # Disk state under the destination (and possibly the marker file)
        # just changed — refresh the fields' inline hints so a destination
        # that's now "already holds a finished dataset" explains itself
        # instead of just quietly disabling the button. ok=True swaps that
        # explanation for a neutral post-run note (ConcatenatePage.revalidate_fields).
        self.page.revalidate_fields(ok=ok)
        # Last statement, by construction — mirrors AnalyzeTab.runFinished's
        # re-entrancy rule: a queue bridge slot connected to this may launch
        # the next job synchronously, so nothing here may run after it.
        try:
            self.jobFinished.emit(ok, message, dest_root)
        except Exception:
            swallowed()

    # ── restart confirmation ─────────────────────────────────────────────────
    # ── lifecycle ────────────────────────────────────────────────────────────
