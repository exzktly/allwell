"""Manage Remotes' Granted node panel: Request a node, Release, Test the node, and the readout (tracker H11).

The panel is driven the way a user drives it (buttons clicked, a host selected) against the real ``GrantedNode`` and the
real scripts it sends, run under ``bash`` against the stand-in cluster (``tests/_hold_cluster.py``). Only the transport
is replaced: the panel builds its ``DatasetCache`` through ``analyze.granted_node_panel.DatasetCache``, which these
tests point at the stand-in. What is asserted is what reached the host (the ``sbatch`` it was sent, the ``scancel``,
the heartbeat file) and what the user is shown, not what the panel computed on the way.

Wrong implementations these reject: a Request button that is built and connected to nothing (§7.6 of CLAUDE.md); a
Release that cancels a node a run is using without asking; a panel that reads the node and does not keep it alive; a
Test that probes the host's own launcher instead of the node's; a Request offered for a host that cannot make one.
"""

from __future__ import annotations

import os
import shutil
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402
from analyze import granted_node_panel as gnp                                    # noqa: E402
from analyze.granted_node_panel import GrantedNodePanel                           # noqa: E402
from analyze.remote_dialog import ManageRemotesDialog                             # noqa: E402
from services.remote_config import RemoteHost, write_remote                       # noqa: E402
from services.remote_hold import node_for                                         # noqa: E402
from tests._hold_cluster import HOST, Cluster, Script, granted_files             # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@pytest.fixture
def cluster(tmp_path, monkeypatch):
    from services import remote_hold

    remote_hold._NODES.clear()
    c = Cluster(tmp_path)
    monkeypatch.setattr(gnp, "DatasetCache", lambda host: c)
    yield c
    for node in remote_hold.live_nodes():
        node.stop_beating()
    remote_hold._NODES.clear()


@pytest.fixture
def panel(qtbot, cluster):
    p = GrantedNodePanel()
    qtbot.addWidget(p)
    yield p
    p._timer.stop()


def _text(panel) -> str:
    return panel._readout.text()


def _seed_granted(cluster, host, *, job=4001, node="gpu-node-1", busy=False):
    cluster.set_job(job, "RUNNING", "None", node)
    d = cluster.hold_dir(host)
    d.mkdir(parents=True, exist_ok=True)
    (d / "job").write_text(str(job), encoding="utf-8")
    granted_files(cluster, host, node=node, job=job)
    (d / "status").write_text(f"state={'busy' if busy else 'idle'}\nidle_s=240\n", encoding="utf-8")


def _will_be_granted(cluster, host, job=4001):
    """Register the handle with a clock that plays the scheduler granting the node on the first nap."""
    def granted():
        cluster.set_job(job, "RUNNING", "None", "gpu-node-1")
        granted_files(cluster, host, job=job)

    (cluster.fake / "next_id").write_text(f"{job}\n", encoding="utf-8")
    sleep = Script(cluster, [granted])
    return node_for(host, cluster, log_fn=lambda _m: None, sleep=sleep, clock=sleep.clock)


# ── what is offered ───────────────────────────────────────────────────────────────────────────────────────────────

def test_a_host_with_no_node_offers_request_and_nothing_else(qtbot, panel, cluster):
    host = RemoteHost(**HOST)
    panel.set_host(host)
    qtbot.waitUntil(lambda: "No node granted." in _text(panel), timeout=10000)
    assert panel._request_btn.isEnabled()
    assert not panel._release_btn.isEnabled() and not panel._test_btn.isEnabled()


@pytest.mark.parametrize("launcher, reason", [
    ("", "needs the host to run compute on a node it schedules"),
    ("sbatch --wrap=x", "needs a Slurm (srun) scheduler command"),
])
def test_a_host_that_cannot_make_a_node_says_so_and_is_not_asked(qtbot, panel, cluster, launcher, reason):
    """Wrong implementation: offering Request for a host whose command cannot become the hold job."""
    panel.set_host(RemoteHost(**{**HOST, "compute_launcher": launcher}))
    assert not panel._request_btn.isEnabled() and reason in _text(panel)
    assert reason in panel._request_btn.toolTip()
    qtbot.wait(300)
    assert cluster.sbatch_calls() == [], "a host that cannot request a node must not be asked about one"


def test_no_host_selected_hides_the_panel(panel):
    panel.set_host(None)
    assert panel.isHidden()


# ── Request ───────────────────────────────────────────────────────────────────────────────────────────────────────

def test_request_sends_the_hosts_command_to_the_scheduler_and_shows_the_node(qtbot, panel, cluster):
    """Wrong implementation: a button connected to nothing — the control is built and nothing reaches the host."""
    host = RemoteHost(**HOST)
    _will_be_granted(cluster, host)
    panel.set_host(host)
    qtbot.waitUntil(lambda: "No node granted." in _text(panel), timeout=10000)
    panel._request_btn.click()
    qtbot.waitUntil(lambda: "Node gpu-node-1 · job 4001 · 1× NVIDIA A10G" in _text(panel), timeout=15000)
    (args,) = cluster.sbatch_calls()
    assert "--partition=gpu" in args and "--gres=gpu:1" in args and "--time=1-00:00:00" in args
    assert panel._release_btn.isEnabled() and panel._test_btn.isEnabled()
    assert not panel._request_btn.isEnabled(), "a second node must not be requestable beside the first"
    assert "released after 30 min idle" in _text(panel)


# ── Release ───────────────────────────────────────────────────────────────────────────────────────────────────────

