"""A remote run that goes silent is noticed, said in words, and checked with the host.

Reported (tracker C1; incident 1): nothing in the app noticed a silent remote stream. There was no last-line
timestamp, and after an hour of silence every surface still read "Processing N/M", "ETA 0s" and a stale
transfer figure — exactly what a healthy run shows.

Driven through the real ``PipelineRunner`` (its own clock, set back as though the stream had been quiet) inside
a real ``AnalyzeTab``, with the probe running its real shell against stand-in ``ssh`` / ``squeue``
(``tests/_remote_standins.py``). The assertions are on the status line, the log lines, and the probe's answer.
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from tests import _remote_standins

PIDFILE = "~/.allwell-staging/r1/run.pid"
LAST = "2026-10-28 09:00:00  INFO      Well A01 — FOV 3/12 done"


@pytest.fixture
def setup(qtbot, tmp_path, monkeypatch):
    from analyze.tab import AnalyzeTab
    from services.pipeline_runner import PipelineRunner
    from services.remote_config import RemoteHost
    from services.remote_runner import LocalInputMode, RemoteRunner
    from services.remote_run_state import _RemoteRun

    host = _remote_standins.install(tmp_path, monkeypatch)
    host.write(PIDFILE, "2147483646\n")
    host.write(PIDFILE + ".slurm", "4242\n")
    remote_host = RemoteHost(name="hpc", ssh_alias="hpc", compute_launcher="srun -p gpu")
    remote = RemoteRunner(remote_host)
    remote._run = _RemoteRun(run_id="r1", cache_key="k", remote_input_dir="~/i", remote_output_dir="~/o",
                             remote_pidfile=PIDFILE, local_output_dir=tmp_path, local_input_mode=LocalInputMode.ZIP)
    runner = PipelineRunner()
    runner._remote, runner._remote_runner = remote_host, remote
    runner._enqueue(("line", LAST + "\n"))
    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    tab._alive = False
    tab._runner, tab._log_q = runner, runner.log_q
    tab._poll_log()
    tab._running = True
    tab._dashboard.reset(remote=True, total=4)
    tab._reset_run_phase(remote=True)
    logged = []
    real = tab._log_lines
    tab._log_lines = lambda batch: (logged.extend(t for t, _tag in batch), real(batch))
    yield tab, runner, host, logged
    tab._running = False


def _quiet_for(runner, seconds):
    runner._last_output_at = time.monotonic() - seconds


def _probe_answer(tab, runner, logged):
    runner._silence_probe.join(30)
    tab._poll_log()
    return [t for t in logged if t.startswith("[remote] no-output check:")]


def test_twenty_minutes_of_silence_is_said_and_checked_with_the_host(setup):
    tab, runner, host, logged = setup
    host.add_job("4242", "RUNNING")
    _quiet_for(runner, 21 * 60)
    tab._check_stream()
    assert tab._dashboard.status_text() == "No output from hpc for 21m00s — checking it is still running…"
    warn = [t for t in logged if t.startswith("[warn] No output from hpc")]
    assert warn and warn[0].startswith(f"[warn] No output from hpc for 21m00s; the last line was: {LAST}")
    assert "press Stop, then Resume" in warn[0] and "not by itself a failure" in warn[0]
    answer = _probe_answer(tab, runner, logged)
    assert answer == ["[remote] no-output check: the scheduler lists job 4242 as RUNNING; the run is still "
                      "going there, so it is only quiet; nothing to do.\n"]
    assert runner.seconds_since_output >= 21 * 60 - 1, "the check's own answer must not count as output"


def test_a_dead_job_is_named_with_what_to_do(setup):
    tab, runner, host, logged = setup                  # the scheduler no longer lists 4242
    _quiet_for(runner, 25 * 60)
    tab._check_stream()
    assert _probe_answer(tab, runner, logged) == [
        "[remote] no-output check: the scheduler no longer lists job 4242; the run is not running there any "
        "more — press Stop, then Resume from the Analyze banner to download the wells it finished and run "
        "the rest.\n"]


def test_a_healthy_quiet_stretch_says_nothing(setup):
    """Nineteen minutes of one long step: no warning. The threshold is above any quiet stretch a healthy run has."""
    tab, runner, _host, logged = setup
    _quiet_for(runner, 19 * 60)
    tab._check_stream()
    assert not any("No output" in t for t in logged)
    assert runner._silence_probe is None


def test_a_scheduler_queue_wait_is_not_silence(setup):
    tab, runner, _host, logged = setup
    tab._apply_progress_event("sched", ("waiting", "", "srun -p gpu"))
    _quiet_for(runner, 3 * 3600)
    tab._check_stream()
    assert not any("No output" in t for t in logged)


def test_the_warning_goes_when_output_resumes_and_is_said_once_per_silence(setup):
    tab, runner, host, logged = setup
    host.add_job("4242", "RUNNING")
    _quiet_for(runner, 21 * 60)
    tab._check_stream()
    tab._check_stream()                                 # 30 s later: still the same silence
    assert sum(t.startswith("[warn] No output") for t in logged) == 1
    _probe_answer(tab, runner, logged)
    runner._enqueue(("line", "2026-10-28 09:21:00  INFO      Well A01 — FOV 4/12 done\n"))
    tab._check_stream()
    assert tab._dashboard.status_text() == ""
