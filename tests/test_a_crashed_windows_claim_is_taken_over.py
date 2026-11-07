"""A claim on the granted node that a crashed All-Well left behind is taken over at once, not waited out.

The report: after the app crashed, Manage Remotes said the node was in use by a run, and restarting the analysis
logged "the granted node is busy with another run; waiting for it". The crashed window's ``busy`` marker counted as
abandoned only once it was ten minutes old (``MID_BATCH_HEARTBEAT_STALE_S``), so every run waited that long for a run
that no longer existed.

The marker now names its owner (``<pid>@<host> <token>``, ``services.remote_hold_owner``). One whose owner is a process
on this computer that is not running is taken over immediately — unless a step of the hold job is still running, in
which case the crashed window's pipeline is still on the node and the new run waits for it, saying so.

Wrong implementations these reject: waiting out the ten minutes (the reported bug, which naps); taking over a claim
whose owner is still running, or is on another computer (two runs on one GPU); taking over while the dead window's
step is still running; two windows both winning one orphan; and Manage Remotes still calling the node in use.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading

import pytest

from services import remote_hold_owner
from services.remote_config import RemoteHost
from services.remote_hold import HoldState, claim_node, describe_state, hold_paths
from services.remote_hold_owner import claim_owner, orphaned_by, takeover_script
from tests._hold_cluster import HOST, Cluster, Script, granted_files

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@pytest.fixture
def cluster(tmp_path):
    return Cluster(tmp_path)


@pytest.fixture(autouse=True)
def _fresh_registry():
    from services import remote_hold

    remote_hold._NODES.clear()
    yield
    for node in remote_hold.live_nodes():
        node.release("test over")
    remote_hold._NODES.clear()


def _dead_pid() -> int:
    """The id of a process on this computer that has exited."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _granted(cluster, host, job=4001):
    import time

    cluster.set_job(job, "RUNNING", "None", "gpu-node-1")
    d = cluster.hold_dir(host)
    d.mkdir(parents=True, exist_ok=True)
    (d / "job").write_text(str(job), encoding="utf-8")
    granted_files(cluster, host, granted_at=int(time.time()) - 60, job=job)


def _left_by(cluster, host, owner: str):
    """A fresh claim, as the window *owner* wrote it moments before it went away."""
    busy = cluster.hold_dir(host) / "busy"
    busy.write_text(owner + "\n", encoding="utf-8")
    return busy


def _claim(cluster, host, sleep, lines):
    return claim_node(host, cluster, log_fn=lines.append, sleep=sleep, clock=sleep.clock)


def test_a_crashed_windows_claim_is_taken_over_without_waiting(cluster):
    """The report. Wrong implementation: the ten-minute wait — the Script nap would be taken, and the line said."""
    host = RemoteHost(**HOST)
    _granted(cluster, host)
    pid = _dead_pid()
    busy = _left_by(cluster, host, f"{pid}@{socket.gethostname()} deadbeef0000")
    lines: list[str] = []
    sleep = Script(cluster, [])
    lease = _claim(cluster, host, sleep, lines)
    assert lease is not None and sleep.naps == []
    assert busy.read_text(encoding="utf-8").strip() == claim_owner()
    assert not any("waiting for it" in line for line in lines), lines
    assert any(f"no longer running (process {pid} on this computer) was taken over" in line for line in lines), lines
    assert not list(busy.parent.glob("busy.orphan.*"))
    lease.release()
    assert not busy.exists()


def test_a_claim_held_by_a_running_all_well_is_waited_for(cluster):
    """Wrong implementation: taking over any claim that is not this process's. The parent of this test process is
    running, on this computer."""
    host = RemoteHost(**HOST)
    _granted(cluster, host)
    busy = _left_by(cluster, host, f"{os.getppid()}@{socket.gethostname()} deadbeef0000")
    lines: list[str] = []
    sleep = Script(cluster, [lambda: busy.unlink()])         # that run ends during the first nap
    lease = _claim(cluster, host, sleep, lines)
    assert lease is not None and len(sleep.naps) == 1
    assert sum("busy with another run; waiting for it" in line for line in lines) == 1
    lease.release()


def test_a_claim_from_another_computer_is_waited_for(cluster):
    """Its process cannot be checked from here: a dead-looking id on another computer may be a live run."""
    host = RemoteHost(**HOST)
    _granted(cluster, host)
    busy = _left_by(cluster, host, f"{_dead_pid()}@not-{socket.gethostname()} deadbeef0000")
    sleep = Script(cluster, [lambda: busy.unlink()])
    lease = _claim(cluster, host, sleep, [])
    assert lease is not None and len(sleep.naps) == 1
    lease.release()


