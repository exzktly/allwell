"""A granted-node request that waits a long time in the queue is not ended because one look at it went unanswered.

The request polls the host every ``HOLD_POLL_S`` for as long as the scheduler keeps the job queued, which on a busy
cluster is hours: hundreds of ssh round trips to the login node. Any one of them can fail — a login node that is slow
to answer, a network blip, a laptop's Wi-Fi changing — and each failure used to raise out of the wait. ``request``
then treated it like any other failure: it cancelled the queued job and reported *could not read the granted node's
state (ssh exit 124): the remote did not answer within 60s*. So the longer a node took to be granted, the surer it was
to be thrown away, with the user's place in the queue.

A look that went unanswered says nothing about the job, which is still queued on the cluster; the request now says so
in the log and looks again at the next poll. The node's own script still ends the node if this app really is gone,
because the heartbeat goes on beating (or not) independently of the polls. Release, the Max queue wait and a job the
scheduler ended still stop the wait as before.

Wrong implementations these reject: re-raising the failed read (the request ends and the job is cancelled);
swallowing it without saying so in the log; reading the state once more after the grant and letting *that* read's
failure cancel a node that is already granted; looking again for ever, ignoring the Max queue wait, while the host
does not answer.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from services.remote_config import RemoteHost
from services.remote_hold import GrantedNode, HoldQueueWaitSpent

from tests._hold_cluster import HOST, Cluster, Script, granted_files

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

#: What ``remote_transport.ssh_control`` returns for a control script the host did not answer in time.
_NO_ANSWER = "the remote did not answer within 60s: bash -s"


class FlakyCluster(Cluster):
    """The stand-in host, whose state reads go unanswered while :attr:`down` is set."""

    down = False

    def _ssh_script(self, script: str, *, check: bool, timeout_s: float = 60):
        if self.down and '"##scontrol"' in script:
            return subprocess.CompletedProcess(["ssh"], 124, "", _NO_ANSWER)
        return super()._ssh_script(script, check=check, timeout_s=timeout_s)


@pytest.fixture
def cluster(tmp_path):
    return FlakyCluster(tmp_path)


@pytest.fixture
def made():
    nodes: list[GrantedNode] = []
    yield nodes
    for node in nodes:
        node.release("test over")


def _node(cluster, made, sleep, **host_kw):
    host = RemoteHost(**{**HOST, **host_kw})
    lines: list[str] = []
    node = GrantedNode(host, cluster, log_fn=lines.append, sleep=sleep, clock=sleep.clock)
    made.append(node)
    return node, host, lines


def _set(cluster, **kw):
    return lambda: setattr(cluster, "down", kw["down"])


def test_an_unanswered_look_while_queued_keeps_the_request_waiting(cluster, made):
    host = RemoteHost(**HOST)
    sleep = Script(cluster, [
        ("PENDING", "Priority", ""),
        _set(cluster, down=True),          # the host stops answering for three looks, with the job still queued
        lambda: None,
        lambda: None,
        _set(cluster, down=False),
        ("PENDING", "Resources", ""),
        lambda: granted_files(cluster, host),
        ("RUNNING", "None", "gpu-node-1"),
    ])
    node, _, lines = _node(cluster, made, sleep)
    state = node.request()

    assert state.phase == "granted" and state.node == "gpu-node-1" and state.job_id == "4001"
    assert cluster.scancels() == [], "the queued job must not be cancelled because the host did not answer"
    assert len(cluster.sbatch_calls()) == 1, "the request keeps its place in the queue: no second sbatch"
    said = [line for line in lines if "did not answer" in line]
    assert len(said) == 1, lines                       # said once for the streak, not on every look
    assert "job 4001" in said[0] and "still waiting" in said[0] and _NO_ANSWER in said[0]
    assert any("answering again" in line for line in lines), lines


def test_a_granted_node_is_not_cancelled_when_the_look_after_the_grant_goes_unanswered(cluster, made):
    """The state the wait saw granted is the one returned: a second read after it, failing, used to cancel the node."""
    host = RemoteHost(**HOST)
    sleep = Script(cluster, [
        lambda: granted_files(cluster, host),
        ("RUNNING", "None", "gpu-node-1"),
    ])
    node, _, _ = _node(cluster, made, sleep)
    real = cluster._ssh_script
    seen = {"granted": False}

    def ssh_script(script, *, check, timeout_s=60):
        if '"##scontrol"' in script:
            if seen["granted"]:                # every look after the one that saw the grant goes unanswered
                return subprocess.CompletedProcess(["ssh"], 124, "", _NO_ANSWER)
            result = real(script, check=check, timeout_s=timeout_s)
            seen["granted"] = "JobState=RUNNING" in result.stdout and "node=gpu-node-1" in result.stdout
            return result
        return real(script, check=check, timeout_s=timeout_s)

    cluster._ssh_script = ssh_script
    state = node.request()
    assert state.phase == "granted" and state.node == "gpu-node-1"
    assert cluster.scancels() == []


def test_a_host_that_never_answers_still_ends_at_the_max_queue_wait(cluster, made):
    host_kw = {"queue_wait_timeout_min": 1}
    sleep = Script(cluster, [_set(cluster, down=True)])
    node, _, _ = _node(cluster, made, sleep, **host_kw)
    with pytest.raises(HoldQueueWaitSpent):
        node.request()
    assert sum(sleep.naps) >= 60


def test_the_max_queue_wait_message_names_the_job_it_cancelled(cluster, made):
    """Only the state reads go unanswered here, so the cancel reaches the host: the message names the job. It used to
    read ``cancelled job .``, because ``release`` clears the id before the message read it."""
    sleep = Script(cluster, [_set(cluster, down=True)])
    node, _, _ = _node(cluster, made, sleep, queue_wait_timeout_min=1)
    with pytest.raises(HoldQueueWaitSpent) as raised:
        node.request()
    assert cluster.scancels(), "the cancel reached the host"
    assert str(raised.value).endswith("cancelled job 4001.")


class DownCluster(Cluster):
    """The stand-in host, every control script to which goes unanswered while :attr:`down` is set — the cancel too."""

    down = False

    def _ssh_script(self, script: str, *, check: bool, timeout_s: float = 60):
        if self.down:
            return subprocess.CompletedProcess(["ssh"], 124, "", _NO_ANSWER)
        return super()._ssh_script(script, check=check, timeout_s=timeout_s)


def test_a_cancel_the_host_did_not_answer_is_not_reported_as_done(tmp_path, made):
    """When the host answers nothing, the job is still queued and this app no longer holds it: the message must not
    say it was cancelled, and must name the job so it can be cancelled by hand."""
    cluster = DownCluster(tmp_path)
    sleep = Script(cluster, [_set(cluster, down=True)])
    node, _, _ = _node(cluster, made, sleep, queue_wait_timeout_min=1)
    with pytest.raises(HoldQueueWaitSpent) as raised:
        node.request()
    assert cluster.scancels() == []
    text = str(raised.value)
    assert "cancelled job" not in text
    assert "could not cancel job 4001" in text and "scancel 4001" in text
