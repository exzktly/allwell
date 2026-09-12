"""One background thread that performs the debounced autosaves' disk I/O.

**Why this exists.** Every autosaving persistence domain ran its write on the
GUI thread, 500 ms after the last edit, through
:func:`review.persistence._io.update_json_atomic`. That function is
deliberately read-modify-verify-write — it re-reads the file and compares it
byte-for-byte against the copy the mutation was built from, because
``pipeline_info.json`` has several writers and one of them is the pipeline in
another process — so a single autosave is seven filesystem round trips:
``exists``, ``read_bytes``, ``exists``, ``read_bytes``, the tmp ``open`` +
``write``, ``os.replace``, and the tmp ``exists``. On a local disk that is a
few milliseconds and nobody notices. On the network share this app is normally
pointed at, each of those round trips is on the order of a hundred
milliseconds, so the window froze for about a second every time the user
paused while renaming a variable or ticking a value onto the plate — measured
at 1057 ms per autosave with every filesystem operation slowed to 150 ms.

The repaint was never the cost: a full Variables-tab rebuild is 7.5 ms at 96
wells and 24 ms at 384. The stall was entirely the write.

**What this is.** One daemon thread consuming one FIFO queue. Serialized on
purpose: two writes to the same sidecar must not interleave, and the
optimistic-concurrency check in ``update_json_atomic`` narrows the race against
*other processes*, not against a second thread of this one. FIFO on purpose
too — the last edit the user made has to be the last one written.

**What stays on the GUI thread.** Everything that reads application state. A
registered ``save`` still runs inline: it reads ``ctx.state``, builds its block,
and closes over the finished payload. Only the file operations are handed over,
and only from the debounced path — an explicit Save button, the smFISH
threshold writer and the gating block all still write inline and still return a
real answer to their caller.

**Failures.** A deferred write has no caller left to return ``False`` to, so a
failure is reported instead of returned: logged here (the log is the durable
record) and emitted on :attr:`PersistenceWriter.failed` for
:class:`~review.persistence.registry.PersistenceRegistry` to put on the
status line. Silence on a failed write is exactly the "degraded output
indistinguishable from good output" this codebase treats as the defect itself —
the user would learn their variables were never saved by finding them gone next
session.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

_logger = logging.getLogger("review")

#: How long :meth:`PersistenceWriter.drain` waits at the two moments the app
#: cannot proceed without the pending writes having landed — quitting, and
#: loading another dataset. Long enough for a slow share to finish a queue of
#: whole-document writes; short enough that a genuinely wedged mount does not
#: hold the window open indefinitely. A timeout is reported, never silent.
DRAIN_TIMEOUT_S = 10.0

#: A no-arg callable that performs the I/O and returns ``(ok, detail)``.
#: ``detail`` is a human-readable reason, used only when *ok* is false.
Job = Callable[[], "tuple[bool, str]"]


class PersistenceWriter(QObject):
    """A serialized background writer for the debounced autosaves.

    Create on the GUI thread (it is a ``QObject`` and its signal is delivered
    to GUI-thread receivers by Qt's queued connection). The worker thread is
    started on the first :meth:`submit`, so an app that never autosaves never
    starts one.
    """

    #: ``(domain, detail)`` — emitted from the worker thread when a job
    #: reported failure or raised. Connect on the GUI thread.
    failed = Signal(str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self._stopping = False

    # -- submission ------------------------------------------------------

    def submit(self, domain: str, job: Job) -> None:
        """Queue *job* to run on the writer thread. Returns immediately."""
        if self._stopping:
            # Shutting down: run it here rather than dropping the user's edit.
            self._run(domain, job)
            return
        self._ensure_thread()
        self._queue.put((domain, job))

    def _ensure_thread(self) -> None:
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._loop, name="allwell-persistence-writer",
                daemon=True,
            )
            self._thread.start()

    # -- draining --------------------------------------------------------

    def drain(self, timeout_s: float = DRAIN_TIMEOUT_S) -> bool:
        """Block until every job queued so far has run. ``True`` if it did.

        Implemented by queueing a sentinel and waiting on it: the queue is FIFO
        with a single consumer, so the sentinel running means everything ahead
        of it has finished. That is exact, unlike polling ``Queue.empty()``,
        which goes true while the last job is still in flight.
        """
        if self._thread is None or not self._thread.is_alive():
            return True
        done = threading.Event()
        self._queue.put((None, done))
        if done.wait(timeout_s):
            return True
        _logger.warning(
            "persistence: background writes did not finish within %.0fs; "
            "the remaining ones will still be attempted, but a save may not "
            "have landed before this point.", timeout_s,
        )
        return False

    def stop(self, timeout_s: float = DRAIN_TIMEOUT_S) -> bool:
        """Drain, then run any later job inline. For interpreter shutdown."""
        ok = self.drain(timeout_s)
        self._stopping = True
        return ok

    # -- the worker ------------------------------------------------------

    def _loop(self) -> None:
        while True:
            domain, item = self._queue.get()
            if domain is None:                       # a drain sentinel
                item.set()
                continue
            self._run(domain, item)

    def _run(self, domain: str, job: Job) -> None:
        try:
            ok, detail = job()
        except Exception as exc:                     # noqa: BLE001 - reported
            _logger.exception(
                "persistence: the background write for %r raised; the edit "
                "was not saved", domain)
            self.failed.emit(domain, str(exc) or exc.__class__.__name__)
            return
        if ok:
            return
        _logger.warning(
            "persistence: %r was not written to disk — %s. The edit stays in "
            "memory for this session and will be lost when it ends.",
            domain, detail or "see the log for the refusal",
        )
        self.failed.emit(domain, detail)
