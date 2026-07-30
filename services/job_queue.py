"""A Qt-free, strictly-serial job queue for Analyze and Datasets-pane tools.

Persists a list of :class:`JobSpec` records — each one a JSON-shaped spec for
a specific tool ("analyze", "concatenate", "validate", "subset",
"import_loose", "merge_plates") plus optional chain metadata linking it to a
predecessor job. :class:`JobQueue` only ever orders and gates jobs; it never
runs one itself. The thing that actually launches a job is whatever adapter
implements :class:`JobRunner` for that job's ``kind`` — a Datasets controller
or ``AnalyzeRunMixin`` — and that adapter is responsible for marshalling its
completion callback onto the GUI thread before calling it, which is what lets
this module stay import-clean of Qt and ``well_viewer``.

Persisted, never auto-started. Loading a queue interrupts any job that was
recorded as ``running`` (the process that owned it is gone) and repairs a
hand-edited file whose stored order violates its own chain links, so
:meth:`JobQueue.next_runnable` can never deadlock on bad persisted data. The
queue's own "is the user running this at all" state (:attr:`JobQueue.is_started`)
is never persisted either — a relaunch always comes up paused; whoever owns
the queue page calls :meth:`JobQueue.start` explicitly.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from services.remote_config import default_state_dir

QUEUE_FILENAME = "job_queue.json"


class JobState:
    """String constants, not an ``Enum`` — the queue's JSON persistence is a
    plain ``str`` field and comparing to these avoids an encode/decode step."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    INTERRUPTED = "interrupted"

    # A predecessor in one of these states can never produce output its
    # successors were waiting on, so its successors cascade to BLOCKED.
    FAILURE_LIKE = frozenset({FAILED, CANCELLED})
    TERMINAL = frozenset({SUCCEEDED, FAILED, CANCELLED, BLOCKED, INTERRUPTED})


# The three shapes a chained job's predecessor might have produced. Only
# Analyze's producer is unambiguous (dataset_root == output_dir.parent in
# every resolve_input_output layout) — Datasets producers genuinely differ in
# kind, which is why this is a per-chain field rather than inferred.
_CHAIN_KINDS = ("out_dir", "dataset_root", "in_dir")


@dataclass
class JobSpec:
    """One persisted queue entry."""

    id: str
    kind: str
    label: str
    spec: dict = field(default_factory=dict)
    chain_from: str | None = None
    chain_target: str | None = None  # dotted path into `spec`, e.g. "opts.raw"
    chain_kind: str | None = None    # one of _CHAIN_KINDS
    state: str = JobState.PENDING
    message: str = ""
    output_dir: str | None = None
    dataset_root: str | None = None
    in_dir: str | None = None

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "spec": self.spec,
            "chain_from": self.chain_from,
            "chain_target": self.chain_target,
            "chain_kind": self.chain_kind,
            "state": self.state,
            "message": self.message,
            "output_dir": self.output_dir,
            "dataset_root": self.dataset_root,
            "in_dir": self.in_dir,
        }

    @classmethod
    def from_json(cls, data: dict) -> JobSpec:
        return cls(
            id=data["id"],
            kind=data["kind"],
            label=data.get("label", data["id"]),
            spec=dict(data.get("spec") or {}),
            chain_from=data.get("chain_from"),
            chain_target=data.get("chain_target"),
            chain_kind=data.get("chain_kind"),
            state=data.get("state", JobState.PENDING),
            message=data.get("message", ""),
            output_dir=data.get("output_dir"),
            dataset_root=data.get("dataset_root"),
            in_dir=data.get("in_dir"),
        )


class JobRunner(Protocol):
    """Implementations MUST marshal completion onto the GUI thread before
    calling ``on_done`` — that rule is what lets :class:`JobQueue` stay
    Qt-free. A ``QThread``-based worker gets this for free via queued
    signals; ``AnalyzeRunMixin`` gets it for free because completion is
    observed inside ``_poll_log``, a main-thread ``QTimer`` callback.

    ``on_done(ok, message, produced)`` — ``produced`` is a dict with whichever
    of ``"out_dir"`` / ``"dataset_root"`` / ``"in_dir"`` keys (as ``str``
    paths) this job actually made, empty on failure or cancellation.
    """

    def start(
        self,
        job: JobSpec,
        spec: dict,
        *,
        on_line: Callable[[str], None],
        on_status: Callable[[str], None],
        on_done: Callable[[bool, str, dict], None],
    ) -> None: ...

    def stop(self) -> None: ...


