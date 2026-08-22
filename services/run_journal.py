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
from util.aw_safe import swallowed

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

#: The outcomes a run can be closed with. ``None`` while it is still open, which
#: after a restart means the run died without closing its own record.
RUN_OUTCOMES = ("ok", "error", "stopped", "suspended")

#: The run modes, matching what ``PipelineRunner`` actually launched.
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
    expected_wells: tuple[str, ...]
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

    def wells_in(self, *states: str) -> tuple[str, ...]:
        """The wells currently recorded in any of *states*, sorted."""
        wanted = set(states)
        return tuple(sorted(w for w, r in self.wells.items() if r.state in wanted))


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
    except OSError:
        swallowed(f"could not write the run journal at {path}")
        return False


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
    expected_wells: list[str] | tuple[str, ...] = (),
) -> bool:
    """Record that a run has begun, before it does any work.

    Replaces any earlier journal in the same directory: the point of the record
    is to describe *this* run, and a previous run's per-well states would other-
    wise be read as this one's. What the previous run produced is still on disk
    and still authoritative — only the narrative is reset.

    ``spec`` is a :meth:`services.run_spec.RunSpec.to_json` payload, kept so a
    run that dies can be offered back to the user with the settings it had; the
    same serialisation ``suspend_record`` and the job queue already use, so
    there is no second form-to-JSON mapping to keep in step.
    """
    _clear_wells(output_dir)
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
        "expected_wells": [str(w) for w in expected_wells],
        "remote": {},
    }
    ok = _atomic_write_json(_run_path(output_dir), payload)
    _index_upsert(str(output_dir), opened_at=now, closed_at=None,
                  outcome=None, mode=str(mode))
    return ok


def record_remote(
    output_dir: Path | str,
    *,
    run_id: str,
    cache_key: str,
    host_alias: str,
    remote_input_dir: str,
    remote_output_dir: str,
    argv_digest: str = "",
) -> bool:
    """Record where on a remote host this run's work was sent.

    The one part of the journal another decision reads back. ``argv_digest`` is
    the same pipeline-argument hash ``remote_runner`` derives the run id from,
    stored so a later run can tell "the same plate, configured the same way"
    from "the same plate, configured differently" without re-deriving it.
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
        "recorded_at": time.time(),
    }
    return _atomic_write_json(_run_path(output_dir), payload)


def record_well(
    output_dir: Path | str,
    well: str,
    state: str,
    *,
    reason: str = "",
) -> bool:
    """Record what just happened to one well.

    One file per well, replaced whole, so two threads recording two wells never
    contend and neither can leave a half-written record. Later states overwrite
    earlier ones: this says where a well *got to*, not every step it took.

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
    payload = {
        "well": label,
        "state": state,
        "reason": str(reason),
        "at": time.time(),
    }
    return _atomic_write_json(_wells_dir(output_dir) / f"{label}.json", payload)


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
        expected_wells=tuple(str(w) for w in (payload.get("expected_wells") or [])),
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
    output_dir: Path | str, *, argv_digest: str,
) -> dict | None:
    """The remote address a prior run of this same configuration recorded.

    ``None`` unless a journal exists, carries a remote block with an output
    directory, and was run with the same pipeline arguments — a different
    ``argv_digest`` means a differently-configured run whose outputs are not
    interchangeable with this one's.

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
    if str(remote.get("argv_digest") or "") != str(argv_digest):
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


def interrupted_runs() -> list[dict]:
    """Index entries whose run never closed its record, newest first.

    Each of these is a run the app was killed during. The entry alone is not
    enough to act on — read the journal in the named directory for the spec and
    the per-well detail — but it is what makes the app aware a run exists at
    all, which is the thing no artefact on disk can say.
    """
    entries = [e for e in _load_index() if e.get("closed_at") is None]
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
