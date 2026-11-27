"""The window header's remote-node indicator: a server glyph with a coloured dot, which opens Manage Remotes.

It sits among the header's right-hand icons in every mode, so whether a granted node is up — and billing — can be
seen from Review and Datasets as well as from Analyze, and Manage Remotes is one click away from any of them.

The dot says what this process last heard about the granted nodes (``services.remote_hold.node_summary``):

* **green** (``success``) — a node is granted, idle or in use by a run;
* **yellow** (``warn``) — a node is asked for and not ready yet: queued, starting, or the scheduler did not answer;
* **grey** (``text_secondary``, the status dot's neutral tone) — no node.

Drawing it never reaches a host: the summary is read from the in-process registry every :data:`SUMMARY_MS`. What
keeps that registry current is whoever is already talking to the host — the Manage Remotes panel, a request's own
wait, a run's claim — plus one thing of its own: while some node was last seen live, the indicator re-reads that
node's state on a worker thread every :data:`LIVE_REFRESH_MS`, so a node the host released on its own (its idle
limit, its time limit) turns grey here without the user having to open the dialog. With no live node it starts no
ssh at all, which is the case for every user without a remote.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QWidget

from core.safe import swallow
from widgets.icon_button import IconButton
from widgets.status_dot import StatusDot

#: How often the dot is redrawn from the in-process registry, in milliseconds. Costs a lock and a loop; no host.
SUMMARY_MS = 2_000
#: How often a node last seen live is re-read from its host, in milliseconds.
LIVE_REFRESH_MS = 30_000

#: The dot's theme token for each level of ``node_summary``.
LEVEL_TONES = {"ready": "success", "pending": "warn", "none": "neutral"}

_HEADLINES = {
    "ready": "Remote node ready",
    "pending": "Remote node requested — not ready yet",
    "none": "No remote node",
}


class _Bridge(QObject):
    """A worker thread's way back onto the GUI thread (a worker has no event loop of its own)."""

    refreshed = Signal()


class RemoteNodeButton(IconButton):
    """The header's server icon. ``clicked`` is the caller's to connect (it opens Manage Remotes)."""

    def __init__(self, parent: QWidget | None = None, *, summary=None, nodes=None) -> None:
        """*summary* is ``node_summary`` and *nodes* is ``live_nodes`` by default; a test passes its own, so that no
        test reaches a host."""
        super().__init__("server", parent)
        if summary is None or nodes is None:
            from services.remote_hold import live_nodes, node_summary
            summary = summary or node_summary
            nodes = nodes or live_nodes
        self._summary = summary
        self._nodes = nodes
        self._level = ""
        self._refreshing = threading.Event()
        # Sized off the font like the button's glyph, a little under half a line, so it marks the icon's corner
        # rather than covering it; clicks go through to the button.
        self._dot = StatusDot("neutral", self, diameter=max(5, round(self.fontMetrics().height() * 0.33)))
        self._dot.setObjectName("RemoteNodeDot")
        self._dot.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._place_dot()
        self._bridge = _Bridge(self)
        self._bridge.refreshed.connect(self.update_status)

        self._summary_timer = QTimer(self)
        self._summary_timer.setInterval(SUMMARY_MS)
        self._summary_timer.timeout.connect(self.update_status)
        self._summary_timer.start()
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(LIVE_REFRESH_MS)
        self._live_timer.timeout.connect(self.refresh_live_nodes)
        self._live_timer.start()
        self.update_status()

    # ── reading ──────────────────────────────────────────────────────────────────────────────────────────────────
    def level(self) -> str:
        """``ready`` / ``pending`` / ``none``: what the dot shows now."""
        return self._level

    def update_status(self) -> None:
        """Redraw the dot and the tooltip from what this process last heard. Never reaches a host."""
        level, lines = "none", []
        with swallow("the remote-node indicator could not read the granted nodes"):
            level, lines = self._summary()
        if level not in LEVEL_TONES:
            level = "none"
        if level != self._level:
            self._level = level
            self._dot.setStatus(LEVEL_TONES[level])
        tip = [_HEADLINES[level], *lines, "", "Click to open Manage Remotes."]
        self.setToolTip("\n".join(tip))

    def refresh_live_nodes(self) -> None:
        """Re-read, on a worker thread, each node this process last saw live — and no other, so a user with no node
        sends nothing. One refresh at a time: a host slow to answer must not pile up threads."""
        live = [n for n in self._nodes() if n.last_state is not None and n.last_state.live]
        if not live or self._refreshing.is_set():
            return
        self._refreshing.set()

        def work() -> None:
            try:
                for node in live:
                    with swallow(f"could not re-read the granted node on {node.host.name}"):
                        node.state()
            finally:
                self._refreshing.clear()
                with swallow("a remote-node refresh reached an indicator that was already closed"):
                    self._bridge.refreshed.emit()

        threading.Thread(target=work, daemon=True, name="aw-remote-node-indicator").start()

    # ── drawing ──────────────────────────────────────────────────────────────────────────────────────────────────
    def _place_dot(self) -> None:
        """Pin the dot to the glyph's bottom-right corner, over the icon rather than beside it."""
        hint = self._dot.sizeHint()
        self._dot.resize(hint)
        self._dot.move(max(0, self.width() - hint.width()), max(0, self.height() - hint.height()))
        self._dot.raise_()

    def resizeEvent(self, event) -> None:  # Qt naming
        super().resizeEvent(event)
        self._place_dot()
