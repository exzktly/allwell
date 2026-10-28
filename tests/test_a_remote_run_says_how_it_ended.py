"""A remote run nobody stopped says what ended it, and never claims a job closed that it cannot see closed.

Two reports from the remote-path review (tracker D1, D3):

* **D1 — a termination the app did not send read as the user's Suspend.** The pipeline catches SIGTERM as a
  cooperative suspend and prints ``SUSPEND requested``; a walltime, a preemption, an ``scancel`` or the batch
  driver's time budget all send one. The run was then journalled ``suspended``, the dashboard read
  "Cancelled — suspended; resume to finish", the raw log stayed shut, and slurmstepd's own ``CANCELLED …
  DUE TO TIME LIMIT`` line went unread.
* **D3 — a dropped connection read as "exited with code 255", then "job closed".** ssh's reason was classed
  INFO and not quoted; "job closed — the remote process has exited" was printed the moment the local ssh
  returned, the Slurm indicator went out, and a single-shot job kept running. Stop with the network down said
  "stopped".

Driven through the real ``PipelineRunner._run_pipeline_remote`` and a real ``RemoteRunner`` whose launch is
replaced by a process that prints the scripted stream; closing the job and probing it run their real shell
against stand-in ``ssh`` / ``squeue`` / ``scancel`` binaries (``tests/_remote_standins.py``). The run's events are
then drained by a real ``AnalyzeTab``. Assertions are on the headline, the raw-log toggle, the journal on disk,
the log lines and what the Slurm indicator is told.
"""

from __future__ import annotations

import io
import os
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from tests import _remote_standins

PIDFILE = "~/.allwell-staging/r1/run.pid"
TIMELIMIT = "slurmstepd: error: *** JOB 4242 ON gpu07 CANCELLED AT 2026-10-28T10:00:00 DUE TO TIME LIMIT ***\n"
SUSPEND = ("2026-10-28 10:00:01  WARNING   SUSPEND requested (signal 15, pid 99) — finishing the FOV-group(s) "
           "already in flight\n")
DONE = "2026-10-28 09:00:00  INFO      Well A01 — done.\n"
#: Spelled here as well as in the runner, so this file measures the same thing against a tree without it.
UNKNOWN_PREFIX = "[remote] scheduler: job state unknown — "


class _Proc:
    """What ``RemoteRunner.spawn`` returns, reduced to the stream and the code."""

    def __init__(self, lines, rc):
        self.stdout = io.StringIO("".join(lines))
        self.returncode, self.pid, self._ssh = rc, 1, None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


def _runner(tmp_path, monkeypatch, *, lines, rc, launcher="srun -p gpu", stop=False):
    import services.pipeline_runner as pr
    from services import run_journal
    from services.remote_config import RemoteHost
    from services.remote_runner import LocalInputMode, RemoteRunner, ZippedInput
    from services.remote_run_state import _RemoteRun
    from services.pipeline_service import build_pipeline_args

    monkeypatch.setattr(run_journal, "index_path", lambda: tmp_path / "index.json")
    zipped = ZippedInput(zip_dir=tmp_path, mode=LocalInputMode.ZIP,
                         well_zips=(tmp_path / "A01.zip",), up_to_date_wells=())
    monkeypatch.setattr(pr, "stage_local_zips", lambda *a, **k: zipped)
    host = RemoteHost(name="hpc", ssh_alias="hpc", compute_launcher=launcher)
    remote = RemoteRunner(host)
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)

    def spawn(*a, **k):
        remote._run = _RemoteRun(run_id="r1", cache_key="k", remote_input_dir="~/i", remote_output_dir="~/o",
                                 remote_pidfile=PIDFILE, local_output_dir=out, local_input_mode=LocalInputMode.ZIP)
        if stop:
            r._stop_requested.set()
        return _Proc(lines, rc)

    monkeypatch.setattr(remote, "spawn", spawn)
    monkeypatch.setattr(remote, "estimate", lambda z: types.SimpleNamespace(format=lambda: "est"))
    monkeypatch.setattr(remote, "verify_and_cleanup", lambda ok, **k: True)
    r = pr.PipelineRunner()
    r._remote, r._remote_runner = host, remote
    opts = {"filename_schema": "experiment:channel:well:fov:timepoint", "filename_sep": "_",
            "fluor_tokens": ["GFP"], "nuclear_token": "DAPI", "csv_prefix": "measurements", "force": True}
    args = build_pipeline_args(Path("process_microscopy.py"), tmp_path / "in", out, opts)
    r._open_journal(tmp_path / "in", out, args, opts)
    r._run_pipeline_remote(tmp_path / "in", out, args, opts)
    r._close_journal()
    r._enqueue(("finished", None))
    return r, out


