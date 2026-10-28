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
  so loudly when it cannot — and reads ssh's own failure (255) and a timeout
  (124) as *unknown*, not as failed: the ``touch`` may have run before the
  connection went, so the driver's own word decides.
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

from services.remote_batch_ledger import fail
from services.remote_commands import BATCH_READY
from services.remote_suspend import _AnySet

#: Attempts at creating one handshake flag before the hand-off is called failed.
#: Each is one bounded ssh call (``remote_upload.SSH_CONTROL_TIMEOUT_S``).
FLAG_ATTEMPTS = 4

#: The pause between attempts — long enough for a dropped connection to come back.
FLAG_RETRY_S = 5.0

#: Exit codes that say nothing about whether the remote command ran: ssh's own
#: failure (255 — the connection went, possibly after the command finished) and
#: ``remote_upload._ssh_run``'s timeout (124), plus no answer at all.
UNKNOWN_SSH_CODES = (124, 255, None)

#: After every ``touch`` of a ready flag came back unknown, how long a driver
#: already waiting for that batch is given to say it started it. It looks for its
#: flag every 3 s, so a flag that landed is acknowledged well inside this.
FLAG_CONFIRM_S = 30.0

#: What a run that ends on a failed hand-off keeps, in words that are true whether
#: or not a resume can adopt the batches already uploaded: the Fetcher drains
#: every well the driver finished, and a resume never reprocesses one.
KEPT = ("The wells finished so far are kept and downloaded; Resume continues from them, and re-sends what had not "
        "run yet.")

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
        #: Batches whose flag the host could not be asked about, already said once.
        self.unchecked: set[int] = set()

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

    def started(self, index: int) -> bool:
        """Whether the driver has said it started batch *index*."""
        with self._lock:
            return index in self._started

    def waiting(self, index: int) -> bool:
        """Whether the driver has said it is waiting for batch *index*."""
        with self._lock:
            return index in self._waiting_at

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


def _halt(proc) -> _AnySet:
    """What ends a wait here: the user's Stop, or the driver going away (``_BatchedRemoteProcess._read_driver``)."""
    return _AnySet(*(e for e in (getattr(proc, "_stop", None), getattr(proc, "_driver_lost", None))
                     if e is not None))


def _rc(result) -> int | None:
    rc = getattr(result, "returncode", None)
    return rc if isinstance(rc, int) else None


def _settled_by_the_driver(proc, index: int) -> bool:
    """After a ``touch`` whose outcome is unknown: whether the driver, already waiting for *index*, says it started it.

    A driver that is waiting looks for its flag every few seconds, so a flag that landed is acknowledged within
    :data:`FLAG_CONFIRM_S`; the wait ends early on a Stop or a lost driver.
    """
    watch, halt = watch_of(proc), _halt(proc)
    deadline = time.monotonic() + FLAG_CONFIRM_S
    while time.monotonic() < deadline and not halt.is_set():
        if watch.started(index):
            return True
        halt.wait(0.2)
    return watch.started(index)


def raise_flag(proc, path: str, index: int | None = None) -> bool:
    """Create the handshake file *path* on the remote; ``False`` when it could not be (an ERROR has said so, unless
    a Stop or a lost driver ended the attempts — those are said by the caller).

    ``touch`` exits 0 only once the file exists, so its return code is proof that it does. Any other code from
    ``touch`` itself proves it does not. ssh's own failure (255) and a call that ran out of time (124) prove
    neither: the command may have run before the connection went. So for a ready flag (*index* given) the driver
    is asked before anything is concluded: a batch it says it started was handed over, whatever ssh said; and a
    driver not yet waiting for the batch — still queued, or still running the one before — cannot answer, so the
    hand-off is left to :func:`chase_handoff`, which checks the flag once the driver asks for it.
    """
    ssh = proc._runner._cache._ssh_run
    halt = _halt(proc)
    watch = watch_of(proc) if index is not None else None
    codes: list[int | None] = []
    for attempt in range(1, FLAG_ATTEMPTS + 1):
        rc = _rc(ssh(["touch", path], check=False))
        if rc == 0:
            return True
        codes.append(rc)
        if watch is not None and watch.started(index):
            return True                         # it landed after all: the driver has the batch
        if attempt < FLAG_ATTEMPTS and halt.wait(FLAG_RETRY_S):
            return False
    if halt.is_set():
        return False
    unknown = any(rc in UNKNOWN_SSH_CODES for rc in codes)
    if watch is not None and unknown:
        if not watch.waiting(index):
            proc._log(f"[remote] WARNING could not confirm that {path} reached the remote (ssh exit "
                      f"{codes[-1]}, {len(codes)} attempt(s)) — the host is not answering. The driver has not asked "
                      f"for this batch yet; once it does, the hand-off is checked and made again if it is missing\n")
            return True
        if _settled_by_the_driver(proc, index):
            return True
        if halt.is_set():
            return False
    proc._log(f"[remote] ERROR could not create {path} on the remote (ssh exit {codes[-1]}, {len(codes)} "
              f"attempt(s)) — {'the connection to the host is failing' if unknown else 'the remote refused it'}\n")
    return False


