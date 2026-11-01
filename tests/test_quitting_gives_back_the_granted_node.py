"""Quitting releases the nodes this window asked for, and never one another window is using (tracker H10).

A granted node bills for as long as it is granted, so closing All-Well must not leave one behind: ``release_all`` gives
back every node this process *requested*. A node it only *found* (``GrantedNode.attach``: granted from another window,
or by this app before a crash) is not its to cancel — another window may be using it — so closing only stops this
process's heartbeat for it, and the node's own script ends it two minutes after the last beat.

Wrong implementations these reject: releasing a node that was only adopted (cancelling another window's node);
releasing none (the node outlives the app until the two-minute rule); a close that hangs on a host that does not answer;
an adopted node nobody beats for (the script would release it two minutes after the user found it); and a close hook
that is written but never called, or called before the run on the node has been stopped.
"""

from __future__ import annotations

import ast
import inspect
import shutil
import textwrap
import time
import types

import pytest

from services.remote_config import RemoteHost
from services.remote_hold import GrantedNode, live_nodes, node_for, release_all
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
        node.stop_beating()
    remote_hold._NODES.clear()


def _requested(cluster, name="mine", job=4001) -> tuple[GrantedNode, RemoteHost]:
    """A node this process asked for: `request()` against the stand-in scheduler, granted on the first nap."""
    host = RemoteHost(**{**HOST, "name": name})
    (cluster.fake / "next_id").write_text(f"{job}\n", encoding="utf-8")

    def granted():
        cluster.set_job(job, "RUNNING", "None", "gpu-node-1")
        granted_files(cluster, host, job=job)

    sleep = Script(cluster, [granted])
    node = node_for(host, cluster, log_fn=lambda _m: None, sleep=sleep, clock=sleep.clock)
    node.request()
    return node, host


def _found(cluster, name="theirs", job=4005) -> tuple[GrantedNode, RemoteHost]:
    """A node some other window asked for, which this process finds already granted."""
    host = RemoteHost(**{**HOST, "name": name})
    cluster.set_job(job, "RUNNING", "None", "gpu-node-2")
    d = cluster.hold_dir(host)
    d.mkdir(parents=True, exist_ok=True)
    (d / "job").write_text(str(job), encoding="utf-8")
    granted_files(cluster, host, node="gpu-node-2", job=job)
    node = node_for(host, cluster, log_fn=lambda _m: None)
    node.attach()
    return node, host


# ── what quitting does ────────────────────────────────────────────────────────────────────────────────────────────

def test_quitting_releases_the_node_this_window_asked_for(cluster):
    mine, host = _requested(cluster)
    assert mine.owned and mine._heartbeat is not None
    assert release_all("All-Well is closing") is True
    assert any("4001" in line for line in cluster.scancels()), "the node this window asked for must be cancelled"
    assert (cluster.hold_dir(host) / "release").exists()
    assert mine._heartbeat is None


def test_quitting_does_not_cancel_a_node_it_only_found(cluster):
    """Wrong implementation: releasing every handle. The node may be another window's, in use right now."""
    mine, _ = _requested(cluster)
    theirs, their_host = _found(cluster)
    assert not theirs.owned and theirs._heartbeat is not None
    release_all("All-Well is closing")
    assert [s for s in cluster.scancels() if "4005" in s] == [], "another window's node was cancelled"
    assert not (cluster.hold_dir(their_host) / "release").exists()
    assert theirs._heartbeat is None, "this process must stop beating for it, so the node can end itself"
    assert any("4001" in s for s in cluster.scancels())


def test_a_host_that_does_not_answer_cannot_hold_the_close_up(cluster, monkeypatch):
    mine, _ = _requested(cluster)

    def hang(script, *, check, timeout_s=60):
        time.sleep(4)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cluster, "_ssh_script", hang)
    began = time.monotonic()
    assert release_all("All-Well is closing", timeout_s=0.5) is False
    assert time.monotonic() - began < 2.5


def test_quitting_with_no_node_does_nothing(cluster):
    assert live_nodes() == [] and release_all("All-Well is closing") is True


# ── finding a node that is already granted ────────────────────────────────────────────────────────────────────────

def test_a_node_found_granted_is_beaten_for_so_its_script_does_not_end_it(cluster):
    """Wrong implementation: reading the state and not beating. The node's script releases a node two minutes after
    the last beat, so one the user found would go under them."""
    import os

    host = RemoteHost(**{**HOST, "name": "found"})
    cluster.set_job(4005, "RUNNING", "None", "gpu-node-2")
    d = cluster.hold_dir(host)
    d.mkdir(parents=True)
    (d / "job").write_text("4005", encoding="utf-8")
    granted_files(cluster, host, node="gpu-node-2", job=4005)
    old = time.time() - 3600
    (d / "heartbeat").write_text("", encoding="utf-8")
    os.utime(d / "heartbeat", (old, old))
    node = node_for(host, cluster, log_fn=lambda _m: None)
    state = node.attach()
    assert state.phase in ("granted", "busy") and node._heartbeat is not None
    assert time.time() - (d / "heartbeat").stat().st_mtime < 30, "attach must beat at once"


