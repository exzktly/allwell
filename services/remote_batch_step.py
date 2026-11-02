"""One data batch of a batched remote run: upload it, hand it to the driver, and read back how it went.

Split out of ``_BatchedRemoteProcess._run_batches`` (``services/remote_process.py``, line-budgeted and at the
complexity limit), which calls :func:`run_batch` once per batch and keeps the loop around it: the tally, the final
pass, the teardown. *proc* is that ``_BatchedRemoteProcess``.

What comes back is either the code the batch counts for — ``0``, or a failure the tally names — or a :class:`Step`
saying the loop should stop sending batches: :attr:`Step.SUSPENDED` (a suspend; the run ends cleanly) or
:attr:`Step.ENDED` (a Stop, a lost driver, a failed upload or hand-off; the run's code is already set and the log
has said why).
"""

from __future__ import annotations

import enum
import logging
import threading
from collections.abc import Callable

from services.remote_batch_ledger import fail, ledger_of, unreported_after_success
from services.remote_batch_resume import download_adopted
from services.remote_batch_staging import prune_staged
from services.remote_handoff import hand_off_and_wait
from services.remote_suspend import _AnySet
from services.remote_wire import TRANSIENT_PREFIX
from util.well_token import canonical_well_label

#: What an upload sent ahead logs in place of the ``[remote] ↑`` of an upload the run is waiting for. The Analyze
#: progress view (``pipeline_runner.ProgressTracker``) reads ``↑`` as "the run is uploading" and puts the headline and
#: the transfer label on it; while a batch computes, that is not what is happening. ``⇡`` moves only the well's own dial.
AHEAD_UP_MARK = "[remote] ⇡"


def _ahead_log(log: Callable[[str], None]) -> Callable[[str], None]:
    """*log* for an upload sent ahead: ``↑`` lines marked :data:`AHEAD_UP_MARK`, rsync's live progress (for the status
    line) dropped."""
    def say(line: str) -> None:
        if line.startswith(TRANSIENT_PREFIX):
            return
        log(line.replace("[remote] ↑", AHEAD_UP_MARK, 1))
    return say


#: How much of the next batch goes up while one runs (the Analyze form's *Upload ahead*, ``remote_upload_ahead``).
#: ``batch``: all of it at once, so the node never waits on an upload after the first batch and the remote holds about
#: two batches' input at the peak. ``well``: one well of it in place of each well of the running batch that leaves
#: the remote, so the remote holds about one batch. ``none``: nothing until the running batch has ended.
UPLOAD_AHEAD_BATCH, UPLOAD_AHEAD_WELL, UPLOAD_AHEAD_NONE = "batch", "well", "none"
UPLOAD_AHEAD_CHOICES = (
    (UPLOAD_AHEAD_BATCH, "Whole next batch while one runs"),
    (UPLOAD_AHEAD_WELL, "One well as each finishes"),
    (UPLOAD_AHEAD_NONE, "Nothing ahead"),
)
DEFAULT_UPLOAD_AHEAD = UPLOAD_AHEAD_BATCH


def upload_ahead_mode(value) -> str:
    """*value* as one of the modes; anything else (blank, an older job without the key) is the default."""
    text = str(value or "").strip().lower()
    return text if text in {m for m, _label in UPLOAD_AHEAD_CHOICES} else DEFAULT_UPLOAD_AHEAD


def peak_batches(mode: str, n_batches: int) -> int:
    """How many batches' input the remote holds at the peak under *mode*."""
    return min(2, n_batches) if upload_ahead_mode(mode) == UPLOAD_AHEAD_BATCH else min(1, n_batches)


class Step(enum.Enum):
    """A batch step that ends the loop rather than counting a code."""

    SUSPENDED = "suspended"
    ENDED = "ended"


def _halted(proc, index: int, n_batches: int, when: str) -> bool:
    """Whether a Stop or a lost driver ends the run *when* batch *index* would go next (and the run's code is set)."""
    if proc._stop.is_set():
        proc._log(f"[remote] stopped {when} batch {index}/{n_batches}\n")
        proc._returncode = 130
        return True
    if proc._driver_lost.is_set():
        fail(proc, f"[remote] ERROR the allocation ended {when} batch {index}/{n_batches} — no more batches will "
                   f"run; the finished wells are still being downloaded\n")
        proc._returncode = proc._returncode or 1
        return True
    return False