def test_a_crashed_windows_run_still_on_the_node_is_waited_for_and_named(cluster):
    """Wrong implementation: taking over while that window's step still runs — two pipelines on one GPU."""
    host = RemoteHost(**HOST)
    _granted(cluster, host)
    cluster.add_step("4001.0")
    pid = _dead_pid()
    busy = _left_by(cluster, host, f"{pid}@{socket.gethostname()} deadbeef0000")
    lines: list[str] = []
    sleep = Script(cluster, [lambda: cluster.end_step("4001.0")])
    lease = _claim(cluster, host, sleep, lines)
    assert lease is not None and len(sleep.naps) == 1
    waits = [line for line in lines if "waiting for it" in line]
    assert len(waits) == 1 and f"still running step 4001.0 of the run started by an All-Well that is no longer " \
                                f"running (process {pid} on this computer)" in waits[0], waits
    assert busy.read_text(encoding="utf-8").strip() == claim_owner()
    lease.release()


def test_a_scheduler_that_cannot_say_about_steps_is_not_taken_over(cluster):
    host = RemoteHost(**HOST)
    _granted(cluster, host)
    (cluster.fake / "squeue.down").write_text("", encoding="utf-8")
    busy = _left_by(cluster, host, f"{_dead_pid()}@{socket.gethostname()} deadbeef0000")
    lines: list[str] = []
    sleep = Script(cluster, [lambda: busy.unlink()])
    lease = _claim(cluster, host, sleep, lines)
    assert lease is not None and len(sleep.naps) == 1
    assert any("could not say whether its run's step is still on the node" in line for line in lines), lines
    lease.release()


def test_a_restarted_app_given_its_old_process_id_still_takes_the_claim_over(monkeypatch):
    """Same id, different token: a new process, so the old one is gone. Its own claim (same token) is not orphaned."""
    me = f"{os.getpid()}@{socket.gethostname()}"
    assert orphaned_by(f"{me} 000000000000") == os.getpid()
    assert orphaned_by(claim_owner()) is None


@pytest.mark.parametrize("holder", ["", "\n", "garbage", "123", "123@host", "x@host tok"])
def test_a_marker_naming_nobody_falls_back_to_the_ten_minute_rule(holder):
    """A marker written before claims named their owner is empty: never taken over early."""
    assert orphaned_by(holder) is None


def test_two_windows_cannot_both_take_over_one_orphan(tmp_path):
    """The marker is moved aside before it is replaced; one ``mv`` of a file succeeds. Twenty racers, one winner."""
    paths = hold_paths(RemoteHost(**{**HOST, "remote_staging_dir": str(tmp_path)}))
    os.makedirs(paths.dir)
    holder = f"{_dead_pid()}@{socket.gethostname()} deadbeef0000"
    racers = 20
    for _ in range(4):
        with open(paths.busy, "w", encoding="utf-8") as fh:
            fh.write(holder + "\n")
        answers: list[str] = []
        lock = threading.Lock()
        gate = threading.Barrier(racers)

        def go(i):
            script = takeover_script(paths.busy, holder, f"{i}@racer tok{i}", "")
            gate.wait()
            out = subprocess.run(["bash", "-s"], input=script, capture_output=True, text=True).stdout.strip()
            with lock:
                answers.append(out)

        threads = [threading.Thread(target=go, args=(i,)) for i in range(racers)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(60)
        assert sorted(answers) == ["AW_CHANGED"] * (racers - 1) + ["AW_LEASED"], answers
        assert open(paths.busy, encoding="utf-8").read().startswith(tuple(f"{i}@racer" for i in range(racers)))
        assert not [n for n in os.listdir(paths.dir) if ".orphan." in n]


def test_manage_remotes_says_the_claim_is_a_crashed_windows(monkeypatch):
    """Wrong implementation: "In use by a run." for a run that no longer exists."""
    pid = _dead_pid()
    state = HoldState(phase="busy", job_id="4001", node="gpu-node-1",
                      busy_by=f"{pid}@{socket.gethostname()} deadbeef0000")
    lines = describe_state(state, 30)
    assert "In use by a run." not in lines
    assert any(f"no longer running (process {pid} on this computer)" in line for line in lines), lines
    live = HoldState(phase="busy", job_id="4001", node="gpu-node-1", busy_by=claim_owner())
    assert "In use by a run." in describe_state(live, 30)


def test_the_state_read_carries_the_claims_owner(cluster):
    """Manage Remotes reads ``busy_by`` from the host's own state read, not from anything this process remembers."""
    from services.remote_hold import node_for

    host = RemoteHost(**HOST)
    _granted(cluster, host)
    (cluster.hold_dir(host) / "status").write_text("state=busy\nidle_s=0\n", encoding="utf-8")
    owner = f"{_dead_pid()}@{socket.gethostname()} deadbeef0000"
    _left_by(cluster, host, owner)
    state = node_for(host, cluster, log_fn=lambda _l: None).state()
    assert state.phase == "busy" and state.busy_by == owner


def test_the_token_is_this_process_s():
    assert claim_owner().endswith(" " + remote_hold_owner._TOKEN)
