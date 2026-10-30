"""A node Slurm allocated but could not boot is still a wait for a node, not a run that started.

The report: on an AWS ParallelCluster host whose nodes are powered down, the run
log read ``srun: job N has been allocated resources`` after ten minutes, then at
once ``Node failure`` / ``still not ready`` / ``Something is wrong with the boot
of the nodes``, then the wrapper's "failed to boot — requesting another node"
and ``requesting allocation via:`` again. The Analyze pane's queue clock went
back to zero at that point, as if the run had started over, although the
wrapper on the host times the host's Max queue wait from the first request.

The cause: Slurm prints ``has been allocated resources`` when it *picks* a node,
which on a cluster that powers nodes down is the moment the boot begins, not the
moment the job starts. The app read that line as the job running in three
places — the progress pane (which stopped and cleared the wait clock), the job
indicator (lit as an open job) and the run's evidence (no longer waiting for a
node, so a Suspend wrote a request file to a job that could not read it instead
of cancelling the queued one). Only the job's own first line,
``[remote] scheduler: allocation granted``, which it prints from inside the
allocation, proves the node booted.

These tests replay the reported sequence of lines through the real tracker and
the real consumers.
"""

from __future__ import annotations

import time

import pytest

from analyze.domain import slurm_job as sj
from services.pipeline_runner import ProgressTracker
from services.run_evidence import RunEvidence

#: The reported sequence, in the order the run log showed it: the wrapper's
#: request, Slurm queueing and then allocating, the boot failing, and the
#: wrapper asking again.
REQUEST = "[remote] scheduler: requesting allocation via: srun --partition=gpu-medium-queue --nodes=1 --ntasks=1\n"
QUEUED = "srun: job 4812345 queued and waiting for resources\n"
ALLOCATED = "srun: job 4812345 has been allocated resources\n"
BOOT_FAILED = (
    "srun: error: Node failure on gpu-medium-queue-dy-g5-24xlarge-1\n",
    "srun: error: Nodes gpu-medium-queue-dy-g5-24xlarge-1 are still not ready\n",
    "srun: error: Something is wrong with the boot of the nodes.\n",
    "[remote] scheduler: node(s) gpu-medium-queue-dy-g5-24xlarge-1 failed to boot — requesting another "
    "node in 30 s (retry 1; failed to boot so far: gpu-medium-queue-dy-g5-24xlarge-1)\n",
)
REQUEST_AGAIN = ("[remote] scheduler: requesting allocation via: srun --partition=gpu-medium-queue --nodes=1 "
                 "--ntasks=1 --exclude=gpu-medium-queue-dy-g5-24xlarge-1\n")
QUEUED_AGAIN = "srun: job 4812399 queued and waiting for resources\n"
STARTED = "[remote] scheduler: allocation granted — job 4812399 on gpu-medium-queue-dy-g5-24xlarge-7\n"


def _sched_events(tracker: ProgressTracker, line: str) -> list[tuple]:
    return [payload for kind, payload in tracker.parse(line) if kind == "sched"]


def test_slurm_allocating_a_node_is_not_the_job_starting():
    tracker = ProgressTracker()
    assert _sched_events(tracker, ALLOCATED) == [("allocated", "4812345", "")]
    assert _sched_events(tracker, STARTED) == [("granted", "4812399", "")]


def _progress_host(qapp):
    from PySide6.QtWidgets import QWidget

    from analyze.progress import AnalyzeProgressMixin
    from widgets.run_dashboard import RunDashboard

    class _Host(AnalyzeProgressMixin, QWidget):
        def __init__(self):
            super().__init__()
            self._dashboard = RunDashboard()
            self._dashboard.reset(remote=True, total=2)
            self._well_total = 2
            self._well_done = 0
            self._well_repeats = 0
            self._pipeline_started_at = None
            self._zipper_done = 0

        def _log_line(self, *_a, **_k):
            return None

    return _Host()


def _replay(host, tracker: ProgressTracker, lines) -> None:
    for line in lines:
        for kind, payload in tracker.parse(line):
            host._apply_progress_event(kind, payload)


def test_the_wait_clock_survives_a_node_that_failed_to_boot(qapp):
    host = _progress_host(qapp)
    tracker = ProgressTracker()
    _replay(host, tracker, [REQUEST, QUEUED])
    # Ten minutes in the queue, as reported, before Slurm picked a node.
    host._sched_since = time.monotonic() - 600
    first_request = host._sched_since

    _replay(host, tracker, [ALLOCATED])
    assert host._sched_since == first_request, "allocating a node stopped the wait clock"
    assert "waiting for it to boot" in host._dashboard._status.text()
    assert "10m" in host._dashboard._status.text()
    assert "Allocation granted" not in host._dashboard._status.text()

    _replay(host, tracker, [*BOOT_FAILED, REQUEST_AGAIN, QUEUED_AGAIN])
    assert host._sched_since == first_request, "the retry started the wait clock again from zero"
    status = host._dashboard._status.text()
    assert status.startswith("Waiting for the scheduler — 10m"), status
    assert "4812399" in status and "boot" not in status

    _replay(host, tracker, [STARTED])
    assert host._sched_since is None
    assert "Allocation granted" in host._dashboard._status.text()


def test_the_job_indicator_stays_dimmed_while_the_node_boots():
    job = sj.SlurmJob()
    for state, jid in (("waiting", ""), ("queued", "4812345"), ("allocated", "4812345")):
        job = sj.on_sched(job, state, jid)
    assert job == sj.SlurmJob(state=sj.QUEUED, job_id="4812345")
    job = sj.on_sched(job, "granted", "4812399")
    assert job.state == sj.OPEN and job.job_id == "4812399"


@pytest.mark.parametrize("cut", [
    pytest.param(1, id="allocated"),
    pytest.param(1 + len(BOOT_FAILED), id="boot-failed"),
    pytest.param(2 + len(BOOT_FAILED), id="asked-again"),
])
def test_the_run_is_still_waiting_for_a_node_until_the_job_says_it_started(cut):
    """What a Suspend reads: still waiting means cancel the queued job, since a job that never booted cannot read
    the suspend request file."""
    evidence = RunEvidence()
    for line in [REQUEST, QUEUED, ALLOCATED, *BOOT_FAILED, REQUEST_AGAIN][:2 + cut]:
        evidence.note(line)
    assert evidence.waiting_for_node is True
    evidence.note(STARTED)
    assert evidence.waiting_for_node is False
