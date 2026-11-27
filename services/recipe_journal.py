"""The log of one recipe application: what each of its jobs did, and when.

Applying a recipe queues a chain of ordinary jobs (``services.job_recipes``),
and the queue already survives a crash: ``job_queue.json`` is rewritten on
every change and a job that was running comes back *interrupted*. What the
queue does not keep is the story — when the watch settled, which file the
conversion chose, what each step wrote, that the app went down halfway — and
it forgets a run's jobs belong together the moment one of them is cleared.

So each application gets a journal: one JSON object per line in
``recipe_runs/<run>.jsonl`` beside the queue, appended as things happen and
never rewritten. Appending is the whole of the crash story: a line is either
on disk or not, and a line cut off by a crash mid-write is skipped when the
journal is read (:func:`read_events`), with everything before it intact. Each
append is flushed and ``fsync``-ed — a journal is written a handful of times
per step, so the cost does not matter and the record does.

Events are dicts with ``ts`` (epoch seconds), ``event`` and, for a job's
events, ``job`` and ``step`` (0-based). The kinds written are
:data:`EVENTS`; a reader ignores fields it does not know, so a later version
can add some.

Qt-free.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from services.remote_config import default_state_dir

_LOG = logging.getLogger("allwell.services.recipe_journal")

RUNS_DIRNAME = "recipe_runs"

#: Every event kind written, with what it records.
EVENTS = {
    "applied": "the recipe was applied: name, input, the job ids in step order, notes",
    "started": "a job started, with the inputs it was given (a link's real folder)",
    "status": "a running job said something worth keeping (a watch's phase, the file a conversion chose)",
    "finished": "a job ended: ok, message, and the folders it reported",
    "suspended": "a job was paused and will resume",
    "interrupted": "the app stopped while the job ran; it is resumable",
    "retried": "the job was put back in the queue",
    "removed": "the job was removed from the queue",
}

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def runs_dir(state_dir: Path | None = None) -> Path:
    return (state_dir if state_dir is not None else default_state_dir()) / RUNS_DIRNAME


def journal_path(run_id: str, state_dir: Path | None = None) -> Path:
    """The journal file for *run_id*. Refuses an id that is not a plain name,
    since it becomes a file name."""
    if not _SAFE_ID.match(run_id or ""):
        raise ValueError(f"not a recipe run id: {run_id!r}")
    return runs_dir(state_dir) / f"{run_id}.jsonl"


def _ends_mid_line(path: Path) -> bool:
    """Whether *path* exists and its last byte is not a newline."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                return False
            fh.seek(-1, os.SEEK_END)
            return fh.read(1) != b"\n"
    except OSError:
        return False


