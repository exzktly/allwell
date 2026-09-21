"""Runs a CZI conversion off the UI thread.

A thin ``QThread`` wrapper: planning (:func:`plan_conversion`) and execution
(:func:`~datasets.czi_import.apply.convert`) run inside :meth:`run`, and their
progress / result / errors are marshalled back to the UI through queued signals
— the only safe way to reach the UI thread from a worker, which has no event
loop of its own (CLAUDE.md §5). The pixel work lives in the Qt-free
``pipeline.czi_explode`` / ``datasets.czi_import.apply`` modules.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from util.aw_safe import swallowed
from datasets.czi_import.apply import convert
from datasets.czi_import.czi_import_compute import (
    ConversionParams,
    CziImportError,
    plan_conversion,
    read_layout,
)
from pipeline.czi_explode import CziExplodeCancelled


class CziImportWorker(QThread):
    """Plans and executes a conversion, streaming progress to the UI."""

    logLine = Signal(str)          # one line for the status window
    progress = Signal(int, int)    # (planes_written, total_planes)
    # (well, done, total) — one well's share of the conversion, so its cell on
    # the acquisition plate can fill as a dial rather than the page having only
    # a single run-wide bar. Quantised in ``apply.convert``; see that module.
    wellFraction = Signal(str, int, int)
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
            self.logLine.emit("Reading CZI metadata…")
            layout = read_layout(self._src)
            plan_conversion(layout, self._params)  # validate before writing
        except CziImportError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # pragma: no cover — defensive
            swallowed("czi-import planning")
            self.failed.emit(f"Unexpected error while planning: {exc}")
            return

        try:
            summary = convert(
                self._src, self._dest, self._params,
                log=self.logLine.emit,
                is_cancelled=self.isInterruptionRequested,
                progress=self.progress.emit,
                well_progress=self.wellFraction.emit,
            )
        except CziExplodeCancelled:
            self.failed.emit(
                "Cancelled — the wells already converted were left in the "
                "destination; re-running with the same settings finishes the "
                "rest rather than repeating them."
            )
        except (CziImportError, ValueError) as exc:
            # A refusal, not a crash: the destination already holds a plate, it
            # holds an interrupted run of a different mapping, or the file and
            # the grid disagree. ``convert`` raises before writing anything, so
            # "Conversion failed: …" would describe a run that never started,
            # and the bare handler below would log a traceback for an ordinary,
            # expected answer.
            self.failed.emit(str(exc))
        except Exception as exc:  # pragma: no cover — defensive
            swallowed("czi-import convert")
            self.failed.emit(f"Conversion failed: {exc}")
        else:
            self.succeeded.emit(str(Path(self._dest)), summary)
