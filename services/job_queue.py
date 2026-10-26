"""A Qt-free, strictly-serial job queue for Analyze and Datasets-pane tools.

Persists a list of :class:`JobSpec` records — each one a JSON-shaped spec for
a specific tool ("analyze", "concatenate", "validate", "subset",
"import_loose", "merge_plates") plus optional chain links filling its inputs
from earlier jobs' outputs — one per input, so a job that takes two datasets
can wait on two jobs. :class:`JobQueue` only ever orders and gates jobs; it never
runs one itself. The thing that actually launches a job is
``datasets.job_queue.controller.QueueBridge``, driving whichever controller
(a Datasets controller, or ``AnalyzeTab`` via ``AnalyzeRunMixin``) is
registered for that job's ``kind`` — kept in a separate, Qt-touching module
so this one stays import-clean of Qt and ``review``.

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
import logging
from util.json_write import write_json_atomic
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.remote_config import default_state_dir
from util.state_dir import adopt_legacy_file, legacy_config_dirs

_LOG = logging.getLogger(__name__)

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
    SUSPENDED = "suspended"

    # A predecessor in one of these states can never produce output its
    # successors were waiting on, so its successors cascade to BLOCKED.
    FAILURE_LIKE = frozenset({FAILED, CANCELLED})

    # Jobs that stopped part-way with their work intact on disk, and can be
    # picked up again. A suspended job was wound down deliberately; an
    # interrupted one was running when the app went away. In both cases the
    # output directory holds every well that finished, which is what the
    # pipeline's ``--incremental`` reads as its done-set — so neither is a
    # dead end, and ``next_runnable`` will select them again. Neither cascades
    # to BLOCKED for the same reason: a successor waiting on one of these is
    # waiting, not blocked.
    RESUMABLE = frozenset({SUSPENDED, INTERRUPTED})

    # Nothing more will happen to these without the user editing the queue.
    TERMINAL = frozenset({SUCCEEDED, FAILED, CANCELLED, BLOCKED})


# The three shapes a chained job's predecessor might have produced. Only
# Analyze's producer is unambiguous (dataset_root == output_dir.parent in
# every resolve_input_output layout) — Datasets producers genuinely differ in
# kind, which is why this is a per-chain field rather than inferred.
_CHAIN_KINDS = ("out_dir", "dataset_root", "in_dir")


@dataclass(frozen=True)
class ChainLink:
    """One input of a job filled from an earlier job's output.

    *source* is the earlier job's id, *target* the dotted path into this job's
    own spec that the output fills (``"opts.raw"``, ``"dataset2"``), and
    *kind* which of the source's outputs it takes (one of ``_CHAIN_KINDS``).
    """

    source: str
    target: str | None = None
    kind: str | None = None

    def to_json(self) -> dict:
        return {"source": self.source, "target": self.target, "kind": self.kind}

    @classmethod
    def from_json(cls, data: dict) -> ChainLink:
        return cls(source=str(data["source"]), target=data.get("target"),
                   kind=data.get("kind"))


@dataclass
class JobSpec:
    """One persisted queue entry."""

    id: str
    kind: str
    label: str
    spec: dict = field(default_factory=dict)
    # A job's input links. The first is kept in these three fields and any
    # further ones in ``extra_links`` — the shape a queue file had when a job
    # could have only one link, so a file written by an earlier version loads
    # unchanged, and a file written now still loads in an earlier version,
    # which keeps the first link and ignores the rest. Read and write them
    # through ``links()`` / ``set_links()`` rather than field by field: the
    # rule that ``extra_links`` is empty whenever ``chain_from`` is lives there.
    chain_from: str | None = None
    chain_target: str | None = None  # dotted path into `spec`, e.g. "opts.raw"
    chain_kind: str | None = None    # one of _CHAIN_KINDS
    extra_links: list[ChainLink] = field(default_factory=list)
    state: str = JobState.PENDING
    message: str = ""
    output_dir: str | None = None
    dataset_root: str | None = None
    in_dir: str | None = None
    #: What a finished converter job recorded about the files it wrote — the
    #: filename schema and separator, and the channel roles set on its page —
    #: so an Analyze job chained after it can take them as known rather than
    #: guess them (``services.job_kinds.produced_layout``). ``None`` for every
    #: other kind, and until the job succeeds.
    layout: dict | None = None

    def links(self) -> list[ChainLink]:
        """Every input this job takes from an earlier job, first link first."""
        first = ([ChainLink(self.chain_from, self.chain_target, self.chain_kind)]
                 if self.chain_from is not None else [])
        # A hand-edited file can hold extra links with no first one; they are
        # still links, and dropping them silently would run the job on its
        # placeholder paths.
        return [*first, *self.extra_links]

    def set_links(self, links: list[ChainLink]) -> None:
        """Replace every input link at once, keeping the stored shape valid."""
        links = list(links)
        first = links[0] if links else None
        self.chain_from = first.source if first else None
        self.chain_target = first.target if first else None
        self.chain_kind = first.kind if first else None
        self.extra_links = links[1:]

    def sources(self) -> list[str]:
        """The ids of the jobs this one waits for, each once, in link order."""
        seen: list[str] = []
        for link in self.links():
            if link.source not in seen:
                seen.append(link.source)
        return seen

    def link_for(self, target: str | None) -> ChainLink | None:
        """The link filling *target*, or ``None`` when that input is not linked."""
        return next((link for link in self.links() if link.target == target), None)

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "spec": self.spec,
            "chain_from": self.chain_from,
            "chain_target": self.chain_target,
            "chain_kind": self.chain_kind,
            "extra_links": [link.to_json() for link in self.extra_links],
            "state": self.state,
            "message": self.message,
            "output_dir": self.output_dir,
            "dataset_root": self.dataset_root,
            "in_dir": self.in_dir,
            "layout": self.layout,
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
            extra_links=[ChainLink.from_json(d) for d in (data.get("extra_links") or [])
                         if isinstance(d, dict) and d.get("source")],
            state=data.get("state", JobState.PENDING),
            message=data.get("message", ""),
            output_dir=data.get("output_dir"),
            dataset_root=data.get("dataset_root"),
            in_dir=data.get("in_dir"),
            layout=dict(data["layout"]) if isinstance(data.get("layout"), dict) else None,
        )


def set_dotted(target: dict, dotted: str, value: Any) -> None:
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
        # What _repair_after_load had to fix, for whoever shows the queue to
        # tell the user. A repair the user cannot see reads as the queue
        # having quietly lost their jobs' state.
        self.repair_notes: list[str] = []

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
        # A successor chained to the removed job loses that link rather than
        # being left pointing at a job that no longer exists. Only that link:
        # a Concatenate fed by two jobs keeps the input the other one fills.
        for successor in self._jobs:
            if job_id in successor.sources():
                successor.set_links([link for link in successor.links()
                                     if link.source != job_id])

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

    def move_to(self, job_id: str, index: int) -> bool:
        """Move ``job_id`` to absolute position ``index``.

        The drag-reorder counterpart of :meth:`move`, which steps by a delta
        and swaps. Dragging picks a destination rather than a number of steps,
        and a swap is the wrong operation for it — dropping a job three rows
        down should push the rows it passed up by one, not exchange it with
        whatever happens to sit at the destination. Validated by the same
        whole-list chain-order check, so a drag can no more strand a job
        before its predecessor than an arrow-key move can.
        """
        idx = self._index(job_id)
        if idx is None or not (0 <= index < len(self._jobs)) or index == idx:
            return False
        trial = list(self._jobs)
        trial.insert(index, trial.pop(idx))
        if not self._chain_order_valid(trial):
            return False
        self._jobs = trial
        return True

    def update_spec(self, job_id: str, spec: dict) -> bool:
        """Replace ``job_id``'s stored spec. Refuses while it is running.

        Deliberately leaves ``state`` alone. Re-arming a finished job on edit
        would leave successors that already succeeded pointing at a
        predecessor that is no longer ``SUCCEEDED`` — a half-state nothing
        else in this class can produce. :meth:`retry` is the single explicit
        way a state moves backwards, and editing then retrying is two clear
        steps rather than one with a hidden second effect.
        """
        job = self._by_id.get(job_id)
        if job is None or job.state == JobState.RUNNING:
            return False
        job.spec = spec
        return True

    def set_label(self, job_id: str, label: str) -> bool:
        """Rename a job. Labels are derived from the spec, so this is how an
        edited job's name catches up with what it now describes."""
        job = self._by_id.get(job_id)
        if job is None:
            return False
        job.label = label
        return True

    def duplicate(self, job_id: str, new_id: str) -> JobSpec | None:
        """Copy ``job_id`` as a fresh pending job directly after it.

        The chain link is deliberately *not* copied: a clone that inherited
        its twin's predecessor would wait on output it has no relationship to,
        and "run this same tool against another dataset" — the reason to
        duplicate at all — is a job with no predecessor. The new id is passed
        in rather than generated here so this module needs no ``uuid`` and a
        test can name the copy.
        """
        job = self._by_id.get(job_id)
        if job is None or new_id in self._by_id:
            return None
        clone = JobSpec(
            id=new_id,
            kind=job.kind,
            label=job.label,
            spec=json.loads(json.dumps(job.spec)),
        )
        self._jobs.insert(self._index(job_id) + 1, clone)
        self._by_id[new_id] = clone
        return clone

    def retry(self, job_id: str) -> bool:
        """Put a finished job back in the queue, and unblock what it stranded.

        Only a job in a ``TERMINAL`` state can be retried — a pending one is
        already waiting and a running one is already going. Successors that
        cascaded to ``BLOCKED`` when this job failed are returned to
        ``PENDING``, since the reason they were blocked is the thing being
        retried; anything they were told about it is cleared with them.

        A ``BLOCKED`` job is refused, because retrying it fixes nothing: it
        never ran, and the predecessor that stopped it is still sitting there
        failed. Allowing it moved the job to ``PENDING`` and wiped the message
        saying why it was stuck, leaving a queue that reported work waiting,
        did nothing when started, and no longer said what the problem was.
        Retry the ancestor instead — that unblocks the whole chain in one
        step, which is what :meth:`_cascade_unblock` is for.
        """
        job = self._by_id.get(job_id)
        if job is None or job.state not in JobState.TERMINAL:
            return False
        if job.state == JobState.BLOCKED:
            return False
        job.state = JobState.PENDING
        job.message = ""
        job.output_dir = job.dataset_root = job.in_dir = None
        job.layout = None
        self._cascade_unblock(job_id)
        return True

    def clear_terminal(self) -> int:
        """Drop every finished job, returning how many went.

        Goes through :meth:`remove` per id rather than filtering the list, so
        a surviving job chained to one of the removed ones loses its link
        instead of keeping a pointer to a job that no longer exists. The ids
        are snapshotted first — ``remove`` mutates the list being read.
        """
        doomed = [j.id for j in self._jobs if j.state in JobState.TERMINAL]
        for job_id in doomed:
            self.remove(job_id)
        return len(doomed)

    def take_repair_notes(self) -> list[str]:
        """Read the load-time repair notes once, clearing them.

        They describe what loading this file had to fix, which stops being
        true the moment the user acts on it — a note saying a job was
        interrupted must not still be on the page after that job has resumed
        and succeeded.
        """
        notes, self.repair_notes = self.repair_notes, []
        return notes

    def counts(self) -> dict[str, int]:
        """How many jobs are in each state — the queue's one-line summary.

        Every state is present with a zero rather than omitted, so a caller
        formatting the summary never has to distinguish "none in this state"
        from "this state does not exist".
        """
        tally = {state: 0 for state in (
            JobState.PENDING, JobState.RUNNING, JobState.SUCCEEDED,
            JobState.FAILED, JobState.CANCELLED, JobState.BLOCKED,
            JobState.INTERRUPTED, JobState.SUSPENDED,
        )}
        for job in self._jobs:
            if job.state in tally:
                tally[job.state] += 1
        return tally

    def set_chain(
        self,
        job_id: str,
        chain_from: str | None,
        *,
        chain_target: str | None = None,
        chain_kind: str | None = None,
    ) -> None:
        """Make ``chain_from`` this job's only input link, or with ``None``
        remove every link. Raises ``ValueError`` on a self-chain or a cycle —
        checked before the link is written, not after."""
        links = ([ChainLink(chain_from, chain_target, chain_kind)]
                 if chain_from is not None else [])
        self._check_links(job_id, links)
        self._by_id[job_id].set_links(links)

    def set_link(
        self,
        job_id: str,
        target: str | None,
        source: str | None,
        *,
        kind: str | None = None,
    ) -> None:
        """Link one input of ``job_id`` — the one at *target* — to ``source``'s
        output, or with ``source=None`` unlink just that input.

        The other inputs are left as they are, which is the whole difference
        from :meth:`set_chain`: a Concatenate's dataset 2 can be linked to a
        second conversion without touching dataset 1's link to the first.
        """
        job = self._by_id[job_id]
        links = [link for link in job.links() if link.target != target]
        if source is not None:
            links.append(ChainLink(source, target, kind))
        self._check_links(job_id, links)
        job.set_links(links)

    def _check_links(self, job_id: str, links: list[ChainLink]) -> None:
        """Refuse links that name an unknown job, the job itself, or a cycle.

        Walks every ancestor through every link, not one chain of single
        parents: once a job can wait on two others, a cycle can close through
        either of them.
        """
        for link in links:
            if link.source == job_id:
                raise ValueError(f"job {job_id!r} cannot chain from itself")
            if link.source not in self._by_id:
                raise ValueError(f"unknown predecessor job id {link.source!r}")
        stack = [link.source for link in links]
        seen: set[str] = set()
        while stack:
            cur = stack.pop()
            if cur == job_id:
                raise ValueError(
                    f"chaining {job_id!r} from {links[0].source!r} would create a cycle"
                    if len(links) == 1 else
                    f"linking {job_id!r}'s inputs that way would create a cycle")
            if cur in seen:
                continue
            seen.add(cur)
            pred = self._by_id.get(cur)
            if pred is not None:
                stack.extend(pred.sources())
        for link in links:
            if self._index(link.source) > self._index(job_id):
                # The queue runs top to bottom and refuses an order that puts a
                # job before what it waits on; a link that starts out that way
                # would leave the job waiting on a job that runs after it.
                raise ValueError(
                    f"job {job_id!r} cannot wait on {link.source!r}, which is "
                    "queued after it")

    def _index(self, job_id: str) -> int | None:
        for i, j in enumerate(self._jobs):
            if j.id == job_id:
                return i
        return None

    @staticmethod
    def _chain_order_valid(jobs: list[JobSpec]) -> bool:
        position = {j.id: i for i, j in enumerate(jobs)}
        for j in jobs:
            for source in j.sources():
                if source in position and position[source] >= position[j.id]:
                    return False
        return True

    # -- scheduling ---------------------------------------------------------

    def next_runnable(self) -> JobSpec | None:
        """First job that can run now, in queue order.

        Pending, or resumable — a suspended or interrupted job is picked up
        where it left off rather than needing to be re-added by hand, which is
        the whole point of persisting the queue across a session.
        """
        for job in self._jobs:
            if job.state != JobState.PENDING and job.state not in JobState.RESUMABLE:
                continue
            preds = [self._by_id.get(source) for source in job.sources()]
            if all(pred is None or pred.state == JobState.SUCCEEDED for pred in preds):
                return job
        return None

    def materialise(self, job_id: str) -> dict:
        """Return a deep copy of ``job_id``'s persisted JSON spec with every
        linked input resolved from its source job's produced output.

        Resolution happens here — at dequeue — never at enqueue time, because
        for Analyze the output location is genuinely unknowable until
        ``resolve_input_output`` runs, and the path a queued job was set up
        with is only the one it was *expected* to write. Raises ``ValueError``
        naming the input if any source hasn't produced the output its link
        takes, so a caller never hands a runner a spec with a placeholder path
        in it.
        """
        job = self._by_id[job_id]
        spec = json.loads(json.dumps(job.spec))
        layout_from: JobSpec | None = None
        for link in job.links():
            pred = self._by_id.get(link.source)
            if pred is None:
                raise ValueError(f"job {job_id!r} chains from unknown job {link.source!r}")
            produced = {
                "out_dir": pred.output_dir,
                "dataset_root": pred.dataset_root,
                "in_dir": pred.in_dir,
            }
            value = produced.get(link.kind)
            if not value:
                where = f" for {link.target!r}" if link.target else ""
                raise ValueError(
                    f"job {job_id!r} chains from {link.source!r}'s {link.kind!r}{where}, "
                    "which hasn't been produced yet"
                )
            if link.target:
                set_dotted(spec, link.target, value)
            if layout_from is None and pred.layout:
                layout_from = pred
        # What the source recorded about the files it wrote travels with the
        # path to them. Only Analyze reads it, and only when the job asked for
        # run-time detection (services.run_detect); a key it ignores is
        # harmless on every other kind, but it is not written there anyway.
        # Analyze has one input, so "the first source that recorded one" is
        # that input's.
        if layout_from is not None and job.kind == "analyze":
            spec["upstream_layout"] = json.loads(json.dumps(layout_from.layout))
        return spec

    def mark_running(self, job_id: str) -> None:
        job = self._by_id[job_id]
        job.state = JobState.RUNNING
        job.message = ""

    def mark_suspended(self, job_id: str, message: str = "") -> None:
        """The user wound this job down; it keeps its place and its progress.

        Deliberately not routed through ``mark_finished``: a suspended job is
        not an outcome, and treating it as a cancel would cascade its
        successors to BLOCKED as though its output were never coming.
        """
        job = self._by_id[job_id]
        job.state = JobState.SUSPENDED
        job.message = message or "suspended — will resume where it left off"

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
            job.layout = produced.get("layout")
        else:
            job.state = JobState.CANCELLED if cancelled else JobState.FAILED
            job.message = message
            self._cascade_block(job_id)

    def _cascade_block(self, failed_job_id: str) -> None:
        """Block every pending job that waits, through any of its inputs, on
        the failed one. One failed input is enough: a Concatenate missing
        either dataset has nothing to concatenate."""
        blocking = {failed_job_id}
        changed = True
        while changed:
            changed = False
            for j in self._jobs:
                if j.id in blocking or j.state != JobState.PENDING:
                    continue
                stopped_by = next((s for s in j.sources() if s in blocking), None)
                if stopped_by is not None:
                    j.state = JobState.BLOCKED
                    j.message = f"predecessor {stopped_by} did not complete"
                    blocking.add(j.id)
                    changed = True

    def _cascade_unblock(self, retried_job_id: str) -> None:
        """The mirror of :meth:`_cascade_block`, walked from a retried job.

        Only ``BLOCKED`` jobs move, and only those reachable from this one:
        a job blocked by a *different* failure further along the queue stays
        blocked, because that failure has not been retried. A job with two
        inputs stays blocked while its *other* source is still failed,
        cancelled or blocked itself — retrying one of two failed conversions
        must not release a Concatenate that still has nothing to read for the
        other dataset.
        """
        unblocking = {retried_job_id}
        changed = True
        while changed:
            changed = False
            for j in self._jobs:
                if j.id in unblocking or j.state != JobState.BLOCKED:
                    continue
                sources = j.sources()
                if not any(s in unblocking for s in sources):
                    continue
                if any(self._still_stopping(s) for s in sources if s not in unblocking):
                    continue
                j.state = JobState.PENDING
                j.message = ""
                unblocking.add(j.id)
                changed = True

    def _still_stopping(self, job_id: str) -> bool:
        """Whether *job_id* would still block a job that waits on it."""
        job = self._by_id.get(job_id)
        return job is not None and (
            job.state in JobState.FAILURE_LIKE or job.state == JobState.BLOCKED)

    # -- persistence ----------------------------------------------------

    def to_json(self) -> dict:
        return {"jobs": [j.to_json() for j in self._jobs]}

    @classmethod
    def from_json(cls, data: dict) -> JobQueue:
        jobs = [JobSpec.from_json(d) for d in (data.get("jobs") or [])]
        q = cls(jobs)
        q.repair_notes = q._repair_after_load()
        return q

    def _repair_after_load(self) -> list[str]:
        """Run once right after loading from disk. Returns human-readable
        notes about anything repaired, for the caller to log — never raises,
        since a hand-edited or crash-truncated file must not stop the app
        from opening the queue page at all."""
        notes: list[str] = []
        for j in self._jobs:
            if j.state == JobState.RUNNING:
                # Not a failure and not a dead end. Whatever the run had
                # finished is on disk, and INTERRUPTED is resumable, so
                # starting the queue picks this job up again rather than
                # requiring the user to re-add it. Its successors stay PENDING
                # and wait for it — they are not cascaded to BLOCKED, because
                # the output they are waiting on is still coming.
                j.state = JobState.INTERRUPTED
                j.message = "interrupted by an app restart — resumable"
                notes.append(
                    f"{j.id}: was running at last save, marked interrupted "
                    f"(start the queue to resume it)")
        if not self._chain_order_valid(self._jobs):
            fixed = self._topological_order(self._jobs)
            if fixed is not None:
                self._jobs = fixed
                self._by_id = {j.id: j for j in self._jobs}
                notes.append("reordered persisted jobs to respect their chain links")
            else:
                for j in self._jobs:
                    j.set_links([])
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
            for source in j.sources():
                if source in by_id:
                    indegree[j.id] += 1
                    children[source].append(j.id)
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
    """``job_queue.json`` in *state_dir*, or in the app's folder.

    In the app's folder, a queue an earlier version left in its config folder
    (``%APPDATA%\\allwell`` or ``~/.config/allwell``) is moved over first, so
    queued jobs survive the upgrade (``util.state_dir.adopt_legacy_file``).
    """
    if state_dir is not None:
        return state_dir / QUEUE_FILENAME
    return adopt_legacy_file(
        default_state_dir() / QUEUE_FILENAME,
        [d / QUEUE_FILENAME for d in legacy_config_dirs()],
        log=_LOG,
    )


