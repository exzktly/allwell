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
from services.job_kinds import (
    INPUT_TARGETS,
    KIND_LABEL,
    describe,
    describe_detail,
    input_label,
    input_targets,
)
from services.job_queue import JobSpec, JobState
from widgets.owned_action import owned_action

# Queue position number at the row's left edge.
_ORDINAL_W = 20
# Inline per-job progress bar.
_ROW_BAR_W = 60
_ROW_BAR_H = 8
# The ⋯ button's least size: a pointer target the row's 14 px glyph alone
# made about 20 px square.
_MENU_BTN_MIN = 28

KIND_ICON = {
    "analyze": "sliders-horizontal",
    "concatenate": "copy",
    "ome_import": "folder-open",
    "czi_import": "microscope",
    "nd2_import": "microscope",
    "validate": "scan-line",
    "subset": "boxes",
    "import_loose": "download",
    "merge_plates": "layout-grid",
}

#: The tools a dataset can be handed to next, derived from what each kind
#: takes as input (``services.job_kinds.INPUT_TARGETS``) rather than written
#: out: a new tool that reads a dataset appears in the menu by declaring its
#: inputs, and a hand-kept roster would miss it (CLAUDE.md §3). One-input
#: kinds are offered directly; two-input kinds as "<tool> with ▸", this job's
#: output being dataset 1. The converters and Zip loose read files, not
#: datasets, so they declare no inputs and are absent.
NEXT_STEP_KINDS = tuple(k for k in INPUT_TARGETS if len(INPUT_TARGETS[k]) == 1)
PAIR_STEP_KINDS = tuple(k for k in INPUT_TARGETS if len(INPUT_TARGETS[k]) == 2)
#: A kind not offered as the next step of the same kind: analysing an
#: analysis's own output re-reads the images it just analysed.
_NOT_AFTER_ITSELF = frozenset({"analyze"})
#: The submenu every row offers. One word for one idea across the queue: a
#: job takes its *input* from another job ("Input from ▸", "Dataset 1 from
#: ▸"), and this is the same link made from the other end.
NEXT_STEP_MENU = "New job using this output"

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
    # (job_id, with the jobs that used its output too)
    retryClicked = Signal(str, bool)
    rowClicked = Signal(str)
    # (job_id, predecessor_id or "" to unchain)
    chainRequested = Signal(str, str)
    # (job_id, input target, source job id or "" to unlink) — one input of a
    # job that takes two, so the other keeps its link
    linkRequested = Signal(str, str, str)
    # (this job's id, the kind to set up next, a second job's id or "") — the
    # second job fills a two-input kind's dataset 2
    nextStepRequested = Signal(str, str, str)
    # (job_id, net ± rows) — emitted once, on release
    dragMoveBy = Signal(str, int)

    def __init__(self, job: JobSpec, parent: QWidget | None = None, *,
                 ordinal: int | None = None) -> None:
        super().__init__(parent)
        self.job_id = job.id
        self._job = job
        self._detail_text = ""
        self._predecessors: list[tuple[str, str]] = []
        self._others: list[tuple[str, str]] = []
        self._below: list[tuple[str, str]] = []
        self._positions: dict[str, int] = {}
        self._finished_downstream = 0
        self._drag_y0: float | None = None
        self._drag_steps = 0

        self.setObjectName("JobRow")          # styled in theme.qss, not inline
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        # Reachable from the keyboard as well as the pointer: Tab to the row,
        # Enter opens the job, the Menu key (or Shift+F10) opens its actions.
        self.setFocusPolicy(Qt.StrongFocus)
        self.setToolTip("Click (or press Enter) to open this job on its own "
                        "page. Its actions are under ⋯ (Menu key).")

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

        # Ignored, and elided with the full name on its tooltip: a long name
        # used to push the status chip and ⋯ off the row (and the page into a
        # sideways scroll), which hid the two things a queue is read for.
        self._name = QLabel(self)
        self._name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._name_text = ""
        top.addWidget(self._name, 1)

        self._chain_glyph = QLabel(self)
        self._chain_glyph.setPixmap(make_icon("waypoints").pixmap(14, 14))
        self._chain_glyph.setToolTip(
            "Reads its input from another queued job's output — see the line "
            "below, and Input from ▸ in the ⋯ menu.")
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
        self._menu_btn.setMinimumSize(_MENU_BTN_MIN, _MENU_BTN_MIN)
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
                    predecessors: list[tuple[str, str]] | None = None,
                    others: list[tuple[str, str]] | None = None,
                    positions: dict[str, int] | None = None,
                    below: list[tuple[str, str]] | None = None,
                    finished_downstream: int = 0) -> None:
        """Rewrite this row for *job*, reusing every widget it already has.

        *predecessors* is the (id, label) list offered by the "Input from"
        submenu — the jobs above this one, which are the only ones it could
        legally chain from without violating queue order. *others* is every
        other job that could still produce an output, offered as the second
        dataset of a next step ("Concatenate with ▸"). *positions* is every
        job's place in the queue, shown beside its name in those menus.
        """
        self._job = job
        self.job_id = job.id
        self._predecessors = list(predecessors or [])
        self._others = list(others or [])
        self._positions = dict(positions or {})
        self._below = list(below or [])
        self._finished_downstream = finished_downstream

        self._ordinal.setText(f"{ordinal}." if ordinal is not None else "")
        self._icon.setPixmap(make_icon(KIND_ICON.get(job.kind, "boxes")).pixmap(16, 16))
        self._name_text = (job.label or describe(job.kind, job.spec)
                           or KIND_LABEL.get(job.kind, job.kind))
        self._name.setToolTip(self._name_text)

        self._chain_glyph.setVisible(bool(job.sources()))
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
        if job.stale_because and job.state == JobState.SUCCEEDED:
            parts.append(f"Out of date — “{job.stale_because}” was run again "
                         "after this finished; Retry to bring it up to date")
        if job.state == JobState.BLOCKED and job.message:
            # Why it cannot run comes first: at the end of an elided line it
            # was cut off at every window width, and it is the one thing a
            # blocked row is read for.
            parts.append(f"Blocked — {job.message}")
        names = dict(self._predecessors)
        if len(input_targets(job.kind)) > 1 and job.links():
            # A two-input job says which input comes from which job: "input
            # from A" would not say whether A is dataset 1 or dataset 2.
            for target in input_targets(job.kind):
                link = job.link_for(target)
                if link is not None:
                    pred = names.get(link.source, link.source)
                    parts.append(f"{input_label(job.kind, target).lower()} "
                                 f"from “{pred}”")
        elif job.sources():
            for source in job.sources():
                pred = names.get(source)
                parts.append(f"input from “{pred}”" if pred else f"input from {source}")
        elif job.kind == "analyze" and job.state == JobState.PENDING \
                and not str((job.spec.get("opts") or {}).get("raw") or "").strip():
            # Allowed to be queued blank so a link can fill it, and refused
            # when it starts if nothing did. Said here first, because a blank
            # job otherwise reads exactly like a ready one until it fails.
            parts.append("no input folder — choose one with Input from ▸ or "
                         "edit the job")
        detail = describe_detail(job.kind, job.spec)
        if detail:
            parts.append(detail)
        if job.message and job.state != JobState.BLOCKED:
            parts.append(job.message)
        return "  ·  ".join(parts)

    def _apply_elision(self) -> None:
        for label, text in ((self._detail, self._detail_text),
                            (self._name, self._name_text)):
            width = max(0, label.width())
            if not text or width <= 0:
                label.setText(text)
                continue
            metrics = QFontMetrics(label.font())
            label.setText(metrics.elidedText(text, Qt.ElideRight, width))

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

        edit = owned_action(menu, "Edit…")
        edit.setEnabled(job.state != JobState.RUNNING)
        edit.triggered.connect(lambda: self.editClicked.emit(self.job_id))

        owned_action(menu, "Duplicate").triggered.connect(
            lambda: self.duplicateClicked.emit(self.job_id))

        # Only a job that ran can be retried: a pending one is already
        # waiting, a running one is already going, and a blocked one never ran
        # — retrying it is refused, so it is not offered (retry what blocked it).
        if job.state in JobState.CLEARABLE:
            self._add_retry(menu)

        if len(input_targets(job.kind)) > 1:
            for target in input_targets(job.kind):
                self._add_input_submenu(menu, target)
        else:
            self._add_chain_submenu(menu)
        self._add_next_step_submenu(menu)

        # Reordering a row that has already run, or never will without an
        # explicit retry, means nothing — so the moves are offered only while
        # one could still change something.
        if job.state not in JobState.TERMINAL:
            menu.addSeparator()
            owned_action(menu, "Move up").triggered.connect(
                lambda: self.moveUpClicked.emit(self.job_id))
            owned_action(menu, "Move down").triggered.connect(
                lambda: self.moveDownClicked.emit(self.job_id))

        menu.addSeparator()
        owned_action(menu, "Remove from queue").triggered.connect(
            lambda: self.removeClicked.emit(self.job_id))
        return menu

    def _job_entry(self, job_id: str, label: str) -> str:
        """A menu entry naming another job: its queue position and its name.

        The position is what tells two duplicated jobs apart — their names
        are identical by construction.
        """
        position = self._positions.get(job_id)
        return f"{position}. {label}" if position else label

    def _add_chain_submenu(self, menu: QMenu) -> None:
        """"Input from ▸" — which earlier job's output this job reads.

        Offers the jobs *above* this one, since a link that ran backwards
        would be refused by the queue's own order check anyway; showing the
        rest would be offering choices that cannot be made. Absent on a kind
        that reads files rather than a dataset (the converters, Zip loose):
        no earlier job's output can be its input.
        """
        if not input_targets(self._job.kind):
            return
        # Constructed with the parent menu rather than via addMenu(str): the
        # QMenu that call returns is not kept alive by its parent on the
        # Python side, so the submenu can be collected out from under the
        # menu that owns it. Passing the parent to the constructor gives it a
        # real C++ owner.
        sub = QMenu("Input from", menu)
        sub.setObjectName("JobRowChainMenu")
        menu.addMenu(sub)
        none_action = owned_action(sub, "Nothing (use the folder on its page)")
        none_action.setCheckable(True)
        none_action.setChecked(not self._job.chain_from)
        none_action.triggered.connect(lambda: self.chainRequested.emit(self.job_id, ""))
        current = self._job.chain_from
        self._add_sources(sub, self._predecessors, self._below,
                          lambda source: current == source,
                          lambda source: self.chainRequested.emit(self.job_id, source))

    def _add_input_submenu(self, menu: QMenu, target: str) -> None:
        """"Dataset 1 from ▸" / "Dataset 2 from ▸" — one input of a job that
        takes two, linked on its own so the other input keeps its link.

        The job already feeding the *other* input is left out: a dataset
        concatenated or merged with itself is never what was meant.
        """
        label = input_label(self._job.kind, target)
        sub = QMenu(f"{label} from", menu)
        sub.setObjectName("JobRowInputMenu")
        menu.addMenu(sub)
        current = self._job.link_for(target)
        taken = {link.source for link in self._job.links() if link.target != target}
        none_action = owned_action(sub, "Nothing (use the folder on its page)")
        none_action.setCheckable(True)
        none_action.setChecked(current is None)
        none_action.triggered.connect(
            lambda _checked=False, t=target: self.linkRequested.emit(self.job_id, t, ""))
        above = [(pid, plabel) for pid, plabel in self._predecessors if pid not in taken]
        below = [(pid, plabel) for pid, plabel in self._below if pid not in taken]
        self._add_sources(sub, above, below,
                          lambda source: current is not None and current.source == source,
                          lambda source, t=target: self.linkRequested.emit(self.job_id, t, source))

    def _add_sources(self, sub: QMenu, above, below, checked, choose) -> None:
        """The jobs an input can come from: those above, then those below
        (choosing one moves this job just below it), or a sentence saying
        why there are none — a disabled submenu said nothing."""
        if not above and not below:
            note = owned_action(sub, "No other queued job can be its input yet "
                                     "— queue one first")
            note.setEnabled(False)
            return
        sub.addSeparator()
        for pred_id, label in above:
            act = owned_action(sub, self._job_entry(pred_id, label))
            act.setCheckable(True)
            act.setChecked(checked(pred_id))
            act.triggered.connect(lambda _checked=False, p=pred_id: choose(p))
        if below:
            sub.addSeparator()
            for pred_id, label in below:
                act = owned_action(
                    sub, f"{self._job_entry(pred_id, label)}  (moves this job below it)")
                act.triggered.connect(lambda _checked=False, p=pred_id: choose(p))

    def _add_retry(self, menu: QMenu) -> None:
        """Retry — and, when finished jobs used this job's output, the choice
        of re-running them too or marking them out of date. Re-running a
        conversion and leaving its analysis "succeeded" described numbers
        from output that no longer exists."""
        n = self._finished_downstream
        if not n:
            owned_action(menu, "Retry").triggered.connect(
                lambda: self.retryClicked.emit(self.job_id, False))
            return
        sub = QMenu("Retry", menu)
        sub.setObjectName("JobRowRetryMenu")
        menu.addMenu(sub)
        owned_action(sub, f"This job and the {n} job(s) that use its output").triggered.connect(
            lambda: self.retryClicked.emit(self.job_id, True))
        owned_action(sub, "This job only — mark those out of date").triggered.connect(
            lambda: self.retryClicked.emit(self.job_id, False))

    def _add_next_step_submenu(self, menu: QMenu) -> None:
        """"New job using this output ▸" — set up a job that reads what this
        one writes, before it has written anything.

        Not offered for a job whose output is never coming (failed, cancelled,
        blocked): a job linked to it could never run.
        """
        if self._job.state in JobState.FAILURE_LIKE or self._job.state == JobState.BLOCKED:
            return
        sub = QMenu(NEXT_STEP_MENU, menu)
        sub.setObjectName("JobRowNextStepMenu")
        menu.addMenu(sub)
        for kind in NEXT_STEP_KINDS:
            if kind == self._job.kind and kind in _NOT_AFTER_ITSELF:
                continue
            owned_action(sub, f"{KIND_LABEL.get(kind, kind)}…").triggered.connect(
                lambda _checked=False, k=kind: self.nextStepRequested.emit(self.job_id, k, ""))
        sub.addSeparator()
        for kind in PAIR_STEP_KINDS:
            pair = QMenu(f"{KIND_LABEL.get(kind, kind)} with", sub)
            pair.setObjectName("JobRowPairMenu")
            sub.addMenu(pair)
            owned_action(pair, "A folder you pick on the page…").triggered.connect(
                lambda _checked=False, k=kind: self.nextStepRequested.emit(self.job_id, k, ""))
            if self._others:
                pair.addSeparator()
            for other_id, other_label in self._others:
                owned_action(pair, self._job_entry(other_id, other_label)).triggered.connect(
                    lambda _checked=False, k=kind, o=other_id:
                        self.nextStepRequested.emit(self.job_id, k, o))

    def keyPressEvent(self, event) -> None:
        """Enter opens the job, as a click does; the Menu key or Shift+F10
        opens its actions, as ⋯ does."""
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.rowClicked.emit(self.job_id)
            return
        if key == Qt.Key_Menu or (key == Qt.Key_F10 and event.modifiers() & Qt.ShiftModifier):
            self._open_menu()
            return
        super().keyPressEvent(event)

    def _open_menu(self) -> None:
        menu = self.build_menu()
        menu_exec.exec_transient_menu(
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
