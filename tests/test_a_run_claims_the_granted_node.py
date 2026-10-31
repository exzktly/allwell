"""A run claims the granted node, one run at a time, and asks for none of its own beside it (tracker H7, H8).

``services.remote_hold.claim_node`` / ``GrantedRun`` are what ``PipelineRunner`` calls as a remote run starts. The
claim is a ``busy`` marker in the hold's directory, created atomically on the host and kept fresh by the run's own
heartbeat; it is what stops the node's idle clock while a plate is prepared and uploaded, and what makes a second run
wait. The scripts run for real under ``bash`` against the stand-in cluster (``tests/_hold_cluster.py``).

Wrong implementations these are written to reject: a second run that gives up waiting and asks for a node of its own
(two runs on one A10G is the out-of-memory failure that started this); a claim that is check-then-create, which two
windows can both win; trusting a marker its run's app left behind; a run that outlasts the node's time limit and is
killed by Slurm part-way; and one that is "refreshed" on a fresh node that is the same dead one.
"""

from __future__ import annotations

import shutil
import threading
import types

import pytest

from services.remote_config import RemoteHost
from services.remote_hold import (
    GrantedNode,
    GrantedRun,
    HoldError,
    HoldQueueWaitSpent,
    claim_node,
    claim_script,
    hold_paths,
    node_for,
    run_estimate_s,
)
from tests._hold_cluster import HOST, Cluster, Script, granted_files

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@pytest.fixture
def cluster(tmp_path):
    return Cluster(tmp_path)


@pytest.fixture(autouse=True)
def _fresh_registry():
    """The registry is per process; a test must not meet another's handle."""
    from services import remote_hold

    remote_hold._NODES.clear()
    yield
    for node in remote_hold.live_nodes():
        node.release("test over")
    remote_hold._NODES.clear()


def _granted(cluster, host, *, running_for_s=60, limit_s=86400, job=4001, state="RUNNING"):
    """A node the hold script has granted, and that its job is RUNNING: what the app finds on the host."""
    import time

    cluster.set_job(job, state, "None", "gpu-node-1")
    d = cluster.hold_dir(host)
    d.mkdir(parents=True, exist_ok=True)
    (d / "job").write_text(str(job), encoding="utf-8")
    granted_files(cluster, host, granted_at=int(time.time()) - running_for_s, limit_s=limit_s, job=job)


def _claim(cluster, host, sleep, lines, **kw):
    return claim_node(host, cluster, log_fn=lines.append, sleep=sleep, clock=sleep.clock, **kw)


# ── claiming ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_host_with_no_granted_node_is_left_to_ask_for_its_own(cluster):
    host = RemoteHost(**HOST)
    lines: list[str] = []
    assert _claim(cluster, host, Script(cluster, []), lines) is None
    assert lines == []
    # And a host with no scheduler is never asked.
    assert claim_node(RemoteHost(name="x", ssh_alias="x"), cluster, log_fn=lines.append) is None


def test_a_granted_node_is_claimed_and_given_back(cluster):
    host = RemoteHost(**HOST)
    _granted(cluster, host)
    lines: list[str] = []
    lease = _claim(cluster, host, Script(cluster, []), lines)
    busy = cluster.hold_dir(host) / "busy"
    assert lease is not None and busy.exists()
    assert lease.target.job_id == "4001" and lease.target.node == "gpu-node-1" and lease.target.cpus == 8
    assert any("using gpu-node-1 (NVIDIA A10G), job 4001" in line for line in lines)
    lease.release()
    assert not busy.exists()
    lease.release()                                  # idempotent


def test_the_claim_keeps_the_marker_fresh_for_as_long_as_the_run_lives(cluster):
    """The marker is the run's heartbeat: a stale one reads as a run whose app died."""
    import os
    import time

    host = RemoteHost(**HOST)
    _granted(cluster, host)
    lease = _claim(cluster, host, Script(cluster, []), [])
    busy = cluster.hold_dir(host) / "busy"
    old = time.time() - 3600
    os.utime(busy, (old, old))
    lease._beater.beat()                              # what the heartbeat thread does every 30 s
    assert time.time() - busy.stat().st_mtime < 30
    lease.release()


def test_a_second_run_waits_for_the_node_and_never_asks_for_its_own(cluster):
    """Wrong implementation: giving up on a busy node and returning None, which launches a node of the run's own."""
    host = RemoteHost(**HOST)
    _granted(cluster, host)
    first = _claim(cluster, host, Script(cluster, []), [])
    lines: list[str] = []
    sleep = Script(cluster, [lambda: None, first.release])           # the first run ends on the second nap
    second = _claim(cluster, host, sleep, lines)
    assert second is not None and len(sleep.naps) == 2
    assert sum("busy with another run; waiting" in line for line in lines) == 1
    second.release()


