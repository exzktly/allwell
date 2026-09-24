"""Cooperative suspend for a pipeline run.

Stop is a kill: SIGTERM to the process group, escalated to SIGKILL five seconds
later. Whatever FOV-group each worker was mid-way through is lost, and so is the
per-well CSV for any well that had not finished — the run's durable progress is
only what the per-FOV checkpoints under ``tmp_images`` happen to hold.

Suspend is the other half of that pair: the same signal, but the run *catches*
it, stops taking on new FOV-groups, lets the ones already in flight finish, and
exits through the normal end-of-well path so every well that got any work done
writes its CSV and its ``<well>_out.zip``. The resulting output directory is
indistinguishable from one produced by a run that ended early for any other
reason, which is the point: ``--incremental`` already knows how to read it and
process only the ``(fov, timepoint)`` groups that are missing. Nothing here
records "where we were" — the artifacts on disk are the record.

The flag lives in this module rather than in ``process_microscopy`` so the well
workers can import it without importing the imaging stack, and so a caller can
ask ``is_suspended()`` without knowing how the signal was delivered.

**A second signal kills.** The handler restores the previous disposition before
returning, so a user who asks twice — or a supervisor escalating to SIGKILL —
gets the hard stop with no further cooperation. A suspend that hangs is worse
than one that loses a FOV-group.
"""

from __future__ import annotations

import os
import signal
import threading

# Set once a suspend signal has been caught. Never cleared: a run that has been
# asked to wind down does not un-wind-down, and each run is its own process.
_SUSPENDED = threading.Event()

# The signals a suspend request can arrive on. SIGTERM is what the GUI's Stop /
# Suspend path sends (via the process group); SIGINT is Ctrl-C for a CLI run.
_SUSPEND_SIGNALS = (signal.SIGTERM, signal.SIGINT)


#: Where :func:`watch_request_file` publishes the request file's path. An
#: environment variable rather than a module global because the well workers
#: are separate processes — spawned or forked, they inherit ``os.environ`` —
#: and each has to be able to see the request for itself.
REQUEST_FILE_ENV = "ALLWELL_SUSPEND_FILE"

# The log the request file is announced on, once, by whichever process sees it
# first. Set by `watch_request_file` in the orchestrator; a worker has none and
# stays quiet, for the reason `worker_initializer` gives.
_request_log = None


def is_suspended() -> bool:
    """Whether this process has been asked to wind down.

    Two ways to be asked. A signal sets the flag directly. The request file is
    for the runs a signal cannot reliably reach: on a remote host the process
    the app can name is ``conda run`` or ``srun``, not this interpreter, and
    signalling the ssh session instead ends it — which took the run's output
    stream, and on some hosts the run itself, down with it. So a remote
    Suspend writes a file, and every FOV-group boundary asks for it: one
    ``stat`` per group, against work measured in seconds per group.
    """
    if _SUSPENDED.is_set():
        return True
    path = os.environ.get(REQUEST_FILE_ENV)
    if path and os.path.exists(path):
        _SUSPENDED.set()
        if _request_log is not None:
            _request_log.warning(
                "SUSPEND requested (request file %s) — finishing the "
                "FOV-group(s) already in flight, then writing each well's CSV "
                "and out.zip from the work done so far. Re-run with "
                "--incremental to continue.", path)
        return True
    return False


def watch_request_file(path, *, log=None) -> None:
    """Treat *path* appearing as a suspend request, in this process and its workers.

    The file is never created or removed here. Whoever asks for the suspend
    creates it, and whoever launches the run clears it first — a request left
    over from an earlier suspend of the same plate would otherwise suspend the
    resumed run before it did anything, and on a remote host the run directory
    *is* reused, because its name is derived from the plate and the arguments.
    """
    global _request_log
    os.environ[REQUEST_FILE_ENV] = str(path)
    _request_log = log


def request_suspend() -> None:
    """Set the flag directly, without a signal. For tests and in-process calls."""
    _SUSPENDED.set()


def install_handlers(*, log=None) -> None:
    """Catch the suspend signals for the rest of this process's life.

    Called once per process — the orchestrator at startup, and each pool worker
    through its initializer, since a signal sent to the process *group* reaches
    every one of them and each has its own FOV loop to wind down.

    Best-effort: a platform or context that will not let us install a handler
    (Windows lacking SIGTERM delivery, a non-main thread) leaves the default
    behaviour in place, which is the pre-existing hard stop. A run must never
    fail to start because it could not arrange to be interrupted politely.
    """
    for sig in _SUSPEND_SIGNALS:
        try:
            previous = signal.getsignal(sig)
            signal.signal(sig, _make_handler(sig, previous, log))
        except (OSError, ValueError, RuntimeError):
            # ValueError: not the main thread. OSError: signal not supported.
            continue


def _make_handler(sig, previous, log):
    """A handler that flags the suspend once, then gets out of the way."""

    def _handle(signum, frame):
        first = not _SUSPENDED.is_set()
        _SUSPENDED.set()
        # Hand the signal back to whatever was handling it before us, so a
        # second request is a hard stop rather than another polite note.
        try:
            signal.signal(sig, previous)
        except (OSError, ValueError, RuntimeError):
            pass
        if first and log is not None:
            log.warning(
                "SUSPEND requested (signal %s, pid %s) — finishing the "
                "FOV-group(s) already in flight, then writing each well's CSV "
                "and out.zip from the work done so far. Re-run with "
                "--incremental to continue. Signal again to stop immediately.",
                signum, os.getpid(),
            )

    return _handle


def worker_initializer() -> None:
    """``multiprocessing.Pool(initializer=...)`` entry point.

    A pool worker is a fresh process: it inherits the parent's *flag value* at
    fork time on POSIX but not the parent's future signals, so it installs its
    own handler. Takes no arguments and returns nothing, which is what Pool
    requires. Logging is deliberately omitted here — one line per worker for a
    single user action is noise, and the orchestrator already said it.
    """
    install_handlers()
