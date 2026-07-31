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

from services.job_queue import JobQueue, JobSpec, JobState, load_queue, save_queue
from datasets.job_queue.view import KIND_LABEL, JobQueuePage

# The one bit of per-kind knowledge chaining needs: which key in a finished
# job's "produced" dict its controller's jobFinished(ok, message, path) third
# argument should be stored under. Every Datasets tool produces a full
# dataset_root (an in/ + out/ pair); Analyze's useful successor input is its
# out/ folder specifically.
_PRODUCED_KEY = {
    "analyze": "out_dir",
    "concatenate": "dataset_root",
    "ome_import": "dataset_root",
    "validate": "dataset_root",
    "subset": "dataset_root",
    "import_loose": "dataset_root",
    "merge_plates": "dataset_root",
}


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

    def show_job(self, job_id: str) -> None:
        job = self._queue.get(job_id)
        if job is None:
            return
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
            return
        # A deep copy, not the queue's own persisted dict — nothing here
        # should be able to mutate what's actually saved by writing through a
        # reference a show_job() implementation was only ever meant to read.
        # job.spec is already guaranteed JSON-shaped (JobQueue.materialise
        # makes the same assumption), so a json round-trip is a cheap, total
        # deep copy rather than a shallow dict() that still shares nested
        # dicts/lists (e.g. Analyze's spec["opts"]).
        runner.show_job(json.loads(json.dumps(job.spec)))

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
        produced = {}
        if ok and output_path:
            produced[_PRODUCED_KEY.get(kind, "dataset_root")] = output_path
        cancelled = (not ok) and self._was_cancelled(kind, message)
        self._queue.mark_finished(
            job_id, ok=ok, message=message, cancelled=cancelled, produced=produced,
        )
        self._save()
        self.queueChanged.emit()
        if cancelled:
            self.pause()
        self._advance()

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
    page's button clicks into bridge calls and the bridge's ``queueChanged``
    back into a page refresh — the same page/worker shape every other
    Datasets controller uses, with the bridge standing in for a ``QThread``
    worker."""

    def __init__(self, *, state_dir=None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.bridge = QueueBridge(state_dir=state_dir, parent=self)
        self._analyze_collect: Callable[[], dict] | None = None

        self.page = JobQueuePage()
        self.page.startToggled.connect(self._on_start_toggled)
        self.page.addAnalyzeRequested.connect(self._on_add_analyze)
        self.page.removeRequested.connect(self.bridge.remove_job)
        self.page.moveUpRequested.connect(lambda job_id: self.bridge.move_job(job_id, -1))
        self.page.moveDownRequested.connect(lambda job_id: self.bridge.move_job(job_id, 1))
        self.page.showRequested.connect(self.bridge.show_job)
        self.bridge.queueChanged.connect(self._refresh_page)
        self._refresh_page()

    def register(self, kind: str, runner) -> None:
        self.bridge.register(kind, runner)

    def set_analyze_provider(self, collect_spec: Callable[[], dict]) -> None:
        """Injects ``AnalyzeTab.collect_spec`` (a bound method, supplied by
        ``allwell.py``) so this page's own "+ Queue current Analyze run"
        button can build a job without this package importing ``analyze_tab``
        — Analyze's own page has no line-budget headroom for the button
        itself (ARCHITECTURE §6b)."""
        self._analyze_collect = collect_spec

    def add_job(self, kind: str, spec: dict) -> None:
        """Called by each Datasets controller's ``on_add_to_queue`` callback."""
        job = JobSpec(id=uuid.uuid4().hex[:10], kind=kind, label=KIND_LABEL.get(kind, kind), spec=spec)
        self.bridge.add_job(job)

    def _on_add_analyze(self) -> None:
        if self._analyze_collect is not None:
            self.add_job("analyze", self._analyze_collect())

    def _on_start_toggled(self) -> None:
        if self.bridge.queue.is_started:
            self.bridge.stop_current() if self.bridge.current_job_id() else self.bridge.pause()
        else:
            self.bridge.start()
        self._refresh_page()

    def _refresh_page(self) -> None:
        self.page.set_jobs(self.bridge.queue.jobs)
        self.page.set_started(self.bridge.queue.is_started)

    def shutdown(self) -> None:
        """Stop the in-flight job (called on app close), mirroring every
        other Datasets controller's ``shutdown``."""
        if self.bridge.current_job_id() is not None:
            self.bridge.stop_current()