def _drain_into_tab(qtbot, runner):
    from analyze.tab import AnalyzeTab

    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    tab._alive = False                    # drained by hand below, not by the timer
    tab._runner, tab._log_q = runner, runner.log_q
    tab._raw_log_cb.setChecked(False)     # the user had closed the raw log
    told = []
    real = tab._slurm_indicator.on_slurm_job
    tab._slurm_indicator.on_slurm_job = lambda what, specs: (told.append(what), real(what, specs))
    lines = []
    real_log = tab._log_lines
    tab._log_lines = lambda batch: (lines.extend(batch), real_log(batch))
    tab._dashboard.reset(remote=True, total=1)
    for _ in range(50):
        tab._poll_log()
        if runner.log_q.empty():
            break
    return tab, told, lines


def _outcome(out):
    from services import run_journal

    return run_journal.read_journal(out).outcome


@pytest.fixture
def host(tmp_path, monkeypatch):
    h = _remote_standins.install(tmp_path, monkeypatch)
    h.write(PIDFILE, "99\n")                       # a pid nothing on this machine has
    h.write(PIDFILE + ".slurm", "4242\n")
    return h


# ── D1: a termination the app did not send ────────────────────────────────────

def test_a_walltime_kill_is_a_named_failure_not_the_user_s_suspend(qtbot, tmp_path, monkeypatch, host):
    runner, out = _runner(tmp_path, monkeypatch, lines=[DONE, TIMELIMIT, SUSPEND], rc=0)
    tab, _told, _lines = _drain_into_tab(qtbot, runner)
    assert _outcome(out) == "error", "a walltime kill must be journalled as a failure, not as suspended"
    assert tab._dashboard.headline_text() == (
        "Failed — Remote pipeline was terminated: the scheduler ended job 4242 at its time limit.")
    assert tab._raw_log_cb.isChecked(), "the raw log names the cause; it must be opened"


def test_a_suspend_marker_on_a_run_that_asked_for_none_is_not_a_suspend(qtbot, tmp_path, monkeypatch, host):
    runner, out = _runner(tmp_path, monkeypatch, lines=[DONE, SUSPEND], rc=0)
    tab, _told, _lines = _drain_into_tab(qtbot, runner)
    assert _outcome(out) == "error"
    assert tab._dashboard.headline_text().startswith(
        "Failed — Remote pipeline was terminated by a signal this app did not send")


def test_a_suspend_the_user_asked_for_is_still_a_suspend(qtbot, tmp_path, monkeypatch, host):
    """The other half: the evidence must not take a real suspend away from the user."""
    import services.pipeline_runner as pr

    monkeypatch.setattr(pr.PipelineRunner, "_ask_remote_to_suspend", lambda self, *a: None)
    real_spawn_tracked = pr.PipelineRunner._spawn_tracked

    def spawn_then_suspend(self, fn):
        proc = real_spawn_tracked(self, fn)
        self._remote_runner._run.suspend_request = "~/r1/suspend"
        self.suspend()
        return proc

    monkeypatch.setattr(pr.PipelineRunner, "_spawn_tracked", spawn_then_suspend)
    runner, out = _runner(tmp_path, monkeypatch, lines=[DONE, SUSPEND], rc=0)
    assert _outcome(out) == "suspended"


# ── D3: a connection that dropped ─────────────────────────────────────────────

SSH_DROP = ["Timeout, server hpc not responding.\n"]


def test_an_ssh_drop_quotes_ssh_and_never_says_the_job_closed(qtbot, tmp_path, monkeypatch, host):
    host.add_job("4242", "RUNNING")
    host.go_down()                                   # the network is gone, and stays gone
    runner, out = _runner(tmp_path, monkeypatch, lines=[DONE, *SSH_DROP], rc=255)
    tab, told, lines = _drain_into_tab(qtbot, runner)
    from services.pipeline_runner import JOB_CLOSED_LINE, classify_log_line

    headline = tab._dashboard.headline_text()
    assert headline == "Failed — Connection to hpc lost (Timeout, server hpc not responding)."
    tip = tab._dashboard._headline.toolTip()
    assert "The job may still be running on the host" in tip and "Resume" in tip
    texts = [t for t, _tag in lines]
    assert JOB_CLOSED_LINE not in texts
    assert any(t.startswith(UNKNOWN_PREFIX) for t in texts), texts
    assert told[-1] == "unknown", f"the Slurm indicator was told {told}"
    assert classify_log_line(SSH_DROP[0]) == "ERROR"
    assert host.jobs == {"4242": "RUNNING"}            # nothing reached the host, and nothing claimed it did
    assert _outcome(out) == "error"


def test_after_a_drop_the_job_the_host_still_lists_is_named(qtbot, tmp_path, monkeypatch, host):
    """The network is back by the time the run ends: the backstop cancels the job, and says so."""
    host.add_job("4242", "RUNNING")
    runner, _out = _runner(tmp_path, monkeypatch, lines=[DONE, *SSH_DROP], rc=255)
    _tab, told, lines = _drain_into_tab(qtbot, runner)
    from services.pipeline_runner import JOB_CLOSED_LINE

    assert host.cancelled == ["4242"]
    assert JOB_CLOSED_LINE in [t for t, _tag in lines]
    assert told[-1] == "closed"


