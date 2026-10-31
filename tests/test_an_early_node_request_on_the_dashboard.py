"""The Analyze dashboard while a node asked for before staging waits for the run.

A remote run on a scheduler host now queues its job before the plate is planned (``services.remote_reservation``),
so the job can be queued — or granted — while this machine is still preparing the plate. The headline must go on
saying what this machine is doing, and the wait's clock must still run, until the run is handed to the job. These
drive the tracker with the lines the app writes and assert the label the user reads.
"""
from __future__ import annotations

import pytest

from analyze.domain import slurm_job as sj
from services.pipeline_runner import ProgressTracker

REQUEST = "[remote] scheduler: early: requesting allocation via: srun -p gpu --time=1:00:00\n"
GRANTED = "[remote] scheduler: allocation granted — job 4812345 on node7 (partition gpu)\n"
HANDED = "[remote] scheduler: the run is handed to the node asked for early.\n"
GIVEN_BACK = "[remote] scheduler: giving back the node asked for early — the run ended before it needed a node.\n"


def _sched(line: str) -> list:
    return [payload for kind, payload in ProgressTracker().parse(line) if kind == "sched"]


def test_the_tracker_tells_an_early_request_from_an_ordinary_one():
    assert _sched(REQUEST) == [("waiting-early", "", "srun -p gpu --time=1:00:00")]
    assert _sched("[remote] scheduler: requesting allocation via: srun -p gpu\n") == [("waiting", "", "srun -p gpu")]
    assert _sched(HANDED) == [("handed", "", "")]
    assert _sched(GIVEN_BACK) == [("early-ended", "", "")]


def test_the_job_indicator_keeps_the_job_the_run_was_handed():
    job = sj.on_sched(sj.SlurmJob(), "waiting-early", "")
    assert job.state == sj.QUEUED
    job = sj.on_sched(job, "granted", "4812345")
    assert (job.state, job.job_id) == (sj.OPEN, "4812345")
    assert sj.on_sched(job, "handed", "") == job
    assert sj.on_sched(job, "early-ended", "") == sj.SlurmJob()


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _host(qapp):
    from PySide6.QtWidgets import QWidget

    from analyze.progress import AnalyzeProgressMixin
    from widgets.run_dashboard import RunDashboard

    class _Host(AnalyzeProgressMixin, QWidget):
        def __init__(self):
            super().__init__()
            self._dashboard = RunDashboard()
            self._dashboard.reset(remote=True, total=2)
            self._well_total = 0
            self._well_done = 0
            self._well_repeats = 0
            self._zipper_done = 0
            self._pipeline_started_at = None

        def _log_line(self, text, level="INFO"):
            pass

    host = _Host()
    host._reset_run_phase(remote=True)
    return host


def _feed(host, line: str) -> None:
    for kind, payload in ProgressTracker().parse(line):
        host._apply_progress_event(kind, payload)


def _headline(host) -> str:
    return host._dashboard._headline.text()


def _status(host) -> str:
    return host._dashboard._status.text()


def test_an_early_request_counts_the_wait_without_calling_the_run_queued(qapp):
    host = _host(qapp)
    before = _headline(host)
    _feed(host, REQUEST)
    assert _headline(host) == before, _headline(host)
    assert _status(host).startswith("Waiting for the scheduler"), _status(host)


def test_a_node_granted_while_the_plate_is_prepared_does_not_say_the_pipeline_is_starting(qapp):
    host = _host(qapp)
    before = _headline(host)
    _feed(host, REQUEST)
    _feed(host, GRANTED)
    assert _headline(host) == before, _headline(host)
    assert "Starting the pipeline" not in _headline(host)
    assert "job 4812345" in _status(host) and "prepares the run" in _status(host), _status(host)
    # Handed the run: the job is the run's now, and the run is starting on it.
    _feed(host, HANDED)
    assert "Starting the pipeline" in _headline(host), _headline(host)
    assert "job 4812345" in _status(host), _status(host)


def test_a_job_still_queued_when_handed_the_run_puts_the_run_in_the_queue(qapp):
    host = _host(qapp)
    _feed(host, REQUEST)
    _feed(host, HANDED)
    assert "Queued on the scheduler" in _headline(host), _headline(host)


def test_a_job_given_back_unused_ends_the_wait(qapp):
    host = _host(qapp)
    before = _headline(host)
    _feed(host, REQUEST)
    _feed(host, GIVEN_BACK)
    assert _status(host) == "", _status(host)
    assert _headline(host) == before
    # An ordinary request after it is an ordinary wait again.
    _feed(host, "[remote] scheduler: requesting allocation via: srun -p gpu\n")
    assert "Queued on the scheduler" in _headline(host), _headline(host)


def test_a_job_handed_the_run_while_its_node_boots_still_says_it_is_booting(qapp):
    """Tracker L22. Slurm had picked the early job's node and was booting it when the plate was ready; the hand-over
    turned the status line back into "Waiting for the scheduler", as though the job were queued again."""
    host = _host(qapp)
    _feed(host, REQUEST)
    _feed(host, "srun: job 4812345 queued and waiting for resources\n")
    _feed(host, "srun: job 4812345 has been allocated resources\n")
    assert _status(host).startswith("Node allocated, waiting for it to boot — "), _status(host)
    _feed(host, HANDED)
    assert _status(host).startswith("Node allocated, waiting for it to boot — "), _status(host)
    assert _status(host).endswith(" (job 4812345)"), _status(host)
    assert "Queued on the scheduler" in _headline(host), _headline(host)


def test_a_job_handed_the_run_while_still_queued_says_it_is_waiting(qapp):
    """The other half of L22: a job still queued at the hand-over is waiting, not booting."""
    host = _host(qapp)
    _feed(host, REQUEST)
    _feed(host, "srun: job 4812345 queued and waiting for resources\n")
    _feed(host, HANDED)
    assert _status(host).startswith("Waiting for the scheduler — "), _status(host)
    assert _status(host).endswith(" (job 4812345)"), _status(host)
