"""The window **Show log** opens in Manage Remotes' Granted node panel: what a node request sent and heard.

A request for a node waits in the scheduler's queue for minutes and ends in one of a dozen ways, and the panel's readout
shows only the latest. This window shows the rest, in two parts:

* **This app's record** (``services.remote_hold.HoldLog``): the ``sbatch`` that was sent, every change in the state and
  the reason the scheduler gave, every answer from the host that was not ok, and what ended it. It fills in live while the
  window is open, and starts with everything since the app began.
* **On the cluster**: what the host holds about the hold job — the job script's own output, why it ended, what it wrote
  when it was granted, and the scheduler's full record of the job (``scontrol show job``). Read over ssh on a worker thread
  when the window opens, on **Refresh**, and every few seconds while it is open.

Not modal: a request can take minutes, and the rest of the app stays usable while it does. Everything that talks to the host
runs on a worker thread and reaches the window through queued signals.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.safe import swallow
from services.remote_hold import GrantedNode, HoldError

#: How often the cluster-side part is read again while the window is open, in milliseconds.
REFRESH_MS = 5_000


class _Bridge(QObject):
    """The worker threads' way back onto the GUI thread (queued signals)."""

    line = Signal(str)            # one line of this app's record
    remote = Signal(str)          # the cluster-side text
    remote_failed = Signal(str)   # why it could not be read


def _view(parent: QWidget) -> QPlainTextEdit:
    view = QPlainTextEdit(parent)
    view.setReadOnly(True)
    view.setLineWrapMode(QPlainTextEdit.NoWrap)
    view.setMaximumBlockCount(5000)
    view.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
    return view


class GrantedNodeLogDialog(QDialog):
    def __init__(self, parent: QWidget | None, node: GrantedNode, host_name: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Granted node log — {host_name}")
        self.setModal(False)
        self.resize(820, 600)
        self._node = node
        self._reading = False

        layout = QVBoxLayout(self)
        title = QLabel("This app's record", self)
        title.setObjectName("SectionTitle")
        layout.addWidget(title)
        hint = QLabel("What was sent to the scheduler and what it answered, since this app started.", self)
        hint.setObjectName("Muted")
        layout.addWidget(hint)
        self._app_view = _view(self)
        layout.addWidget(self._app_view, 3)

        remote_title = QLabel("On the cluster", self)
        remote_title.setObjectName("SectionTitle")
        layout.addWidget(remote_title)
        self._remote_hint = QLabel("The hold job's own output and the scheduler's record of it.", self)
        self._remote_hint.setObjectName("Muted")
        layout.addWidget(self._remote_hint)
        self._remote_view = _view(self)
        layout.addWidget(self._remote_view, 4)

        row = QHBoxLayout()
        row.addStretch(1)
        self._refresh_btn = QPushButton("Refresh", self)
        self._refresh_btn.clicked.connect(self.refresh)
        self._copy_btn = QPushButton("Copy all", self)
        self._copy_btn.clicked.connect(self._copy)
        self._close_btn = QPushButton("Close", self)
        self._close_btn.clicked.connect(self.close)
        for button in (self._refresh_btn, self._copy_btn, self._close_btn):
            row.addWidget(button)
        layout.addLayout(row)

        self._bridge = _Bridge(self)
        self._bridge.line.connect(self._app_view.appendPlainText)
        self._bridge.remote.connect(self._on_remote)
        self._bridge.remote_failed.connect(self._on_remote_failed)

        self._app_view.setPlainText("\n".join(node.transcript.lines()))
        self._stop_listening = node.transcript.subscribe(self._on_line)
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.finished.connect(lambda _code: (self._stop_listening(), self._timer.stop()))
        self.refresh()

    # ── live record ──────────────────────────────────────────────────────────────────────────────────────────────
    def _on_line(self, line: str) -> None:
        """Called from whichever thread wrote the line."""
        with swallow("a granted-node log line reached a window that was already closed"):
            self._bridge.line.emit(line)

    # ── cluster side ─────────────────────────────────────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        """Read the cluster-side text on a worker thread. One read at a time: a host that answers slowly must not pile
        reads up behind each other."""
        if self._reading:
            return
        self._reading = True

        def work() -> None:
            try:
                text = self._node.read_remote_log()
            except HoldError as exc:
                self._emit("remote_failed", str(exc))
            else:
                self._emit("remote", text)

        threading.Thread(target=work, daemon=True, name="aw-granted-node-log-read").start()

    def _emit(self, signal: str, text: str) -> None:
        with swallow("a granted-node log read finished after its window was closed"):
            getattr(self._bridge, signal).emit(text)

    def _on_remote(self, text: str) -> None:
        self._reading = False
        bar = self._remote_view.verticalScrollBar()
        keep = bar.value()
        at_end = keep >= bar.maximum()
        self._remote_view.setPlainText(text)
        bar.setValue(bar.maximum() if at_end else keep)
        self._remote_hint.setText("The hold job's own output and the scheduler's record of it. Read from the host just now.")

    def _on_remote_failed(self, why: str) -> None:
        self._reading = False
        self._remote_hint.setText(f"Could not read it from the host just now: {why}")

    # ── copy ─────────────────────────────────────────────────────────────────────────────────────────────────────
    def _copy(self) -> None:
        QApplication.clipboard().setText(
            "== This app's record ==\n" + self._app_view.toPlainText()
            + "\n\n== On the cluster ==\n" + self._remote_view.toPlainText())
