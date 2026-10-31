"""The ``srun`` wrapper: what it retries, how long it waits, and what it refuses.

Three things the wrapper around a scheduled launch
(``services.remote_srun_retry.wrap_srun_launch``) has to get right, each run
here under the system ``sh`` with the stand-in cluster of
``tests/test_srun_boot_retry.py``:

* **A run that started is never replayed.** ``srun: error: Node failure on X``
  is printed both when a granted node fails to boot and when a node dies under
  a running job, and the wrapper retried both — re-running the batch driver
  from batch 1 or a single-shot run from its top with ``--force``. Only a
  failure before the job's own first line (``ALLOCATION_GRANTED``) is a boot
  failure. The batch driver is also made safe against a replay.
* **Max queue wait is a deadline.** The host's ``queue_wait_timeout_min`` was
  only ever an ssh keepalive count, which a queued job never trips; a run sat
  in the queue for as long as the cluster kept it there. The wrapper now
  cancels a job still waiting for its node when the setting's time is up, and
  says so naming the setting; ``0`` still waits indefinitely.
* **A launcher that sends the job's output elsewhere is refused**
  (``-o`` / ``--output`` / ``-e`` / ``--error`` / ``--pty``): the app reads the
  whole run off ``srun``'s stdout and would hear nothing.
"""

from __future__ import annotations

import shlex
import subprocess
import threading
from pathlib import Path

import pytest

from services.remote_srun_retry import (
    ALLOCATION_GRANTED,
    QUEUE_WAIT_EXIT,
    queue_wait_seconds,
    redirect_launcher_words,
    redirect_words_message,
    wrap_srun_launch,
)
from tests.test_srun_boot_retry import _fake_cluster, _write_exe

_SINFO = "n01 idle\nn02 idle~\n"
#: The job's script, as the app composes it: its first line says the node is
#: granted (`services.remote_commands`), then it works.
_INNER = f'echo "{ALLOCATION_GRANTED} — job 1 on n01 (partition cpu)"; echo "pipeline ran on the node"'


def _cluster(tmp_path: Path, outcomes) -> Path:
    """`_fake_cluster`, plus outcomes this file needs: written over the
    numbered outcome files it leaves, after it has made them."""
    extra = {
        # The job starts on the node, works, and the node dies under it.
        "node_fail_midrun": ('shift_to_bash "$@"\n'
                             'echo "srun: error: Node failure on $NODE" >&2\nexit 1\n'),
        # Queued until scancel removes the marker (or SIGTERM); gives up by itself after 15 s.
        "pending_long": ('echo "srun: job $JOB queued and waiting for resources" >&2\n'
                         ': > "$S/pending$JOB"\n'
                         "trap 'rm -f \"$S/pending$JOB\"' TERM\n"
                         'i=0; while [ "$i" -lt 150 ] && [ -f "$S/pending$JOB" ]; do sleep 0.1; i=$((i+1)); done\n'
                         'echo "srun: error: Job allocation $JOB has been revoked" >&2\nexit 1\n'),
        # Queued for three seconds, then granted and run.
        "pending_then_run": ('echo "srun: job $JOB queued and waiting for resources" >&2\nsleep 3\n'
                             'echo "srun: job $JOB has been allocated resources" >&2\nshift_to_bash "$@"\n'),
        # Allocated at once, then the node boots for a long while before the job's script runs.
        "booting_long": ('echo "srun: job $JOB queued and waiting for resources" >&2\n'
                         'echo "srun: job $JOB has been allocated resources" >&2\n'
                         ': > "$S/pending$JOB"\n'
                         "trap 'rm -f \"$S/pending$JOB\"' TERM\n"
                         'i=0; while [ "$i" -lt 150 ] && [ -f "$S/pending$JOB" ]; do sleep 0.1; i=$((i+1)); done\n'
                         '[ -f "$S/pending$JOB" ] || { echo "srun: error: Job allocation $JOB has been revoked" >&2; exit 1; }\n'
                         'shift_to_bash "$@"\n'),
    }
    plain = [(k if k not in extra else "run", node) for k, node in outcomes]
    bindir = _fake_cluster(tmp_path, plain, _SINFO)
    state = bindir.parent / "state"
    for i, (kind, node) in enumerate(outcomes, start=1):
        if kind in extra:
            _write_exe(state / f"outcome{i}", f"JOB={1000 + i}\nNODE={node}\n" + extra[kind])
    return bindir


