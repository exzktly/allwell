"""Wait for a Datasets tool's worker to finish — without failing open.

Six test modules carried a byte-identical copy of this helper:

    worker = ctrl._worker
    assert worker is not None
    worker.wait(5000)
    for _ in range(10):
        QApplication.processEvents()

Both lines after the assert are guesses, and both fail *open* — they hand back
a half-finished run and let the test assert against it, so the failure surfaces
somewhere else entirely.

``QThread.wait(5000)`` returns ``False`` on timeout and nobody read it. Five
seconds is ample when a test file runs alone and not always ample inside a
full-lane run, where thousands of tests have already been through the process;
past the deadline the helper returned anyway, with the worker still running.
That is how ``test_subset_page.py::test_dashboard_hidden_until_a_run_starts``
came to fail once in a full lane and pass every time it was re-run alone: the
page hides its dashboard on a source change only when a run is *not* in flight
(``SubsetPage._update_status_preview`` returns early while ``self._running``),
so a worker that had not finished left the dashboard up and the assertion read
``assert False is True``.

The fixed pump is the same shape of guess. ``finished`` reaches the controller
through a queued connection, so "ten turns of the event loop" is a bet on how
many turns that chain needs rather than a statement that it completed.

So this waits on the *condition* — the thread is finished and the page has left
its running state — and turns each way of not reaching it into a named failure.
"""

from __future__ import annotations

import time

from PySide6.QtWidgets import QApplication

#: Generous by design. It is not a performance budget: nothing here should take
#: seconds, and a test that needs more than this has hung rather than got slow.
#: The old five seconds sat close enough to real full-lane timings to be reached.
WORKER_TIMEOUT_MS = 60_000

#: How long to keep pumping the event loop for the queued ``finished`` chain to
#: settle once the thread itself has stopped.
SETTLE_TIMEOUT_S = 10.0

#: How long each blocking slice of the wait lasts before the event loop is
#: pumped again. Short enough that a worker waiting on the GUI thread is not
#: held up materially; long enough not to spin.
_WAIT_SLICE_MS = 25


def run_worker_to_completion(ctrl, *, timeout_ms: int = WORKER_TIMEOUT_MS) -> None:
    """Block until *ctrl*'s worker has finished and its page is idle again.

    ``isRunning()`` polling is not an option and never was: a worker over a tiny
    dataset can finish in well under a millisecond, so ``isRunning()`` reads
    ``False`` both before the thread starts and after it ends, and the two are
    indistinguishable from that alone. Hence waiting on the thread object, then
    on the page's own running flag.
    """
    worker = getattr(ctrl, "_worker", None)
    assert worker is not None, (
        "no worker on the controller — the run never started, so waiting for it "
        "to finish would pass vacuously"
    )
    # Wait in slices, pumping the event loop between them, rather than blocking
    # on one long ``wait(timeout)``. A worker that needs the GUI thread to drain
    # its queue before it can finish — anything routed through a bridge object,
    # or a ``BlockingQueued`` connection — deadlocks against a caller holding
    # that thread, and the deadlock only shows under a loaded full-lane run
    # where the timing shifts. Pumping costs nothing when the worker is already
    # independent, and is the difference between a hang and a pass when it
    # isn't.
    deadline = time.monotonic() + timeout_ms / 1000.0
    while not worker.wait(_WAIT_SLICE_MS):
        QApplication.processEvents()
        assert time.monotonic() < deadline, (
            f"worker still running after {timeout_ms} ms. Returning here would "
            f"hand the test a half-finished run to assert against, which is how "
            f"this used to surface as an unrelated assertion elsewhere."
        )

    # The thread has stopped; its ``finished`` signal is queued and the
    # controller's handlers have not necessarily run yet.
    page = getattr(ctrl, "page", None)
    deadline = time.monotonic() + SETTLE_TIMEOUT_S
    while True:
        QApplication.processEvents()
        if not getattr(page, "_running", False):
            return
        assert time.monotonic() < deadline, (
            f"worker thread finished but {type(page).__name__}._running is still "
            f"True after {SETTLE_TIMEOUT_S}s of event processing — the finished "
            f"signal never reached the page"
        )