def test_a_healthy_run_s_job_is_closed_only_once_squeue_no_longer_lists_it(qtbot, tmp_path, monkeypatch, host):
    runner, _out = _runner(tmp_path, monkeypatch, lines=[DONE], rc=0)    # srun took the job down with it
    _tab, told, lines = _drain_into_tab(qtbot, runner)
    from services.pipeline_runner import JOB_CLOSED_LINE

    texts = [t for t, _tag in lines]
    assert texts.count(JOB_CLOSED_LINE) == 1
    assert texts.index(JOB_CLOSED_LINE) > texts.index(DONE)
    assert told[-1] == "closed"


def test_a_job_still_listed_after_the_run_is_not_called_closed(qtbot, tmp_path, monkeypatch, host):
    """scancel refused (another user's id, a scheduler hiccup): squeue still lists it, so it is not closed."""
    from services.pipeline_runner import JOB_CLOSED_LINE

    host.add_job("4242", "RUNNING")
    (host.root / "bin" / "scancel").write_text("#!/bin/bash\nexit 1\n")
    runner, _out = _runner(tmp_path, monkeypatch, lines=[DONE], rc=0)
    _tab, told, lines = _drain_into_tab(qtbot, runner)
    texts = [t for t, _tag in lines]
    assert JOB_CLOSED_LINE not in texts
    assert any("the scheduler lists job 4242 as RUNNING" in t for t in texts), texts
    assert told[-1] == "unknown"


def test_stop_with_the_network_down_says_the_job_could_not_be_confirmed_cancelled(
        qtbot, tmp_path, monkeypatch, host):
    host.add_job("4242", "RUNNING")
    host.go_down()
    runner, out = _runner(tmp_path, monkeypatch, lines=[DONE], rc=143, stop=True)
    tab, _told, _lines = _drain_into_tab(qtbot, runner)
    headline = tab._dashboard.headline_text()
    assert headline.startswith("Cancelled — stopped here, but not confirmed on the host (hpc did not answer")
    assert "it may still be running" in headline
    assert _outcome(out) == "stopped"


def test_stop_that_reaches_the_host_is_a_plain_stop(qtbot, tmp_path, monkeypatch, host):
    host.add_job("4242", "RUNNING")
    runner, _out = _runner(tmp_path, monkeypatch, lines=[DONE], rc=143, stop=True)
    tab, _told, _lines = _drain_into_tab(qtbot, runner)
    assert host.cancelled == ["4242"]
    assert tab._dashboard.headline_text() == "Cancelled — stopped"


def test_the_unknown_line_is_the_runner_s_own():
    import services.pipeline_runner as pr

    assert pr.JOB_UNKNOWN_PREFIX == UNKNOWN_PREFIX


def test_a_local_run_terminated_from_outside_is_not_reported_complete(monkeypatch, tmp_path):
    """The local sibling of D1: a SIGTERM this app did not send (a shutdown, a kill from a terminal) makes the
    pipeline wind down and exit 0, which read as "Pipeline completed successfully" — success hooks and all."""
    import services.pipeline_runner as pr

    monkeypatch.setattr(pr, "spawn_pipeline", lambda args: _Proc([DONE, SUSPEND], 0))
    hooks = []
    r = pr.PipelineRunner()
    r._run_pipeline_subprocess(["x"], on_success=lambda: hooks.append("cleanup"))
    events = []
    while not r.log_q.empty():
        events.append(r.log_q.get_nowait())
    kinds = [k for k, _p in events]
    assert "done" not in kinds and hooks == [], "a terminated run is not a finished one"
    errors = [p for k, p in events if k == "error"]
    assert errors and errors[0].startswith("Pipeline was terminated by a signal this app did not send")


def test_a_read_failure_mid_run_is_not_called_a_failed_start(monkeypatch, tmp_path):
    """Tracker D6: a pipeline whose output stopped being readable part way was reported "Failed to start
    pipeline", sending the user to look for a launch problem that did not exist."""
    import services.pipeline_runner as pr

    class _Broken(_Proc):
        def __init__(self):
            super().__init__([], 0)
            self.stdout = self._lines()

        def _lines(self):
            yield DONE
            raise OSError(5, "Input/output error")

    signalled = []
    monkeypatch.setattr(pr, "spawn_pipeline", lambda args: _Broken())
    monkeypatch.setattr(pr.PipelineRunner, "_signal_subprocess", staticmethod(lambda p, **k: signalled.append(p)))
    r = pr.PipelineRunner()
    r._run_pipeline_subprocess(["x"])
    errors = []
    while not r.log_q.empty():
        kind, payload = r.log_q.get_nowait()
        if kind == "error":
            errors.append(payload)
    assert signalled, "the pipeline must still be signalled"
    assert errors == ["Pipeline stopped part way through the run: reading its output failed "
                      "(OSError: [Errno 5] Input/output error), so it was stopped. The wells that finished "
                      "are kept.\n"]
