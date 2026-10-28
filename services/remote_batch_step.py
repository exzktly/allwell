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
from collections.abc import Callable

from services.remote_batch_ledger import fail
from services.remote_batch_resume import download_adopted
from services.remote_batch_staging import prune_staged
from services.remote_handoff import hand_off_and_wait
from services.remote_suspend import _AnySet


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


def _upload(proc, index: int, n_batches: int, batch_zips) -> int | Step:
    """Upload batch *index* into its staging directory: ``0``, or the :class:`Step` that ends the loop."""
    proc._log(f"[remote] batch {index}/{n_batches}: uploading {len(batch_zips)} well-zip(s)\n")
    # Each batch's rsync reports 0 → 1; scaled so the "Transferring…" label advances across the whole run instead
    # of resetting once per batch.
    outer = proc._progress
    progress: Callable[[float], None] | None = (
        None if outer is None else (lambda pct: outer((index - 1 + pct) / n_batches)))
    prune_staged(proc._runner._cache, proc._batch_staging_dir(index), batch_zips, f"batch {index}", proc._log)
    sent_bytes, _elapsed, rc = proc._runner._cache.upload_subset(
        batch_zips, proc._batch_staging_dir(index), log_fn=proc._log, acquisition_input=proc._zipped,
        progress_fn=progress,
        # A suspend ends the upload too — a batch sent after the user asked for the machine back is thrown away
        # unrun — and so does a driver that went away: there is no node left to send it to.
        stop_event=_AnySet(proc._stop, proc._suspending, proc._driver_lost))
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


def run_batch(proc, index: int, n_batches: int, batch_zips) -> int | Step:
    """Send data batch *index* of *n_batches* and wait for it: the code it counts for, or a :class:`Step`."""
    if proc._suspend_before(index, n_batches):
        return Step.SUSPENDED
    if _halted(proc, index, n_batches, "before"):
        return Step.ENDED
    sent = _upload(proc, index, n_batches, batch_zips)
    if isinstance(sent, Step):
        return sent
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
        proc._log(f"[remote] batch {index} pipeline exited {rc}; continuing with the remaining batches\n")
        return rc
    proc._log(f"[remote] batch {index}/{n_batches} complete\n")
    return 0
