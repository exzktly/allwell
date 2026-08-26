"""The writer half of the per-cell export: a thread that only serialises.

The UI thread slices — one well per event-loop tick, into a bounded queue — and
this drains it. That split is not stylistic. Cached frames are rewritten in
place while the app runs: ``home/cell_gating/gating_compute.apply_fluor_gating``
replaces ``Included`` / ``Gate_Pass`` / ``Gate_On`` from a ``QThread``, and
``image_browser/review_image/collect`` writes ``Included`` for manual curation.
A worker holding a reference to a cached frame and serialising it later would
race both. It never sees one: what arrives on the queue is a boolean-``.loc``
slice, which pandas materialises as a copy.

Nothing here touches Qt. Progress and completion are plain callables that
``core.worker_bridge.run_off_thread`` marshals back onto the GUI thread.

**A cancelled or failed export leaves nothing behind that looks finished.**
Rows stream into a temp file beside the destination and the destination only
comes into existence on the closing ``os.replace``. If that replace never
happens the temp file is removed.
"""

from __future__ import annotations

import csv
import logging
import os
import queue
import threading
import time
from pathlib import Path

import pandas as pd

_logger = logging.getLogger("well_viewer")

#: Sentinel put on the queue when the last well has been handed over. Also put
#: by whoever cancels, because setting the cancel event does **not** wake a
#: thread already blocked in ``Queue.get()``.
DONE = object()

#: How long the writer waits on an empty queue before re-checking the cancel
#: event. Bounded for that reason alone: a bare blocking ``get()`` survives both
#: the event and a short ``join()``, leaving a thread parked for the life of the
#: process with a temp file beside the user's destination.
_GET_TIMEOUT_S = 0.25


class CellExportWriter:
    """Drain *chunks* into *out_path*, atomically, until DONE or cancelled."""

    def __init__(self, *, out_path: Path, header: list[str],
                 scope_lines: list[str], chunks: queue.Queue,
                 cancel: threading.Event, total_wells: int = 0):
        self.out_path = Path(out_path)
        self.header = list(header)
        self.scope_lines = list(scope_lines)
        self.chunks = chunks
        self.cancel = cancel
        self.total_wells = int(total_wells)
        self.rows_written = 0
        self.wells_written = 0
        #: Set once the drain has unwound, temp file included. A canceller
        #: waits on this rather than joining a thread it has no handle on;
        #: a daemon thread killed at interpreter exit never runs its
        #: ``finally``, and the temp file is what that ``finally`` removes.
        self.finished = threading.Event()
        self._tmp = self.out_path.with_name(
            f".{self.out_path.name}.{os.getpid()}.tmp")

    def __call__(self, report=None):
        """Run the drain. Returns a summary dict; raises on a write failure."""
        replaced = False
        try:
            with open(self._tmp, "w", newline="", encoding="utf-8") as fh:
                for line in self.scope_lines:
                    fh.write(line if line.endswith("\n") else line + "\n")
                wrote_header = False
                while not self.cancel.is_set():
                    try:
                        item = self.chunks.get(timeout=_GET_TIMEOUT_S)
                    except queue.Empty:
                        continue
                    if item is DONE:
                        break
                    frame = self._align(item)
                    frame.to_csv(fh, index=False, header=not wrote_header,
                                 quoting=csv.QUOTE_NONNUMERIC)
                    wrote_header = True
                    self.rows_written += len(frame)
                    self.wells_written += 1
                    if report is not None:
                        report.progress(self.wells_written, self.total_wells)
                if self.cancel.is_set():
                    return {"cancelled": True, "rows": self.rows_written}
                if not wrote_header:
                    # No well produced a row. Write the header alone rather than
                    # a file that is only a comment block, so the columns the
                    # export would have carried are still visible.
                    pd.DataFrame(columns=self.header).to_csv(
                        fh, index=False, quoting=csv.QUOTE_NONNUMERIC)
            self._retry_replace()
            replaced = True
            return {"cancelled": False, "rows": self.rows_written,
                    "wells": self.wells_written, "path": str(self.out_path)}
        except Exception:
            # The bridge delivers ``str(exc)`` only, so the traceback has to be
            # logged here or the one diagnostic for a mid-plate failure is lost.
            _logger.exception("cell export failed writing %s", self.out_path)
            raise
        finally:
            if not replaced:
                try:
                    self._tmp.unlink(missing_ok=True)
                except OSError:
                    _logger.warning("could not remove export temp %s", self._tmp)
            self.finished.set()

    def _align(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Put *frame* onto the file's column order.

        ``reindex`` fills a column this well does not have with ``NaN``, which
        ``to_csv`` writes as an empty field — a measurement that was not made,
        not a measurement of zero.
        """
        return frame.reindex(columns=self.header)

    def _retry_replace(self, attempts: int = 4) -> None:
        """``os.replace`` onto the destination, retried on PermissionError.

        Same-filesystem by construction (the temp sits beside the destination),
        so the replace is atomic. The retry is for Windows, where a destination
        held open — by Excel, most often — raises until it is closed.
        """
        for i in range(attempts):
            try:
                os.replace(self._tmp, self.out_path)
                return
            except PermissionError:
                if i == attempts - 1:
                    raise
                time.sleep(0.25 * (2 ** i))
