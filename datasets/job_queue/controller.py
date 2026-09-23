"""Wires the persisted :class:`~services.job_queue.JobQueue` to the per-kind
tool controllers (the five Datasets controllers, plus ``AnalyzeTab`` via
``AnalyzeRunMixin``).

:class:`QueueBridge` is the only place that knows how to run a job — the
queue model itself (``services/job_queue.py``) only orders and gates. It owns
a ``kind -> QueueableController`` registry built by whoever constructs
:class:`~datasets.tab.DatasetsTab` (each Datasets controller registers
itself; ``allwell.py`` injects the already-built ``AnalyzeTab`` under kind
``"analyze"`` the same way it injects ``on_open_in_review`` /
``on_open_in_analyze`` today) — so this module never imports ``analyze.tab``
or ``review`` itself, only the duck-typed :class:`QueueableController`
shape.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import QObject, QTimer, Signal


from services.job_kinds import (
    CHAIN_KIND,
    CHAIN_TARGET,
    describe,
    produced_for,
)
from services.job_queue import JobQueue, JobSpec, JobState, load_queue, save_queue
from datasets.job_queue.session import QueueSession
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
        if runner.is_busy():
            # The user started this tool by hand while the queue was running.
            # Every tool's ``_launch`` opens with "if a worker is already
            # running, return" and emits no ``jobFinished``, so dispatching now
            # would mark the job running against a controller that never
            # reports back — ``_current`` stays set and the whole queue stops
            # for the session. The job keeps its place instead, and the manual
            # run's own completion is what starts it (see ``_on_job_finished``).
            return
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
            # Not the queue's own in-flight job — the user ran this tool by
            # hand. Nothing to record, but that controller has just become free,
            # and a job ``_advance`` declined to dispatch because the runner was
            # busy is waiting on exactly this. Without the nudge the queue would
            # sit still until something else happened to call ``_advance``.
            if self._current is None:
                QTimer.singleShot(0, self._advance)
            return
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

    The **edit session** — "a tool's page is currently standing in for a
    queued job" — is :class:`~datasets.job_queue.session.QueueSession`, held
    here as ``self.sessions``. It moved out because its rules are substantial
    and none of them are about this class's job of wiring a page to a bridge;
    the methods below are a thin façade so existing callers keep working.
    """

    def __init__(self, *, state_dir=None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.bridge = QueueBridge(state_dir=state_dir, parent=self)
        # "Show that tool's page, touching nothing", installed by DatasetsTab.
        # Answers False until then, so a click on a running row reports that it
        # could not get there instead of appearing to have worked.
        self._reveal: Callable[[str], bool] = lambda _kind: False
        # Dispatched through self at call time, not bound here: _notify is the
        # one seam a test patches to assert a refusal was reported, and
        # capturing the bound method would leave the session calling the
        # original past the patch.
        self.sessions = QueueSession(
            self.bridge,
            notify=lambda text, kind="warn": self._notify(text, kind),
            add_job=lambda kind, spec: self.add_job(kind, spec),
        )

        self.page = JobQueuePage()
        self.page.startToggled.connect(self._on_start_toggled)
        self.page.stopRequested.connect(self._on_stop_requested)
        self.page.clearCompletedRequested.connect(self._on_clear_completed)
        self.page.addKindRequested.connect(self.sessions.begin_new_job)
        self.page.editRequested.connect(self.sessions.begin_edit)
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
            self.sessions.register_collector(kind, collect)

    def set_analyze_provider(
        self,
        collect_spec: Callable[[], dict],
        validate: Callable[[], bool] | None = None,
    ) -> None:
        """Injects ``AnalyzeTab.collect_spec`` (a bound method) so this page
        can build an Analyze job without importing ``analyze.tab``.

        It goes into the same collector registry every other kind uses, so
        "+ Add job → Analyze" and an Analyze edit session both reach it by the
        one path rather than Analyze keeping a second, special one.

        *validate* is ``AnalyzeTab.validate_for_queue`` — the same gate its
        own "+ Queue" button applies — so saving a session cannot queue a form
        that button would have refused."""
        self.sessions.register_collector("analyze", collect_spec)
        if validate is not None:
            self.sessions.register_validator("analyze", validate)

    def set_session_hooks(
        self,
        on_session: Callable[[str, str], bool | None],
        on_session_end: Callable[[str], None],
    ) -> None:
        """``DatasetsTab`` supplies "show that tool's page with this banner on
        it" and "take the banner off again"."""
        self.sessions.set_hooks(on_session, on_session_end)

    def set_reveal_hook(self, reveal: Callable[[str], bool]) -> None:
        """``DatasetsTab`` supplies "show that tool's page, touching nothing".

        Separate from the session hooks because it must be usable when a
        session may *not* start — a click on a running row is exactly that
        case, and it needs the page shown without anything being written to it.
        """
        self._reveal = reveal

    def add_job(self, kind: str, spec: dict) -> None:
        """Called by each Datasets controller's ``on_add_to_queue`` callback."""
        job = JobSpec(id=uuid.uuid4().hex[:10], kind=kind,
                      label=describe(kind, spec), spec=spec)
        self.bridge.add_job(job)

    # ── edit / new-job sessions (owned by QueueSession) ──────────────────
    def begin_edit(self, job_id: str) -> bool:
        return self.sessions.begin_edit(job_id)

    def begin_new_job(self, kind: str) -> bool:
        return self.sessions.begin_new_job(kind)

    def session(self) -> tuple[str, str | None] | None:
        return self.sessions.current()

    def save_session(self) -> bool:
        return self.sessions.save()

    def end_session(self) -> None:
        self.sessions.end()

    # ── queue actions ────────────────────────────────────────────────────
    def _on_show_requested(self, job_id: str) -> None:
        """Clicking a row to go and look at that job on its own page.

        Two shapes, because a running job and a waiting one want opposite
        things from the same click.

        A **running** job is shown by navigating and writing nothing. Its page
        is already displaying that run's own live progress — the dashboard, the
        plate map filling in — which is exactly what the click is asking to
        see, and ``show_job`` would overwrite it: it writes fields the
        controller is actively driving as its progress display, and
        ``set_running``'s field locks stop user clicks, not this programmatic
        path. That is why the click used to be dropped here entirely, leaving a
        running job with no route to its page at all (its ``Edit…`` is disabled
        too). Navigation was never the unsafe half; populating was.

        Any **other** job is populated first and then shown, so the preview the
        click asked for is actually in front of the user. Writing the fields
        into a page nobody is looking at is the same silence in a quieter form.

        Refused outright while a session has that same page open for editing:
        preview writes the clicked job's fields straight into the page, so the
        next Save would collect *that* job's settings into the one under edit
        and silently turn it into a duplicate. Nothing about the queue changes,
        so `_abandon_session_if_stale` would never have caught it.
        """
        job = self.bridge.queue.get(job_id)
        if job is None:
            return
        blocked = self.sessions.blocks_preview_of(job.kind, job_id)
        if blocked is None:
            return      # already open for editing; the click is a no-op
        if blocked:
            self._notify(
                "Finish or cancel the job you're editing before opening "
                "another one on that page.", "warn")
            return
        if job.state == JobState.RUNNING:
            if not self._reveal(job.kind):
                self._notify(
                    "Can't open that job's page from here — switch to it "
                    "yourself to watch the run.", "warn")
            return
        if not self.bridge.show_job(job_id):
            # The kind's controller is mid-run on a *different* job, so its
            # page cannot be repopulated. Showing it anyway would be a lie
            # about which job the fields describe.
            self._notify(
                "That tool is busy with another run — its page will show this "
                "job once the run finishes.", "warn")
            return
        self._reveal(job.kind)

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
        self.sessions.abandon_if_stale()
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