def _wrapped(tmp_path, bindir, launcher="srun -p cpu", **kw):
    pidfile = tmp_path / "run.pid"
    kw.setdefault("boot_retry_pause_s", 0)
    cmd = wrap_srun_launch(launcher, shlex.quote(_INNER), shlex.quote(str(pidfile)),
                           shlex.quote(str(pidfile) + ".srun-err"), **kw)
    out = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True,
                         env={"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path)}, timeout=60)
    state = bindir.parent / "state"
    calls = int((state / "count").read_text()) if (state / "count").exists() else 0
    cancelled = (state / "scancelled").read_text().split() if (state / "scancelled").exists() else []
    return out, calls, cancelled


# ── a run that started is never replayed (review E3) ────────────────────────

def test_a_node_that_fails_under_a_running_job_is_not_retried(tmp_path):
    bindir = _cluster(tmp_path, [("node_fail_midrun", "n01"), ("run", "n03")])
    out, calls, _ = _wrapped(tmp_path, bindir)
    assert calls == 1, f"the started run was asked for again ({calls} launches):\n{out.stdout}"
    assert out.returncode == 1
    assert "pipeline ran on the node" in out.stdout
    assert ("ERROR node n01 failed while the run was going on it — not retried, because asking "
            "again would start the run from its beginning") in out.stdout
    assert "failed to boot" not in out.stdout


def test_a_node_that_fails_to_boot_before_the_job_starts_is_still_retried(tmp_path):
    """The control: no job line yet, so the node never ran the job."""
    bindir = _cluster(tmp_path, [("boot_fail", "n01"), ("run", "n03")])
    out, calls, _ = _wrapped(tmp_path, bindir)
    assert calls == 2, out.stdout
    assert out.returncode == 0, out.stdout
    assert "node(s) n01 failed to boot — requesting another node in 0 s (retry 1; failed to boot so far: n01)" in out.stdout


@pytest.mark.parametrize("compose", ["single", "driver"])
def test_both_launches_print_the_line_the_wrapper_reads_as_started(compose):
    """The wrapper decides "started" by the job's first line. Both launch shapes
    must print exactly that line, or every failure reads as a boot failure."""
    from services.remote_commands import (
        _compose_driver_cmd,
        _compose_remote_cmd,
    )

    if compose == "single":
        cmd = _compose_remote_cmd("srun -p cpu", "/r/run.pid", ["python", "pm.py"], cache_hit=False)
    else:
        cmd = _compose_driver_cmd("srun -p cpu", "/r/run.pid", "echo driver")
    inner = cmd.split(" bash -lc ", 1)[1]
    assert ALLOCATION_GRANTED in shlex.split(inner)[0], inner[:300]


def _driver(tmp_path, *, done_1: bool, staged_flag_2: bool) -> subprocess.CompletedProcess:
    """The real batch driver over two batches, its pipeline a listing of `in/`.

    Both batches are flagged ready and neither has a staged ``b<i>`` directory
    left: batch 2's archive is already in ``in/`` — the state a pass leaves
    when it is interrupted after moving batch 2 in.
    """
    from services.remote_commands import (
        BATCH_DONE,
        BATCH_READY,
        BATCH_STAGED,
        _batch_driver_snippet,
    )

    run = tmp_path / "run"
    (run / "in").mkdir(parents=True)
    (run / "in" / "A03.zip").write_bytes(b"batch two's archive")
    for i in (1, 2):
        (run / BATCH_READY.format(i=i)).touch()
    if done_1:
        (run / BATCH_DONE.format(i=1)).write_text("0\n")
    if staged_flag_2:
        (run / BATCH_STAGED.format(i=2)).touch()
    pipeline = ["sh", "-c", f"ls {run}/in >> {run}/seen"]
    snippet = _batch_driver_snippet(str(run), 2, pipeline)
    return subprocess.run(["bash", "-c", snippet], capture_output=True, text=True, timeout=30)


def test_a_replayed_driver_skips_finished_batches_and_keeps_the_input_in_flight(tmp_path):
    out = _driver(tmp_path, done_1=True, staged_flag_2=True)
    run = tmp_path / "run"
    assert "batch-driver: batch 1 already finished (rc=0) — not run again" in out.stdout, out.stdout
    assert "batch-driver: batch 1 finished rc=0" in out.stdout
    assert "batch-driver: batch 2 finished rc=0" in out.stdout, out.stdout
    assert (run / "in" / "A03.zip").exists(), "the batch in flight lost its input"
    assert (run / "seen").read_text().split() == ["A03.zip"], (
        "batch 1 ran again, or batch 2 ran over something other than its own input")


def test_without_its_staged_directory_a_fresh_batch_is_not_run_over_leftovers(tmp_path):
    """The control for the guard above: in a launch that was not replayed there
    is no staged flag, and `in/` holding archives is the *previous* batch's
    leftovers — running the pipeline over them would process wells twice. The
    batch fails to stage, as it always did."""
    out = _driver(tmp_path, done_1=True, staged_flag_2=False)
    assert "ERROR batch 2: could not stage its input" in out.stdout, out.stdout
    assert "batch-driver: batch 2 finished rc=90" in out.stdout
    assert not (tmp_path / "run" / "seen").exists()


# ── Max queue wait is a deadline (review C3) ────────────────────────────────

def test_a_job_still_queued_at_the_max_queue_wait_is_cancelled_and_says_so(tmp_path):
    bindir = _cluster(tmp_path, [("pending_long", ""), ("run", "n03")])
    out, calls, cancelled = _wrapped(tmp_path, bindir, queue_wait_s=2)
    assert out.returncode == QUEUE_WAIT_EXIT, out.stdout + out.stderr
    assert cancelled == ["1001"], out.stdout
    assert calls == 1, "a run past its queue wait was asked for again"
    assert ("ERROR no node was granted within 2 s, this host's Max queue wait — cancelled job 1001 "
            "and stopped. Raise Max queue wait for this host in Remotes") in out.stdout, out.stdout
    assert "pipeline ran on the node" not in out.stdout


def test_a_node_that_is_still_booting_counts_as_waiting(tmp_path):
    """The setting is how long to wait for a node "to be allocated (or spun
    up)": the run has not started until the job's first line."""
    bindir = _cluster(tmp_path, [("booting_long", "n02")])
    out, _calls, cancelled = _wrapped(tmp_path, bindir, queue_wait_s=2)
    assert out.returncode == QUEUE_WAIT_EXIT, out.stdout
    assert cancelled == ["1001"]


def test_a_job_granted_in_time_is_never_cancelled_however_long_it_runs(tmp_path):
    bindir = _cluster(tmp_path, [("pending_then_run", "n01")])
    out, _calls, cancelled = _wrapped(tmp_path, bindir, queue_wait_s=5)
    assert out.returncode == 0, out.stdout
    assert cancelled == []
    assert "pipeline ran on the node" in out.stdout


def test_wait_indefinitely_has_no_deadline(tmp_path):
    bindir = _cluster(tmp_path, [("pending_then_run", "n01")])
    out, _calls, cancelled = _wrapped(tmp_path, bindir, queue_wait_s=0)
    assert out.returncode == 0, out.stdout
    assert cancelled == []
    assert "_aw_t0 )) -ge" not in wrap_srun_launch("srun -p cpu", "x", "p", "e", queue_wait_s=0)


@pytest.mark.parametrize("minutes, seconds", [(30, 1800), (0, 0), (7, 420)])
def test_the_host_s_setting_is_the_deadline_the_single_launch_is_sent(
        monkeypatch, tmp_path, stop_fetchers, minutes, seconds):
    from services.remote_commands import unpack_login_shell_command
    from tests.test_remote_allocation_overlap import _spawn_recording_order

    spawned: list[list[str]] = []
    _spawn_recording_order(monkeypatch, tmp_path, stop_fetchers, "srun --partition=gpu",
                           spawned=spawned, queue_wait_timeout_min=minutes)
    script = unpack_login_shell_command(spawned[0][-1])
    if seconds:
        assert f"_aw_t0 )) -ge {seconds} ]" in script
        assert f"no node was granted within {minutes} min" in script
    else:
        assert "_aw_t0 )) -ge" not in script


def test_the_host_s_setting_is_the_deadline_the_batch_driver_is_sent(tmp_path, monkeypatch):
    from services.remote_commands import unpack_login_shell_command
    from tests.test_batch_driver import _FakeProc, _batched, _stub_runner

    proc = _FakeProc()
    p = _batched(tmp_path, ["A01"])
    _stub_runner(p, proc, [])
    p._runner.host.queue_wait_timeout_min = 3
    spawns: list = []
    monkeypatch.setattr("subprocess.Popen", lambda argv, **kw: spawns.append(argv) or proc)
    worker = threading.Thread(target=p._run_batches, daemon=True)
    worker.start()
    proc.say("[remote] batch-driver: batch 1 finished rc=0\n")
    proc.finish()
    worker.join(timeout=20)
    assert spawns, "the driver was never launched"
    script = unpack_login_shell_command(spawns[0][-1])
    assert "_aw_t0 )) -ge 180 ]" in script
    assert "no node was granted within 3 min" in script


def test_queue_wait_seconds_reads_the_setting():
    from services.remote_config import RemoteHost

    assert queue_wait_seconds(RemoteHost(name="h", ssh_alias="h", queue_wait_timeout_min=12)) == 720
    assert queue_wait_seconds(RemoteHost(name="h", ssh_alias="h", queue_wait_timeout_min=0)) == 0
    assert queue_wait_seconds(RemoteHost(name="h", ssh_alias="h", queue_wait_timeout_min=-5)) == 0


# ── a launcher that sends the output elsewhere is refused (review E5) ───────

@pytest.mark.parametrize("launcher, words", [
    ("srun -p cpu -o job.log", ["-o"]),
    ("srun -p cpu -ojob.log", ["-ojob.log"]),
    ("srun --output=job.log -p cpu", ["--output=job.log"]),
    ("srun -p cpu --error /tmp/e", ["--error"]),
    ("srun -e err.txt --time=1:00:00", ["-e"]),
    ("srun --pty -p cpu", ["--pty"]),
    ("srun -p cpu --exclusive --exclude=n01 -x n02 --time=1:00:00", []),
    ("sbatch --wait -o x", []),
])
def test_output_redirection_in_an_srun_launcher_is_found(launcher, words):
    assert redirect_launcher_words(launcher) == words


@pytest.mark.parametrize("launcher", ["srun -p cpu --output=job.log", "srun --pty -p cpu"])
def test_a_launch_that_would_send_its_output_elsewhere_is_refused(launcher):
    from services.remote_commands import _compose_driver_cmd, _compose_remote_cmd

    with pytest.raises(ValueError, match="sends the run's output to a file or a terminal"):
        _compose_remote_cmd(launcher, "/r/run.pid", ["python", "pm.py"], cache_hit=False)
    with pytest.raises(ValueError, match="sends the run's output to a file or a terminal"):
        _compose_driver_cmd(launcher, "/r/run.pid", "echo driver")


@pytest.mark.qt
def test_the_remotes_dialog_will_not_save_such_a_launcher(qtbot, monkeypatch):
    import analyze.remote_dialog as ard

    written: list = []
    monkeypatch.setattr(ard, "write_remote", lambda host: written.append(host))
    said: list = []
    monkeypatch.setattr(ard.QMessageBox, "critical", lambda *a, **k: said.append(a[2]))
    dlg = ard.RemoteFormDialog(None, host=None)
    qtbot.addWidget(dlg)
    dlg._name_edit.setText("hpc")
    dlg._ssh_alias_edit.setText("hpc.example")
    dlg._compute_loc_combo.setCurrentIndex(1)
    dlg._compute_launcher_edit.setText("srun -p cpu --output=job.log")

    dlg._on_ok()

    assert written == [], "a launcher that hides the run's output was saved"
    assert said == [redirect_words_message("srun -p cpu --output=job.log")]


def test_a_node_that_fails_while_the_early_job_waits_for_its_run_is_not_called_a_run(tmp_path):
    """Tracker L24. The job asked for before staging (``services.remote_reservation``) prints its first line, then
    waits on its node for the app to hand it the run. A node that failed in that wait was reported as failing
    "while the run was going on it … resume the run to continue from where it stopped" — but nothing had run, and
    the app asks for another node once the run is ready. Still not retried here: the job's first line is in."""
    from services.remote_srun_retry import HANDOVER_WAIT_LINE

    bindir = _cluster(tmp_path, [("run", "n01"), ("run", "n03")])
    _write_exe(bindir.parent / "state" / "outcome1", (
        "JOB=1001\nNODE=n01\n"
        f'echo "{ALLOCATION_GRANTED} — job 1001 on n01 (partition cpu)"\n'
        f'echo "{HANDOVER_WAIT_LINE}"\n'
        'echo "srun: error: Node failure on $NODE" >&2\nexit 1\n'))
    out, calls, _ = _wrapped(tmp_path, bindir)
    assert calls == 1, out.stdout
    assert out.returncode == 1
    assert ("[remote] scheduler: ERROR node n01 failed while it waited for the run to be prepared — nothing had "
            "started on it; another node is asked for once the run is ready") in out.stdout, out.stdout
    assert "resume the run" not in out.stdout, out.stdout


def test_a_node_that_fails_after_the_early_job_took_its_run_is_still_a_run(tmp_path):
    """The other half of L24: once the early job has started the run it was handed, a node failure is the run's."""
    from services.remote_srun_retry import HANDOVER_TAKEN_LINE, HANDOVER_WAIT_LINE

    bindir = _cluster(tmp_path, [("run", "n01"), ("run", "n03")])
    _write_exe(bindir.parent / "state" / "outcome1", (
        "JOB=1001\nNODE=n01\n"
        f'echo "{ALLOCATION_GRANTED} — job 1001 on n01 (partition cpu)"\n'
        f'echo "{HANDOVER_WAIT_LINE}"\necho "{HANDOVER_TAKEN_LINE}"\n'
        'echo "srun: error: Node failure on $NODE" >&2\nexit 1\n'))
    out, calls, _ = _wrapped(tmp_path, bindir)
    assert calls == 1, out.stdout
    assert ("ERROR node n01 failed while the run was going on it — not retried, because asking again would start "
            "the run from its beginning; resume the run to continue from where it stopped") in out.stdout, out.stdout
