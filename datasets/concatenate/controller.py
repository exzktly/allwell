"""Wires the Concatenate page to its background worker.

Owns the :class:`~datasets.concatenate.view.ConcatenatePage` and the running
:class:`~datasets.concatenate.worker.ConcatenateWorker`, translating the page's
``runRequested`` into a worker launch and the worker's queued signals back into
status-window lines. Kept separate from the view so the page stays a pure Qt
builder (CLAUDE.md §3).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject

from core.safe import swallowed
from datasets.core.dataset_paths import shorten_headline
from datasets.concatenate.concatenate_compute import destination_has_marker
from datasets.concatenate.view import ConcatenatePage
from datasets.concatenate.worker import ConcatenateWorker, UndoAppendWorker
from datasets.concatenate.raw_compute import is_raw_dataset
from datasets.core.tool_controller import DestinationToolController


class ConcatenateController(DestinationToolController):
    """Behaviour for the Concatenate Datasets feature."""

    _shutdown_label = "concatenate"
    # Declared on the class as well as set in __init__: _teardown reads it on
    # app close, and must not fail on a controller whose undo never ran.
    _undo_worker: UndoAppendWorker | None = None
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
        self._undo_worker: UndoAppendWorker | None = None
        self._undo_root, self._undo_mode = "", "undo"

        self.page = ConcatenatePage()
        self.page.runRequested.connect(self._on_run)
        self.page.queueRequested.connect(self._on_queue_requested)
        self.page.openInReviewRequested.connect(self._on_open_in_review_requested)
        self.page.undoAppendRequested.connect(self._on_undo_append_requested)

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

    def _on_undo_append_requested(self, root: str) -> None:
        """Undo an unfinished in-place addition in *root*, after the user confirms.

        Three steps, each asked before it is taken, and each run off the UI
        thread (:class:`~datasets.concatenate.worker.UndoAppendWorker`; an undo
        reads every member's local header). First an ordinary undo, which
        leaves alone a run that may still be going. If that is blocked — a run
        recorded on another computer or by a live process — a second question
        names why before it is undone anyway. If the undo is *refused* because
        an archive is no longer the one journalled, the last question offers to
        keep the archives as they are and discard the notes, and the log then
        says what each archive holds.
        """
        if self.is_busy() or self._undo_busy() or not root:
            return
        if self._ask("Undo the unfinished addition?",
                     f"{root} holds an in-place addition that did not finish.\n\nUndoing it "
                     "puts every archive it touched back exactly as it was before. Continue?"):
            self._start_undo(root, "undo")

    def _ask(self, title: str, text: str) -> bool:
        from PySide6.QtWidgets import QMessageBox

        return QMessageBox.question(self.page, title, text, QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) == QMessageBox.Yes

    def _undo_busy(self) -> bool:
        return self._undo_worker is not None and self._undo_worker.isRunning()

    def _start_undo(self, root: str, mode: str) -> None:
        self._undo_root, self._undo_mode = root, mode
        worker = UndoAppendWorker(root, mode)
        # A bound method of this (GUI-thread) object, so the connection is
        # queued back onto the UI thread.
        worker.done.connect(self._on_undo_done)
        self._undo_worker = worker
        self.page.set_undo_busy(True)
        worker.start()

    def _on_undo_done(self, kind: str, payload) -> None:
        root, mode = self._undo_root, self._undo_mode
        self.page.set_undo_busy(False)
        name = Path(root).name
        if kind == "refused":
            self.page.append_log(f"✗ {payload}")
            if mode != "keep" and self._ask(
                    "Keep the archives as they are?",
                    f"{payload}\n\nKeep every archive exactly as it is now and discard the "
                    "unfinished addition's notes? The log will say which wells hold images added "
                    "since the addition began."):
                self._start_undo(root, "keep")
                return
        else:
            for line in payload.lines:
                self.page.append_log(f"Recovery ({name}): {line}")
            if payload.blocked and mode == "undo" and self._ask(
                    "Undo it anyway?",
                    f"{payload.blocked}\n\nUndo it anyway? Only do this if no other computer or "
                    "program is still adding to this dataset."):
                self._start_undo(root, "force")
                return
            if payload.blocked:
                self.page.append_log(f"✗ {payload.blocked}")
            if mode == "keep":
                self.page.append_log(
                    f"Recovery ({name}): Analyze checks the acquisition record against these "
                    "archives before it runs and names any well they disagree on.")
        self.page.datasets_changed_on_disk()

    def _teardown(self) -> None:
        # The undo worker is this tool's second thread; an app closing under it
        # must not tear the page down while it is still reporting.
        if self._undo_busy():
            self._undo_worker.wait(5000)

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
