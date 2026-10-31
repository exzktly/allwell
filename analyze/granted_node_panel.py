"""The **Granted node** panel in Manage Remotes: Request a node, Release, Test the node, and what the node is doing.

Every remote run on a host with a granted node runs on it instead of queueing for a node of its own
(``services.remote_hold``); this is where the user asks for the node and gives it back. The panel shows the node of
the host selected in the dialog above it:

* **Request a node** asks the host's scheduler for a node with the host's own scheduler command and keeps it. The
  request waits in the queue like any job and can take minutes, so it runs on a worker thread; the panel shows
  *Queued…*, *Node starting…* and then the node.
* **Release** gives the node back at once. While a run is using it the user is asked first, because releasing stops
  that run.
* **Test the node** sends the scheduler Test's job to the node as a run would be sent, to see the CPUs and GPUs a run
  on it gets.

Everything that talks to the host runs on a worker thread and reaches the panel through queued signals
(:class:`_Bridge`): a worker thread has no event loop, so a ``QTimer.singleShot`` from one drops the call.
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from core.safe import swallow
from services.remote_config import RemoteHost
from services.remote_hold import (
    GrantedNode,
    HoldError,
    HoldState,
    StepTarget,
    can_request,
    describe_state,
    node_for,
    step_launcher,
)
from services.remote_upload import DatasetCache

#: How often the selected host's node is read while the panel is on screen, in milliseconds.
POLL_MS = 10_000


def confirm_release_while_busy(parent: QWidget) -> bool:
    """Ask whether to release a node a run is using. A function of its own so a test can answer it: a modal reached from
    a test blocks for ever under the offscreen QPA."""
    answer = QMessageBox.question(
        parent, "Release the node?",
        "A run is using this node. Releasing it stops that run.\n\nRelease anyway?",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
    return answer == QMessageBox.Yes


class _Bridge(QObject):
    """The worker threads' way back onto the GUI thread."""

    state = Signal(str, object)         # host name, HoldState
    note = Signal(str, str)             # host name, a line the request logged
    failed = Signal(str, str)           # host name, why a request or a read did not complete
    finished = Signal(str)              # host name: a request or a release is over


class GrantedNodePanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._host: RemoteHost | None = None
        self._states: dict[str, HoldState] = {}
        self._notes: dict[str, str] = {}
        self._working: set[str] = set()            # hosts with a request or a release in flight
        self._probe_dialog = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        title = QLabel("Granted node", self)
        title.setObjectName("SectionTitle")
        layout.addWidget(title)
        self._readout = QLabel("", self)
        self._readout.setObjectName("Muted")
        self._readout.setWordWrap(True)
        layout.addWidget(self._readout)
        row = QHBoxLayout()
        row.setSpacing(6)
        self._request_btn = QPushButton("Request a node", self)
        self._request_btn.setProperty("variant", "primary")
        self._request_btn.clicked.connect(self._on_request)
        self._release_btn = QPushButton("Release", self)
        self._release_btn.clicked.connect(self._on_release)
        self._test_btn = QPushButton("Test the node", self)
        self._test_btn.clicked.connect(self._on_test)
        for button in (self._request_btn, self._release_btn, self._test_btn):
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)

        self._bridge = _Bridge(self)
        self._bridge.state.connect(self._on_state)
        self._bridge.note.connect(self._on_note)
        self._bridge.failed.connect(self._on_failed)
        self._bridge.finished.connect(self._on_finished)
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self._render()

    # ── which host ───────────────────────────────────────────────────────────────────────────────────────────────
    def set_host(self, host: RemoteHost | None) -> None:
        """Show *host*'s node (``None``: no host selected). Reads it from the host at once; the same host again (the
        dialog recomputes its buttons for many reasons) is left alone."""
        if host == self._host:
            return
        self._host = host
        self._render()
        self.refresh()

    def _node(self) -> GrantedNode | None:
        host = self._host
        if host is None:
            return None
        log = lambda m, n=host.name: self._emit("note", n, m.strip())      # noqa: E731
        node = node_for(host, DatasetCache(host), log_fn=log)
        node.listen(log_fn=log, on_state=lambda st, n=host.name: self._emit("state", n, st))
        return node

    def _emit(self, signal: str, *args) -> None:
        """Emit from a worker thread. The panel may have been closed while the host was answering."""
        with swallow("a granted-node update reached a panel that was already closed"):
            getattr(self._bridge, signal).emit(*args)

    # ── reading the node ─────────────────────────────────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        """Read the selected host's node on a worker thread, and adopt one that is already granted (a heartbeat is
        started for it, or its script would release it two minutes later)."""
        host, node = self._host, self._node()
        if host is None or node is None or host.name in self._working or not can_request(host)[0]:
            return

        def work() -> None:
            try:
                self._emit("state", host.name, node.attach())
            except HoldError as exc:
                self._emit("failed", host.name, str(exc))

        threading.Thread(target=work, daemon=True, name="aw-granted-node-read").start()

    # ── Request ──────────────────────────────────────────────────────────────────────────────────────────────────
    def _on_request(self) -> None:
        host, node = self._host, self._node()
        if host is None or node is None or host.name in self._working:
            return
        self._working.add(host.name)
        self._notes[host.name] = ""
        self._render()

        def work() -> None:
            try:
                node.request()
            except HoldError as exc:
                self._emit("failed", host.name, str(exc))
            finally:
                self._emit("finished", host.name)

        threading.Thread(target=work, daemon=True, name="aw-granted-node-request").start()

    # ── Release ──────────────────────────────────────────────────────────────────────────────────────────────────
    def _on_release(self) -> None:
        host, node = self._host, self._node()
        if host is None or node is None:
            return
        state = self._states.get(host.name)
        if state is not None and state.phase == "busy" and not confirm_release_while_busy(self):
            return
        in_flight = host.name in self._working
        self._working.add(host.name)
        self._render()

        def work() -> None:
            try:
                node.release("released from Manage Remotes")
                for _ in range(10):                  # until the scheduler stops listing it (scancel is not instant)
                    state = node.state()
                    if not state.live or in_flight:
                        break
                    time.sleep(2)
                if not in_flight:
                    self._emit("state", host.name, state)
            except HoldError as exc:
                self._emit("failed", host.name, str(exc))
            finally:
                if not in_flight:
                    self._emit("finished", host.name)

        threading.Thread(target=work, daemon=True, name="aw-granted-node-release").start()

    # ── Test ─────────────────────────────────────────────────────────────────────────────────────────────────────
    def _on_test(self) -> None:
        host, state = self._host, self._states.get(self._host.name) if self._host else None
        if host is None or state is None or state.phase not in ("granted", "busy"):
            return
        from analyze.scheduler_test_dialog import SchedulerTestDialog
        from services.scheduler_probe import ProbeRun

        try:
            probe = ProbeRun(host, launcher=step_launcher(StepTarget.from_state(state)))
        except ValueError as exc:
            QMessageBox.warning(self, "Test the node", str(exc))
            return
        self._probe_dialog = SchedulerTestDialog(self, probe, f"{host.name or host.ssh_alias} (granted node)")
        self._probe_dialog.show()

    # ── answers from the worker threads ──────────────────────────────────────────────────────────────────────────
    def _on_state(self, name: str, state: HoldState) -> None:
        self._states[name] = state
        if self._host is not None and self._host.name == name:
            self._render()

    def _on_note(self, name: str, text: str) -> None:
        self._notes[name] = text
        if self._host is not None and self._host.name == name:
            self._render()

    def _on_failed(self, name: str, why: str) -> None:
        self._notes[name] = why
        if self._host is not None and self._host.name == name:
            self._render()

    def _on_finished(self, name: str) -> None:
        self._working.discard(name)
        self.refresh()
        self._render()

    # ── drawing ──────────────────────────────────────────────────────────────────────────────────────────────────
    def _render(self) -> None:
        host = self._host
        if host is None:
            self.setVisible(False)
            return
        self.setVisible(True)
        ok, why = can_request(host)
        state = self._states.get(host.name)
        live = state is not None and state.live
        working = host.name in self._working
        self._request_btn.setEnabled(ok and not live and not working)
        self._request_btn.setToolTip(why or "Ask the scheduler for a node and keep it for every run on this host.")
        self._release_btn.setEnabled(live or working)
        self._test_btn.setEnabled(state is not None and state.phase in ("granted", "busy"))
        if not ok:
            lines = [why]
        else:
            lines = describe_state(state, host.granted_idle_release_min) if state is not None else ["Checking…"]
            if working and (state is None or not state.live):
                lines = ["Asking the scheduler for a node…"]
            note = self._notes.get(host.name, "")
            if note:
                lines.append(note)
        self._readout.setText("\n".join(lines))
