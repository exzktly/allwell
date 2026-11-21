"""The real ``GrantedNode`` keeps the state its host last reported, which is all the header indicator reads.

Run against the stand-in cluster (``tests/_hold_cluster.py``): real hold scripts under ``bash`` with fake Slurm
programs, so what is asserted is the state parsed from what the "host" answered. The indicator itself is tested in
``tests/test_remote_node_indicator.py``.

Wrong implementations these reject: a ``last_state`` never written (the dot stays grey with a node up); one written by
``attach`` but not by a request's own wait or a plain read; a release that leaves the node green.
"""

from __future__ import annotations

import shutil

import pytest

from services import remote_hold
from services.remote_config import RemoteHost
from services.remote_hold import node_for, node_summary
from tests._hold_cluster import HOST, Cluster, granted_files

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@pytest.fixture
def cluster(tmp_path):
    remote_hold._NODES.clear()
    yield Cluster(tmp_path)
    for node in remote_hold.live_nodes():
        node.stop_beating()
    remote_hold._NODES.clear()


def _seed(cluster, host, *, state="RUNNING", job=4001, node="gpu-node-1"):
    cluster.set_job(job, state, "None" if state == "RUNNING" else "Priority", node if state == "RUNNING" else "")
    d = cluster.hold_dir(host)
    d.mkdir(parents=True, exist_ok=True)
    (d / "job").write_text(str(job), encoding="utf-8")
    if state == "RUNNING":
        granted_files(cluster, host, node=node, job=job)
        (d / "status").write_text("state=idle\nidle_s=10\n", encoding="utf-8")


def test_a_fresh_handle_has_heard_nothing(cluster):
    node = node_for(RemoteHost(**HOST), cluster, log_fn=lambda _m: None)
    assert node.last_state is None
    assert node_summary() == ("none", [])


def test_a_read_records_a_granted_node_and_the_summary_turns_ready(cluster):
    host = RemoteHost(**HOST)
    _seed(cluster, host)
    node = node_for(host, cluster, log_fn=lambda _m: None)
    state = node.state()
    assert node.last_state is state and state.phase == "granted"
    assert node_summary() == ("ready", ["gpu: node granted on gpu-node-1"])


def test_a_queued_node_is_pending(cluster):
    host = RemoteHost(**HOST)
    _seed(cluster, host, state="PENDING")
    node = node_for(host, cluster, log_fn=lambda _m: None)
    assert node.state().phase == "queued"
    assert node_summary()[0] == "pending"


def test_release_forgets_the_node(cluster):
    host = RemoteHost(**HOST)
    _seed(cluster, host)
    node = node_for(host, cluster, log_fn=lambda _m: None)
    node.attach()
    assert node_summary()[0] == "ready"
    node.release("test")
    assert node.last_state is None
    assert node_summary() == ("none", [])


def test_a_node_the_host_ended_reads_as_none(cluster):
    """The indicator's own re-read (``refresh_live_nodes``) is a plain ``state()``; a node the host ended since must
    turn the dot grey through it, not stay green on the last good read."""
    host = RemoteHost(**HOST)
    _seed(cluster, host)
    node = node_for(host, cluster, log_fn=lambda _m: None)
    node.state()
    cluster.set_job(4001, "CANCELLED", "None", "")
    assert node.state().phase == "ended"
    assert node_summary() == ("none", [])


def test_a_release_that_left_another_window_s_node_alone_still_shows_that_node(cluster):
    """The hold directory is shared by every window: once another window has asked for a node there, this handle's
    release answers ``AW_MOVED`` and leaves that node running — and billing. Wrong implementation: forgetting the
    state on every release, which turns the dot grey ("No remote node") while the host holds a granted node, and,
    with nothing live left to re-read, keeps it grey."""
    host = RemoteHost(**HOST)
    _seed(cluster, host)
    node = node_for(host, cluster, log_fn=(lines := []).append)
    node.attach()
    _seed(cluster, host, job=4002, node="gpu-node-2")      # another window's request, granted since
    node.release("test")
    assert any("no longer the one this window asked for" in ln for ln in lines), lines
    assert node_summary() == ("ready", ["gpu: node granted on gpu-node-2"])


def test_a_release_the_host_did_not_confirm_keeps_the_node_shown(cluster, monkeypatch):
    """A cancel the host answered with a failure leaves the node up until its own two-minute rule ends it. Wrong
    implementation: reading the attempt as done and showing no node for those minutes."""
    host = RemoteHost(**HOST)
    _seed(cluster, host)
    node = node_for(host, cluster, log_fn=(lines := []).append)
    node.attach()
    real = cluster._ssh_script

    def cancel_fails(script, **kw):
        if "AW_SCANCELLED" in script:
            import subprocess
            return subprocess.CompletedProcess(["ssh"], 255, "", "ssh: connect to host: Connection reset")
        return real(script, **kw)

    monkeypatch.setattr(cluster, "_ssh_script", cancel_fails)
    assert node.release("test") is False
    assert any("could not release it (ssh exit 255)" in ln for ln in lines), lines
    assert node_summary() == ("ready", ["gpu: node granted on gpu-node-1"])


def test_a_release_of_another_user_s_job_forgets_it(cluster, monkeypatch):
    """``AW_NOTMINE``: the job in the directory is not this user's, so it is not this user's node to show."""
    host = RemoteHost(**HOST)
    _seed(cluster, host)
    node = node_for(host, cluster, log_fn=(lines := []).append)
    node.attach()
    (cluster.fake / "squeue.owner").write_text("someone-else", encoding="utf-8")
    node.release("test")
    assert any("belongs to another user" in ln for ln in lines), lines
    assert node_summary() == ("none", [])
