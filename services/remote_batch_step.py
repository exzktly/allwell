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
    """rsync batch *index* into its staging directory: ``(bytes sent, rsync's code)``. Nothing is decided here, so it can
    run on a thread of its own while the batch before it runs (*ahead_of*), and :func:`_judge_upload` reads the code on
    the loop's thread afterwards — a failure it reports must not land while the batch before it is still being counted.
    """
    while_running = f" while batch {ahead_of} runs" if ahead_of else ""
    proc._log(f"[remote] batch {index}/{n_batches}: uploading {len(batch_zips)} well-zip(s){while_running}\n")
    # Each batch's rsync reports 0 → 1; scaled so the "Transferring…" label advances across the whole run instead
    # of resetting once per batch. An upload sent ahead drives no label: the run is computing, not transferring.
    outer = None if ahead_of else proc._progress
    progress: Callable[[float], None] | None = (
        None if outer is None else (lambda pct: outer((index - 1 + pct) / n_batches)))
    log = _ahead_log(proc._log) if ahead_of else proc._log
    prune_staged(proc._runner._cache, proc._batch_staging_dir(index), batch_zips, f"batch {index}", log)
    events = [proc._stop, proc._suspending, proc._driver_lost] + ([cancel] if cancel is not None else [])
    sent_bytes, _elapsed, rc = proc._runner._cache.upload_subset(
        batch_zips, proc._batch_staging_dir(index), log_fn=log, acquisition_input=proc._zipped,
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
    """The next batch's upload, running on its own thread while the current batch runs on the node.

    Batches used to go strictly one after another — upload batch *i*, run it, then upload batch *i + 1* — so every
    upload but the first was time the node sat waiting. The driver takes batch *i* only once its ``b<i>`` directory is
    moved into ``in/`` (``remote_commands``), so ``b<i + 1>`` can fill meanwhile without the running batch seeing it;
    the remote then holds two batches' input at the peak instead of one."""

    def __init__(self, proc, index: int, n_batches: int, batch_zips, ahead_of: int) -> None:
        self.index = index
        self.zips = list(batch_zips)
        self.cancel = threading.Event()
        self._result: tuple[int, int] | None = None
        self._error: BaseException | None = None

        def send() -> None:
            try:
                self._result = _send(proc, index, n_batches, self.zips, ahead_of=ahead_of, cancel=self.cancel)
            except BaseException as exc:          # handed to the loop's thread, which raises it there
                self._error = exc

        self._thread = threading.Thread(target=send, name=f"aw-batch-{index}-upload", daemon=True)
        self._thread.start()

    def result(self) -> tuple[int, int]:
        self._thread.join()
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result

    def abandon(self, wait_s: float = 30.0) -> None:
        """The loop ended without this batch: stop its rsync and wait a little for the thread. An error the thread
        raised is logged here, since nothing will ask for its result."""
        self.cancel.set()
        self._thread.join(wait_s)
        if self._error is not None:
            logging.getLogger(__name__).warning("the upload of batch %d, sent ahead and not needed, failed",
                                                self.index, exc_info=self._error)


def abandon_upload_ahead(proc) -> None:
    """Stop the upload sent ahead, if one is in flight — for the loop to call however it ends."""
    ahead = getattr(proc, "_upload_ahead", None)
    proc._upload_ahead = None
    if ahead is not None:
        ahead.abandon()


def run_batch(proc, index: int, n_batches: int, batch_zips, next_zips=None) -> int | Step:
    """Send data batch *index* of *n_batches* and wait for it: the code it counts for, or a :class:`Step`.

    *next_zips*, the batch after this one, starts uploading as soon as this batch is handed to the node, so it is
    on the remote when this one ends (:class:`_Ahead`); the call for it then finds its upload done or under way."""
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
    if next_zips:
        proc._upload_ahead = _Ahead(proc, index + 1, n_batches, next_zips, ahead_of=index)
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
