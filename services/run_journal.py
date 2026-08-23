"""What an Analyze run intended, where it sent the work, and what became of it.

Every other record this app keeps about a run is written **when the run ends**:
``pipeline/run_report.py``'s per-well coverage, ``pipeline/completeness.py``'s
``run_check`` block, the remote fetch reconciliation. That is exactly why a run
that dies mid-way leaves nothing behind — the output directory holds whatever
finished, and the artefacts are still the honest answer to "what is done", but
nothing on disk says a run was ever started, what it was configured with, or
where on a remote host its work was sent.

This module is that missing record. It is opened before any well is processed,
amended as the run proceeds, and closed with an outcome. Two rules define it:

**It is evidence, never authority.** ``pipeline.completeness`` remains the only
answer to "is this well complete", and the artefacts remain the source of truth
for the done-set an incremental run reads. A journal that went missing, or that
described a directory someone has since edited by hand, must never make a good
dataset look empty. What it answers is the question the artefacts cannot: what
was *intended*, and why something is absent — failed, never uploaded, computed
remotely but never fetched, or skipped as already up to date. Absence and
failure look identical on disk; they do not look identical here.

**It drives exactly one decision.** A remote re-run may re-attach to the remote
output directory this journal recorded, instead of the one it would re-derive
from the input archives' mtimes. Everything else it holds is reported, not acted
on. See ``services.remote_runner._choose_remote_output_dir``, which still
verifies the recorded address against the remote before using it: the journal
proposes, the remote listing decides.

The on-disk shape is deliberately copied from ``pipeline/run_report.py``, which
already solved the concurrency problem this has — one small file per unit,
written atomically and never appended to, so the orchestrator, the worker pool
and the remote Fetcher thread can all record without a lock and without a
partial line. A single append-only log would need one.

::

    <output_dir>/.aw_journal/
        run.json              the run: intent, remote address, outcome
        wells/<WELL>.json     the last thing that happened to one well

The journal lives beside the outputs so it travels with the dataset, and a
small index under the app's state directory points at the output directories
that have one — because at start-up the app does not yet know which folder to
look in, and a run whose entry is still open is a run that died.

Deliberately **not** a block inside ``pipeline_info.json``: that file is
round-tripped through ``pipeline/sidecar.py``'s user-block preservation and is
overwritten wholesale when a remote run fetches the sidecar back, so a journal
kept there would be destroyed on arrival.

Qt-free and stdlib-only, so the services layer, a CLI driver and a test can all
read it without the GUI.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from services.remote_config import default_state_dir
from util.aw_safe import swallow, swallowed

#: Bumped when the on-disk shape changes incompatibly. A record written by any
#: other version reads as "no journal" rather than raising — see
#: :func:`read_journal`.
JOURNAL_VERSION = 1

#: Hidden so no dataset scan mistakes it for output; kept after the run, unlike
#: ``run_report``'s staging directory, because the record *is* the deliverable.
JOURNAL_DIRNAME = ".aw_journal"

_RUN_FILENAME = "run.json"
_WELLS_DIRNAME = "wells"

#: The states one well can be recorded in, and the only place they are spelled.
#: Deliberately disjoint from ``pipeline.completeness.HEALTH_STATES``: these
#: describe *what this run did to a well*, not whether the well's output is
#: complete, and conflating the two vocabularies is how a plate legend and a
#: classifier drift apart. ``tests/test_no_second_journal_vocabulary.py`` fails
#: on a second literal roster anywhere in the repo.
WELL_STATES = (
    "uploaded",      # the well's input reached the remote
    "analysed",      # the pipeline announced this well finished
    "fetched",       # its output landed locally
    "skipped",       # deliberately not processed / not transferred, with a reason
    "failed",        # an error was recorded against it
    "not-fetched",   # output exists on the remote but never arrived here
)

#: How far through a remote run each state is, for the states that describe one
#: well's progress along a single path. An earlier state does not overwrite a
#: later one — see :func:`record_well`. A *narrowing*, not a guarantee: the
#: check is a read followed by a write with no lock between them, so an
#: interleaving inside that window can still land the wrong way round. The
#: window is one small file read wide against a download, which is why this is
#: worth doing; it is not why it would be worth claiming.
#:
#: Two independent writers record these on a remote run and neither can be
#: ordered against the other: ``analyze_progress`` writes ``analysed`` from a
#: parsed stdout line, drained by the GUI's poll timer, while the Fetcher
#: thread writes ``fetched`` after polling the remote sidecar. A small well can
#: download before the GUI queue drains, and the late ``analysed`` then
#: permanently downgraded a well already on disk — so the fetch report printed
#: "analysed but not transferred" for a well that had transferred.
#:
#: States absent from this map are terminal verdicts rather than stages
#: (``skipped``, ``failed``, ``not-fetched``) and always take effect: they are
#: written once, by whichever party made the decision.
_WELL_STAGE_ORDER = {
    "uploaded": 1,
    "analysed": 2,
    "fetched": 3,
}

#: How the well states are grouped when a run is described to a user, and the
#: words used for each group. Every member of ``WELL_STATES`` appears exactly
#: once — ``tests/test_run_journal.py`` holds that, because a state missing here
#: is a well the user is never told about, which is the same silent omission the
#: roster guard exists to prevent. Two surfaces render this (the resume banner
#: and the pre-run readout) and both had their own copy, which between them
#: dropped ``uploaded`` and ``skipped``.
TALLY_GROUPS = (
    ("finished", ("analysed", "fetched")),
    ("failed", ("failed",)),
    ("never fetched", ("not-fetched",)),
    ("skipped", ("skipped",)),
    ("uploaded only", ("uploaded",)),
)

#: The outcomes a run can be closed with. ``None`` while it is still open, which
#: after a restart means the run died without closing its own record.
RUN_OUTCOMES = ("ok", "error", "stopped", "suspended")

#: The closed outcomes that leave work worth picking up. Every one of them
#: leaves each finished well's CSV and out-zip on disk, which is what an
#: incremental re-run reads as its done-set — so what separates them from
#: ``"ok"`` is only that the plate is not done yet. Named once here rather than
#: restated by each surface that offers a resume.
#:
#: This roster differs from ``JobState.RESUMABLE``, deliberately. The queue calls a *failed*
#: job terminal, because its successors are waiting on output that is never
#: coming and have to be told. Nothing is chained behind a resume banner, so the
#: only question here is whether the plate has work left worth picking up — and
#: after a failure it does, in the wells that got through.
UNFINISHED_OUTCOMES = ("error", "stopped", "suspended")

#: The run modes, matching what ``PipelineRunner`` actually launched, and the
#: only place they are spelled. :func:`open_run` refuses anything else, for the
#: same reason ``record_well`` refuses an unknown state: a mode nobody renders
#: is a run the readout describes as neither local nor remote.
RUN_MODES = ("local", "remote", "remote-batched")

INDEX_FILENAME = "run_index.json"


# ── shapes ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WellRecord:
    """The last thing this run did to one well."""

    well: str
    state: str
    reason: str = ""
    at: float = 0.0


@dataclass(frozen=True)
class RunJournal:
    """One run's record, as read back off disk."""

    output_dir: str
    input_dir: str
    mode: str
    opened_at: float
    closed_at: float | None
    outcome: str | None
    message: str
    argv: tuple[str, ...]
    spec: dict
    remote: dict
    wells: dict[str, WellRecord] = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        """Whether the run never closed its own record — i.e. it died.

        A *stopped* or *suspended* run closes normally and is not "open": the
        user ended it and the app was there to say so. An open record is the
        one case nothing else on disk can report.
        """
        return self.closed_at is None

    @property
    def is_unfinished(self) -> bool:
        """Whether this run left a plate that is not done.

        Wider than :attr:`is_open` and the question a resume offer actually
        asks. A run that failed, was stopped or was suspended closed its record
        properly — the app was there — but the plate is no more finished than
        if the process had been killed, and every well that got through is on
        disk either way.
        """
        return self.is_open or self.outcome in UNFINISHED_OUTCOMES

    def wells_in(self, *states: str) -> tuple[str, ...]:
        """The wells currently recorded in any of *states*, sorted."""
        wanted = set(states)
        return tuple(sorted(w for w, r in self.wells.items() if r.state in wanted))

    def tally(self) -> str:
        """What this run recorded, as one sentence, or ``""`` for nothing.

        Reported, never derived: these are states the run wrote down as it went,
        and they say nothing about whether the *output* is complete — that is
        ``pipeline.completeness``'s answer. Lives here rather than at each
        surface because the grouping is a statement about ``WELL_STATES``, and
        the two surfaces that render it had drifted into dropping two states
        between them.
        """
        parts = [f"{n} {label}" for label, states in TALLY_GROUPS
                 if (n := len(self.wells_in(*states)))]
        return f" It recorded {', '.join(parts)}." if parts else ""


