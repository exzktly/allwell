"""The run heartbeat: the file a remote run's app touches to say it is still there.

Split out of ``services/remote_process.py``, which is line-budgeted and imports
:class:`_UploadHeartbeat` from here under its old name, so no caller moved. The
remote side that reads the file — the single-shot job's upload wait, the batch
driver's wait between batches and its watchdog beside each batch — is
``services/remote_wait_snippets.py``.
"""

from __future__ import annotations

import posixpath
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from services.remote_wait_snippets import (
    MID_BATCH_HEARTBEAT_STALE_S,
    UPLOAD_HEARTBEAT_S,
    UPLOAD_HEARTBEAT_STALE_S,
)
from services.remote_run_id import HEARTBEAT_DIR
from util.aw_safe import swallowed

if TYPE_CHECKING:
    from services.remote_upload import DatasetCache

#: Consecutive beats that did not land before the log says so, and before it says
#: so as an error naming when the remote will act on the silence. One missed beat
#: is an ssh blip the remote tolerates; three is a minute and a half of silence
#: against a remote that gives the node back after two.
WARN_AFTER_FAILURES = 2
ERROR_AFTER_FAILURES = 3

#: How long one beat's ``touch`` may take before it counts as missed. Not the beat
#: interval: on a cluster whose shared filesystem is busy (a GPU batch writing
#: masks and images as fast as it can), a ``touch`` can take more than 30 s while
#: ssh itself answers at once, and a beat abandoned at 30 s was counted as missed
#: even though it would have landed a few seconds later. The beats run one at a
#: time on one thread, so a long allowance cannot queue them up; it only has to
#: leave a beat that lands at the very end of it inside the remote's tolerance —
#: the next beat starts ``UPLOAD_HEARTBEAT_S`` after the last one that landed, so
#: the remote sees a gap of at most ``UPLOAD_HEARTBEAT_S + BEAT_TIMEOUT_S``, which
#: must stay under ``UPLOAD_HEARTBEAT_STALE_S``.
#: ``tests/test_a_slow_heartbeat_is_measured_and_tolerated.py`` holds that sum.
BEAT_TIMEOUT_S = 75

#: A beat that took at least this long is logged with its duration, landed or not,
#: so a run on a stalling filesystem leaves a record of how long the stalls were.
SLOW_BEAT_S = 10


def _reason(stderr) -> str:
    """The last non-blank line of a failed beat's ``stderr``, trimmed for one log line."""
    if isinstance(stderr, (bytes, bytearray)):
        stderr = stderr.decode("utf-8", errors="replace")
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    return lines[-1][:200] if lines else ""