def _set_dotted(target: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = target
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


class JobQueue:
    """Ordered, dependency-gated, strictly-serial job list. Pure bookkeeping —
    it decides what's runnable next and records outcomes; it never starts a
    subprocess or a ``QThread`` itself.
    """

    def __init__(self, jobs: list[JobSpec] | None = None) -> None:
        self._jobs: list[JobSpec] = list(jobs or [])
        self._by_id: dict[str, JobSpec] = {j.id: j for j in self._jobs}
        # Never set true by loading from disk — see the module docstring.
        self._started = False

    # -- basic access ---------------------------------------------------

    @property
    def jobs(self) -> list[JobSpec]:
        return list(self._jobs)

    def get(self, job_id: str) -> JobSpec | None:
        return self._by_id.get(job_id)

    @property
    def is_started(self) -> bool:
        return self._started

    def start(self) -> None:
        self._started = True

    def pause(self) -> None:
        self._started = False

    # -- editing ----------------------------------------------------------

    def add(self, job: JobSpec) -> None:
        if job.id in self._by_id:
            raise ValueError(f"duplicate job id {job.id!r}")
        self._jobs.append(job)
        self._by_id[job.id] = job

    def remove(self, job_id: str) -> None:
        job = self._by_id.pop(job_id, None)
        if job is None:
            return
        self._jobs.remove(job)
        # A successor chained to the removed job loses the link rather than
        # being left pointing at a job that no longer exists.
        for successor in self._jobs:
            if successor.chain_from == job_id:
                successor.chain_from = None
                successor.chain_target = None
                successor.chain_kind = None

    def move(self, job_id: str, delta: int) -> bool:
        """Reorder ``job_id`` by ``delta`` positions. Refuses (returns
        ``False``, leaving the queue unchanged) a reorder that would place a
        chained job before its predecessor."""
        idx = self._index(job_id)
        if idx is None:
            return False
        new_idx = idx + delta
        if not (0 <= new_idx < len(self._jobs)):
            return False
        trial = list(self._jobs)
        trial[idx], trial[new_idx] = trial[new_idx], trial[idx]
        if not self._chain_order_valid(trial):
            return False
        self._jobs = trial
        return True

    def set_chain(
        self,
        job_id: str,
        chain_from: str | None,
        *,
        chain_target: str | None = None,
        chain_kind: str | None = None,
    ) -> None:
        """Link ``job_id`` to run after ``chain_from`` succeeds. Raises
        ``ValueError`` on a self-chain or a cycle — checked by walking
        ``chain_from`` ancestors before the link is written, not after."""
        if chain_from is not None:
            if chain_from == job_id:
                raise ValueError(f"job {job_id!r} cannot chain from itself")
            if chain_from not in self._by_id:
                raise ValueError(f"unknown predecessor job id {chain_from!r}")
            seen = {job_id}
            cur: str | None = chain_from
            while cur is not None:
                if cur in seen:
                    raise ValueError(
                        f"chaining {job_id!r} from {chain_from!r} would create a cycle"
                    )
                seen.add(cur)
                pred = self._by_id.get(cur)
                cur = pred.chain_from if pred is not None else None
        job = self._by_id[job_id]
        job.chain_from = chain_from
        job.chain_target = chain_target
        job.chain_kind = chain_kind

    def _index(self, job_id: str) -> int | None:
        for i, j in enumerate(self._jobs):
            if j.id == job_id:
                return i
        return None

    @staticmethod
    def _chain_order_valid(jobs: list[JobSpec]) -> bool:
        position = {j.id: i for i, j in enumerate(jobs)}
        for j in jobs:
            if j.chain_from is not None and j.chain_from in position:
                if position[j.chain_from] >= position[j.id]:
                    return False
        return True

    # -- scheduling ---------------------------------------------------------

    def next_runnable(self) -> JobSpec | None:
        """First pending job, in queue order, whose predecessor (if any) has
        succeeded. ``None`` when nothing can run right now."""
        for job in self._jobs:
            if job.state != JobState.PENDING:
                continue
            if job.chain_from is None:
                return job
            pred = self._by_id.get(job.chain_from)
            if pred is None or pred.state == JobState.SUCCEEDED:
                return job
        return None

    def materialise(self, job_id: str) -> dict:
        """Return a deep copy of ``job_id``'s persisted JSON spec with its
        ``chain_target`` resolved from the predecessor's produced output.

        Resolution happens here — at dequeue — never at enqueue time, because
        for Analyze the output location is genuinely unknowable until
        ``resolve_input_output`` runs. Raises ``ValueError`` if the
        predecessor hasn't produced the output this job's ``chain_kind``
        names, so a caller never hands a runner a spec with a placeholder
        path in it.
        """
        job = self._by_id[job_id]
        spec = json.loads(json.dumps(job.spec))
        if job.chain_from is None:
            return spec
        pred = self._by_id.get(job.chain_from)
        if pred is None:
            raise ValueError(f"job {job_id!r} chains from unknown job {job.chain_from!r}")
        produced = {
            "out_dir": pred.output_dir,
            "dataset_root": pred.dataset_root,
            "in_dir": pred.in_dir,
        }
        value = produced.get(job.chain_kind)
        if not value:
            raise ValueError(
                f"job {job_id!r} chains from {job.chain_from!r}'s {job.chain_kind!r}, "
                "which hasn't been produced yet"
            )
        if job.chain_target:
            _set_dotted(spec, job.chain_target, value)
        return spec

    def mark_running(self, job_id: str) -> None:
        job = self._by_id[job_id]
        job.state = JobState.RUNNING
        job.message = ""

    def mark_finished(
        self,
        job_id: str,
        *,
        ok: bool,
        message: str = "",
        cancelled: bool = False,
        produced: dict | None = None,
    ) -> None:
        """Record a job's outcome. A failed or cancelled job cascades every
        transitive successor still pending to ``blocked`` — those jobs never
        ran, which is why they're distinguished from ``failed``: a "retry all
        failed" action must not touch them, since there's nothing to retry.
        """
        job = self._by_id[job_id]
        produced = produced or {}
        if ok:
            job.state = JobState.SUCCEEDED
            job.message = message
            job.output_dir = produced.get("out_dir")
            job.dataset_root = produced.get("dataset_root")
            job.in_dir = produced.get("in_dir")
        else:
            job.state = JobState.CANCELLED if cancelled else JobState.FAILED
            job.message = message
            self._cascade_block(job_id)

    def _cascade_block(self, failed_job_id: str) -> None:
        blocking = {failed_job_id}
        changed = True
        while changed:
            changed = False
            for j in self._jobs:
                if (
                    j.id not in blocking
                    and j.chain_from in blocking
                    and j.state == JobState.PENDING
                ):
                    j.state = JobState.BLOCKED
                    j.message = f"predecessor {j.chain_from} did not complete"
                    blocking.add(j.id)
                    changed = True

    # -- persistence ----------------------------------------------------

    def to_json(self) -> dict:
        return {"jobs": [j.to_json() for j in self._jobs]}

    @classmethod
    def from_json(cls, data: dict) -> JobQueue:
        jobs = [JobSpec.from_json(d) for d in (data.get("jobs") or [])]
        q = cls(jobs)
        q._repair_after_load()
        return q

    def _repair_after_load(self) -> list[str]:
        """Run once right after loading from disk. Returns human-readable
        notes about anything repaired, for the caller to log — never raises,
        since a hand-edited or crash-truncated file must not stop the app
        from opening the queue page at all."""
        notes: list[str] = []
        for j in self._jobs:
            if j.state == JobState.RUNNING:
                j.state = JobState.INTERRUPTED
                j.message = "interrupted by an app restart"
                notes.append(f"{j.id}: was running at last save, marked interrupted")
        if not self._chain_order_valid(self._jobs):
            fixed = self._topological_order(self._jobs)
            if fixed is not None:
                self._jobs = fixed
                self._by_id = {j.id: j for j in self._jobs}
                notes.append("reordered persisted jobs to respect their chain links")
            else:
                for j in self._jobs:
                    j.chain_from = j.chain_target = j.chain_kind = None
                notes.append(
                    "persisted queue had a chain cycle — all chain links were dropped"
                )
        return notes

    @staticmethod
    def _topological_order(jobs: list[JobSpec]) -> list[JobSpec] | None:
        """Kahn's algorithm, tie-broken by original position so the result is
        the original order whenever it was already valid. Returns ``None`` on
        a genuine cycle."""
        order_index = {j.id: i for i, j in enumerate(jobs)}
        by_id = {j.id: j for j in jobs}
        indegree = {j.id: 0 for j in jobs}
        children: dict[str, list[str]] = {j.id: [] for j in jobs}
        for j in jobs:
            if j.chain_from is not None and j.chain_from in by_id:
                indegree[j.id] += 1
                children[j.chain_from].append(j.id)
        ready = sorted(
            (jid for jid, deg in indegree.items() if deg == 0),
            key=order_index.get,
        )
        result: list[str] = []
        while ready:
            jid = ready.pop(0)
            result.append(jid)
            newly_ready = []
            for child in children[jid]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    newly_ready.append(child)
            ready = sorted(ready + newly_ready, key=order_index.get)
        if len(result) != len(jobs):
            return None
        return [by_id[jid] for jid in result]


def queue_path(state_dir: Path | None = None) -> Path:
    return (state_dir or default_state_dir()) / QUEUE_FILENAME


def load_queue(state_dir: Path | None = None) -> JobQueue:
    """Load the persisted queue, or return an empty one if it's never been
    written. A malformed file is treated the same as a missing one — a
    corrupt job_queue.json must not block the app from opening the queue
    page, it should just start the user from an empty queue."""
    path = queue_path(state_dir)
    if not path.is_file():
        return JobQueue()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return JobQueue()
    return JobQueue.from_json(data)


def save_queue(queue: JobQueue, state_dir: Path | None = None) -> None:
    """Write the queue via a tmp file + ``os.replace`` so a crash mid-write
    can't leave a truncated ``job_queue.json`` behind."""
    path = queue_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(queue.to_json(), indent=2))
    os.replace(str(tmp), str(path))
