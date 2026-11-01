"""The **Granted node log** window, opened from Manage Remotes' Granted node panel (its **Log…** button).

The panel's readout shows one line — the node's state and the last thing it said — and each new line replaces the
one before. This window shows everything the selected host's granted node has said this session, oldest first, with
the time of each line: the request's queue and boot progress, heartbeat warnings, a run's lease warnings, failures to
reach the host, and the release. The lines are kept on the node's process-wide handle (``services.node_log``), so the
window also shows what was said while Manage Remotes was closed.

It is not modal: it stays open beside the dialog, follows the host selected there, and adds lines as they arrive.
"""

from __future__ import annotations

from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from services.node_log import NodeLog

#: Shown when no host is selected, or the host's node has said nothing yet this session.
EMPTY_TEXT = "Nothing has been logged for this host's granted node in this session."


class GrantedNodeLogWindow(QDialog):
    """A read-only view of one host's :class:`~services.node_log.NodeLog`."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModal(False)
        self.setWindowTitle("Granted node log")
        self.resize(760, 420)
        self._host_name = ""
        self._log: NodeLog | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(8)
        self._title = QLabel("", self)
        self._title.setObjectName("SectionTitle")
        layout.addWidget(self._title)
        hint = QLabel("Everything this host's granted node has said in this session, oldest first. The node's own "
                      "files on the host (its holds directory's log and ended) say what happened while the app "
                      "was not running.", self)
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._text = QPlainTextEdit(self)
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono = QFont("Monaco")
        mono.setStyleHint(QFont.TypeWriter)
        self._text.setFont(mono)
        layout.addWidget(self._text, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        self._copy_btn = QPushButton("Copy", self)
        self._copy_btn.clicked.connect(self._on_copy)
        row.addWidget(self._copy_btn)
        close = QPushButton("Close", self)
        close.clicked.connect(self.close)
        row.addWidget(close)
        layout.addLayout(row)
        self.refresh()

    def show_log(self, host_name: str, log: NodeLog | None) -> None:
        """Show *host_name*'s node log (``log`` ``None``: no host selected, or none made for it yet)."""
        self._host_name, self._log = host_name, log
        self.refresh()

    def refresh(self) -> None:
        """Re-read the log. The view stays at the bottom when it was there, so new lines stay in sight; a user who
        scrolled up to read an older line is left where they are."""
        self._title.setText(f"Granted node — {self._host_name}" if self._host_name else "Granted node")
        text = self._log.render() if self._log is not None else ""
        shown = text or EMPTY_TEXT
        if shown == self._text.toPlainText():
            return
        bar = self._text.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum()
        kept = bar.value()
        self._text.setPlainText(shown)
        bar.setValue(bar.maximum() if at_bottom else kept)
        self._copy_btn.setEnabled(bool(text))

    def text(self) -> str:
        return self._text.toPlainText()

    def _on_copy(self) -> None:
        QGuiApplication.clipboard().setText(self._log.render() if self._log is not None else "")
