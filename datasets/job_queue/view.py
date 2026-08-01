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
    QProgressBar,
    QPushButton,
    QScrollArea,
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
    "import_loose": "Zip loose",
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
    JobState.SUSPENDED: "warn",
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

    def __init__(
        self, job: JobSpec, parent: QWidget | None = None, *, ordinal: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.job_id = job.id
        # The whole row is a click target (rowClicked -> "show this job" on
        # its own page) with no other visual cue that it's interactive —
        # a pointing-hand cursor and a tooltip on hover are the only signal
        # a user gets before clicking.
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to open this job on its own page.")
        # A thin divider under every row -- eight nearly-identical lines with
        # no separation at all read as one undifferentiated block.
        self.setStyleSheet(f"border-bottom: 1px solid {theme.Colors.border_subtle};")
        hl = QHBoxLayout(self)
        hl.setContentsMargins(theme.Spacing.sm, theme.Spacing.xs,
                               theme.Spacing.sm, theme.Spacing.xs)
        hl.setSpacing(theme.Spacing.sm)

        # "What runs next" couldn't be read at a glance among interleaved
        # pending/succeeded/failed rows with no position numbers at all.
        # Only pending rows get one -- a number on a row that already has a
        # final outcome doesn't mean "queue position" anymore.
        ordinal_lbl = QLabel(f"{ordinal}." if ordinal is not None else "", self)
        ordinal_lbl.setObjectName("Muted")
        ordinal_lbl.setFixedWidth(20)
        ordinal_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        hl.addWidget(ordinal_lbl)

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

        if job.state == JobState.RUNNING:
            # No per-job percentage is ever reported (a controller only ever
            # sets a free-text status message), so the running row otherwise
            # looked identical to an idle pending one until it finished.
            progress = QProgressBar(self)
            progress.setRange(0, 0)  # indeterminate
            progress.setTextVisible(False)
            progress.setFixedSize(60, 8)
            hl.addWidget(progress)

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

        # Reordering a terminal row (it already ran, or never will without an
        # explicit unchain/retry) means nothing -- shown only while a move
        # could still do something.
        if job.state not in JobState.TERMINAL:
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
    stopRequested = Signal()
    addAnalyzeRequested = Signal()
    removeRequested = Signal(str)
    moveUpRequested = Signal(str)
    moveDownRequested = Signal(str)
    showRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        # Wrap in a scroll area — see datasets/concatenate/view.py's
        # _build_ui for why. Job Queue was the one page of the seven left
        # out of this pattern, with a long-enough job list able to run into
        # the same "crushed into whatever space is left" problem the other
        # six were already fixed against.
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        page_layout.addWidget(scroll)

        content = QWidget(scroll)
        scroll.setWidget(content)
        outer = QVBoxLayout(content)
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
            "Pause winds the running job down and keeps what it has finished, "
            "so starting the queue again — even in a new session — carries on "
            "from there. Stop job discards whatever is in flight.",
            self,
        )
        blurb.setObjectName("Muted")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        header = QWidget(self)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(theme.Spacing.sm)
        # Primary action at the left edge, matching every other Datasets
        # page's Run/Check/Convert/Subset/Merge button -- Start queue used
        # to sit far right with a secondary button on the left, the one
        # page in the pane with its hierarchy backwards.
        self._start_btn = QPushButton("▶  Start queue", self)
        self._start_btn.setProperty("variant", "primary")
        self._start_btn.clicked.connect(self.startToggled)
        hl.addWidget(self._start_btn)
        # Pause winds the running job down and keeps its progress; this
        # discards whatever is in flight. Two buttons because they are two
        # different answers to "I want this to stop", and the destructive one
        # should never be the one you reach for by default.
        self._stop_btn = QPushButton("■  Stop job", self)
        self._stop_btn.setProperty("variant", "danger")
        self._stop_btn.setEnabled(False)
        self._stop_btn.setToolTip(
            "Cancel the running job outright, discarding whatever it has in "
            "flight. Pause queue winds it down instead, keeping its progress.")
        self._stop_btn.clicked.connect(self.stopRequested)
        hl.addWidget(self._stop_btn)
        self._add_analyze_btn = QPushButton("+ Queue current Analyze run", self)
        self._add_analyze_btn.setProperty("variant", "secondary")
        self._add_analyze_btn.clicked.connect(self.addAnalyzeRequested)
        hl.addWidget(self._add_analyze_btn)
        hl.addStretch(1)
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
        pending_ordinal = 0
        for job in jobs:
            ordinal = None
            if job.state == JobState.PENDING:
                pending_ordinal += 1
                ordinal = pending_ordinal
            row = _JobRow(job, self._list, ordinal=ordinal)
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

    def set_job_running(self, running: bool) -> None:
        """Stop only means something while a job is actually in flight."""
        self._stop_btn.setEnabled(bool(running))
