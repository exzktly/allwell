"""One job's row on the Job Queue page.

Two lines, not one. The single cramped line this replaces had to fit an
ordinal, an icon, a name, a state chip, a status message and three buttons
side by side, which left the message a fixed 240px regardless of how wide the
pane actually was and gave every job of a kind the same undifferentiated
appearance. Here the identifying half sits on the first line and everything
descriptive — what the job runs against, what its predecessor is, what it last
said — goes on a muted second line that elides against the row's real width.

The three buttons become one overflow menu. That is not only tidier: it is
where *Edit…*, *Duplicate*, *Run after* and *Retry* can live at all, none of
which fits as another glyph in the cluster.

:meth:`JobRow.update_from` rewrites a row in place. The page refreshes on every
``queueChanged``, which includes each status-message tick during a run, and
rebuilding every row for those threw away scroll position and hover state
several times a second. Every child is therefore built once in ``__init__``
and shown or hidden afterwards — nothing here is conditionally constructed.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui import theme
from widgets.icon_button import IconButton
from widgets import menu_exec
from widgets.icons import make_icon
from widgets.status_dot import StatusDot
from services.job_kinds import KIND_LABEL, describe, describe_detail
from services.job_queue import JobSpec, JobState

# Queue position number at the row's left edge.
_ORDINAL_W = 20
# Inline per-job progress bar.
_ROW_BAR_W = 60
_ROW_BAR_H = 8

KIND_ICON = {
    "analyze": "sliders-horizontal",
    "concatenate": "copy",
    "ome_import": "folder-open",
    "validate": "scan-line",
    "subset": "boxes",
    "import_loose": "download",
    "merge_plates": "layout-grid",
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


class JobRow(QWidget):
    """A single queue entry, rewritable in place."""

    removeClicked = Signal(str)
    moveUpClicked = Signal(str)
    moveDownClicked = Signal(str)
    editClicked = Signal(str)
    duplicateClicked = Signal(str)
    retryClicked = Signal(str)
    rowClicked = Signal(str)
    # (job_id, predecessor_id or "" to unchain)
    chainRequested = Signal(str, str)
    # (job_id, net ± rows) — emitted once, on release
    dragMoveBy = Signal(str, int)

    def __init__(self, job: JobSpec, parent: QWidget | None = None, *,
                 ordinal: int | None = None) -> None:
        super().__init__(parent)
        self.job_id = job.id
        self._job = job
        self._detail_text = ""
        self._predecessors: list[tuple[str, str]] = []
        self._drag_y0: float | None = None
        self._drag_steps = 0

        self.setObjectName("JobRow")          # styled in theme.qss, not inline
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to open this job on its own page.")

        outer = QHBoxLayout(self)
        outer.setContentsMargins(theme.Spacing.sm, theme.Spacing.xs,
                                 theme.Spacing.sm, theme.Spacing.xs)
        outer.setSpacing(theme.Spacing.sm)

        # A grab handle rather than a draggable row: the row itself is a click
        # target ("open this job"), and a widget that is both would make every
        # click a potential drag. Mirrors widgets/saved_selections_list.py.
        self._handle = QLabel(self)
        self._handle.setPixmap(make_icon("move").pixmap(12, 14))
        self._handle.setCursor(Qt.OpenHandCursor)
        self._handle.setToolTip("Drag to reorder")
        self._handle.mousePressEvent = self._handle_press
        self._handle.mouseMoveEvent = self._handle_move
        self._handle.mouseReleaseEvent = self._handle_release
        outer.addWidget(self._handle, 0, Qt.AlignTop)

        # "What runs next" is unreadable among interleaved pending/succeeded
        # rows with no positions. Only pending rows carry one — a number on a
        # row with a final outcome no longer means "queue position".
        self._ordinal = QLabel(self)
        self._ordinal.setObjectName("Muted")
        self._ordinal.setFixedWidth(_ORDINAL_W)
        self._ordinal.setAlignment(Qt.AlignRight | Qt.AlignTop)
        outer.addWidget(self._ordinal, 0, Qt.AlignTop)

        self._icon = QLabel(self)
        outer.addWidget(self._icon, 0, Qt.AlignTop)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(1)
        outer.addLayout(body, 1)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(theme.Spacing.sm)
        body.addLayout(top)

        self._name = QLabel(self)
        self._name.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        top.addWidget(self._name, 1)

        self._chain_glyph = QLabel(self)
        self._chain_glyph.setPixmap(make_icon("waypoints").pixmap(14, 14))
        top.addWidget(self._chain_glyph)

        # Indeterminate: a controller only ever reports a free-text status
        # message, never a percentage, so there is no fraction to draw.
        self._progress = QProgressBar(self)
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        self._progress.setFixedSize(_ROW_BAR_W, _ROW_BAR_H)
        top.addWidget(self._progress)

        self._dot = StatusDot("neutral", self)
        top.addWidget(self._dot)

        self._menu_btn = IconButton("more-horizontal", self, tooltip="Job actions")
        self._menu_btn.clicked.connect(self._open_menu)
        top.addWidget(self._menu_btn)

        # The descriptive line. Elided against the width it is actually given
        # rather than a fixed pixel budget, with the untruncated text on the
        # tooltip — a hard clip mid-character says nothing about there being
        # more, and 240px was wrong at every window size but one.
        self._detail = QLabel(self)
        self._detail.setObjectName("Muted")
        self._detail.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        body.addWidget(self._detail)

        self.update_from(job, ordinal=ordinal)

    # ── content ──────────────────────────────────────────────────────────
    def update_from(self, job: JobSpec, *, ordinal: int | None = None,
                    predecessors: list[tuple[str, str]] | None = None) -> None:
        """Rewrite this row for *job*, reusing every widget it already has.

        *predecessors* is the (id, label) list offered by the "Run after"
        submenu — the jobs above this one, which are the only ones it could
        legally chain from without violating queue order.
        """
        self._job = job
        self.job_id = job.id
        self._predecessors = list(predecessors or [])

        self._ordinal.setText(f"{ordinal}." if ordinal is not None else "")
        self._icon.setPixmap(make_icon(KIND_ICON.get(job.kind, "boxes")).pixmap(16, 16))
        self._name.setText(job.label or describe(job.kind, job.spec)
                           or KIND_LABEL.get(job.kind, job.kind))

        self._chain_glyph.setVisible(bool(job.chain_from))
        self._progress.setVisible(job.state == JobState.RUNNING)
        self._dot.setStatus(_STATE_DOT.get(job.state, "neutral"))
        self._dot.setLabel(job.state)

        self._detail_text = self._compose_detail(job)
        self._detail.setVisible(bool(self._detail_text))
        self._detail.setToolTip(self._detail_text)
        self._apply_elision()

    def _compose_detail(self, job: JobSpec) -> str:
        """The second line: where this job runs, what it waits for, what it said.

        Joined into one line rather than stacked, because a queue is read down
        the first line and the second exists to be scanned, not studied.
        """
        parts = []
        if job.chain_from:
            pred = dict(self._predecessors).get(job.chain_from)
            parts.append(f"after “{pred}”" if pred else f"after {job.chain_from}")
        detail = describe_detail(job.kind, job.spec)
        if detail:
            parts.append(detail)
        if job.message:
            parts.append(job.message)
        return "  ·  ".join(parts)

    def _apply_elision(self) -> None:
        width = max(0, self._detail.width())
        if not self._detail_text or width <= 0:
            self._detail.setText(self._detail_text)
            return
        metrics = QFontMetrics(self._detail.font())
        self._detail.setText(metrics.elidedText(self._detail_text, Qt.ElideRight, width))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_elision()

    # ── actions ──────────────────────────────────────────────────────────
    def build_menu(self) -> QMenu:
        """The row's actions, as a menu the caller may exec *or inspect*.

        Returned rather than opened so a test can read and trigger the actions
        without exec()ing a popup — under the offscreen platform that would
        block, and pytest's signal timeout cannot interrupt Qt's C++ event
        loop (tests/conftest.py's modal-dialog note).
        """
        job = self._job
        menu = QMenu(self)
        menu.setObjectName("JobRowMenu")

        edit = menu.addAction("Edit…")
        edit.setEnabled(job.state != JobState.RUNNING)
        edit.triggered.connect(lambda: self.editClicked.emit(self.job_id))

        menu.addAction("Duplicate").triggered.connect(
            lambda: self.duplicateClicked.emit(self.job_id))

        # Only a finished job can be retried: a pending one is already waiting
        # and a running one is already going.
        if job.state in JobState.TERMINAL:
            menu.addAction("Retry").triggered.connect(
                lambda: self.retryClicked.emit(self.job_id))

        self._add_chain_submenu(menu)

        # Reordering a row that has already run, or never will without an
        # explicit retry, means nothing — so the moves are offered only while
        # one could still change something.
        if job.state not in JobState.TERMINAL:
            menu.addSeparator()
            menu.addAction("Move up").triggered.connect(
                lambda: self.moveUpClicked.emit(self.job_id))
            menu.addAction("Move down").triggered.connect(
                lambda: self.moveDownClicked.emit(self.job_id))

        menu.addSeparator()
        menu.addAction("Remove from queue").triggered.connect(
            lambda: self.removeClicked.emit(self.job_id))
        return menu

    def _add_chain_submenu(self, menu: QMenu) -> None:
        """"Run after ▸" — the only way to build a chain from the interface.

        Offers the jobs *above* this one, since a chain that ran backwards
        would be refused by the queue's own order check anyway; showing the
        rest would be offering choices that cannot be made.
        """
        # Constructed with the parent menu rather than via addMenu(str): the
        # QMenu that call returns is not kept alive by its parent on the
        # Python side, so the submenu can be collected out from under the
        # menu that owns it. Passing the parent to the constructor gives it a
        # real C++ owner.
        sub = QMenu("Run after", menu)
        sub.setObjectName("JobRowChainMenu")
        menu.addMenu(sub)
        none_action = sub.addAction("Nothing (run on its own)")
        none_action.setCheckable(True)
        none_action.setChecked(not self._job.chain_from)
        none_action.triggered.connect(lambda: self.chainRequested.emit(self.job_id, ""))
        if not self._predecessors:
            sub.setEnabled(bool(self._job.chain_from))
            return
        sub.addSeparator()
        for pred_id, label in self._predecessors:
            act = sub.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._job.chain_from == pred_id)
            act.triggered.connect(
                lambda _checked=False, p=pred_id: self.chainRequested.emit(self.job_id, p))

    def _open_menu(self) -> None:
        menu = self.build_menu()
        menu_exec.exec_menu(
            menu, self._menu_btn.mapToGlobal(self._menu_btn.rect().bottomLeft()))

    # ── drag-handle reorder ──────────────────────────────────────────────
    def _handle_press(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_y0 = event.globalPosition().y()
            self._drag_steps = 0
            self._handle.setCursor(Qt.ClosedHandCursor)

    def _handle_move(self, event) -> None:
        if self._drag_y0 is None:
            return
        height = max(1.0, float(self.height()))
        self._drag_steps = int(round((event.globalPosition().y() - self._drag_y0) / height))

    def _handle_release(self, _event) -> None:
        steps = self._drag_steps
        self._drag_y0 = None
        self._drag_steps = 0
        self._handle.setCursor(Qt.OpenHandCursor)
        if steps:
            self.dragMoveBy.emit(self.job_id, steps)

    def mouseReleaseEvent(self, event) -> None:
        # A release that came from the handle is the end of a drag, not a
        # click on the row — opening the job's page underneath a reorder the
        # user just performed is not what they asked for.
        if self._drag_y0 is None and not self._handle.underMouse():
            self.rowClicked.emit(self.job_id)
        super().mouseReleaseEvent(event)
