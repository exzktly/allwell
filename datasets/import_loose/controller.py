"""Wires the Import Loose Folder page to its background worker.

Same shape as ``datasets.ome_import.controller.OmeImportController``.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox

from util.aw_safe import swallowed
from datasets._common.dataset_paths import shorten_headline
from datasets.import_loose.view import ImportLoosePage
from datasets.import_loose.worker import ImportLooseWorker
from datasets._common.tool_controller import DatasetToolController


class ImportLooseController(DatasetToolController):
    """Behaviour for the Import Loose Folder feature."""

    _shutdown_label = "import-loose"

    def _request_worker_stop(self, worker) -> None:
        """This worker blocks inside the zipper subprocess's stdout read
        loop and never polls the interruption flag, so Qt's cooperative
        request is a no-op here; ``request_stop`` also signals the tracked
        child, which would otherwise outlive the app."""
        worker.request_stop()

    def __init__(
        self,
        *,
        on_open_in_analyze: Callable[[str], None] | None = None,
        on_add_to_queue: Callable[[str, dict], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_open_in_analyze = on_open_in_analyze
        self._on_add_to_queue = on_add_to_queue
        self._worker: ImportLooseWorker | None = None
        self._last_result: tuple[bool, str, str] = (False, "", "")
        self._launch_source_root = ""

        self.page = ImportLoosePage()
        self.page.runRequested.connect(self._on_run)
        self.page.queueRequested.connect(self._on_queue_requested)
        self.page.openInAnalyzeRequested.connect(self._on_open_in_analyze_requested)

    def _on_run(self) -> None:
        if self.page.in_place() and not self._confirm_in_place():
            return
        self._launch()

    def _launch(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self.page.clear_log()
        self.page.dashboard.reset()
        self.page.dashboard.set_headline("Scanning…")
        self.page.dashboard.setVisible(True)
        self.page.set_running(True)
        # Captured at launch, not re-read from the page afterward — the
        # source field is locked for the run's duration (ImportLoosePage.set_running),
        # but _on_succeeded still needs the root this specific run actually
        # converted, not whatever the (now-unlocked) field holds once it fires.
        self._launch_source_root = self.page.source_path()

        worker = ImportLooseWorker(
            self._launch_source_root, self.page.destination_path(),
            filename_schema=self.page.filename_schema(),
            filename_sep=self.page.filename_sep(),
            in_place=self.page.in_place(),
            move_source_tifs=self.page.in_place(),
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

    # ── job queue ────────────────────────────────────────────────────────────
    def collect_spec(self) -> dict:
        return {
            "source": self.page.source_path(),
            "destination": self.page.destination_path(),
            "in_place": self.page.in_place(),
            "filename_schema": self.page.filename_schema(),
            "filename_sep": self.page.filename_sep(),
        }

    def _on_queue_requested(self) -> None:
        if self._on_add_to_queue is not None:
            self._on_add_to_queue("import_loose", self.collect_spec())

    def show_job(self, spec: dict) -> None:
        self.page.set_source_path(spec.get("source", ""))
        self.page.set_destination_path(spec.get("destination", ""))
        self.page.set_in_place(bool(spec.get("in_place", False)))
        self.page.set_filename_schema(spec.get("filename_schema", ""))
        self.page.set_filename_sep(spec.get("filename_sep", ""))

    def start_from_queue(self, spec: dict) -> None:
        """A queued in-place import proceeds without the interactive
        "this deletes your loose TIFs" confirmation — unattended by
        definition, same trade-off as the other tools' restart confirms."""
        self.show_job(spec)
        self._launch()

    def _on_wells_planned(self, wells: list) -> None:
        self.page.dashboard.set_wells(wells)
        self.page.dashboard.set_headline(f"Zipping {len(wells)} well(s)…")

    def _on_succeeded(self, dest_root: str, summary: str) -> None:
        self.page.append_log(f"✓ {summary}")
        # In-place mode's dest_root is "" (worker.py) -- shorten_headline
        # skips empty paths, so the launched source root covers that case's
        # own embedded path ("Grouped loose TIFs in <source> into...").
        self.page.dashboard.set_done(
            shorten_headline(summary, dest_root, self._launch_source_root), tooltip=summary)
        produced = dest_root
        if dest_root:
            self.page.show_open_in_analyze(dest_root)
        elif self.page.in_place():
            produced = self._launch_source_root
            self.page.show_open_in_analyze(produced)
            # The loose images in that folder are archives now, so there is
            # nothing left for a second run to group. Tell the page, or the
            # button stays armed and the next click trades this result for a
            # failure.
            self.page.mark_in_place_done()
        self._last_result = (True, summary, produced)

    def _on_failed(self, message: str) -> None:
        self.page.append_log(f"✗ {message}")
        if message.startswith("Cancelled"):
            self.page.dashboard.set_cancelled(message)
        else:
            self.page.dashboard.set_failed(message)
        self._last_result = (False, message, "")

    def _on_finished(self) -> None:
        self.page.set_running(False)
        ok, message, dataset_root = self._last_result
        self.page.revalidate_fields(ok=ok)
        self.page.refresh_readout()
        try:
            self.jobFinished.emit(ok, message, dataset_root)
        except Exception:
            swallowed()

    def _on_open_in_analyze_requested(self, dataset_root: str) -> None:
        if self._on_open_in_analyze is not None and dataset_root:
            self._on_open_in_analyze(dataset_root)

    def _confirm_in_place(self) -> bool:
        """Ask before the one action on this page that destroys anything.

        The page scanned this folder moments ago, so the dialog can say what it
        is about to act on rather than describing the operation in the
        abstract — the counts are the whole difference between "yes, that is my
        plate" and "wait, that is the wrong folder". The path goes on its own
        line (the house shape, ``_common/tool_controller.py``) instead of being
        interpolated mid-sentence with ``!r``, where a long path made one very
        wide line wrapped in quotes.

        The left-alone note matters at exactly this moment: the readout says
        non-matching files are left where they are, and a user consenting to a
        delete should not have to remember that from another part of the page.
        """
        tail = ["The loose originals are deleted once each well is safely "
                "zipped. This can't be undone."]
        note = self.page.unmatched_note()
        if note:
            tail.insert(0, note)
        answer = QMessageBox.question(
            self.page,
            "Group in place?",
            f"{self.page.confirm_counts()}directly in:\n\n"
            f"{self.page.source_path()}\n\n"
            + " ".join(tail),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

