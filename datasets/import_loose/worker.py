"""Runs an Import Loose Folder conversion off the UI thread.

Same shape as ``datasets.concatenate.worker.ConcatenateWorker`` /
``datasets.subset.worker.SubsetWorker`` — the wells the scan already found
(``import_loose_compute.scan_folder``, run up front for the preview) are
reported once via ``wellsPlanned`` and then tracked one at a time via
``wellStage``, driving a per-well plate map instead of a plain progress bar.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from services.subprocess_kill import signal_subprocess
from util.aw_safe import swallowed
from datasets.import_loose.apply import convert_in_place, convert_to_new_destination
from datasets.import_loose.import_loose_compute import scan_folder


class ImportLooseWorker(QThread):
    """Scans + converts a loose-TIF folder, streaming progress to the UI."""

    logLine = Signal(str)
    wellsPlanned = Signal(list)   # the wells this conversion will actually touch
    wellStage = Signal(str, str)  # (well, "writing"|"done")
    # (well, done, total) — sub-progress *within* one well, so its plate cell
    # can render as a filling dial instead of a flat "writing" until the whole
    # well lands. See datasets._common.containers.WellFraction.
    wellFraction = Signal(str, int, int)
    succeeded = Signal(str, str)  # (destination root — "" for in-place, summary)
    failed = Signal(str)

    def __init__(
        self, search_dir: str, dest_root: str, *, filename_schema: str,
        filename_sep: str, in_place: bool, move_source_tifs: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._search_dir = search_dir
        self._dest_root = dest_root
        self._filename_schema = filename_schema
        self._filename_sep = filename_sep
        self._in_place = in_place
        self._move_source_tifs = move_source_tifs
        self._proc = None
        self._proc_lock = threading.Lock()

    def _track_proc(self, proc) -> None:
        with self._proc_lock:
            self._proc = proc

    def request_stop(self) -> None:
        """Signals both the QThread interruption flag (polled between wells
        by convert_to_new_destination's is_cancelled) and the tracked
        pipeline/well_plate_zipper.py subprocess convert_in_place shells out to for its
        in-place mode -- requestInterruption() alone is a no-op while run()
        is blocked inside that subprocess's stdout read loop, since nothing
        in the in-place path ever polls the interruption flag."""
        self.requestInterruption()
        with self._proc_lock:
            proc = self._proc
        signal_subprocess(proc)

    def run(self) -> None:  # noqa: D401 — QThread entry point
        try:
            preview = scan_folder(
                self._search_dir, filename_schema=self._filename_schema,
                filename_sep=self._filename_sep,
            )
        except Exception as exc:  # pragma: no cover — defensive
            swallowed("import-loose scan")
            self.failed.emit(f"Unexpected error while scanning: {exc}")
            return
        if not preview.matched:
            self.failed.emit(
                "No files matched the filename schema — check the schema and "
                "separator against the actual filenames."
            )
            return

        self.wellsPlanned.emit(list(preview.wells))

        try:
            if self._in_place:
                summary = convert_in_place(
                    self._search_dir, filename_schema=self._filename_schema,
                    filename_sep=self._filename_sep,
                    move_source_tifs=self._move_source_tifs,
                    log=self.logLine.emit, progress=self.wellStage.emit,
                    sub_progress=self.wellFraction.emit,
                    proc_hook=self._track_proc,
                )
                dest = ""
            else:
                summary = convert_to_new_destination(
                    self._search_dir, self._dest_root,
                    filename_schema=self._filename_schema,
                    filename_sep=self._filename_sep,
                    log=self.logLine.emit, is_cancelled=self.isInterruptionRequested,
                    progress=self.wellStage.emit,
                    sub_progress=self.wellFraction.emit,
                )
                dest = self._dest_root
        except ValueError as exc:
            self.failed.emit(str(exc))
        except RuntimeError as exc:
            # request_stop() killing the tracked zipper subprocess surfaces
            # here as a nonzero-exit RuntimeError, not a clean cancellation —
            # rewrite it to the "Cancelled" convention every other Datasets
            # worker's failed() message uses for a Stop-driven exit, so
            # QueueBridge._was_cancelled recognises it instead of marking the
            # queued job FAILED (which would refuse a "retry all failed").
            if self._in_place and self.isInterruptionRequested():
                self.failed.emit("Cancelled — the zipper subprocess was stopped.")
            else:
                self.failed.emit(str(exc))
        except Exception as exc:  # pragma: no cover — defensive
            swallowed("import-loose convert")
            self.failed.emit(f"Conversion failed: {exc}")
        else:
            self.succeeded.emit(dest, summary)