def test_nothing_is_beaten_for_a_host_with_no_granted_node(cluster):
    host = RemoteHost(**{**HOST, "name": "none"})
    node = node_for(host, cluster, log_fn=lambda _m: None)
    assert node.attach().phase == "none" and node._heartbeat is None
    assert not cluster.hold_dir(host).exists(), "looking must not create a hold directory"


# ── the window's close ────────────────────────────────────────────────────────────────────────────────────────────

def test_the_close_gives_the_nodes_back_behind_an_on_screen_notice(cluster):
    from allwell import GRANTED_CLOSE_NOTICE, _release_granted_nodes

    mine, _ = _requested(cluster)
    shown: list[str] = []
    dismissed: list[bool] = []

    def notice(text):
        shown.append(text)
        return lambda: dismissed.append(True)

    _release_granted_nodes(notice=notice)
    assert shown == [GRANTED_CLOSE_NOTICE] and dismissed == [True]
    assert any("4001" in s for s in cluster.scancels())


def test_the_close_shows_no_notice_when_there_is_nothing_to_give_back():
    from allwell import _release_granted_nodes

    shown: list[str] = []
    _release_granted_nodes(notice=lambda text: shown.append(text))
    assert shown == []


def test_the_real_close_calls_it_after_the_run_is_stopped_and_with_the_notice():
    """Building `AllWellApp` is a slow-lane cost, so its `closeEvent` is read rather than run (as
    tests/test_close_stops_analyze_runner.py does for the runner): a hook that is written and never called, or that runs
    before the run on the node has been stopped, would pass every test above."""
    from allwell import AllWellApp

    tree = ast.parse(textwrap.dedent(inspect.getsource(AllWellApp.closeEvent)))
    calls = {getattr(n.func, "id", ""): n for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert "_release_granted_nodes" in calls, "closeEvent no longer gives back the granted node"
    assert "_stop_analyze_runner" in calls
    assert calls["_stop_analyze_runner"].lineno < calls["_release_granted_nodes"].lineno
    assert any(kw.arg == "notice" and ast.unparse(kw.value) == "self._show_close_notice"
               for kw in calls["_release_granted_nodes"].keywords)


def test_a_window_that_released_its_node_does_not_cancel_the_one_another_window_asked_for_since(cluster):
    """Wrong implementation: `owned` staying true after a release, and release acting on whatever job the shared
    directory names now."""
    mine, host = _requested(cluster, name="shared", job=4001)
    mine.release("done with it")
    assert mine.owned is False
    # Another window asks for a node on the same host, in the same directory.
    cluster.set_job(4007, "RUNNING", "None", "gpu-node-7")
    d = cluster.hold_dir(host)
    d.mkdir(parents=True, exist_ok=True)
    (d / "release").unlink(missing_ok=True)           # the new request clears what the earlier release left
    (d / "job").write_text("4007", encoding="utf-8")
    granted_files(cluster, host, node="gpu-node-7", job=4007)
    before = list(cluster.scancels())
    release_all("All-Well is closing")
    assert cluster.scancels() == before, "quitting cancelled another window's node"
    assert not (d / "release").exists()


def test_the_close_shows_no_notice_for_a_host_that_merely_had_a_run(cluster):
    """Every run on a scheduler host makes a handle; only one that is beating for a node or asked for one needs the
    close to wait."""
    from allwell import _release_granted_nodes

    host = RemoteHost(**{**HOST, "name": "justarun"})
    node = node_for(host, cluster, log_fn=lambda _m: None)
    node.attach()                                  # no hold on the host
    assert live_nodes() == [node] and not node.active
    shown: list[str] = []
    _release_granted_nodes(notice=lambda text: shown.append(text))
    assert shown == []


def test_a_node_that_ended_stops_its_heartbeat(cluster):
    """Wrong implementation: a thread that keeps touching a heartbeat file for a job the node's script already ended —
    one ssh every thirty seconds per node ever granted."""
    host = RemoteHost(**{**HOST, "name": "ended"})
    cluster.set_job(4005, "RUNNING", "None", "n5")
    d = cluster.hold_dir(host)
    d.mkdir(parents=True)
    (d / "job").write_text("4005", encoding="utf-8")
    granted_files(cluster, host, node="n5", job=4005)
    node = node_for(host, cluster, log_fn=lambda _m: None)
    node.attach()
    assert node._heartbeat is not None
    (d / "ended").write_text("idle 30 min\n", encoding="utf-8")
    assert node.state().phase == "ended"
    assert node._heartbeat is None
