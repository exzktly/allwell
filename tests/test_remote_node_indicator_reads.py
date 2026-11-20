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
