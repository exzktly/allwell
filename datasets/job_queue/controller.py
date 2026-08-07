"""Wires the persisted :class:`~services.job_queue.JobQueue` to the per-kind
tool controllers (the five Datasets controllers, plus ``AnalyzeTab`` via
``AnalyzeRunMixin``).

:class:`QueueBridge` is the only place that knows how to run a job — the
queue model itself (``services/job_queue.py``) only orders and gates. It owns
a ``kind -> QueueableController`` registry built by whoever constructs
:class:`~datasets_tab.DatasetsTab` (each Datasets controller registers
itself; ``allwell.py`` injects the already-built ``AnalyzeTab`` under kind
``"analyze"`` the same way it injects ``on_open_in_review`` /
``on_open_in_analyze`` today) — so this module never imports ``analyze_tab``
or ``well_viewer`` itself, only the duck-typed :class:`QueueableController`
shape.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import QObject, QTimer, Signal

from well_viewer.core.safe import swallowed

from services.job_kinds import (
    CHAIN_KIND,
    CHAIN_TARGET,
    KIND_LABEL,
    describe,
    produced_for,
)
from services.job_queue import JobQueue, JobSpec, JobState, load_queue, save_queue
from datasets.job_queue.view import JobQueuePage


class QueueableController(Protocol):
    """A controller (or ``AnalyzeTab``, via ``AnalyzeRunMixin``) that a job
    queue can drive.

    ``jobFinished`` MUST be emitted only once per run, as the final statement
    of that run's completion handler — mirroring ``AnalyzeTab.runFinished``'s
    re-entrancy rule, since :class:`QueueBridge`'s slot may start the next
    job synchronously from inside the emit.
    """

    jobFinished: Signal  # (ok: bool, message: str, output_path: str)

    def start_from_queue(self, spec: dict) -> None: ...
    def show_job(self, spec: dict) -> None: ...
    def stop(self) -> None: ...
    def is_busy(self) -> bool: ...
    # Optional. Probed with getattr, not required: only Analyze has a boundary
    # it can wind down to, so the Datasets tools omit both and get a cancel.
    # def suspend(self) -> None: ...
    # def was_suspended(self) -> bool: ...


class QueueBridge(QObject):
    """Owns the persisted queue and drives it against the controller
    registry. Strictly serial: only ever one job in flight, tracked as
    ``(job_id, kind)`` so a completion signal from a controller that isn't
    the queue's own in-flight job — e.g. the user pressed that tool's own Run
    button by hand — is dropped rather than misattributed.
    """

    queueChanged = Signal()

    def __init__(self, *, state_dir=None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._state_dir = state_dir
        self._runners: dict[str, QueueableController] = {}
        self._queue: JobQueue = load_queue(state_dir)
        self._current: tuple[str, str] | None = None  # (job_id, kind)

    @property
    def queue(self) -> JobQueue:
        return self._queue

    def current_job_id(self) -> str | None:
        return self._current[0] if self._current is not None else None

    def register(self, kind: str, runner: QueueableController) -> None:
        self._runners[kind] = runner
        runner.jobFinished.connect(
            lambda ok, msg, path, k=kind: self._on_job_finished(k, ok, msg, path)
        )

    # ── editing ──────────────────────────────────────────────────────────
    def add_job(self, job: JobSpec) -> None:
        self._queue.add(job)
        self._save()
        self.queueChanged.emit()
        self._advance()

    def remove_job(self, job_id: str) -> None:
        job = self._queue.get(job_id)
        if job is not None and job.state == JobState.RUNNING:
            return  # can't remove the job currently in flight
        self._queue.remove(job_id)
        self._save()
        self.queueChanged.emit()

    def move_job(self, job_id: str, delta: int) -> bool:
        moved = self._queue.move(job_id, delta)
        if moved:
            self._save()
            self.queueChanged.emit()
        return moved

    def move_job_to(self, job_id: str, index: int) -> bool:
        """Absolute-position reorder, for a dragged row."""
        moved = self._queue.move_to(job_id, index)
        if moved:
            self._save()
            self.queueChanged.emit()
        return moved

    def update_job(self, job_id: str, spec: dict) -> bool:
        """Write an edited spec back over a queued job, renaming it to match.

        The label is re-derived rather than kept: it describes the spec, so an
        edited job whose name still said what it used to run against would be
        worse than one with no name at all.
        """
        job = self._queue.get(job_id)
        if job is None or not self._queue.update_spec(job_id, spec):
            return False
        self._queue.set_label(job_id, describe(job.kind, spec))
        self._save()
        self.queueChanged.emit()
        return True

    def duplicate_job(self, job_id: str) -> bool:
        clone = self._queue.duplicate(job_id, uuid.uuid4().hex[:10])
        if clone is None:
            return False
        self._save()
        self.queueChanged.emit()
        self._advance()
        return True

    def retry_job(self, job_id: str) -> bool:
        if not self._queue.retry(job_id):
            return False
        self._save()
        self.queueChanged.emit()
        self._advance()
        return True

    def clear_completed(self) -> int:
        removed = self._queue.clear_terminal()
        if removed:
            self._save()
            self.queueChanged.emit()
        return removed

    def set_chain(self, job_id: str, chain_from: str) -> bool:
        """Link (or, with an empty *chain_from*, unlink) a job.

        The two per-kind halves of the link are derived rather than asked for:
        which of the predecessor's outputs to take comes from its kind, and
        where to put it in this job's spec comes from this one's. A user
        choosing "run after that one" should not also have to know that
        Analyze receives its input at ``opts.raw`` and Validate at ``dataset``.
        """
        job = self._queue.get(job_id)
        if job is None:
            return False
        if not chain_from:
            self._queue.set_chain(job_id, None)
            self._save()
            self.queueChanged.emit()
            return True
        pred = self._queue.get(chain_from)
        target = CHAIN_TARGET.get(job.kind)
        if pred is None or target is None:
            return False
        try:
            self._queue.set_chain(
                job_id, chain_from, chain_target=target, chain_kind=CHAIN_KIND,
            )
        except ValueError:
            return False       # self-chain or cycle; the caller reports it
        self._save()
        self.queueChanged.emit()
        return True

    def show_job(self, job_id: str) -> bool:
        """Populate the job's own tool page. Returns whether it happened.

        The return value matters to the edit session, which must not open on a
        page that was never populated: saving it would write the page's
        unrelated current contents over the job's spec.
        """
        job = self._queue.get(job_id)
        if job is None:
            return False
        runner = self._runners.get(job.kind)
        if runner is None or runner.is_busy():
            # Populating a page mid-run doesn't just show stale information —
            # for the Datasets tools, show_job() writes fields the controller
            # is actively driving as its own run-progress display (Subset's
            # plate map, its timepoint/FOV checkbox rows), and set_running's
            # field locks only block user clicks, not this programmatic path.
            # Silently ignored rather than queued/deferred: the click is a
            # "preview this job" request, not something that needs to happen
            # eventually.
            return False
        # A deep copy, not the queue's own persisted dict — nothing here
        # should be able to mutate what's actually saved by writing through a
        # reference a show_job() implementation was only ever meant to read.
        # job.spec is already guaranteed JSON-shaped (JobQueue.materialise
        # makes the same assumption), so a json round-trip is a cheap, total
        # deep copy rather than a shallow dict() that still shares nested
        # dicts/lists (e.g. Analyze's spec["opts"]).
        runner.show_job(json.loads(json.dumps(job.spec)))
        return True

    def runner_busy(self, kind: str) -> bool:
        """Whether this kind's controller has a run of its own in flight."""
        runner = self._runners.get(kind)
        return bool(runner is not None and runner.is_busy())

    def hydrate_job(self, job_id: str) -> list[str] | None:
        """Fill the job's page fully enough that collecting it back is safe.

        Returns the hydrator's own list of things it could not restore
        faithfully (empty when all was well), or ``None`` when the page could
        not be populated at all — the two are different answers and a caller
        deciding whether to open an edit session needs to tell them apart.

        ``show_job`` is a preview: for Analyze it sets the input folder and
        nothing else, deliberately. Collecting a previewed page over a stored
        spec would replace every option the preview did not write with
        whatever the form happened to hold, so an edit session goes through
        the runner's ``hydrate_job`` when it has one and falls back to
        ``show_job`` for the tools whose ``show_job`` is already a complete
        inverse of their ``collect_spec``.
        """
        job = self._queue.get(job_id)
        if job is None:
            return None
        runner = self._runners.get(job.kind)
        if runner is None or runner.is_busy():
            return None
        spec = json.loads(json.dumps(job.spec))
        hydrate = getattr(runner, "hydrate_job", None)
        if callable(hydrate):
            return list(hydrate(spec) or [])
        runner.show_job(spec)
        return []

    # ── run/pause ────────────────────────────────────────────────────────
    def start(self) -> None:
        self._queue.start()
        self._save()
        self._advance()

    def pause(self) -> None:
        self._queue.pause()
        self._save()

    def stop_current(self) -> None:
        """Cancel the in-flight job. Per the queue's decision table,
        cancelling pauses the queue — resuming afterward is explicit."""
        if self._current is None:
            return
        _job_id, kind = self._current
        runner = self._runners.get(kind)
        if runner is not None:
            runner.stop()
        self.pause()

    def suspend_current(self) -> bool:
        """Wind the in-flight job down instead of killing it, and pause.

        The job keeps its place in the queue and its progress on disk, so
        starting the queue again picks it up rather than needing it re-added.
        Returns whether a runner actually supported being suspended — only
        Analyze does today; the Datasets tools have no equivalent boundary to
        stop at, so for those this is a plain cancel and the caller is told so.
        """
        if self._current is None:
            return False
        job_id, kind = self._current
        runner = self._runners.get(kind)
        suspender = getattr(runner, "suspend", None) if runner is not None else None
        if not callable(suspender):
            self.stop_current()
            return False
        suspender()
        # Marked here rather than waiting for jobFinished: the run may take a
        # while to wind down, and the row should say what is happening now.
        # _on_job_finished sees the state is already SUSPENDED and leaves it.
        self._queue.mark_suspended(job_id, "suspending — finishing the current field")
        self._save()
        self.queueChanged.emit()
        self.pause()
        return True

    # ── scheduling ───────────────────────────────────────────────────────
    def _advance(self) -> None:
        if not self._queue.is_started or self._current is not None:
            return
        job = self._queue.next_runnable()
        if job is None:
            return
        runner = self._runners.get(job.kind)
        if runner is None:
            return  # no adapter registered for this kind — leave it pending
        try:
            spec = self._queue.materialise(job.id)
        except ValueError as exc:
            self._queue.mark_finished(job.id, ok=False, message=str(exc))
            self._save()
            self.queueChanged.emit()
            QTimer.singleShot(0, self._advance)
            return
        self._current = (job.id, job.kind)
        self._queue.mark_running(job.id)
        self._save()
        self.queueChanged.emit()
        runner.start_from_queue(spec)

    def _on_job_finished(self, kind: str, ok: bool, message: str, output_path: str) -> None:
        if self._current is None or self._current[1] != kind:
            return  # not the queue's own in-flight job
        job_id, _kind = self._current
        self._current = None
        produced = produced_for(kind, output_path) if ok else {}
        if not ok and self._was_suspended(kind, job_id):
            # Suspended, not cancelled: the job keeps its place and its
            # progress, its successors keep waiting rather than blocking, and
            # the queue stays paused until the user starts it again.
            self._queue.mark_suspended(job_id)
            self._save()
            self.queueChanged.emit()
            self.pause()
            return
        cancelled = (not ok) and self._was_cancelled(kind, message)
        self._queue.mark_finished(
            job_id, ok=ok, message=message, cancelled=cancelled, produced=produced,
        )
        self._save()
        self.queueChanged.emit()
        if cancelled:
            self.pause()
        self._advance()

    def _was_suspended(self, kind: str, job_id: str) -> bool:
        """Whether this job wound down rather than failing or being cancelled.

        Two ways to know, because they cover different moments:
        ``suspend_current`` already marked the row SUSPENDED when the user
        clicked, and the runner reports it for a run that was suspended from
        the Analyze page directly, where the queue never saw the click.
        """
        job = self._queue.get(job_id)
        if job is not None and job.state == JobState.SUSPENDED:
            return True
        runner = self._runners.get(kind)
        probe = getattr(runner, "was_suspended", None)
        return bool(probe()) if callable(probe) else False

    def _was_cancelled(self, kind: str, message: str) -> bool:
        runner = self._runners.get(kind)
        probe = getattr(runner, "was_cancelled", None)
        if callable(probe):
            return bool(probe())
        # Every Datasets worker's failed() message is prefixed "Cancelled"
        # for a requestInterruption()-driven stop (see e.g.
        # datasets/concatenate/apply.py's cancellation checks) — the same
        # convention the controllers already use for their own dashboards.
        return message.startswith("Cancelled")

    def _save(self) -> None:
        save_queue(self._queue, self._state_dir)