class _UploadHeartbeat:
    """Touches the remote heartbeat for as long as this app owns the run.

    A daemon thread rather than a hook inside the transfer: rsync can be busy
    with one large archive for minutes without returning to any per-file
    callback, and a heartbeat that only advances between files would read as
    dead exactly during the slowest part of the upload.

    Stopping is the point. Whatever ends the run — success, failure, the Stop
    button, the process being killed — takes the beat with it, and a job still
    waiting for its input frees its node about a minute later instead of
    holding it for the full deadline.

    It beats for the whole run, not only the upload, because it is also what
    :meth:`RemoteRunner._sweep_orphaned_allocation` reads to decide whether an
    allocation still has an owner. ``run_id`` is deterministic in the plate, so
    two sessions on the same plate meet at the same sidecar; a beat that
    stopped when the upload finished would make an hour-long compute look
    abandoned and get itself cancelled by the second session.
    """

    def __init__(self, cache: "DatasetCache", path: str,
                 interval_s: float = UPLOAD_HEARTBEAT_S, *,
                 log_fn: Callable[[str], None] | None = None,
                 batched: bool = False, clock: Callable[[], float] = time.time,
                 beat_timeout_s: float = BEAT_TIMEOUT_S,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._cache = cache
        self._path = path
        self._interval = interval_s
        self._beat_timeout = beat_timeout_s
        self._monotonic = monotonic
        # The heartbeat lives in a folder of its own inside the run directory (see
        # ``remote_run_id.HEARTBEAT_DIR``); nothing else creates that folder, so the
        # first beat does. A heartbeat elsewhere (a reservation's) needs nothing.
        self._needs_dir = posixpath.basename(posixpath.dirname(path)) == HEARTBEAT_DIR
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # A failing beat used to be invisible until the remote acted on the silence — gave the node back, or
        # suspended the batch — and the remote's message then blamed the app. *log_fn* is told instead.
        self._log = log_fn or (lambda _m: None)
        self._batched = batched
        self._clock = clock
        self._failures = 0
        self._last_ok: float | None = None

    def beat(self) -> None:
        """One touch, now. Called once before the job starts waiting, so the
        first thing it looks at is a heartbeat that already exists.

        Bounded by :data:`BEAT_TIMEOUT_S` rather than the default: long enough
        for a filesystem that is slow to update one file, short enough that a
        beat landing at the very end of it still reaches the remote before the
        remote reads the silence as an app that died. A beat that took
        :data:`SLOW_BEAT_S` or longer is logged with how long it took.
        """
        if self._needs_dir:
            self._make_dir()
        started = self._monotonic()
        try:
            result = self._cache._ssh_run(["touch", self._path], check=False,
                                          timeout_s=self._beat_timeout)
        except Exception:
            self._missed(None)
            raise
        rc = getattr(result, "returncode", None)
        took = self._monotonic() - started
        if took >= SLOW_BEAT_S:
            self._log(f"[remote] the heartbeat took {took:.0f} s to "
                      + ("reach the host" if rc == 0 else f"fail (ssh exit {rc})")
                      + " — the host's filesystem or connection is slow to answer\n")
        if rc == 0:
            if self._failures >= WARN_AFTER_FAILURES:
                self._log(f"[remote] the heartbeat is reaching the host again after {self._failures} missed beat(s)\n")
            self._failures, self._last_ok = 0, self._clock()
        else:
            self._missed(rc, _reason(getattr(result, "stderr", "")))

    def _make_dir(self) -> None:
        """Create the heartbeat's folder, once. A failure is not fatal here: the
        ``touch`` that follows fails too, and is counted and reported as a miss."""
        result = self._cache._ssh_run(["mkdir", "-p", posixpath.dirname(self._path)],
                                      check=False, timeout_s=self._beat_timeout)
        if getattr(result, "returncode", None) == 0:
            self._needs_dir = False

    def _missed(self, rc: int | None, why: str = "") -> None:
        """Count a beat that did not land, and say so at the 2nd and 3rd in a row.

        *why* is what ssh (or ``ssh_control``, for a timeout) said, so the log
        names the failure — "Connection reset", "did not answer within 75s" —
        and not only its exit code.
        """
        self._failures += 1
        if self._failures not in (WARN_AFTER_FAILURES, ERROR_AFTER_FAILURES):
            return
        since = self._last_ok if self._last_ok is not None else self._clock()
        at = lambda s: time.strftime("%H:%M:%S", time.localtime(since + s))     # noqa: E731
        code = f"ssh exit {rc}: {why}" if why else f"ssh exit {rc}"
        if self._failures == WARN_AFTER_FAILURES:
            self._log(f"[remote] WARNING the run's heartbeat did not reach the host {self._failures} times in a row "
                      f"({code}) — still trying\n")
            return
        mid = (f"; a batch already running is suspended at about {at(MID_BATCH_HEARTBEAT_STALE_S)} "
               f"({MID_BATCH_HEARTBEAT_STALE_S // 60} min)") if self._batched else ""
        self._log(f"[remote] ERROR the run's heartbeat has not reached the host {self._failures} times in a row "
                  f"({code}). If it does not recover, the host takes the app for gone: a node waiting for "
                  f"{'a batch' if self._batched else 'the upload'} is given back at about "
                  f"{at(UPLOAD_HEARTBEAT_STALE_S)} ({UPLOAD_HEARTBEAT_STALE_S} s after the last beat that landed)"
                  f"{mid}\n")

    def start(self) -> None:
        """Begin beating. Paired with :meth:`stop` — see the class docstring."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="aw-upload-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop beating, and wait for the thread to notice.

        The whole mechanism is "a beat that stops means the app is gone", so a
        beat that outlives its run is not a leak of one thread — it tells the
        remote a dead run is alive, holds the node for the full deadline, and
        keeps the orphan sweep from ever sweeping.
        """
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)

    def __enter__(self) -> "_UploadHeartbeat":
        self.beat()
        self.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.beat()
            except Exception:
                # A missed beat is not fatal on its own — the job tolerates
                # `UPLOAD_HEARTBEAT_STALE_S` of silence — and an ssh blip must
                # not take down an upload that is otherwise fine.
                swallowed("upload heartbeat")

    def __exit__(self, *_exc) -> None:
        self.stop()