# ── paths ───────────────────────────────────────────────────────────────────

def journal_dir(output_dir: Path | str) -> Path:
    return Path(output_dir) / JOURNAL_DIRNAME


def _run_path(output_dir: Path | str) -> Path:
    return journal_dir(output_dir) / _RUN_FILENAME


def _wells_dir(output_dir: Path | str) -> Path:
    return journal_dir(output_dir) / _WELLS_DIRNAME


def index_path() -> Path:
    """``<state dir>/run_index.json`` — the same directory the job queue uses.

    Not ``~/.allwell``, where ``services/suspend_record.py`` writes: that path
    predates :func:`services.remote_config.default_state_dir` and is not the
    platform-correct location on Windows. New state goes to the state dir.
    """
    return default_state_dir() / INDEX_FILENAME


# ── writing ─────────────────────────────────────────────────────────────────

def _atomic_write_json(path: Path, payload: dict | list) -> bool:
    """Replace *path* with *payload*, atomically. ``False`` on any failure.

    A run that cannot write its own bookkeeping must still produce data, so
    every caller here treats the return value as advisory and carries on.
    """
    tmp = ""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_",
                                   suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return True
    except (OSError, TypeError, ValueError):
        # ``TypeError`` and ``ValueError`` are not hypothetical here: the
        # payload carries a ``spec`` handed in by the caller, and one
        # unserialisable value in it makes ``json.dump`` raise rather than the
        # filesystem. Catching only ``OSError`` let that escape a function
        # whose whole contract is that bookkeeping never fails a run, past
        # callers that guard the same narrow type, and into the worker thread.
        swallowed(f"could not write the run journal at {path}")
        return False
    finally:
        # ``mkstemp`` created a file; every failure after that point leaves it
        # behind, and these are per-run and per-well writes, so a plate that
        # keeps failing would silt the output folder up with ``.tmp_*.json``.
        # ``os.replace`` consumed it on the success path, hence ``missing_ok``.
        if tmp:
            with swallow(f"could not remove the temporary journal file {tmp}"):
                Path(tmp).unlink(missing_ok=True)


