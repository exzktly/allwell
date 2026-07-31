"""Qt builder for the Job Queue page.

One compact row per job — kind icon, label, a colour-coded state chip, a
one-line status message, a link glyph on chained rows, and ↑ / ↓ / × controls
— under a header with Start/Pause and (since Analyze's own page has no
budget headroom for its own "+ Queue" button — see ARCHITECTURE §6b) a
"Queue current Analyze run" button that lives here instead.

Pure view: it owns widgets and emits intent; :mod:`~datasets.job_queue.controller`
wires it to the real :class:`~datasets.job_queue.controller.QueueBridge`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import theme
from widgets.icons import make_icon
from widgets.status_dot import StatusDot
from services.job_queue import JobSpec, JobState

_KIND_ICON = {
    "analyze": "sliders-horizontal",
    "concatenate": "copy",
    "ome_import": "folder-open",
    "validate": "scan-line",
    "subset": "boxes",
    "import_loose": "download",
    "merge_plates": "layout-grid",
}

KIND_LABEL = {
    "analyze": "Analyze",
    "concatenate": "Concatenate",
    "ome_import": "Convert OME-TIFF",
    "validate": "Validate",
    "subset": "Subset",
    "import_loose": "Import loose",
    "merge_plates": "Merge plates",
}

_STATE_DOT = {
    JobState.PENDING: "neutral",
    JobState.RUNNING: "accent",
    JobState.SUCCEEDED: "success",
    JobState.FAILED: "danger",
    JobState.CANCELLED: "warn",
    JobState.BLOCKED: "warn",
    JobState.INTERRUPTED: "warn",
}


def _hrule(parent: QWidget) -> QFrame:
    rule = QFrame(parent)
    rule.setFrameShape(QFrame.HLine)
    rule.setObjectName("HRule")
    return rule


class _JobRow(QWidget):
    """One job's row: icon + label + state chip + message + controls."""

    removeClicked = Signal(str)
    moveUpClicked = Signal(str)
    moveDownClicked = Signal(str)
    rowClicked = Signal(str)

    def __init__(self, job: JobSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.job_id = job.id
        # The whole row is a click target (rowClicked -> "show this job" on
        # its own page) with no other visual cue that it's interactive —
        # a pointing-hand cursor and a tooltip on hover are the only signal
        # a user gets before clicking.
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to open this job on its own page.")
        hl = QHBoxLayout(self)
        hl.setContentsMargins(theme.Spacing.sm, theme.Spacing.xs,
                               theme.Spacing.sm, theme.Spacing.xs)
        hl.setSpacing(theme.Spacing.sm)

        icon_lbl = QLabel(self)
        icon_lbl.setPixmap(make_icon(_KIND_ICON.get(job.kind, "boxes")).pixmap(16, 16))
        hl.addWidget(icon_lbl)

        label = QLabel(job.label or KIND_LABEL.get(job.kind, job.kind), self)
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        hl.addWidget(label, 1)

        if job.chain_from:
            link_lbl = QLabel(self)
            link_lbl.setPixmap(make_icon("waypoints").pixmap(14, 14))
            link_lbl.setToolTip(f"Runs after {job.chain_from!r} succeeds.")
            hl.addWidget(link_lbl)

        dot = StatusDot(_STATE_DOT.get(job.state, "neutral"), self)
        dot.setLabel(job.state)
        hl.addWidget(dot)

        if job.message:
            msg = QLabel(self)
            msg.setObjectName("Muted")
            msg.setToolTip(job.message)
            msg.setMaximumWidth(240)
            # Elided with "…", not hard-clipped -- setMaximumWidth alone cut
            # the label off mid-character with no indication text was
            # missing; the full message was reachable only via the tooltip,
            # which nothing about the label's own appearance suggested existed.
            metrics = QFontMetrics(msg.font())
            msg.setText(metrics.elidedText(job.message, Qt.ElideRight, 240))
            hl.addWidget(msg)

        up_btn = QToolButton(self)
        up_btn.setIcon(make_icon("chevron-up"))
        up_btn.setAutoRaise(True)
        up_btn.setToolTip("Move up")
        up_btn.clicked.connect(lambda: self.moveUpClicked.emit(self.job_id))
        hl.addWidget(up_btn)

        down_btn = QToolButton(self)
        down_btn.setIcon(make_icon("chevron-down"))
        down_btn.setAutoRaise(True)
        down_btn.setToolTip("Move down")
        down_btn.clicked.connect(lambda: self.moveDownClicked.emit(self.job_id))
        hl.addWidget(down_btn)

        remove_btn = QToolButton(self)
        remove_btn.setIcon(make_icon("x"))
        remove_btn.setAutoRaise(True)
        remove_btn.setToolTip("Remove from queue")
        remove_btn.clicked.connect(lambda: self.removeClicked.emit(self.job_id))
        hl.addWidget(remove_btn)

    def mouseReleaseEvent(self, event) -> None:
        self.rowClicked.emit(self.job_id)
        super().mouseReleaseEvent(event)


class JobQueuePage(QWidget):
    """The Job Queue feature body."""

    startToggled = Signal()
    addAnalyzeRequested = Signal()
    removeRequested = Signal(str)
    moveUpRequested = Signal(str)
    moveDownRequested = Signal(str)
    showRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.Spacing.lg, theme.Spacing.lg,
                                  theme.Spacing.lg, theme.Spacing.lg)
        outer.setSpacing(theme.Spacing.md)

        heading = QLabel("Job Queue", self)
        heading.setObjectName("Heading")
        outer.addWidget(heading)
        outer.addWidget(_hrule(self))

        blurb = QLabel(
            "Line up Analyze runs and Datasets tools to work through "
            "unattended. Strictly serial — one job runs at a time, in order. "
            "A queued run proceeds without the interactive confirmations its "
            "own page would show, since there's no one there to ask. "
            "Stopping the running job pauses the queue; resuming is explicit.",
            self,
        )
        blurb.setObjectName("Muted")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        header = QWidget(self)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(theme.Spacing.sm)
        self._add_analyze_btn = QPushButton("+ Queue current Analyze run", self)
        self._add_analyze_btn.setProperty("variant", "secondary")
        self._add_analyze_btn.clicked.connect(self.addAnalyzeRequested)
        hl.addWidget(self._add_analyze_btn)
        hl.addStretch(1)
        self._start_btn = QPushButton("▶  Start queue", self)
        self._start_btn.setProperty("variant", "primary")
        self._start_btn.clicked.connect(self.startToggled)
        hl.addWidget(self._start_btn)
        outer.addWidget(header)

        self._list = QListWidget(self)
        self._list.setObjectName("JobQueueList")
        self._list.setSelectionMode(QListWidget.NoSelection)
        outer.addWidget(self._list, 1)
        self._show_empty_hint()

    # ── public API used by the controller ───────────────────────────────
    def _show_empty_hint(self) -> None:
        # Lives inside the (otherwise large, blank) list itself, not as a
        # separate caption below it — an empty container that big with no
        # explanation inside it reads as broken, not "nothing queued yet".
        label = QLabel(
            "No jobs queued yet — use “+ Queue” on any Datasets tool's page, "
            "or “+ Queue current Analyze run” above.", self._list,
        )
        label.setObjectName("Muted")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignCenter)
        item = QListWidgetItem(self._list)
        item.setFlags(item.flags() & ~(Qt.ItemIsSelectable | Qt.ItemIsEnabled))
        item.setSizeHint(label.sizeHint())
        self._list.addItem(item)
        self._list.setItemWidget(item, label)

    def set_jobs(self, jobs: list[JobSpec]) -> None:
        self._list.clear()
        if not jobs:
            self._show_empty_hint()
            return
        for job in jobs:
            row = _JobRow(job, self._list)
            row.removeClicked.connect(self.removeRequested)
            row.moveUpClicked.connect(self.moveUpRequested)
            row.moveDownClicked.connect(self.moveDownRequested)
            row.rowClicked.connect(self.showRequested)
            item = QListWidgetItem(self._list)
            item.setSizeHint(row.sizeHint())
            item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
            self._list.addItem(item)
            self._list.setItemWidget(item, row)

    def set_started(self, started: bool) -> None:
        self._start_btn.setText("⏸  Pause queue" if started else "▶  Start queue")
