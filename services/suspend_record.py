"""What a suspended Analyze run leaves behind so it can be picked up again.

Suspending a run does not need to capture "where we were" — the output directory
already is that record. Every well that finished wrote its CSV and its
``<well>_out.zip``, and ``--incremental`` reads those back as the done-set of
``(fov, timepoint)`` groups, so a resumed run recomputes only what is missing.
What the *app* loses on a restart is not progress but intent: which folders, and
which of the several dozen form fields, produced that half-finished output.

So this module persists exactly one thing — the :class:`services.run_spec.RunSpec`
that was running, plus enough context to describe it back to the user — and
nothing about the run's internal state. The same ``to_json`` the job queue
already uses; no second serialisation format to keep in step with the form.

It lives beside the scratch directory in the user's home rather than in the
output folder, because at startup the app does not yet know which output folder
to look in. One file, holding a list: several suspended runs can coexist (a user
who suspends one plate, starts another, and suspends that too), and each is keyed
by its output directory so resuming the same plate twice replaces its entry
instead of stacking duplicates.

Qt-free and stdlib-only, so the Analyze services layer and any CLI driver can
both read it.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from util.json_write import try_write_json_atomic

RECORD_VERSION = 1


def record_path() -> Path:
    """``~/.allwell/suspended_runs.json`` — beside the default scratch base."""
    return Path.home() / ".allwell" / "suspended_runs.json"


@dataclass(frozen=True)
class SuspendedRun:
    """One suspended run: the spec to restart, and how to describe it."""

    spec: dict                 # RunSpec.to_json()
    input_dir: str
    output_dir: str
    suspended_at: float        # POSIX seconds
    wells_done: int = 0
    wells_total: int = 0
    note: str = ""
    #: Whether the job queue started the run this record describes, and so owns
    #: offering it back. The same question ``RunJournal.queued`` answers, asked
    #: of the other pointer, because a suspend writes *both* — filtering one and
    #: not the other left the Analyze banner offering a plate the queue was
    #: still holding, which is the whole defect. It is the *record* that carries
    #: it rather than the reader inferring it, so a reader that forgets to ask
    #: fails visibly rather than by omission.
    queued: bool = False

    def to_json(self) -> dict:
        return {
            "version": RECORD_VERSION,
            "spec": self.spec,
            "input_dir": self.input_dir,
            "output_dir": self.output_dir,
            "suspended_at": self.suspended_at,
            "wells_done": self.wells_done,
            "wells_total": self.wells_total,
            "note": self.note,
            "queued": self.queued,
        }

    @classmethod
    def from_json(cls, data: dict) -> SuspendedRun | None:
        """``None`` for anything this version cannot use, rather than raising.

        A record written by a future build, or one whose file was truncated by a
        power cut, must not stop the app from starting — the worst outcome of
        dropping one is that the user reruns a plate by hand.
        """
        if not isinstance(data, dict) or data.get("version") != RECORD_VERSION:
            return None
        spec = data.get("spec")
        output_dir = data.get("output_dir")
        if not isinstance(spec, dict) or not output_dir:
            return None
        return cls(
            spec=spec,
            input_dir=str(data.get("input_dir") or ""),
            output_dir=str(output_dir),
            suspended_at=float(data.get("suspended_at") or 0.0),
            wells_done=int(data.get("wells_done") or 0),
            wells_total=int(data.get("wells_total") or 0),
            note=str(data.get("note") or ""),
            queued=bool(data.get("queued")),
        )

    def describe(self) -> str:
        """One line for the banner: which plate, how far it got, when."""
        name = plate_name(self.output_dir)
        if self.wells_total:
            progress = f"{self.wells_done} of {self.wells_total} well(s) done"
        else:
            progress = "progress unknown"
        when = (time.strftime("%d %b %H:%M", time.localtime(self.suspended_at))
                if self.suspended_at else "an earlier session")
        return f"{name} — {progress}, suspended {when}"


def plate_name(output_dir: str) -> str:
    """The name a user would recognise the plate by.

    Every dataset's output folder is ``<plate>/out``, so the folder's own name
    is the word "out" for essentially every run — which says nothing, and says
    it identically for two different plates. That was survivable while the only
    offer on screen was for the folder already in the form; it is not, now that
    a banner can name a plate the user is not looking at. Name the dataset
    instead, falling back to the folder's own name for a layout that does not
    end in ``out/``.
    """
    path = Path(output_dir)
    if path.name.lower() == "out" and path.parent.name:
        return path.parent.name
    return path.name or output_dir


def load_all() -> list[SuspendedRun]:
    """Every usable record, newest first. Never raises."""
    try:
        raw = json.loads(record_path().read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    runs = [r for r in (SuspendedRun.from_json(d) for d in raw) if r is not None]
    runs.sort(key=lambda r: r.suspended_at, reverse=True)
    return runs


def save(run: SuspendedRun) -> bool:
    """Record *run*, replacing any earlier entry for the same output dir.

    Returns whether it was written. Best-effort: a run that cannot record its
    own suspension still suspends — the output directory is the real record, and
    the user can point Analyze at it by hand.
    """
    keep = [r for r in load_all() if not same_dir(r.output_dir, run.output_dir)]
    return _write_all([run, *keep])


def clear(output_dir: str | os.PathLike[str]) -> bool:
    """Drop the record for *output_dir*. Called on resume and on discard.

    Also the last step of a run that completes normally: a finished plate must
    not keep offering to resume itself.
    """
    target = str(output_dir)
    keep = [r for r in load_all() if not same_dir(r.output_dir, target)]
    return _write_all(keep)


def same_dir(a: str, b: str) -> bool:
    """Path equality that survives a trailing slash or a relative spelling."""
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(
            str(Path(b).resolve()))
    except OSError:
        return os.path.normcase(a.rstrip("/\\")) == os.path.normcase(b.rstrip("/\\"))


def _write_all(runs: list[SuspendedRun]) -> bool:
    """Replace the file atomically, or return False having changed nothing.

    The writer is shared with the run journal (``util.json_write``) rather
    than restated here. The copy that used to sit in this function caught
    ``OSError`` alone, and the record carries a ``spec`` the caller handed in —
    so one unserialisable value in it raised a ``TypeError`` out of a function
    whose whole contract is a boolean, past ``_suspend``'s outer handler and
    *before* ``PipelineRunner.suspend()`` was ever reached. The Suspend button
    then did nothing at all and said nothing about it.
    """
    return try_write_json_atomic(record_path(), [r.to_json() for r in runs],
                                what="suspended-run record")