def test_release_cancels_the_node_and_offers_request_again(qtbot, panel, cluster):
    host = RemoteHost(**HOST)
    _seed_granted(cluster, host)
    panel.set_host(host)
    qtbot.waitUntil(lambda: panel._release_btn.isEnabled(), timeout=10000)
    panel._release_btn.click()
    qtbot.waitUntil(lambda: any("4001" in s for s in cluster.scancels()), timeout=10000)
    assert (cluster.hold_dir(host) / "release").exists()
    qtbot.waitUntil(lambda: panel._request_btn.isEnabled(), timeout=10000)
    assert "Released" in _text(panel)


def test_release_asks_before_stopping_a_run_and_does_nothing_if_refused(qtbot, panel, cluster, monkeypatch):
    """Wrong implementation: cancelling a node a run is using without a word."""
    host = RemoteHost(**HOST)
    _seed_granted(cluster, host, busy=True)
    panel.set_host(host)
    qtbot.waitUntil(lambda: "In use by a run." in _text(panel), timeout=10000)
    asked: list[object] = []
    monkeypatch.setattr(gnp, "confirm_release_while_busy", lambda parent: asked.append(parent) or False)
    panel._release_btn.click()
    qtbot.wait(500)
    assert asked == [panel] and cluster.scancels() == []

    monkeypatch.setattr(gnp, "confirm_release_while_busy", lambda parent: True)
    panel._release_btn.click()
    qtbot.waitUntil(lambda: any("4001" in s for s in cluster.scancels()), timeout=10000)


def test_release_does_not_ask_when_no_run_is_using_the_node(qtbot, panel, cluster, monkeypatch):
    host = RemoteHost(**HOST)
    _seed_granted(cluster, host, busy=False)
    panel.set_host(host)
    qtbot.waitUntil(lambda: panel._release_btn.isEnabled(), timeout=10000)
    monkeypatch.setattr(gnp, "confirm_release_while_busy", lambda parent: pytest.fail("asked about an idle node"))
    panel._release_btn.click()
    qtbot.waitUntil(lambda: any("4001" in s for s in cluster.scancels()), timeout=10000)


# ── finding a node that is already granted ────────────────────────────────────────────────────────────────────────

def test_selecting_a_host_finds_its_granted_node_and_keeps_it_alive(qtbot, panel, cluster):
    """Wrong implementation: showing the node without beating for it, which its script would end two minutes later."""
    host = RemoteHost(**HOST)
    _seed_granted(cluster, host)
    d = cluster.hold_dir(host)
    (d / "heartbeat").write_text("", encoding="utf-8")
    old = time.time() - 3600
    os.utime(d / "heartbeat", (old, old))
    panel.set_host(host)
    qtbot.waitUntil(lambda: "Node gpu-node-1" in _text(panel), timeout=10000)
    assert time.time() - (d / "heartbeat").stat().st_mtime < 30


# ── Test the node ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_test_the_node_probes_through_the_node_not_the_hosts_own_launcher(qtbot, panel, cluster, monkeypatch):
    """Wrong implementation: the host's `srun --partition=gpu …` line, which asks for a node of its own."""
    from analyze import scheduler_test_dialog
    from services.remote_commands import unpack_login_shell_command

    shown: list[tuple] = []

    class _Recorder:
        def __init__(self, parent, probe, name):
            shown.append((probe, name))

        def show(self):
            pass

    monkeypatch.setattr(scheduler_test_dialog, "SchedulerTestDialog", _Recorder)
    host = RemoteHost(**HOST)
    _seed_granted(cluster, host)
    panel.set_host(host)
    qtbot.waitUntil(lambda: panel._test_btn.isEnabled(), timeout=10000)
    panel._test_btn.click()
    (probe, name), = shown
    script = unpack_login_shell_command(probe.argv[-1])
    assert "srun --jobid=4001 --overlap --nodes=1 --ntasks=1 --cpus-per-task=8" in script
    assert "--partition=gpu" not in script
    assert name == "gpu (granted node)"


# ── in the dialog ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_dialog_shows_the_panel_for_the_selected_host(qtbot, tmp_path, monkeypatch, cluster):
    monkeypatch.setenv("ALLWELL_REMOTES", str(tmp_path / "remotes.toml"))
    write_remote(RemoteHost(**HOST), path=tmp_path / "remotes.toml")
    write_remote(RemoteHost(name="plain", ssh_alias="plain"), path=tmp_path / "remotes.toml")
    dlg = ManageRemotesDialog()
    qtbot.addWidget(dlg)
    dlg._node_panel._timer.stop()
    assert dlg._node_panel.isHidden(), "no host is selected yet"
    for row in range(dlg._list.count()):
        if dlg._list.item(row).data(Qt.UserRole) == "gpu":
            dlg._list.setCurrentRow(row)
    qtbot.waitUntil(lambda: dlg._node_panel._host is not None and dlg._node_panel._host.name == "gpu", timeout=5000)
    qtbot.waitUntil(lambda: "No node granted." in _text(dlg._node_panel), timeout=10000)
    assert dlg._node_panel._request_btn.isEnabled()
    for row in range(dlg._list.count()):
        if dlg._list.item(row).data(Qt.UserRole) == "plain":
            dlg._list.setCurrentRow(row)
    qtbot.waitUntil(lambda: dlg._node_panel._host.name == "plain", timeout=5000)
    assert not dlg._node_panel._request_btn.isEnabled() and "schedules" in _text(dlg._node_panel)