class JobQueueController(QObject):
    """Owns the Job Queue page and its :class:`QueueBridge`, translating the
    page's clicks into bridge calls and the bridge's ``queueChanged`` back
    into a page refresh — the same page/worker shape every other Datasets
    controller uses, with the bridge standing in for a ``QThread`` worker.

    It also owns the **edit session**: the state of "a tool's page is
    currently standing in for a queued job". The controller cannot reach the
    rail or another tool's page itself, so ``DatasetsTab`` injects
    ``on_session`` and ``on_session_end`` callbacks — the same way it injects
    ``on_open_in_review`` — and this class decides when a session may begin,
    when it must be abandoned, and what saving one means.
    """

    def __init__(self, *, state_dir=None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.bridge = QueueBridge(state_dir=state_dir, parent=self)
        self._collectors: dict[str, Callable[[], dict]] = {}
        self._on_session: Callable[[str, str], None] | None = None
        self._on_session_end: Callable[[str], None] | None = None
        # (kind, job_id or None) — None means "a new job of this kind".
        self._session: tuple[str, str | None] | None = None

        self.page = JobQueuePage()
        self.page.startToggled.connect(self._on_start_toggled)
        self.page.stopRequested.connect(self._on_stop_requested)
        self.page.clearCompletedRequested.connect(self._on_clear_completed)
        self.page.addKindRequested.connect(self.begin_new_job)
        self.page.editRequested.connect(self.begin_edit)
        self.page.duplicateRequested.connect(self._on_duplicate)
        self.page.retryRequested.connect(self._on_retry)
        self.page.chainRequested.connect(self._on_chain)
        self.page.removeRequested.connect(self._on_remove)
        self.page.moveUpRequested.connect(lambda job_id: self._on_move(job_id, -1))
        self.page.moveDownRequested.connect(lambda job_id: self._on_move(job_id, 1))
        self.page.moveByRequested.connect(self._on_drag_move)
        self.page.showRequested.connect(self._on_show_requested)
        self.bridge.queueChanged.connect(self._refresh_page)
        self._refresh_page()

    def register(self, kind: str, runner) -> None:
        self.bridge.register(kind, runner)
        collect = getattr(runner, "collect_spec", None)
        if callable(collect):
            self._collectors[kind] = collect

    def set_analyze_provider(self, collect_spec: Callable[[], dict]) -> None:
        """Injects ``AnalyzeTab.collect_spec`` (a bound method) so this page
        can build an Analyze job without importing ``analyze_tab``.

        It goes into the same collector registry every other kind uses, so
        "+ Add job → Analyze" and an Analyze edit session both reach it by the
        one path rather than Analyze keeping a second, special one."""
        self._collectors["analyze"] = collect_spec

    def set_session_hooks(
        self,
        on_session: Callable[[str, str], None],
        on_session_end: Callable[[str], None],
    ) -> None:
        """``DatasetsTab`` supplies "show that tool's page with this banner on
        it" and "take the banner off again"; this class supplies everything
        about what the session means."""
        self._on_session = on_session
        self._on_session_end = on_session_end

    def add_job(self, kind: str, spec: dict) -> None:
        """Called by each Datasets controller's ``on_add_to_queue`` callback."""
        job = JobSpec(id=uuid.uuid4().hex[:10], kind=kind,
                      label=describe(kind, spec), spec=spec)
        self.bridge.add_job(job)

    # ── edit / new-job sessions ──────────────────────────────────────────
    def begin_edit(self, job_id: str) -> bool:
        """Open *job_id* on its own page for editing.

        Refused while that tool has a run of its own in flight. That is not
        politeness: populating a page mid-run overwrites fields the controller
        is actively driving as its progress display, and the page would then
        be collected back over the job on save.
        """
        job = self.bridge.queue.get(job_id)
        if job is None:
            return False
        if job.state == JobState.RUNNING:
            self._notify("That job is running — stop it first to edit it.", "warn")
            return False
        if self.bridge.runner_busy(job.kind):
            self._notify(f"{KIND_LABEL.get(job.kind, job.kind)} is busy with its "
                         "own run — try again when it finishes.", "warn")
            return False
        problems = self.bridge.hydrate_job(job_id)
        if problems is None:
            self._notify("That job's page isn't available to edit.", "warn")
            return False
        problems += self._round_trip_losses(job)
        message = f"Editing queued job “{job.label}”."
        if job.state in JobState.TERMINAL:
            # update_spec deliberately leaves state alone, so saving this
            # changes what the job says it will do without putting it back in
            # the queue. Said here rather than discovered after pressing Save.
            message += (" This job has already finished — saving updates it, "
                        "but it won't run again until you choose Retry.")
        for problem in problems:
            self._notify(problem, "warn")
        self._start_session(job.kind, job_id, message)
        return True

    def _round_trip_losses(self, job: JobSpec) -> list[str]:
        """Which of the job's settings the page could not take on.

        A tool's ``show_job`` is only as faithful as the page can be right
        now. Subset's, for instance, populates its timepoint and FOV filters
        from checkboxes it builds by reading the source dataset — so if that
        dataset has moved or not been built yet, the boxes are never created,
        ``all_timepoints_selected()`` answers "yes" over an empty list, and
        collecting the page back turns a one-timepoint subset into every
        timepoint. That is a widening, and it is invisible.

        Rather than special-casing each tool, the session collects the page it
        just populated and reports any setting that was there and is now not.
        A value that merely changed form (a well relabelled ``A1`` → ``A01``)
        is not a loss and is not reported.
        """
        collect = self._collectors.get(job.kind)
        if collect is None:
            return []
        try:
            round_tripped = collect()
        except Exception:
            # A page too empty to collect from cannot report a loss either;
            # begin_edit's own guards already refused the cases that matter.
            swallowed("collecting a page to check an edit round-trip")
            return []
        lost = [key for key, value in (job.spec or {}).items()
                if value and not round_tripped.get(key)]
        if not lost:
            return []
        return ["This page couldn't show every setting on that job "
                f"({', '.join(sorted(lost))}) — saving would clear "
                "them. Check the input path first."]

    def begin_new_job(self, kind: str) -> bool:
        """Open *kind*'s page to set up a job that does not exist yet."""
        if self.bridge.runner_busy(kind):
            self._notify(f"{KIND_LABEL.get(kind, kind)} is busy with its own run.",
                         "warn")
            return False
        if kind not in self._collectors:
            self._notify(f"{KIND_LABEL.get(kind, kind)} isn't available in this build.",
                         "warn")
            return False
        self._start_session(kind, None,
                            f"Setting up a new {KIND_LABEL.get(kind, kind)} job. "
                            "Fill the page in, then add it to the queue.")
        return True

    def _start_session(self, kind: str, job_id: str | None, message: str) -> None:
        if self._session is not None and self._session != (kind, job_id):
            # Silently overwriting it discarded whatever the user had typed on
            # the other page, with the banner disappearing from a page they
            # were no longer looking at.
            self._notify("Your previous edit was closed without saving.", "warn")
        self._session = (kind, job_id)
        if self._on_session is not None:
            self._on_session(kind, message)

    def session(self) -> tuple[str, str | None] | None:
        """The live session, as ``(kind, job_id or None)``."""
        return self._session

    def save_session(self) -> bool:
        """Collect the session's page and either update its job or add one."""
        if self._session is None:
            return False
        kind, job_id = self._session
        collect = self._collectors.get(kind)
        if collect is None:
            self._notify(f"{KIND_LABEL.get(kind, kind)} can't be saved to the "
                         "queue in this build — nothing was changed.", "warn")
            self.end_session()
            return False
        spec = collect()
        if job_id is None:
            self.add_job(kind, spec)
        elif not self.bridge.update_job(job_id, spec):
            # The job started running, or left the queue, between opening the
            # session and saving it. Silence here reads as a successful save.
            self._notify("That job changed while you were editing it — your "
                         "changes were not saved.", "warn")
            self.end_session()
            return False
        self.end_session()
        return True

    def end_session(self) -> None:
        self._session = None
        if self._on_session_end is not None:
            self._on_session_end("")

    def _abandon_session_if_stale(self) -> None:
        """Drop a session whose job started running or left the queue.

        Both would make saving it meaningless — one writes over a spec already
        handed to a runner, the other writes to a job that no longer exists —
        and the banner would otherwise sit there offering to do it.
        """
        if self._session is None:
            return
        _kind, job_id = self._session
        if job_id is None:
            return
        job = self.bridge.queue.get(job_id)
        if job is None:
            self._notify("The job you were editing was removed.", "warn")
            self.end_session()
        elif job.state == JobState.RUNNING:
            self._notify("The job you were editing has started running.", "warn")
            self.end_session()

    # ── queue actions ────────────────────────────────────────────────────
    def _on_show_requested(self, job_id: str) -> None:
        """Clicking a row to preview it on its own page.

        Refused while a session has that same page open for editing: preview
        writes the clicked job's fields straight into the page, so the next
        Save would collect *that* job's settings into the one under edit and
        silently turn it into a duplicate. Nothing about the queue changes, so
        `_abandon_session_if_stale` would never have caught it.
        """
        job = self.bridge.queue.get(job_id)
        if job is None:
            return
        if self._session is not None and self._session[0] == job.kind:
            if self._session[1] == job_id:
                return      # already open for editing; the click is a no-op
            self._notify(
                "Finish or cancel the job you're editing before opening "
                "another one on that page.", "warn")
            return
        self.bridge.show_job(job_id)

    def _on_remove(self, job_id: str) -> None:
        job = self.bridge.queue.get(job_id)
        if job is not None and job.state == JobState.RUNNING:
            self._notify("Can't remove the running job — stop it first.", "warn")
            return
        self.bridge.remove_job(job_id)

    def _on_move(self, job_id: str, delta: int) -> None:
        """The menu's Move up / Move down: one step, a swap with the neighbour."""
        if not self.bridge.move_job(job_id, delta):
            self._notify_move_refusal(job_id, delta)

    def _on_drag_move(self, job_id: str, steps: int) -> None:
        """A dragged row, which lands at a *destination* rather than stepping.

        Deliberately not `move_job`: swapping with whatever sits N rows away
        is not what dropping a row there looks like — the rows it passed
        should shuffle up by one, and the job three rows down should not be
        teleported to where this one started. The index is clamped rather than
        refused, since a drag past either end of the list plainly means "put
        it at the end".
        """
        jobs = self.bridge.queue.jobs
        index = next((i for i, j in enumerate(jobs) if j.id == job_id), None)
        if index is None:
            return
        target = max(0, min(len(jobs) - 1, index + steps))
        if target == index:
            return
        if not self.bridge.move_job_to(job_id, target):
            self._notify_move_refusal(job_id, target - index)

    def _notify_move_refusal(self, job_id: str, delta: int) -> None:
        """Say why a move did nothing. It used to just not happen.

        Running off the end of the list needs no explanation — the row is
        visibly already there — but a move refused by a chain link does, since
        nothing on screen says the two jobs are tied together.
        """
        job = self.bridge.queue.get(job_id)
        jobs = self.bridge.queue.jobs
        index = next((i for i, j in enumerate(jobs) if j.id == job_id), None)
        if index is None or not (0 <= index + delta < len(jobs)):
            return
        if job is not None and (job.chain_from or any(j.chain_from == job_id for j in jobs)):
            self._notify("That job is chained — it can't move past the job it "
                         "runs after, or the ones that run after it.", "warn")

    def _on_duplicate(self, job_id: str) -> None:
        self.bridge.duplicate_job(job_id)

    def _on_retry(self, job_id: str) -> None:
        self.bridge.retry_job(job_id)

    def _on_chain(self, job_id: str, chain_from: str) -> None:
        if not self.bridge.set_chain(job_id, chain_from):
            self._notify("Those two jobs can't be chained that way.", "warn")

    def _on_clear_completed(self) -> None:
        """Clearing a finished job unlinks any pending job chained to it.

        It has to — a survivor pointing at an id that is gone can never become
        runnable — but the consequence is that the survivor falls back to
        whatever placeholder path its own spec still holds and runs against
        that. Silence made a tidy-up look free.
        """
        orphaned = self._pending_successors_of_terminal_jobs()
        removed = self.bridge.clear_completed()
        if not removed:
            self._notify("Nothing finished to clear.", "neutral")
        elif orphaned:
            self._notify(
                f"{len(orphaned)} queued job(s) were waiting on a job you "
                "cleared — check their input before starting the queue: "
                + ", ".join(orphaned[:3]), "warn")

    def _pending_successors_of_terminal_jobs(self) -> list[str]:
        jobs = self.bridge.queue.jobs
        terminal = {j.id for j in jobs if j.state in JobState.TERMINAL}
        return [j.label or j.id for j in jobs
                if j.chain_from in terminal and j.state not in JobState.TERMINAL]

    def _on_start_toggled(self) -> None:
        if self.bridge.queue.is_started:
            # Pause winds the in-flight job down rather than killing it: it
            # keeps its place and its progress, and starting the queue again
            # resumes it. Stop job, beside it, is the destructive answer.
            if self.bridge.current_job_id():
                self.bridge.suspend_current()
            else:
                self.bridge.pause()
        else:
            self.bridge.start()
        self._refresh_page()

    def _on_stop_requested(self) -> None:
        self.bridge.stop_current()
        self._refresh_page()

    # ── plumbing ─────────────────────────────────────────────────────────
    def _notify(self, text: str, kind: str = "warn") -> None:
        """One seam for every "that did nothing, and here is why".

        A single method rather than scattered Toast calls so a test can assert
        that a refusal was reported without standing up a real Toast, which
        carries a fade animation and a dismiss timer of its own.
        """
        from widgets.toast import Toast

        Toast.show_message(self.page.window(), text, kind=kind)

    def _refresh_page(self) -> None:
        self._abandon_session_if_stale()
        queue = self.bridge.queue
        self.page.set_jobs(queue.jobs)
        self.page.set_summary(queue.counts())
        # Taken, not read: a note saying a job was interrupted stops being
        # true the moment that job resumes, and it used to sit on the page for
        # the rest of the session regardless.
        notes = queue.take_repair_notes()
        if notes:
            self.page.set_repair_notes(notes)
        self.page.set_started(queue.is_started)
        self.page.set_job_running(self.bridge.current_job_id() is not None)

    def shutdown(self) -> None:
        """Stop the in-flight job (called on app close), mirroring every
        other Datasets controller's ``shutdown``."""
        if self.bridge.current_job_id() is not None:
            self.bridge.stop_current()