def _send(proc, index: int, n_batches: int, batch_zips, *, ahead_of: int = 0,
          cancel: threading.Event | None = None) -> tuple[int, int]:
    """rsync batch *index* into its staging directory: ``(bytes sent, rsync's code)``. The loop's own upload, for a
    batch nothing was sent ahead of (the first, or one whose trickle was abandoned); :class:`_Ahead` sends the rest."""
    proc._log(f"[remote] batch {index}/{n_batches}: uploading {len(batch_zips)} well-zip(s)\n")
    prune_staged(proc._runner._cache, proc._batch_staging_dir(index), batch_zips, f"batch {index}", proc._log)
    return _rsync(proc, index, n_batches, batch_zips, ahead=False, cancel=cancel)


def _rsync(proc, index: int, n_batches: int, zips, *, ahead: bool,
           cancel: threading.Event | None = None, batch_zips=None) -> tuple[int, int]:
    """One rsync of *zips* into batch *index*'s staging directory. Nothing is decided here, so it can run on a thread
    of its own while the batch before it runs (*ahead*), and :func:`_judge_upload` reads the code on the loop's thread
    afterwards — a failure it reports must not land while the batch before it is still being counted."""
    # Each batch's rsync reports 0 → 1; scaled so the "Transferring…" label advances across the whole run instead
    # of resetting once per batch. An upload sent ahead drives no label: the run is computing, not transferring.
    outer = None if ahead else proc._progress
    progress: Callable[[float], None] | None = (
        None if outer is None else (lambda pct: outer((index - 1 + pct) / n_batches)))
    log = _ahead_log(proc._log) if ahead else proc._log
    events = [proc._stop, proc._suspending, proc._driver_lost] + ([cancel] if cancel is not None else [])
    sent_bytes, _elapsed, rc = proc._runner._cache.upload_subset(
        list(zips), proc._batch_staging_dir(index), log_fn=log, acquisition_input=proc._zipped,
        # The record and anchors beside the archives describe the whole batch (*batch_zips*), however few of its
        # wells this rsync sends: each rsync rewrites them, and the driver hands the pipeline the last one.
        record_paths=list(batch_zips) if batch_zips is not None else None,
        progress_fn=progress,
        # A suspend ends the upload too — a batch sent after the user asked for the machine back is thrown away
        # unrun — and so does a driver that went away: there is no node left to send it to; and an upload sent
        # ahead stops when the loop ends without wanting it.
        stop_event=_AnySet(*events))
    return sent_bytes, rc


def _judge_upload(proc, index: int, n_batches: int, sent_bytes: int, rc: int) -> int | Step:
    """What batch *index*'s upload means for the loop: ``0``, or the :class:`Step` that ends it."""
    if rc != 0:
        # A Stop, a suspend or a lost driver comes back as rsync's non-zero code; none of them is an upload fault.
        if proc._stop.is_set():
            proc._log(f"[remote] batch {index}/{n_batches}: upload interrupted\n")
            proc._returncode = 130
            return Step.ENDED
        if proc._driver_lost.is_set():
            fail(proc, f"[remote] ERROR the allocation ended while batch {index}/{n_batches} was uploading — no "
                       f"more batches will run; the finished wells are still being downloaded\n")
            proc._returncode = proc._returncode or 1
            return Step.ENDED
        if proc._suspend_before(index, n_batches):
            return Step.SUSPENDED
        fail(proc, f"[remote] ERROR batch {index} upload failed (rsync rc={rc}); aborting remaining batches\n")
        proc._returncode = rc
        return Step.ENDED
    if proc._suspend_before(index, n_batches):
        return Step.SUSPENDED
    proc._log(f"[remote] batch {index}: upload complete ({sent_bytes / (1024**2):.1f} MB); handing it to the "
              f"driver\n")
    return 0


