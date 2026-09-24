"""Asking a running remote pipeline to suspend.

A remote run cannot be suspended the way a local one is. The process the app
can name on the host is ``conda run`` or ``srun``, not the pipeline, and the
signal Stop sends goes to the local ssh session, which ends the session and the
run's downloads with it. So the request is a file the pipeline polls; this
module is the one place it is made. Split out of ``services/remote_runner.py``,
which keeps ``RemoteRunner.request_suspend`` as a one-line delegate and
re-exports every name here.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

#: A suspend request is one ``touch``. Bounded tighter than the other control
#: calls because the user is waiting on the answer: a host that has not answered
#: in this long is not going to, and the run should be left to carry on.
SUSPEND_REQUEST_TIMEOUT_S = 30.0


class _AnySet:
    """An ``Event``-shaped view that reads as set when any of *events* is."""

    def __init__(self, *events: threading.Event) -> None:
        self._events = events

    def is_set(self) -> bool:
        return any(e.is_set() for e in self._events)

    def wait(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
        return True


def request_suspend(runner, proc=None, *,
                    log_fn: Callable[[str], None] | None = None) -> bool:
    """Ask the running remote pipeline to wind down. ``True`` if a suspend is in effect.

    A suspend cannot go the way a Stop does. The process the app can name
    on the remote is the login shell's ``conda run`` or ``srun``, not the
    pipeline, so a signal to it reaches the pipeline only if those forward
    it; and signalling the local ssh session instead ends the session —
    which is what a remote Suspend used to do, taking the run's output
    stream, the per-well downloads (``RemoteRunner.cancel`` stops the
    Fetcher) and
    on a scheduler the allocation (``scancel``) down with it. What was left
    was a Stop with the wells in flight thrown away and the finished ones
    stranded on the remote.

    So the request is a file. The pipeline was started with
    ``--suspend_file`` naming it and checks for it at every FOV-group
    boundary, exactly where it checks for the signal; a batched driver
    checks it before starting each batch. Nothing else about the run is
    touched: the stream keeps flowing, the Fetcher keeps downloading, and
    the run ends through its ordinary exit with code 0.

    ``False`` when there is nothing to ask — no pipeline launched yet (the
    request path is set only once the upload is done), or a cache hit that
    runs no pipeline — or when a single-shot run's ``touch`` failed. A failed
    ``touch`` is not proof the request is absent: a timeout can fail after the
    command ran. ``PipelineRunner`` therefore settles the question at exit
    from the pipeline's own ``pipeline.suspend.LOG_MARKER`` line. A batched run
    answers ``True`` even then, because the local side has already stopped
    sending batches.
    """
    log = log_fn or (lambda _m: None)
    run = runner._run
    if run is None or not run.suspend_request:
        return False
    # A batched run is told *before* the file exists. The driver may see the
    # file and exit within one poll, and a reader that had not yet been told
    # would take that exit for a lost node and cancel the downloads. Told
    # first, the local side has stopped feeding batches whatever the host
    # says — so for a batched run the suspend is in effect either way.
    notify = getattr(proc, "note_suspend_requested", None)
    batched = callable(notify)
    if batched:
        notify()
    result = runner._cache._ssh_run(["touch", run.suspend_request], check=False,
                                    timeout_s=SUSPEND_REQUEST_TIMEOUT_S)
    if result is None or result.returncode != 0:
        detail = (result.stderr or "").strip() if result is not None else ""
        then = ("the batch running now will finish, and no more will be sent"
                if batched else
                "it is still running. Press Suspend again, or Stop to cancel it")
        log("[remote] warn: could not reach the host to ask the run to "
            f"suspend{f' ({detail})' if detail else ''}; {then}.\n")
        return batched
    log("[remote] suspend requested on the host — the run will finish the "
        "FOV-group(s) in flight and download what it has written.\n")
    return True