def _read_run_payload(output_dir: Path | str) -> dict | None:
    """The raw ``run.json`` dict, or ``None`` if there is not a usable one."""
    try:
        raw = json.loads(_run_path(output_dir).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("version") != JOURNAL_VERSION:
        return None
    return raw


def open_run(
    output_dir: Path | str,
    *,
    spec: dict | None,
    mode: str,
    input_dir: Path | str,
    argv: list[str] | tuple[str, ...] = (),
) -> float | None:
    """Record that a run has begun, before it does any work.

    Replaces any earlier journal in the same directory: the point of the record
    is to describe *this* run, and a previous run's per-well states would other-
    wise be read as this one's. What the previous run produced is still on disk
    and still authoritative — only the narrative is reset.

    **The remote address is the exception, and it is not a detail.** It names a
    directory on a worker host that outlives the run which filled it, which is
    the entire reason it is written down: the next run of the same plate reads
    it to find work a churned cache key would otherwise strand. Resetting it
    here left :func:`stored_remote_address` returning ``None`` for ever — the
    journal is opened by ``PipelineRunner`` *before* ``RemoteRunner.spawn``
    reads it, so every run wiped the address a moment before asking for it, and
    the one decision this record exists to drive could not fire at all. It is
    carried forward instead; ``argv_digest`` is what decides whether it is still
    applicable, and ``record_remote`` overwrites it with this run's own address
    as soon as there is one.

    ``spec`` is a :meth:`services.run_spec.RunSpec.to_json` payload, kept so a
    run that dies can be offered back to the user with the settings it had; the
    same serialisation ``suspend_record`` and the job queue already use, so
    there is no second form-to-JSON mapping to keep in step.

    An unrecognised *mode* is refused rather than written, like an unrecognised
    well state: every surface that renders this record words the run by its
    mode, and one nobody spells is a run described as neither local nor remote.
    """
    if mode not in RUN_MODES:
        raise ValueError(
            f"{mode!r} is not a run-journal run mode; expected one of "
            f"{', '.join(RUN_MODES)}")
    prior = _read_run_payload(output_dir) or {}
    carried = prior.get("remote")
    now = time.time()
    payload = {
        "version": JOURNAL_VERSION,
        "opened_at": now,
        "closed_at": None,
        "outcome": None,
        "message": "",
        "mode": str(mode),
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "argv": [str(a) for a in argv],
        "spec": spec if isinstance(spec, dict) else {},
        "remote": carried if isinstance(carried, dict) else {},
    }
    ok = _atomic_write_json(_run_path(output_dir), payload)
    # Only once the new record is on disk. Clearing first meant a write that
    # failed — a full disk, a volume gone read-only between the read and the
    # write — left the *previous* run's ``run.json`` beside an empty wells
    # directory: a record that still presents as complete while its per-well
    # detail has silently gone. The wells belong to whichever ``run.json`` is
    # there, so they are dropped only when this one replaced it.
    if ok:
        _clear_wells(output_dir)
    # The index entry is what makes a run findable at the next start-up, so it
    # is written whether or not the record itself landed: an entry naming a
    # directory whose journal cannot be read costs one wasted read, while a
    # missing entry costs the offer entirely.
    _index_upsert(str(output_dir), opened_at=now, closed_at=None,
                  outcome=None, mode=str(mode))
    # The token, not a bare success flag: every later write says which run it
    # belongs to by handing this back. ``None`` for a record that was not
    # written, which is falsy exactly where the old ``bool`` was.
    return now if ok else None


def record_remote(
    output_dir: Path | str,
    *,
    run_id: str,
    cache_key: str,
    host_alias: str,
    remote_input_dir: str,
    remote_output_dir: str,
    argv_digest: str = "",
    source_key: str = "",
) -> bool:
    """Record where on a remote host this run's work was sent.

    The one part of the journal another decision reads back, and the two keys
    beside the address are what make reading it back safe. ``argv_digest`` is
    the pipeline-argument hash ``remote_runner`` derives the run id from, so a
    later run can tell "configured the same way" from "configured differently"
    without re-deriving it. ``source_key`` fingerprints the user's own input
    archives, so it can also tell "the same images" from "the same plate,
    re-exported" — a distinction the upload cache key cannot make, because that
    one churns whenever staging rewrites as well as whenever the images change.
    """
    payload = _read_run_payload(output_dir)
    if payload is None:
        return False
    payload["remote"] = {
        "run_id": str(run_id),
        "cache_key": str(cache_key),
        "host_alias": str(host_alias),
        "remote_input_dir": str(remote_input_dir),
        "remote_output_dir": str(remote_output_dir),
        "argv_digest": str(argv_digest),
        "source_key": str(source_key),
        "recorded_at": time.time(),
    }
    return _atomic_write_json(_run_path(output_dir), payload)


def record_well(
    output_dir: Path | str,
    well: str,
    state: str,
    *,
    reason: str = "",
    run_token: float | None = None,
) -> bool:
    """Record what just happened to one well.

    One file per well, replaced whole, so two threads recording two wells never
    contend and neither can leave a half-written record. This says where a well
    *got to*, not every step it took.

    **A well is not moved backwards along the upload → analysed → fetched path**
    — narrowed, not guaranteed: read-then-write with no lock, so an
    interleaving inside that window can still land the wrong way round.
    Those three are written by two writers that cannot be ordered against each
    other — the GUI thread draining parsed stdout, and the Fetcher thread
    polling the remote sidecar — so a small well that downloads before the GUI
    queue drains would otherwise have its ``fetched`` overwritten by a late
    ``analysed`` and be reported as "analysed but not transferred" while
    sitting on disk. Any state outside that path (``skipped``, ``failed``,
    ``not-fetched``) is a verdict rather than a stage, is written once by
    whoever decided it, and always takes effect.

    ``run_token`` is the ``opened_at`` :func:`open_run` returned, and it is how
    a writer says *which* run this belongs to. A record whose token does not
    match the one on disk is refused; a caller that does not know its token
    passes ``None`` and is trusted. It exists because a run whose own
    ``open_run`` failed would otherwise write into the previous run's
    surviving record — and because the obvious cheaper test, "is a run still
    open here", refuses a run's *own* completion lines, which arrive after the
    worker thread has closed the record.

    An unrecognised *state* is refused rather than written — a typo'd state is
    a well that silently disappears from every count that reads this back.
    """
    if state not in WELL_STATES:
        raise ValueError(
            f"{state!r} is not a run-journal well state; expected one of "
            f"{', '.join(WELL_STATES)}")
    label = str(well).strip()
    if not label:
        return False
    payload = _read_run_payload(output_dir)
    if payload is None:
        return False
    if run_token is not None and float(payload.get("opened_at") or 0.0) != run_token:
        # The record on disk belongs to a *different* run. That happens for one
        # reason and it is not hypothetical: ``open_run``'s write is allowed to
        # fail, and when it does the previous run's ``run.json`` and its wells
        # are what remain. Writing into it would file this run's progress under
        # someone else's run — and with the no-regress rule below, a `fetched`
        # left there silently refused this run's `uploaded` and `analysed`, so
        # the journal reported a well as fetched for a run that had only
        # uploaded it.
        #
        # **Which run, not whether one is open.** The first version of this
        # asked ``closed_at is not None``, and those two questions coincide
        # only in the case above. A run's own completion lines are parsed on
        # the GUI thread, in bounded slices, while ``_close_journal`` runs in
        # the worker thread's ``finally`` the instant the subprocess exits — so
        # the tail of every plate arrives after the record is closed, and
        # asking "is a run open" refused all of it. A 24-well plate recorded
        # four wells and the pre-run readout told the user so.
        return False
    rank = _WELL_STAGE_ORDER.get(state)
    if rank is not None:
        existing = _read_well_state(output_dir, label)
        if _WELL_STAGE_ORDER.get(existing or "", 0) > rank:
            return False
    payload = {
        "well": label,
        "state": state,
        "reason": str(reason),
        "at": time.time(),
    }
    return _atomic_write_json(_wells_dir(output_dir) / f"{label}.json", payload)


def _read_well_state(output_dir: Path | str, well: str) -> str | None:
    """The state already recorded for *well* in this run, or ``None``.

    One file read, on the path that writes one file — the cost is the same
    order as the write it guards, and the alternative is an in-memory map that
    the two writing threads would then have to share. Safe to read as "this
    run's" because :func:`open_run` clears the previous run's wells, and
    :func:`record_well` refuses to write into a run that has closed.
    """
    try:
        rec = json.loads((_wells_dir(output_dir) / f"{well}.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict):
        return None
    state = str(rec.get("state") or "")
    return state or None


def close_run(
    output_dir: Path | str,
    *,
    outcome: str,
    message: str = "",
) -> bool:
    """Close the record with how the run ended.

    Until this lands the record reads as open, which after a restart is how the
    app knows a run died rather than finished. So it must run on **every** exit
    the app survives — success, failure, Stop and Suspend alike.
    """
    payload = _read_run_payload(output_dir)
    if payload is None:
        return False
    closed_at = time.time()
    payload["closed_at"] = closed_at
    payload["outcome"] = outcome if outcome in RUN_OUTCOMES else "error"
    payload["message"] = str(message)
    ok = _atomic_write_json(_run_path(output_dir), payload)
    _index_upsert(str(output_dir), opened_at=float(payload.get("opened_at") or 0.0),
                  closed_at=closed_at, outcome=payload["outcome"],
                  mode=str(payload.get("mode") or ""))
    return ok


def _clear_wells(output_dir: Path | str) -> None:
    """Drop the previous run's per-well records. Never raises."""
    wells = _wells_dir(output_dir)
    try:
        for path in wells.glob("*.json"):
            path.unlink(missing_ok=True)
    except OSError:
        swallowed(f"could not clear the previous per-well journal in {wells}")


# ── reading ─────────────────────────────────────────────────────────────────

def read_journal(output_dir: Path | str) -> RunJournal | None:
    """The journal for *output_dir*, or ``None`` when there is not a usable one.

    ``None`` for absent, unreadable, malformed, and wrong-version records
    alike — the same forgiving contract ``SuspendedRun.from_json`` has, and for
    the same reason: the worst outcome of dropping a record is that the user is
    told less than they could have been, while raising here would stop the
    Analyze pane from rendering at all.
    """
    payload = _read_run_payload(output_dir)
    if payload is None:
        return None
    closed_at = payload.get("closed_at")
    outcome = payload.get("outcome")
    return RunJournal(
        output_dir=str(payload.get("output_dir") or output_dir),
        input_dir=str(payload.get("input_dir") or ""),
        mode=str(payload.get("mode") or ""),
        opened_at=float(payload.get("opened_at") or 0.0),
        closed_at=float(closed_at) if closed_at is not None else None,
        outcome=str(outcome) if outcome else None,
        message=str(payload.get("message") or ""),
        argv=tuple(str(a) for a in (payload.get("argv") or [])),
        spec=payload.get("spec") if isinstance(payload.get("spec"), dict) else {},
        remote=payload.get("remote") if isinstance(payload.get("remote"), dict) else {},
        wells=_read_wells(output_dir),
    )


def _read_wells(output_dir: Path | str) -> dict[str, WellRecord]:
    """Every per-well record on disk. A file that cannot be read is skipped."""
    out: dict[str, WellRecord] = {}
    wells = _wells_dir(output_dir)
    try:
        paths = sorted(wells.glob("*.json"))
    except OSError:
        return out
    for path in paths:
        try:
            rec = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict):
            continue
        state = str(rec.get("state") or "")
        if state not in WELL_STATES:
            continue
        label = str(rec.get("well") or path.stem)
        out[label] = WellRecord(
            well=label, state=state,
            reason=str(rec.get("reason") or ""),
            at=float(rec.get("at") or 0.0),
        )
    return out


def stored_remote_address(
    output_dir: Path | str, *, argv_digest: str, source_key: str,
    host_alias: str,
) -> dict | None:
    """The remote address a prior run of the same images and settings recorded.

    ``None`` unless a journal exists, carries a remote block with an output
    directory, and **all three** keys match.

    A differing ``argv_digest`` means a differently-configured run, whose
    outputs are not interchangeable with this one's — and it covers the flags
    the *host* adds as well as the ones the form produces, because a CPU-only
    host, a host override and a changed Fetch filter all change what the remote
    writes. A differing ``source_key`` means the images themselves changed, and
    that is the dangerous one: the recorded directory holds results computed
    from pictures the user has since replaced, and handing it back would let
    the pre-flight check find it complete, skip the pipeline entirely, and
    fetch the old numbers as though they were this run's. A differing
    ``host_alias`` means the address names a path on a machine this run is not
    talking to; two hosts can share a filesystem, so the path may well resolve
    and the listing succeed, which is precisely why it has to be asked rather
    than inferred from the directory being there. All three are required, and
    an empty key on either side matches nothing — a record written before the
    key existed, or an input directory that cannot be read, declines rather
    than guesses.

    The caller must still verify the address against the remote before using
    it. This says where a previous run *sent* work, which is not the same claim
    as the work still being there.
    """
    journal = read_journal(output_dir)
    if journal is None:
        return None
    remote = journal.remote or {}
    if not remote.get("remote_output_dir"):
        return None
    for name, given in (("argv_digest", argv_digest), ("source_key", source_key),
                        ("host_alias", host_alias)):
        stored = str(remote.get(name) or "")
        # An empty key on either side is "I could not determine this", never
        # "it matched". A record written before a key existed, and an input
        # directory that cannot be read, both look like this — and treating
        # the absence of evidence as agreement is how a reuse decision fires
        # in exactly the situation nobody understood.
        if not stored or not given or stored != str(given):
            return None
    return dict(remote)


# ── the index of output directories that have a journal ─────────────────────

#: The index is a convenience for finding runs, not an archive. Old entries
#: point at directories that may not exist any more, and the answer the app
#: needs from it — "did something die recently?" — is at the front.
_INDEX_MAX = 50


def _load_index() -> list[dict]:
    """Every index entry. Never raises; a corrupt index reads as empty."""
    try:
        raw = json.loads(index_path().read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    return [e for e in raw if isinstance(e, dict) and e.get("output_dir")]


def _index_upsert(
    output_dir: str, *, opened_at: float, closed_at: float | None,
    outcome: str | None, mode: str,
) -> bool:
    """Replace this output directory's index entry, newest first."""
    from services.suspend_record import same_dir

    keep = [e for e in _load_index()
            if not same_dir(str(e.get("output_dir")), output_dir)]
    entry = {
        "output_dir": output_dir,
        "opened_at": opened_at,
        "closed_at": closed_at,
        "outcome": outcome,
        "mode": mode,
    }
    return _atomic_write_json(index_path(), [entry, *keep][:_INDEX_MAX])


def unfinished_runs() -> list[dict]:
    """Index entries for runs that did not finish, newest first.

    Two shapes qualify and the distinction matters to what the user is told,
    not to whether they are offered. An entry with no ``closed_at`` is a run the
    app was killed during — the case no artefact can report, since a killed run
    and a run that simply processed fewer wells leave the same output. One
    closed with any outcome in :data:`UNFINISHED_OUTCOMES` ended for a reason
    the app was there to record.

    All of them leave every finished well's CSV and out-zip on disk, which is
    what ``--incremental`` reads as its done-set, so all of them can be picked
    up. A failure especially: it is the case where the user most wants to carry
    on, and offering nothing there was the gap this list exists to close.

    The entry alone is not enough to act on — read the journal in the named
    directory for the spec and the per-well detail.
    """
    entries = [
        e for e in _load_index()
        if e.get("closed_at") is None or e.get("outcome") in UNFINISHED_OUTCOMES
    ]
    entries.sort(key=lambda e: float(e.get("opened_at") or 0.0), reverse=True)
    return entries


def forget(output_dir: Path | str) -> bool:
    """Drop *output_dir*'s index entry, leaving the journal itself in place.

    Called when the user has been shown an interrupted run and dealt with it,
    so the same banner does not reappear for ever. The journal stays because it
    is the dataset's provenance; only the "look at this" pointer goes.
    """
    from services.suspend_record import same_dir

    target = str(output_dir)
    keep = [e for e in _load_index()
            if not same_dir(str(e.get("output_dir")), target)]
    return _atomic_write_json(index_path(), keep)