class _Ahead:
    """The next batch's upload, on a thread of its own while the running batch computes on the node.

    Batches used to go strictly one after another — upload batch *i*, run it, then upload batch *i + 1* — so every
    upload but the first was time the node sat waiting. Under :data:`UPLOAD_AHEAD_BATCH` (*whole*, the default) the
    whole of batch *i + 1* goes up into ``b<i + 1>`` at once, so it is usually there before batch *i* ends, at the
    cost of about two batches' input on the remote at the peak. Under :data:`UPLOAD_AHEAD_WELL` it goes up a well at
    a time: each well of batch *i* whose remote files the Fetcher deletes once it has landed
    here (:func:`well_removed`) frees one slot, and one well of the next batch takes it. The remote therefore never
    holds more than one batch's input. The driver takes ``b<i + 1>`` only once it is moved into ``in/``
    (``remote_commands``), so the running batch never sees it. When batch *i* ends, its input goes with it (the
    driver's ``rm -rf in``), so :meth:`release_all` sends whatever is left in one rsync — which is all of it for a
    nanowell run, whose wells stay on the remote until their batch's end (``remote_fetch_nanowells``)."""

    def __init__(self, proc, index: int, n_batches: int, batch_zips, ahead_of: int, running_zips=(),
                 whole: bool = False) -> None:
        self.index = index
        self.whole = whole
        self.zips = list(batch_zips)
        self.cancel = threading.Event()
        # The running batch's wells, by label: only their removal frees a slot here.
        self._running = {_label(z) for z in running_zips}
        self._freed: set[str] = set()
        self._all = whole                                     # the whole batch may go at once
        self._wake = threading.Condition()
        self._result: tuple[int, int] | None = None
        self._error: BaseException | None = None
        self._stops = (proc._stop, proc._suspending, proc._driver_lost, self.cancel)

        def send() -> None:
            try:
                self._result = self._trickle(proc, n_batches, ahead_of)
            except BaseException as exc:          # handed to the loop's thread, which raises it there
                self._error = exc

        self._thread = threading.Thread(target=send, name=f"aw-batch-{index}-upload", daemon=True)
        self._thread.start()

    def _trickle(self, proc, n_batches: int, ahead_of: int) -> tuple[int, int]:
        if self.whole:
            proc._log(f"[remote] batch {self.index}/{n_batches}: uploading {len(self.zips)} well-zip(s) while batch "
                      f"{ahead_of} runs\n")
        else:
            proc._log(f"[remote] batch {self.index}/{n_batches}: {len(self.zips)} well-zip(s) to upload while batch "
                      f"{ahead_of} runs, one as each of its wells leaves the remote\n")
        prune_staged(proc._runner._cache, proc._batch_staging_dir(self.index), self.zips, f"batch {self.index}",
                     _ahead_log(proc._log))
        sent_bytes, done = 0, 0
        while done < len(self.zips):
            allowed = self._wait_for_room(done)
            if allowed is None:
                return sent_bytes, 20                         # ended by Stop, suspend, a lost driver or the cancel
            some, rc = _rsync(proc, self.index, n_batches, self.zips[done:allowed], ahead=True, cancel=self.cancel,
                              batch_zips=self.zips)
            sent_bytes += some
            if rc != 0:
                return sent_bytes, rc
            done = allowed
        return sent_bytes, 0

    def _room(self) -> int:
        return len(self.zips) if self._all else min(len(self.zips), len(self._freed))

    def _wait_for_room(self, done: int) -> int | None:
        """How many of the batch's wells may be on the remote, once that is more than *done*; ``None`` when the upload
        is to end instead."""
        with self._wake:
            while self._room() <= done:
                if any(e.is_set() for e in self._stops):
                    return None
                self._wake.wait(0.5)                          # the stop events are not ours to notify on
            return None if any(e.is_set() for e in self._stops) else self._room()

    def free(self, well: str) -> None:
        """*well* has left the remote: if it was one of the running batch's, one more of this batch may go up."""
        label = _label(well)
        with self._wake:
            if label in self._running and label not in self._freed:
                self._freed.add(label)
                self._wake.notify_all()

    def release_all(self) -> None:
        """The running batch has ended, and its input leaves with it: send whatever of this batch is left."""
        with self._wake:
            self._all = True
            self._wake.notify_all()

    def result(self) -> tuple[int, int]:
        self.release_all()
        self._thread.join()
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result

    def abandon(self, wait_s: float = 30.0) -> None:
        """The loop ended without this batch: stop its rsync and wait a little for the thread. An error the thread
        raised is logged here, since nothing will ask for its result."""
        self.cancel.set()
        with self._wake:
            self._wake.notify_all()
        self._thread.join(wait_s)
        if self._error is not None:
            logging.getLogger(__name__).warning("the upload of batch %d, sent ahead and not needed, failed",
                                                self.index, exc_info=self._error)


