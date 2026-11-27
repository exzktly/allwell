"""The header's remote-node indicator: the dot's colour for each node state, its tooltip, and when it reaches a host.

``services.remote_hold.node_summary`` is driven with stand-in node handles carrying real ``HoldState`` values, and
``ui.remote_node_button.RemoteNodeButton`` is asserted on the colour the dot actually paints (``StatusDot.color``),
not on the level it computed. ``tests/test_remote_node_indicator_reads.py`` holds the other half — that the real
``GrantedNode`` records the state a host reports, and forgets it on release — against the stand-in cluster.

Wrong implementations these reject: a dot left grey whatever the node does; a queued node shown green; one ready node
on one host drawn yellow because another host is still queued; an indicator that sshes to every host every few
seconds when no node is live; a refresh that piles a thread per tick onto a host slow to answer.
"""

from __future__ import annotations

import os
import threading
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtGui import QColor  # noqa: E402

from services.remote_hold import HoldState, node_summary  # noqa: E402
from ui import theme  # noqa: E402


def _node(name: str, phase: str | None, node: str = "gpu-node-1"):
    state = None if phase is None else HoldState(phase=phase, node=node, job_id="4001")
    reads: list[str] = []

    def read():
        reads.append(name)
        return state

    return types.SimpleNamespace(host=types.SimpleNamespace(name=name), last_state=state, state=read, reads=reads)


# ── the summary ───────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("phase, level", [
    (None, "none"), ("none", "none"), ("ended", "none"),
    ("queued", "pending"), ("starting", "pending"), ("unknown", "pending"),
    ("granted", "ready"), ("busy", "ready"),
])
def test_each_phase_maps_to_its_level(phase, level):
    assert node_summary([_node("gpu", phase)])[0] == level


def test_no_nodes_at_all_is_none():
    assert node_summary([]) == ("none", [])


def test_one_ready_node_is_ready_whatever_else_is_queued():
    """Queued host listed first, so a rule of "first node wins" or "worst node wins" answers pending."""
    level, lines = node_summary([_node("cpu", "queued"), _node("gpu", "granted")])
    assert level == "ready"
    assert lines == ["cpu: node requested, waiting", "gpu: node granted on gpu-node-1"]


def test_lines_name_the_host_and_what_its_node_is_doing():
    _level, lines = node_summary([_node("gpu", "busy", node="n7"), _node("old", "ended"), _node("s", "starting")])
    assert lines == ["gpu: node in use by a run on n7", "s: node starting"]


# ── the button ────────────────────────────────────────────────────────────────────────────────────────────────────

def _button(qtbot, summary, nodes=lambda: []):
    from ui.remote_node_button import RemoteNodeButton

    btn = RemoteNodeButton(summary=summary, nodes=nodes)
    qtbot.addWidget(btn)
    btn._summary_timer.stop()
    btn._live_timer.stop()
    return btn


@pytest.mark.parametrize("level, token", [("ready", "success"), ("pending", "warn"), ("none", "text_secondary")])
def test_the_dot_paints_the_levels_colour(qtbot, level, token):
    btn = _button(qtbot, lambda: (level, []))
    assert btn._dot.color() == QColor(getattr(theme.Colors, token))
    assert btn.level() == level


def test_the_dot_follows_the_node_without_being_clicked(qtbot):
    """A dot computed once at construction and never again would pass the test above."""
    said = {"level": "none"}
    btn = _button(qtbot, lambda: (said["level"], []))
    for level, token in (("pending", "warn"), ("ready", "success"), ("none", "text_secondary")):
        said["level"] = level
        btn.update_status()
        assert btn._dot.color() == QColor(getattr(theme.Colors, token))


def test_the_summary_timer_redraws_the_dot(qtbot):
    from ui.remote_node_button import SUMMARY_MS

    said = {"level": "none"}
    btn = _button(qtbot, lambda: (said["level"], []))
    btn._summary_timer.start()
    said["level"] = "ready"
    qtbot.waitUntil(lambda: btn.level() == "ready", timeout=SUMMARY_MS * 3)


def test_the_tooltip_names_the_hosts_and_the_click(qtbot):
    btn = _button(qtbot, lambda: ("ready", ["gpu: node granted on n1"]))
    tip = btn.toolTip()
    assert tip.splitlines()[:2] == ["Remote node ready", "gpu: node granted on n1"]
    assert "Manage Remotes" in tip


def test_a_summary_that_raises_draws_no_node_rather_than_crashing(qtbot):
    def broken():
        raise RuntimeError("registry gone")

    btn = _button(qtbot, broken)
    assert btn.level() == "none"


def test_the_real_registry_is_the_default(qtbot):
    from services import remote_hold
    from ui.remote_node_button import RemoteNodeButton

    btn = RemoteNodeButton()
    qtbot.addWidget(btn)
    btn._summary_timer.stop()
    btn._live_timer.stop()
    assert btn._summary is remote_hold.node_summary and btn._nodes is remote_hold.live_nodes


def test_a_click_reaches_the_connected_handler(qtbot):
    btn = _button(qtbot, lambda: ("none", []))
    clicks = []
    btn.clicked.connect(lambda: clicks.append(1))
    btn.click()
    assert clicks == [1]


# ── reaching the host ─────────────────────────────────────────────────────────────────────────────────────────────

def _join_refresh():
    for thread in threading.enumerate():
        if thread.name == "aw-remote-node-indicator":
            thread.join(10)


def test_with_no_live_node_nothing_is_read_from_any_host(qtbot):
    nodes = [_node("a", None), _node("b", "ended"), _node("c", "none")]
    btn = _button(qtbot, lambda: ("none", []), nodes=lambda: nodes)
    btn.refresh_live_nodes()
    _join_refresh()
    assert [n.reads for n in nodes] == [[], [], []]


def test_only_the_live_nodes_are_reread_and_the_dot_redrawn_after(qtbot):
    nodes = [_node("q", "queued"), _node("g", "granted"), _node("e", "ended")]
    said = {"level": "pending"}
    btn = _button(qtbot, lambda: (said["level"], []), nodes=lambda: nodes)
    said["level"] = "ready"                       # what the re-read will have found
    btn.refresh_live_nodes()
    _join_refresh()
    assert [n.reads for n in nodes] == [["q"], ["g"], []]
    qtbot.waitUntil(lambda: btn.level() == "ready", timeout=5000)


def test_a_refresh_still_waiting_on_a_host_is_not_started_twice(qtbot):
    gate = threading.Event()
    node = _node("slow", "granted")
    calls = []

    def slow_read():
        calls.append(1)
        gate.wait(10)

    node.state = slow_read
    btn = _button(qtbot, lambda: ("ready", []), nodes=lambda: [node])
    btn.refresh_live_nodes()
    btn.refresh_live_nodes()
    btn.refresh_live_nodes()
    gate.set()
    _join_refresh()
    assert calls == [1]
    btn.refresh_live_nodes()                      # and once it is over, the next one runs
    _join_refresh()
    assert calls == [1, 1]