def test_a_node_that_stays_busy_ends_the_wait_with_the_queue_wait_and_still_asks_for_none(cluster):
    host = RemoteHost(**{**HOST, "queue_wait_timeout_min": 1})
    _granted(cluster, host)
    first = _claim(cluster, host, Script(cluster, []), [])
    sleep = Script(cluster, [])
    with pytest.raises(HoldQueueWaitSpent, match="busy with another run for 1 min.*no node of its own"):
        _claim(cluster, host, sleep, [])
    assert sum(sleep.naps) > 60
    first.release()


def test_stop_ends_the_wait_for_a_busy_node(cluster):
    host = RemoteHost(**HOST)
    _granted(cluster, host)
    first = _claim(cluster, host, Script(cluster, []), [])
    stop = threading.Event()
    stop.set()
    with pytest.raises(HoldError, match="stopped while it waited"):
        _claim(cluster, host, Script(cluster, []), [], stop_event=stop)
    first.release()


def test_two_windows_cannot_both_claim_the_node():
    """The create is `set -C`, atomic on the host. Wrong implementation: test-then-create, which two windows can both
    pass. Forty processes race for the marker, six times over; exactly one may win each round. (A race cannot be
    forced from outside the script, so this is evidence of the property and not a proof: it fails a check-then-create
    claim often, not always.)"""
    import subprocess
    import tempfile
    from pathlib import Path

    racers, rounds = 40, 6
    with tempfile.TemporaryDirectory() as tmp:
        paths = hold_paths(RemoteHost(**{**HOST, "remote_staging_dir": tmp}))
        Path(paths.dir).mkdir(parents=True)
        script = claim_script(paths)
        for _ in range(rounds):
            Path(paths.busy).unlink(missing_ok=True)
            answers: list[str] = []
            lock = threading.Lock()
            gate = threading.Barrier(racers)

            def go():
                gate.wait()
                out = subprocess.run(["bash", "-s"], input=script, capture_output=True, text=True).stdout.strip()
                with lock:
                    answers.append(out)

            threads = [threading.Thread(target=go) for _ in range(racers)]
            for th in threads:
                th.start()
            for th in threads:
                th.join(60)
            assert sorted(answers) == ["AW_BUSY"] * (racers - 1) + ["AW_LEASED"], answers


def test_a_marker_its_run_left_behind_is_not_a_reason_to_wait_for_ever(cluster):
    """Wrong implementation: trusting any busy file. A run whose app died leaves one, and the hold script already
    reads one that old as not busy."""
    import os
    import time

    host = RemoteHost(**HOST)
    _granted(cluster, host)
    stale = cluster.hold_dir(host) / "busy"
    stale.write_text("", encoding="utf-8")
    old = time.time() - 3600
    os.utime(stale, (old, old))
    lease = _claim(cluster, host, Script(cluster, []), [])
    assert lease is not None
    lease.release()


def test_a_node_that_is_still_being_granted_is_waited_for(cluster):
    host = RemoteHost(**HOST)
    cluster.set_job(4001, "PENDING", "Resources", "")
    d = cluster.hold_dir(host)
    d.mkdir(parents=True)
    (d / "job").write_text("4001", encoding="utf-8")
    lines: list[str] = []
    sleep = Script(cluster, [lambda: (cluster.set_job(4001, "RUNNING", "None", "gpu-node-1"), granted_files(cluster, host))])
    lease = _claim(cluster, host, sleep, lines)
    assert lease is not None and any("still being granted; waiting" in line for line in lines)
    lease.release()


def test_a_released_node_sends_the_run_to_ask_for_its_own_and_says_why(cluster):
    host = RemoteHost(**HOST)
    _granted(cluster, host)
    (cluster.hold_dir(host) / "ended").write_text("idle 30 min\n", encoding="utf-8")
    lines: list[str] = []
    assert _claim(cluster, host, Script(cluster, []), lines) is None
    assert any("was released: idle 30 min; this run asks for a node of its own" in line for line in lines)


def test_a_host_that_cannot_be_reached_is_left_to_the_runs_own_ssh_to_explain(cluster, monkeypatch):
    host = RemoteHost(**HOST)
    lines: list[str] = []

    def down(script, *, check, timeout_s=60):
        return types.SimpleNamespace(returncode=255, stdout="", stderr="ssh: connect to host: timed out")

    monkeypatch.setattr(cluster, "_ssh_script", down)
    assert _claim(cluster, host, Script(cluster, []), lines) is None
    assert any("could not tell whether this host has a granted node" in line and "node of its own" in line
               for line in lines)
    assert not any("timed out" in line or "ssh" in line for line in lines), "the host's own error is the run's to report"


# ── the registry ──────────────────────────────────────────────────────────────────────────────────────────────────

def test_one_handle_per_host_in_this_process(cluster):
    host = RemoteHost(**HOST)
    a = node_for(host, cluster, log_fn=lambda _m: None)
    assert node_for(host, cluster, log_fn=lambda _m: None) is a
    other = RemoteHost(**{**HOST, "name": "other"})
    assert node_for(other, cluster, log_fn=lambda _m: None) is not a
    assert isinstance(a, GrantedNode)


