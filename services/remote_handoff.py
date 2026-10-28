"""Handing a batch to the batch driver, and noticing when the driver never took it.

A batched remote run hands each uploaded batch to the driver already holding the
node by creating one empty file on the remote, ``<run>/.aw_batch_<i>_ready``, with
a single ``ssh touch``. The driver polls for that file, and once it sees it prints
``[remote] batch-driver: batch <i> starting`` and runs the pipeline; the app learns
the batch is over from the ``… finished rc=N`` marker on the same stream.

Two failures used to leave both ends waiting on each other in silence:

* **The ``touch`` failed.** It ran with ``check=False`` and its result was never
  read, so a dropped connection or a remote that took longer than the control
  timeout to answer lost the hand-off with nothing in the log. The app's last word
  was "handing it to the driver"; the driver went on polling for a file that would
  never appear until its upload wait ran out — two hours — and only then said
  "batch N never arrived". :func:`raise_flag` reads the result, retries, and says
  so loudly when it cannot.
* **The driver had the flag but nothing came back.** Even a hand-off that did
  arrive is only proven by the driver saying it started. :class:`HandoffWatch`
  follows the driver's own lines and :func:`chase_handoff` acts when a driver
  *known to be waiting* for a batch has not started it within
  :data:`ACK_TIMEOUT_S` of being handed it: it looks for the flag, raises it again
  when it is missing, gives the run up when a second hand-off is not taken either,
  and when the flag is there says plainly that the node has the batch but its
  output is not reaching the app.

The clock only runs once the driver has said ``waiting for batch <i>``. Before
that — the first batch of a run whose allocation is still queued, or a driver
still finishing the previous batch — silence is expected and lasts as long as the
scheduler or the pipeline takes, so nothing here may read it as a lost hand-off.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable

from services.remote_commands import BATCH_READY

#: Attempts at creating one handshake flag before the hand-off is called failed.
#: Each is one bounded ssh call (``remote_upload.SSH_CONTROL_TIMEOUT_S``).
FLAG_ATTEMPTS = 4

#: The pause between attempts — long enough for a dropped connection to come back.
FLAG_RETRY_S = 5.0

#: How long a driver that has said it is waiting for a batch may go, after the
#: batch was handed to it, without saying it started it. The driver looks for its
#: flag every ``remote_wait_snippets.UPLOAD_WAIT_POLL_S`` (3 s) and between seeing
#: it and saying "starting" does only an ``rm -rf`` and a ``mv`` of the input
#: directory on one filesystem — seconds. Five minutes is far past any of that and
#: still well short of the two hours the driver's own upload wait allows.
ACK_TIMEOUT_S = 300.0

_WAITING_RE = re.compile(r"\[remote\] batch-driver: waiting for batch (\d+) of")
_STARTED_RE = re.compile(r"\[remote\] batch-driver: batch (\d+) starting")

_CREATE_LOCK = threading.Lock()


class HandoffWatch:
    """What the driver has said about each batch, and when it was handed over.

    Written by two threads — the driver's reader (:meth:`observe`) and the batch
    loop (:meth:`handed`, :meth:`overdue`) — so every access holds the lock.
    """

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._waiting_at: dict[int, float] = {}
        self._handed_at: dict[int, float] = {}
        self._started: set[int] = set()
        #: Batches whose flag was raised a second time by :func:`chase_handoff`.
        self.re_raised: set[int] = set()
        #: Batches already reported as handed over but unheard from.
        self.warned: set[int] = set()

    def observe(self, line: str) -> None:
        """Note the driver's "waiting for batch N" and "batch N starting" lines."""
        m = _WAITING_RE.search(line)
        if m:
            with self._lock:
                self._waiting_at.setdefault(int(m.group(1)), self._clock())
            return
        m = _STARTED_RE.search(line)
        if m:
            with self._lock:
                self._started.add(int(m.group(1)))

    def handed(self, index: int) -> None:
        """Batch *index*'s flag was raised now; the acknowledgement clock restarts."""
        with self._lock:
            self._handed_at[index] = self._clock()

    def overdue(self, index: int) -> bool:
        """Whether a waiting driver has gone :data:`ACK_TIMEOUT_S` without starting *index*."""
        with self._lock:
            if index in self._started or index not in self._handed_at or index not in self._waiting_at:
                return False
            since = max(self._handed_at[index], self._waiting_at[index])
            return self._clock() - since > ACK_TIMEOUT_S


