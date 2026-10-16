"""The window the Remotes dialog's **Test** button opens.

Runs a :class:`services.scheduler_probe.ProbeRun` on a worker thread, streams
what the launch prints, and ends with what the job found on its node: which
node, how many of its CPUs the job got, the GPUs and whether Torch can use
them, and Cellpose. Closing the window ends the ssh session, which hands back
an allocation the job is still waiting for or holding.

Not modal: a queue wait can take minutes, and the rest of the app should stay
usable while it does.
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.safe import swallowed
from services.scheduler_probe import ProbeResult, ProbeRun, summarize


class _Bridge(QObject):
    """The worker thread's way back onto the GUI thread (queued signals)."""

    line = Signal(str)
    done = Signal(object)


class SchedulerTestDialog(QDialog):
    def __init__(self, parent: QWidget | None, probe: ProbeRun, host_name: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Test scheduler — {host_name}")
        self.resize(720, 460)
        self._probe = probe
        self._started = time.monotonic()
        self._granted = False
        self._finished = False

        layout = QVBoxLayout(self)
        self._status_lbl = QLabel("Sending the test job…", self)
        self._status_lbl.setObjectName("SectionTitle")
        layout.addWidget(self._status_lbl)
        hint = QLabel(
            "The job goes through the same launch as a run — the scheduler "
            "command, the conda environment, the node — and only looks at "
            "where it landed. A node that has to start can take several "
            "minutes.", self)
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._log = QPlainTextEdit(self)
        self._log.setReadOnly(True)
        layout.addWidget(self._log, 1)
        self._summary_lbl = QLabel("", self)
        self._summary_lbl.setWordWrap(True)
        layout.addWidget(self._summary_lbl)
        row = QHBoxLayout()
        row.addStretch(1)
        self._close_btn = QPushButton("Cancel", self)
        self._close_btn.clicked.connect(self.close)
        row.addWidget(self._close_btn)
        layout.addLayout(row)

        self._bridge = _Bridge(self)
        self._bridge.line.connect(self._on_line)
        self._bridge.done.connect(self._on_done)
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick)
        self._clock.start()
        threading.Thread(target=self._work, daemon=True).start()

    # ── worker thread ─────────────────────────────────────────────────────
    def _work(self) -> None:
        try:
            result = self._probe.run(self._bridge.line.emit)
        except Exception as exc:
            swallowed("running the scheduler test")
            result = ProbeResult(returncode=None, output=[f"{exc}\n"])
        self._bridge.done.emit(result)

    # ── GUI thread ────────────────────────────────────────────────────────
    def _tick(self) -> None:
        secs = int(time.monotonic() - self._started)
        what = ("Running on the node" if self._granted
                else "Waiting for the scheduler to grant a node")
        self._status_lbl.setText(f"{what}… {secs // 60}:{secs % 60:02d}")

    def _on_line(self, line: str) -> None:
        if "allocation granted" in line:
            self._granted = True
        self._log.appendPlainText(line.rstrip("\n"))

    def _on_done(self, result: ProbeResult) -> None:
        self._finished = True
        self._clock.stop()
        passed, sentences = summarize(result)
        self._status_lbl.setText("Test passed" if passed
                                 else "Test finished — see below")
        self._summary_lbl.setText("\n".join(sentences))
        self._close_btn.setText("Close")

    def closeEvent(self, event) -> None:  # Qt override
        if not self._finished:
            self._probe.cancel()
        self._clock.stop()
        super().closeEvent(event)