# ── the run's length against the node's time left ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("launcher, n_wells, batch, nanowell, seconds", [
    ("srun --time=4:00:00", 10, 5, False, 4 * 3600 * 3),     # 2 batches + the transfer allowance, as the node-asking path scales it
    ("srun --time=4:00:00", 10, 0, False, 4 * 3600),          # single-shot: one budget
    ("srun --time=4:00:00", 10, 5, True, 4 * 3600 * 4),       # a nanowell run adds its re-anchoring pass
    ("srun --gres=gpu:1", 10, 5, False, 0),                    # no --time: nothing to compare
])
def test_the_runs_length_is_the_scheduler_commands_time_times_its_batches(launcher, n_wells, batch, nanowell, seconds):
    host = RemoteHost(name="h", ssh_alias="h", compute_launcher=launcher)
    assert run_estimate_s(host, n_wells, batch, nanowell) == seconds


def _runner_for(cluster, host):
    """A real `RemoteRunner` over the stand-in cluster: the facade `GrantedRun` calls is `claim_granted_node`."""
    from services.remote_runner import RemoteRunner

    runner = RemoteRunner(host)
    runner._cache = cluster
    return runner


def test_a_run_that_fits_in_the_time_left_stays_on_the_node(cluster):
    host = RemoteHost(**HOST)                                 # --time=4:00:00 a batch
    _granted(cluster, host, running_for_s=60, limit_s=86400)  # 23 h 59 min left
    lines: list[str] = []
    runner = _runner_for(cluster, host)
    run = GrantedRun(runner, batch_size=5, nanowell=False, log_fn=lines.append, stop_event=threading.Event(),
                     sleep=Script(cluster, []), clock=Script(cluster, []).clock)
    run.claim()
    assert run.active and runner.step_target.job_id == "4001"
    run.check_fit(10)                                         # 12 h wanted, ~24 h left
    assert runner.step_target.job_id == "4001" and cluster.sbatch_calls() == []
    run.release()
    assert runner.step_target is None and not run.active
    assert not (cluster.hold_dir(host) / "busy").exists()


def test_a_run_that_would_outlast_the_node_gets_a_fresh_one_and_runs_there(cluster):
    """Wrong implementations: starting it anyway (Slurm kills it part-way at the limit), or releasing the node and
    running with none."""
    host = RemoteHost(**HOST)
    _granted(cluster, host, running_for_s=23 * 3600, limit_s=24 * 3600)      # 1 h left
    lines: list[str] = []
    runner = _runner_for(cluster, host)

    def fresh_node_is_granted():
        cluster.set_job(4002, "RUNNING", "None", "gpu-node-2")
        granted_files(cluster, host, node="gpu-node-2", job=4002, limit_s=24 * 3600)

    (cluster.fake / "next_id").write_text("4002\n", encoding="utf-8")       # the replacement is the cluster's next job
    sleep = Script(cluster, [], job=4002)
    sleep.steps = [lambda: None, lambda: None, fresh_node_is_granted]
    run = GrantedRun(runner, batch_size=5, nanowell=False, log_fn=lines.append, stop_event=threading.Event(),
                     sleep=sleep, clock=sleep.clock)
    run.claim()
    assert runner.step_target.job_id == "4001"
    run.check_fit(10)                                         # 12 h wanted, 1 h left
    assert any("needs about 12 h 0 min and the granted node has" in line and "requesting a fresh one" in line
               for line in lines), lines
    assert any("4001" in s for s in cluster.scancels()), "the old node must be released"
    assert len(cluster.sbatch_calls()) == 1, "exactly one fresh node is requested"
    assert runner.step_target.job_id == "4002" and runner.step_target.node == "gpu-node-2"
    assert run.active
    run.release()


def test_a_command_with_no_time_cannot_be_compared_and_stays_on_the_node(cluster):
    host = RemoteHost(**{**HOST, "compute_launcher": "srun --partition=gpu --gres=gpu:1"})
    _granted(cluster, host, running_for_s=23 * 3600, limit_s=24 * 3600)
    runner = _runner_for(cluster, host)
    run = GrantedRun(runner, batch_size=5, nanowell=False, log_fn=lambda _m: None, stop_event=threading.Event(),
                     sleep=Script(cluster, []), clock=Script(cluster, []).clock)
    run.claim()
    run.check_fit(10)
    assert runner.step_target.job_id == "4001" and cluster.sbatch_calls() == []
    run.release()


def test_the_time_left_is_read_on_the_nodes_clock(cluster):
    """`granted_at` is the node's epoch; the state carries the node's `date +%s` beside it, so no two machines' clocks
    are compared."""
    host = RemoteHost(**HOST)
    _granted(cluster, host, running_for_s=7200, limit_s=86400)
    node = GrantedNode(host, cluster, log_fn=lambda _m: None)
    left = node.state().time_left_s
    assert left is not None and abs(left - (86400 - 7200)) < 30