def append(run_id: str, event: str, *, state_dir: Path | None = None,
           now: float | None = None, **fields) -> bool:
    """Append one event to *run_id*'s journal. Returns whether it was written.

    Never raises: a journal that cannot be written (a full disk, a read-only
    folder) must not stop the queue running the job it describes. The failure
    is logged instead.
    """
    record = {"ts": time.time() if now is None else now, "event": event, **fields}
    try:
        path = journal_path(run_id, state_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        torn = _ends_mid_line(path)
        with open(path, "a", encoding="utf-8") as fh:
            # A crash can leave the last line without its newline; continuing
            # it would make this event part of an unreadable line too.
            fh.write(("\n" if torn else "") + line)
            fh.flush()
            os.fsync(fh.fileno())
        return True
    except (OSError, ValueError, TypeError) as exc:
        _LOG.warning("Could not write to the journal of recipe run %s (%s).", run_id, exc)
        return False


def read_events(run_id: str, state_dir: Path | None = None) -> list[dict]:
    """Every event in *run_id*'s journal, oldest first.

    A line that does not parse — the last one, cut off by a crash — is
    skipped; so is a line that is not an object. A missing journal is no
    events.
    """
    try:
        path = journal_path(run_id, state_dir)
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []
    events: list[dict] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and "event" in record:
            events.append(record)
    return events


def _clock(ts: object) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except (TypeError, ValueError, OverflowError):
        return "?"


def describe_event(record: dict, labels: dict[str, str] | None = None) -> str:
    """One line a person reads: the time, the step, what happened."""
    labels = labels or {}
    event = str(record.get("event", ""))
    step = record.get("step")
    who = ""
    if isinstance(step, int):
        who = f"step {step + 1}"
        name = labels.get(str(record.get("job", "")))
        if name:
            who += f" ({name})"
        who += ": "
    if event == "applied":
        body = (f"applied “{record.get('name', '')}” to {record.get('input', '')} — "
                f"{len(record.get('jobs') or [])} job(s)")
        notes = record.get("notes") or []
        if notes:
            body += ". " + " ".join(str(n) for n in notes)
    elif event == "started":
        inputs = record.get("inputs") or {}
        body = "started" + (" on " + ", ".join(f"{v}" for v in inputs.values()) if inputs else "")
    elif event == "finished":
        outcome = {True: "succeeded", False: "failed"}.get(record.get("ok"), "ended")
        if record.get("cancelled"):
            outcome = "cancelled"
        body = outcome
        if record.get("message"):
            body += f" — {record['message']}"
        wrote = record.get("wrote")
        if wrote:
            body += f" (wrote {wrote})"
    else:
        body = event
        if record.get("message"):
            body += f" — {record['message']}"
    return f"{_clock(record.get('ts'))}  {who}{body}"


def render(run_id: str, state_dir: Path | None = None,
           labels: dict[str, str] | None = None) -> str:
    """The whole journal as text, one event per line."""
    events = read_events(run_id, state_dir)
    if not events:
        return "No events recorded for this recipe run."
    return "\n".join(describe_event(e, labels) for e in events)


# ── what a run's journal adds up to ──────────────────────────────────────────

#: What each event leaves a step's state as. A status line leaves it as it was.
_STEP_STATE = {"started": "running", "suspended": "paused", "interrupted": "interrupted",
               "retried": "waiting", "removed": "removed"}


@dataclass
class RunSummary:
    """One recipe application, as its journal tells it."""

    run: str
    name: str = ""
    input: str = ""
    applied_at: float | None = None
    last_at: float | None = None
    #: One state per step: waiting, running, paused, interrupted, succeeded,
    #: failed, cancelled or removed.
    steps: list[str] = field(default_factory=list)
    #: The folder each step reported when it succeeded, by step.
    wrote: dict[int, str] = field(default_factory=dict)

    @property
    def done(self) -> int:
        return sum(1 for s in self.steps if s == "succeeded")

    @property
    def status(self) -> str:
        """The run in one word, worst first: a failure is what needs reading."""
        for state in ("failed", "cancelled", "interrupted", "running", "paused"):
            if state in self.steps:
                return state
        live = [s for s in self.steps if s != "removed"]
        if live and all(s == "succeeded" for s in live):
            return "done"
        return "waiting"

    def results(self) -> list[str]:
        """The folders the run's steps wrote, last step first."""
        return [self.wrote[i] for i in sorted(self.wrote, reverse=True)]


def summarise(run_id: str, state_dir: Path | None = None) -> RunSummary | None:
    """Add *run_id*'s journal up; ``None`` when it has no ``applied`` event."""
    events = read_events(run_id, state_dir)
    applied = next((e for e in events if e.get("event") == "applied"), None)
    if applied is None:
        return None
    summary = RunSummary(run=run_id, name=str(applied.get("name") or ""),
                         input=str(applied.get("input") or ""),
                         applied_at=_number(applied.get("ts")),
                         steps=["waiting"] * len(applied.get("jobs") or []))
    for event in events:
        summary.last_at = _number(event.get("ts")) or summary.last_at
        step = event.get("step")
        if not isinstance(step, int) or not 0 <= step < len(summary.steps):
            continue
        kind = event.get("event")
        if kind == "finished":
            ok = event.get("ok") is True
            summary.steps[step] = ("succeeded" if ok else
                                   "cancelled" if event.get("cancelled") else "failed")
            if ok and event.get("wrote"):
                summary.wrote[step] = str(event["wrote"])
            elif not ok:
                summary.wrote.pop(step, None)
        elif kind in _STEP_STATE:
            summary.steps[step] = _STEP_STATE[kind]
            if kind == "retried":
                summary.wrote.pop(step, None)
    return summary


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def list_runs(state_dir: Path | None = None, name: str | None = None) -> list[RunSummary]:
    """Every recorded run, newest first; with *name*, only that recipe's."""
    folder = runs_dir(state_dir)
    try:
        files = sorted(folder.glob("*.jsonl"))
    except OSError:
        return []
    runs = [s for s in (summarise(f.stem, state_dir) for f in files
                        if _SAFE_ID.match(f.stem)) if s is not None]
    if name is not None:
        runs = [r for r in runs if r.name == name]
    return sorted(runs, key=lambda r: r.applied_at or 0.0, reverse=True)