def _label(well_or_zip) -> str:
    name = getattr(well_or_zip, "stem", None) or str(well_or_zip)
    return canonical_well_label(name) or name.upper()


def well_removed(proc, well: str) -> None:
    """The Fetcher has deleted *well*'s files from the remote (``Fetcher.on_well_removed``): the next batch's upload,
    if one is waiting for room, may send a well in its place. Called on the Fetcher's thread."""
    ahead = getattr(proc, "_upload_ahead", None)
    if ahead is not None:
        ahead.free(well)


def abandon_upload_ahead(proc) -> None:
    """Stop the upload sent ahead, if one is in flight — for the loop to call however it ends."""
    ahead = getattr(proc, "_upload_ahead", None)
    proc._upload_ahead = None
    if ahead is not None:
        ahead.abandon()


def run_batch(proc, index: int, n_batches: int, batch_zips, next_zips=None) -> int | Step:
    """Send data batch *index* of *n_batches* and wait for it: the code it counts for, or a :class:`Step`.

    *next_zips*, the batch after this one, goes up while this one runs as the run's *Upload ahead* says
    (:class:`_Ahead`; nothing under :data:`UPLOAD_AHEAD_NONE`); the call for it sends what is left and finds the rest
    already there."""
    result = _run_batch(proc, index, n_batches, batch_zips, next_zips)
    if isinstance(result, Step):
        abandon_upload_ahead(proc)
    return result


def _run_batch(proc, index: int, n_batches: int, batch_zips, next_zips) -> int | Step:
    ahead: _Ahead | None = getattr(proc, "_upload_ahead", None)
    proc._upload_ahead = None
    if ahead is not None and ahead.index != index:
        ahead.abandon()
        ahead = None
    if proc._suspend_before(index, n_batches):
        if ahead is not None:
            ahead.abandon()
        return Step.SUSPENDED
    if _halted(proc, index, n_batches, "before"):
        if ahead is not None:
            ahead.abandon()
        return Step.ENDED
    sent_bytes, rc = ahead.result() if ahead is not None else _send(proc, index, n_batches, batch_zips)
    sent = _judge_upload(proc, index, n_batches, sent_bytes, rc)
    if isinstance(sent, Step):
        return sent
    # Batch 1 went up while the run's granted node was still being granted: the driver is launched now it is on the
    # host (`_BatchedRemoteProcess.launch_deferred_driver`), once the node is granted.
    launch = getattr(proc, "launch_deferred_driver", None)
    if launch is not None and not launch():
        return Step.ENDED
    mode = upload_ahead_mode(getattr(proc, "_upload_ahead_mode", None))
    if next_zips and mode != UPLOAD_AHEAD_NONE:
        proc._upload_ahead = _Ahead(proc, index + 1, n_batches, next_zips, ahead_of=index, running_zips=batch_zips,
                                    whole=mode == UPLOAD_AHEAD_BATCH)
    # The pipeline for this batch runs inside the allocation the driver already holds — no second queue wait, no
    # second argv. One hand-off that cannot be made ends the run (`remote_handoff`).
    rc = hand_off_and_wait(proc, index, n_batches,
                           # the job has its first batch: drain the earlier attempt's wells meanwhile
                           meanwhile=lambda: download_adopted(proc))
    proc._release_nanowell_batch(rc, batch_zips)
    if rc is None:
        return Step.ENDED       # the driver is gone, or the run was stopped; `_await_batch` has said which
    if rc != 0:
        # Kept, not fatal: one bad well should not cost the plate the node it holds. The tally after the loop
        # names every batch that did this, and the run's own code carries the first of them.
        why = ("exceeded its time budget and was stopped" if ledger_of(proc).over_budget(index)
               else f"pipeline exited {rc}")
        proc._log(f"[remote] batch {index} {why}; continuing with the remaining batches\n")
        return rc
    if unreported_after_success(proc, index, batch_zips):
        return 1
    proc._log(f"[remote] batch {index}/{n_batches} complete\n")
    return 0
