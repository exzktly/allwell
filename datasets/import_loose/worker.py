"""Runs an Import Loose Folder conversion off the UI thread.

Same shape as ``datasets.concatenate.worker.ConcatenateWorker`` /
``datasets.subset.worker.SubsetWorker`` — the wells the scan already found
(``import_loose_compute.scan_folder``, run up front for the preview) are
reported once via ``wellsPlanned`` and then tracked one at a time via
``wellStage``, driving a per-well plate map instead of a plain progress bar.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from util.aw_safe import swallowed
from datasets.import_loose.apply import convert_in_place, convert_to_new_destination
from datasets.import_loose.import_loose_compute import scan_folder


class ImportLooseWorker(QThread):
    """Scans + converts a loose-TIF folder, streaming progress to the UI."""

    logLine = Signal(str)
    wellsPlanned = Signal(list)   # the wells this conversion will actually touch
    wellStage = Signal(str, str)  # (well, "writing"|"done")
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
                )
                dest = ""
            else:
                summary = convert_to_new_destination(
                    self._search_dir, self._dest_root,
                    filename_schema=self._filename_schema,
                    filename_sep=self._filename_sep,
                    log=self.logLine.emit, is_cancelled=self.isInterruptionRequested,
                    progress=self.wellStage.emit,
                )
                dest = self._dest_root
        except ValueError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # pragma: no cover — defensive
            swallowed("import-loose convert")
            self.failed.emit(f"Conversion failed: {exc}")
        else:
            self.succeeded.emit(dest, summary)
