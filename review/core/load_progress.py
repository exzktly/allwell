"""Say what a dataset load is doing after its last CSV, well by well.

The load's progress bar used to count the CSVs and stop. On a plate larger than
the row cache only the first well or two is read during that count (the rest
are registered from their summaries), so it raced to ``40/40`` — and then the
window sat on that line, with a busy cursor, through everything that follows:
restoring the saved analysis, measuring each channel's range, preparing the
tabs and drawing the plots. Each of those reads wells from disk, and on a plate
of 300 MB wells that is minutes with nothing on screen to say it is not stuck.

A :class:`LoadProgress` names each of those phases on the status line and in
the opening dialog, and — through ``DatasetState.read_listener``, which both
read paths call just before reading a well from disk — names the well being
read and how far through the plate the pass is: ``Preparing tabs — reading
well B03 (12 of 40)``. The count is of distinct wells in the current pass; a
well read again starts a new pass and the count restarts, so it never exceeds
the plate (a count of reads printed "70 well reads so far, 40 wells in the
plate" for a phase that walked the plate twice). The bar is a busy bar, because
a phase does not know in advance how many passes it makes. A phase that reads
nothing from disk shows only its name, briefly.

Only reads on the GUI thread are reported: the Cell Gating worker reads on its
own thread and reports through its own progress signal, whose status text waits
until the load's own status line has finished.
"""

from __future__ import annotations

import threading
import time

from startup import boot_diag

#: The status line is repainted at most this often while wells are read, so a
#: phase reading small wells from a warm cache does not spend its time painting.
_REPAINT_INTERVAL_S = 0.1


class LoadProgress:
    """The status line of one dataset load, from its last CSV to its end."""

    def __init__(self, ctx, n_wells: int) -> None:
        self._ctx = ctx
        self._n = max(1, int(n_wells))
        self._phase = ""
        self._seen: set[str] = set()
        self._last_paint = 0.0
        self._thread = threading.main_thread()

    def __enter__(self) -> LoadProgress:
        self._ctx.state.dataset.read_listener = self.well_read
        return self

    def __exit__(self, *_exc) -> None:
        boot_diag.boot("dataset load: finished")
        if self._ctx.state.dataset.read_listener == self.well_read:
            self._ctx.state.dataset.read_listener = None
        bar = self._ctx.widgets.get_or_none("_progress_bar")
        if bar is not None:
            bar.setVisible(False)

    def phase(self, text: str) -> None:
        """Start a phase: name it, reset the well count, repaint now."""
        self._phase = text
        self._seen = set()
        # One flushed line per phase in the boot log: a load the system kills
        # for memory leaves no traceback, and this is then the only record of
        # which phase it reached.
        boot_diag.boot(f"dataset load: {text}")
        self._paint(f"{text}…")

    def well_read(self, label: str) -> None:
        """A well is about to be read from disk in the current phase."""
        if threading.current_thread() is not self._thread or not self._phase:
            return
        if label in self._seen:
            # The phase has come back to a well it already read: a second pass
            # over the plate (or part of it). Counting starts again, so the line
            # never claims more wells than the plate has.
            self._seen = set()
        self._seen.add(label)
        now = time.monotonic()
        # The first read of a phase is always shown: with narrow reads a whole
        # phase can fall inside one repaint interval, and the line would then
        # never say which wells it was reading.
        if len(self._seen) > 1 and now - self._last_paint < _REPAINT_INTERVAL_S:
            return
        self._paint(f"{self._phase} — reading well {label} "
                    f"({min(len(self._seen), self._n)} of {self._n})")

    def _paint(self, text: str) -> None:
        from review.core.load_controller import _opening_set_phase
        from review.core.ui_helpers import step_progress

        self._last_paint = time.monotonic()
        bar = self._ctx.widgets.get_or_none("_progress_bar")
        if bar is not None and (not bar.isVisible() or bar.maximum() != 0):
            # Busy, not a fraction: a phase does not know in advance how many
            # reads it will make (it can walk the plate more than once).
            bar.setRange(0, 0)
            bar.setVisible(True)
        step_progress(self._ctx, 0, text, allow_user_input=False)
        _opening_set_phase(self._ctx, text, value=0, maximum=0)
