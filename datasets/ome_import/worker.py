"""Runs an OME-TIFF conversion off the UI thread.

A thin ``QThread`` wrapper: planning (:func:`plan_conversion`) and execution
(:func:`~datasets.ome_import.apply.convert`) run inside :meth:`run`, and their
progress / result / errors are marshalled back to the UI through queued signals —
the only safe way to reach the UI thread from a worker, which has no event loop
of its own (CLAUDE.md §5). The pixel work lives in the Qt-free
``pipeline.ome_explode`` / ``datasets.ome_import.apply`` modules.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from util.aw_safe import swallowed
from datasets.ome_import.apply import convert
from datasets.ome_import.ome_import_compute import (
    ConversionParams,
    OmeImportError,
    plan_conversion,
    read_layout,
)
from pipeline.ome_explode import OmeExplodeCancelled


class OmeImportWorker(QThread):
    """Plans and executes a conversion, streaming progress to the UI."""

    logLine = Signal(str)          # one line for the status window
    progress = Signal(int, int)    # (planes_written, total_planes)
    succeeded = Signal(str, str)   # (destination root, summary line)
    failed = Signal(str)           # user-facing error message

    def __init__(
        self, src: str, dest: str, params: ConversionParams, parent=None,
    ) -> None:
        super().__init__(parent)
        self._src = src
        self._dest = dest
        self._params = params

    def run(self) -> None:  # noqa: D401 — QThread entry point
        try:
            self.logLine.emit("Reading OME metadata…")
            layout = read_layout(self._src)
            plan_conversion(layout, self._params)  # validate before writing
        except OmeImportError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # pragma: no cover — defensive
            swallowed("ome-import planning")
            self.failed.emit(f"Unexpected error while planning: {exc}")
            return

        try:
            summary = convert(
                self._src, self._dest, self._params,
                log=self.logLine.emit,
                is_cancelled=self.isInterruptionRequested,
                progress=self.progress.emit,
            )
        except OmeExplodeCancelled:
            self.failed.emit(
                "Cancelled — partial output was left in the destination; "
                "re-running overwrites it."
            )
        except Exception as exc:  # pragma: no cover — defensive
            swallowed("ome-import convert")
            self.failed.emit(f"Conversion failed: {exc}")
        else:
            self.succeeded.emit(str(Path(self._dest)), summary)
