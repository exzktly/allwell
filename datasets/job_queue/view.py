"""Qt builder for the Job Queue page.

A header of queue-wide actions over a list of :class:`~datasets.job_queue.row.JobRow`
rows. The page owns widgets and emits intent;
:mod:`~datasets.job_queue.controller` wires it to the real
:class:`~datasets.job_queue.controller.QueueBridge`.

The rows sit in a plain ``QVBoxLayout`` rather than a ``QListWidget``, for two
reasons that both come out of updating a row in place. A ``QListWidgetItem``'s
size hint is set once from the row's ``sizeHint()``, so a row that later shows
or hides its second line changes height while the item's hint does not, and
the rows clip or overlap. And the drag-handle reorder is
``widgets/saved_selections_list.py``'s, which hosts its own rows in a box
layout — matching it makes that a like-for-like transfer. The page already
lives in a ``QScrollArea``, so the list widget's own scrolling was redundant
anyway.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import theme
from datasets._common.log_panel import hrule as _hrule
from services.job_kinds import KIND_LABEL
from services.job_queue import JobSpec, JobState
from datasets.job_queue.row import JobRow
from widgets import menu_exec

# The order the "+ Add job" menu offers the tools in — Analyze first because
# it is the run everything else exists to prepare for or tidy up after, then
# the Datasets tools in the order their own rail lists them.
ADD_KINDS = ("analyze", "concatenate", "ome_import", "validate", "subset",
             "import_loose", "merge_plates")

_STATE_WORD = {
    JobState.PENDING: "pending",
    JobState.RUNNING: "running",
    JobState.SUCCEEDED: "done",
    JobState.FAILED: "failed",
    JobState.CANCELLED: "cancelled",
    JobState.BLOCKED: "blocked",
    JobState.INTERRUPTED: "interrupted",
    JobState.SUSPENDED: "suspended",
}




class JobQueuePage(QWidget):
    """The Job Queue feature body."""

    startToggled = Signal()
    stopRequested = Signal()
    clearCompletedRequested = Signal()
    addKindRequested = Signal(str)
    removeRequested = Signal(str)
    moveUpRequested = Signal(str)
    moveDownRequested = Signal(str)
    moveByRequested = Signal(str, int)
    editRequested = Signal(str)
    duplicateRequested = Signal(str)
    retryRequested = Signal(str)
    chainRequested = Signal(str, str)
    showRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: dict[str, JobRow] = {}
        self._order: list[str] = []
        self._build_ui()

    def _build_ui(self) -> None:
        # Wrap in a scroll area — see datasets/concatenate/view.py's _build_ui
        # for why. A long job list would otherwise be crushed into whatever
        # space is left, the same problem the other six pages were fixed
        # against.
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        page_layout.addWidget(self._scroll)

        content = QWidget(self._scroll)
        self._scroll.setWidget(content)
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

        outer.addWidget(self._build_header())
        outer.addWidget(self._build_summary())

        # The guidance for an empty queue is a plain caption, not a row inside
        # the list. Put inside, it had to bring the whole list with it — a
        # bordered box carrying a layout stretch, so one sentence rendered as
        # a page-height empty panel.
        self._empty_hint = QLabel(
            "No jobs queued yet — use “+ Add job” above, or “+ Queue” on any "
            "tool's own page.", self,
        )
        self._empty_hint.setObjectName("Muted")
        self._empty_hint.setWordWrap(True)
        outer.addWidget(self._empty_hint)

        self._list = QWidget(self)
        self._list.setObjectName("JobQueueList")
        self._list_layout = QVBoxLayout(self._list)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(0)
        outer.addWidget(self._list)

        # Trailing stretch so an empty page ends under its caption instead of
        # stretching whatever is left to fill the scroll area's viewport.
        outer.addStretch(1)
        self._show_empty_hint()

    def _build_header(self) -> QWidget:
        header = QWidget(self)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(theme.Spacing.sm)

        # Primary action at the left edge, matching every other Datasets
        # page's own Run/Check/Convert button.
        self._start_btn = QPushButton("▶  Start queue", self)
        self._start_btn.setProperty("variant", "primary")
        self._start_btn.clicked.connect(self.startToggled)
        hl.addWidget(self._start_btn)

        # Pause winds the running job down and keeps its progress; this
        # discards whatever is in flight. Two buttons because they are two
        # different answers to "I want this to stop", and the destructive one
        # should never be the one reached for by default.
        self._stop_btn = QPushButton("■  Stop job", self)
        self._stop_btn.setProperty("variant", "danger")
        self._stop_btn.setEnabled(False)
        self._stop_btn.setToolTip(
            "Cancel the running job outright, discarding whatever it has in "
            "flight. Pause queue winds it down instead, keeping its progress.")
        self._stop_btn.clicked.connect(self.stopRequested)
        hl.addWidget(self._stop_btn)

        # The page can now answer "how do I add one?" by itself. Previously
        # the only thing here was a button for Analyze specifically, and every
        # other kind had to be started from its own page.
        self._add_btn = QPushButton("+  Add job", self)
        self._add_btn.setProperty("variant", "secondary")
        self._add_btn.setToolTip(
            "Open a tool's page to set up a new queued job.")
        self._add_btn.clicked.connect(self._open_add_menu)
        hl.addWidget(self._add_btn)

        hl.addStretch(1)

        self._clear_btn = QPushButton("Clear completed", self)
        self._clear_btn.setToolTip(
            "Remove every finished job — succeeded, failed, cancelled or "
            "blocked. Pending and running jobs are left alone.")
        self._clear_btn.clicked.connect(self.clearCompletedRequested)
        hl.addWidget(self._clear_btn)
        return header

    def _build_summary(self) -> QWidget:
        wrap = QWidget(self)
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        self._summary = QLabel("", self)
        self._summary.setObjectName("Muted")
        col.addWidget(self._summary)
        # What loading the queue had to repair. Silent before this: a queue
        # that came back from a crash with a job marked interrupted said so
        # nowhere the user could see.
        self._notes = QLabel("", self)
        self._notes.setObjectName("Warn")
        self._notes.setWordWrap(True)
        self._notes.setVisible(False)
        col.addWidget(self._notes)
        return wrap

    def _open_add_menu(self) -> None:
        menu = self.build_add_menu()
        menu_exec.exec_menu(
            menu, self._add_btn.mapToGlobal(self._add_btn.rect().bottomLeft()))

    def build_add_menu(self) -> QMenu:
        """The "+ Add job" menu, returned rather than opened.

        Same reason as ``JobRow.build_menu``: exec()ing a popup under the
        offscreen platform blocks, and pytest's signal timeout cannot
        interrupt Qt's C++ event loop.
        """
        menu = QMenu(self)
        menu.setObjectName("AddJobMenu")
        for kind in ADD_KINDS:
            act = menu.addAction(KIND_LABEL.get(kind, kind))
            act.triggered.connect(lambda _checked=False, k=kind:
                                  self.addKindRequested.emit(k))
        return menu

    # ── public API used by the controller ───────────────────────────────
    def _show_empty_hint(self) -> None:
        """Swap the list out for the one-line caption."""
        self._clear_rows()
        self._list.setVisible(False)
        self._empty_hint.setVisible(True)

    def _clear_rows(self) -> None:
        for row in self._rows.values():
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()
        self._order.clear()

    def set_jobs(self, jobs: list[JobSpec]) -> None:
        """Render *jobs*, reusing the existing rows whenever the queue's
        membership and order are unchanged.

        The page refreshes on every ``queueChanged``, and during a run that
        includes each status-message tick. Rebuilding every row for those
        threw away the scroll position and any hover state several times a
        second; now only a genuine add, remove or reorder rebuilds.
        """
        if not jobs:
            self._show_empty_hint()
            return
        self._empty_hint.setVisible(False)
        self._list.setVisible(True)

        ordinals = self._pending_ordinals(jobs)
        ids = [job.id for job in jobs]
        if ids != self._order:
            self._rebuild_rows(jobs)
        for job in jobs:
            row = self._rows.get(job.id)
            if row is not None:
                row.update_from(job, ordinal=ordinals.get(job.id),
                                predecessors=self._predecessors_for(job.id, jobs))

    @staticmethod
    def _pending_ordinals(jobs: list[JobSpec]) -> dict[str, int]:
        """Queue positions, counted over pending jobs only.

        A number on a row that already has a final outcome does not mean
        "queue position" any more, so those rows get none.
        """
        ordinals: dict[str, int] = {}
        n = 0
        for job in jobs:
            if job.state == JobState.PENDING:
                n += 1
                ordinals[job.id] = n
        return ordinals

    @staticmethod
    def _predecessors_for(job_id: str, jobs: list[JobSpec]) -> list[tuple[str, str]]:
        """The jobs above *job_id*, as (id, label) for its "Run after" menu.

        Only the ones above: the queue refuses an order that would place a
        chained job before its predecessor, so offering the rest would be
        offering choices that cannot be made.
        """
        out: list[tuple[str, str]] = []
        for job in jobs:
            if job.id == job_id:
                break
            out.append((job.id, job.label or job.kind))
        return out

    def _rebuild_rows(self, jobs: list[JobSpec]) -> None:
        self._clear_rows()
        for job in jobs:
            row = JobRow(job, self._list)
            row.removeClicked.connect(self.removeRequested)
            row.moveUpClicked.connect(self.moveUpRequested)
            row.moveDownClicked.connect(self.moveDownRequested)
            row.editClicked.connect(self.editRequested)
            row.duplicateClicked.connect(self.duplicateRequested)
            row.retryClicked.connect(self.retryRequested)
            row.chainRequested.connect(self.chainRequested)
            row.dragMoveBy.connect(self.moveByRequested)
            row.rowClicked.connect(self.showRequested)
            self._list_layout.addWidget(row)
            self._rows[job.id] = row
        self._order = [job.id for job in jobs]

    def set_summary(self, counts: dict[str, int]) -> None:
        """The one-line tally above the list. States with no jobs are left
        out — a row of zeroes is noise, and "0 failed" is not news."""
        parts = [f"{counts.get(state, 0)} {word}"
                 for state, word in _STATE_WORD.items() if counts.get(state)]
        self._summary.setText("  ·  ".join(parts))
        self._summary.setVisible(bool(parts))

    def set_repair_notes(self, notes: list[str]) -> None:
        self._notes.setText("  ".join(notes))
        self._notes.setVisible(bool(notes))

    def set_started(self, started: bool) -> None:
        self._start_btn.setText("⏸  Pause queue" if started else "▶  Start queue")

    def set_job_running(self, running: bool) -> None:
        """Stop only means something while a job is actually in flight."""
        self._stop_btn.setEnabled(bool(running))