def _ended_by(proc, index: int, n_batches: int) -> int | None:
    """When a hand-off stopped short because the run is ending anyway: the run's code, having said why."""
    if getattr(proc, "_stop", None) is not None and proc._stop.is_set():
        proc._log(f"[remote] stopped while handing batch {index}/{n_batches} to the driver\n")
        return 130
    lost = getattr(proc, "_driver_lost", None)
    if lost is not None and lost.is_set():
        fail(proc, f"[remote] ERROR the allocation ended while batch {index}/{n_batches} was being handed to the "
                   f"driver — no more batches will run; {KEPT}\n")
        return 1
    return None


def hand_off(proc, index: int, n_batches: int, path: str) -> bool:
    """Hand batch *index* to the driver by raising its ready flag *path*.

    ``False`` means the hand-off could not be made, and says so in the log in
    words a user can act on: the driver would otherwise wait for it until its
    own upload wait gave up.
    """
    if raise_flag(proc, path, index):
        watch_of(proc).handed(index)
        return True
    ended = _ended_by(proc, index, n_batches)
    if ended is not None:
        proc._hand_off_code = ended
        return False
    fail(proc, f"[remote] ERROR batch {index}/{n_batches} could not be handed to the driver; ending the run "
               f"rather than leaving the node waiting for it. {KEPT}\n")
    return False


def hand_off_and_wait(proc, index: int, n_batches: int, *, meanwhile: Callable[[], object] = lambda: None) -> int | None:
    """Hand batch *index* to the driver, run *meanwhile*, and wait for the batch's exit code.

    ``None`` when there will be no code: the hand-off could not be made (the
    run's code is then 1 — or 130 for a Stop that cut it short — and the log has
    said why), or the driver went away (``_BatchedRemoteProcess._await_batch``).
    The final nanowell pass is the driver's batch ``n_batches + 1`` and comes
    through here too.
    """
    proc._hand_off_code = None
    if not hand_off(proc, index, n_batches, proc._batch_flag(BATCH_READY, index)):
        code = proc._hand_off_code
        proc._returncode = code if code == 130 else (proc._returncode or code or 1)
        return None
    meanwhile()
    return proc._await_batch(index, n_batches)


def _flag_state(proc, path: str) -> int | None:
    """``test -f`` on the remote: its code (0 present, 1 absent), or ``None`` when the check itself did not run."""
    rc = _rc(proc._runner._cache._ssh_run(["test", "-f", path], check=False))
    return None if rc in UNKNOWN_SSH_CODES else rc


def chase_handoff(proc, index: int, n_batches: int, flag_path: Callable[[], str]) -> bool:
    """Called while waiting on batch *index*: ``False`` when the run should give up on it.

    *flag_path* gives the ready flag's remote path; it is asked only once the
    hand-off is :meth:`HandoffWatch.overdue`. The flag present means the driver
    has the batch and only its word is missing — the batch may well be
    running, and its own time budget still bounds it, so the run keeps waiting
    after saying so once. The flag missing means the hand-off was lost, so it is
    raised again, once; a driver that takes neither is given up on. A check that
    could not run (ssh 255, or no answer in time) says nothing about the flag:
    it is said once and asked again an acknowledgement window later, never read
    as "missing" — that is how a run used to be ended with a diagnosis that was
    not true.
    """
    watch = watch_of(proc)
    if index in watch.warned or not watch.overdue(index):
        return True
    minutes = int(ACK_TIMEOUT_S // 60)
    state = _flag_state(proc, flag_path())
    if state is None:
        watch.handed(index)                     # ask again one window from now, not every second
        if index not in watch.unchecked:
            watch.unchecked.add(index)
            proc._log(f"[remote] WARNING the driver has been waiting for batch {index}/{n_batches} for {minutes} "
                      f"min, and the host did not answer whether its hand-off arrived — still waiting\n")
        return True
    if state == 0:
        watch.warned.add(index)
        proc._log(f"[remote] WARNING batch {index}/{n_batches} was handed to the driver {minutes} min ago and "
                  f"the node has it, but nothing has come back since — the node's output is not reaching the "
                  f"app. Still waiting; if nothing changes, Stop and Resume keep every finished well.\n")
        return True
    if index in watch.re_raised:
        fail(proc, f"[remote] ERROR the driver is waiting for batch {index}/{n_batches} but did not start it "
                   f"within {minutes} min of it being handed over twice; ending the run. {KEPT}\n")
        return False
    watch.re_raised.add(index)
    proc._log(f"[remote] WARNING the driver has been waiting for batch {index}/{n_batches} for {minutes} min "
              f"and its hand-off never reached the remote — handing it over again\n")
    return hand_off(proc, index, n_batches, flag_path())