def watch_of(proc) -> HandoffWatch:
    """*proc*'s :class:`HandoffWatch`, made on first use."""
    with _CREATE_LOCK:
        watch = getattr(proc, "_handoff", None)
        if watch is None:
            watch = HandoffWatch()
            proc._handoff = watch
        return watch


def raise_flag(proc, path: str) -> bool:
    """Create the handshake file *path* on the remote; ``False`` (and an ERROR line) if it could not be.

    ``touch`` exits 0 only once the file exists, so its return code is the proof.
    A Stop during the pause between attempts ends the retries.
    """
    ssh = proc._runner._cache._ssh_run
    stop = getattr(proc, "_stop", None)
    rc = None
    attempts = FLAG_ATTEMPTS
    for attempt in range(1, attempts + 1):
        rc = getattr(ssh(["touch", path], check=False), "returncode", None)
        if rc == 0:
            return True
        if attempt < attempts and stop is not None and stop.wait(FLAG_RETRY_S):
            break
    proc._log(f"[remote] ERROR could not create {path} on the remote (ssh exit {rc}, "
              f"{attempts} attempt(s)) — the connection to the host is failing\n")
    return False


def hand_off(proc, index: int, n_batches: int, path: str) -> bool:
    """Hand batch *index* to the driver by raising its ready flag *path*.

    ``False`` means the hand-off could not be made, and says so in the log in
    words a user can act on: the driver would otherwise wait two hours for it.
    """
    if raise_flag(proc, path):
        watch_of(proc).handed(index)
        return True
    proc._log(f"[remote] ERROR batch {index}/{n_batches} could not be handed to the driver; ending the run "
              f"rather than leaving the node waiting for it. The uploaded wells stay on the remote — "
              f"Resume picks them up.\n")
    return False


def hand_off_and_wait(proc, index: int, n_batches: int, *, meanwhile: Callable[[], object] = lambda: None) -> int | None:
    """Hand batch *index* to the driver, run *meanwhile*, and wait for the batch's exit code.

    ``None`` when there will be no code: the hand-off could not be made (the
    run's code is then 1, and the log has said why), or the driver went away
    (``_BatchedRemoteProcess._await_batch``). The final nanowell pass is the
    driver's batch ``n_batches + 1`` and comes through here too.
    """
    if not hand_off(proc, index, n_batches, proc._batch_flag(BATCH_READY, index)):
        proc._returncode = proc._returncode or 1
        return None
    meanwhile()
    return proc._await_batch(index, n_batches)


def chase_handoff(proc, index: int, n_batches: int, flag_path: Callable[[], str]) -> bool:
    """Called while waiting on batch *index*: ``False`` when the run should give up on it.

    *flag_path* gives the ready flag's remote path; it is asked only once the
    hand-off is overdue. Acts only once the hand-off is :meth:`HandoffWatch.overdue`. The flag missing
    means the hand-off was lost, so it is raised again, once; a driver that takes
    neither is given up on. The flag present means the driver has the batch and
    only its word is missing — the batch may well be running, and its own time
    budget still bounds it, so the run keeps waiting after saying so once.
    """
    watch = watch_of(proc)
    if index in watch.warned or not watch.overdue(index):
        return True
    minutes = int(ACK_TIMEOUT_S // 60)
    path = flag_path()
    if index in watch.re_raised:
        proc._log(f"[remote] ERROR the driver is waiting for batch {index}/{n_batches} but did not start it "
                  f"within {minutes} min of it being handed over twice; ending the run. The uploaded wells stay "
                  f"on the remote — Resume picks them up.\n")
        return False
    ssh = proc._runner._cache._ssh_run
    present = getattr(ssh(["test", "-f", path], check=False), "returncode", None) == 0
    if present:
        watch.warned.add(index)
        proc._log(f"[remote] WARNING batch {index}/{n_batches} was handed to the driver {minutes} min ago and "
                  f"the node has it, but nothing has come back since — the node's output is not reaching the "
                  f"app. Still waiting; if nothing changes, Stop and Resume keep every finished well.\n")
        return True
    watch.re_raised.add(index)
    proc._log(f"[remote] WARNING the driver has been waiting for batch {index}/{n_batches} for {minutes} min "
              f"and its hand-off never reached the remote — handing it over again\n")
    return hand_off(proc, index, n_batches, path)