def load_queue(state_dir: Path | None = None) -> JobQueue:
    """Load the persisted queue, or return an empty one if it's never been
    written. A malformed file is treated the same as a missing one — a
    corrupt job_queue.json must not block the app from opening the queue
    page, it should just start the user from an empty queue.

    ``JobQueue.from_json``/``JobSpec.from_json`` are included in the guarded
    section, not just the JSON parse itself — well-formed JSON that isn't a
    well-formed *queue* (a job entry missing ``id``/``kind``, or a ``jobs``
    value that isn't a list of objects) raised ``KeyError``/``TypeError``
    uncaught here, and ``load_queue`` runs unconditionally and synchronously
    from ``QueueBridge.__init__`` at ``DatasetsTab`` construction — so a
    hand-edited or truncated file didn't just break the queue page, it took
    down the whole app's startup.
    """
    path = queue_path(state_dir)
    if not path.is_file():
        return JobQueue()
    try:
        data = json.loads(path.read_text())
        return JobQueue.from_json(data)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError):
        return JobQueue()


def save_queue(queue: JobQueue, state_dir: Path | None = None) -> None:
    """Write the queue via a tmp file + ``os.replace`` so a crash mid-write
    can't leave a truncated ``job_queue.json`` behind."""
    # ``durable=False``: written on every queue state change, from the GUI
    # thread. The queue is bookkeeping about runs, not the runs' results.
    write_json_atomic(queue_path(state_dir), queue.to_json(), durable=False)
