"""The dataset's provenance log: an append-only history of what was done to it.

Every other record the app keeps about a dataset describes its *current* state.
``pipeline_info.json`` is rewritten by every run, ``run_report`` and
``run_check`` describe the last run only, the run journal
(``services/run_journal.py``) is replaced when the next run opens, and
``persistence.json`` holds the gates, ratios and sample definitions as they
stand now. None of them can answer "why does this plate's number differ from
the figure I exported in March", because none of them keeps a sequence.

This module is that sequence. It is one file, ``provenance.jsonl``, in the
directory that holds ``pipeline_info.json``, with one JSON object per line.

Four rules define it.

**Append-only.** Nothing in the application rewrites, truncates or "repairs"
the file. A line is added with a single ``O_APPEND`` write, so the bytes
already on disk are never at risk from a later write.

**Evidence, never authority.** No decision anywhere in the application reads
this file. If it is deleted, nothing about the dataset changes; the run journal
states the same rule about itself for the same reason. That is what makes it
safe to write from several places without a lock: the worst a lost or torn
line can do is make the history incomplete, and the reader says so.

**A damaged line is counted, not fatal.** A process killed mid-append leaves a
partial last line. On a network volume, or on Windows, two processes appending
at once can interleave. :func:`read_events` therefore parses line by line,
skips what it cannot parse, and reports how many it skipped
(:attr:`ProvenanceRead.unreadable`) — an incomplete history must not read as a
complete one, and one bad line must not hide the hundreds before it. A line
written by a newer build (a ``schema_version`` this one does not know) is
counted separately as :attr:`ProvenanceRead.foreign` rather than guessed at.

**A closed roster of events.** :data:`EVENT_FIELDS` names every event type and
the detail keys it must carry. :func:`append_event` refuses an unknown type or a
missing key with ``ValueError``, the way ``run_journal.open_run`` refuses an
unknown mode: a history whose entries each spell themselves differently cannot
be read back by anything. A refusal is a programming error, found by the test
that drives the emitting path. An I/O failure is not: it is logged at WARNING
and answered ``False``, because a save, a run or a repair must never fail
because its history could not be written.

Qt-free and stdlib-only. It sits in ``util/`` because its writers span four
layers — the Analyze run (``services``), Review's persistence, and the Datasets
tools — and only a root package below all of them holds every user.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_LOG = logging.getLogger("allwell.provenance")

#: The file's name, in the directory that holds ``pipeline_info.json``.
LOG_FILENAME = "provenance.jsonl"

#: Bumped when a line's shape changes incompatibly. A reader meeting a higher
#: number counts the line as foreign rather than misreading it.
SCHEMA_VERSION = 1

#: Who wrote an event. Spelled once, here, for the same reason the event types
#: are: a reader groups and words entries by these.
SOURCES = ("analyze", "review", "datasets", "report")

#: Every event type, and the ``detail`` keys each one must carry. A key may be
#: ``None`` when its value is genuinely unknown — absent is not the same as
#: unknown, and the roster requires the key so that difference survives.
EVENT_FIELDS: dict[str, tuple[str, ...]] = {
    # An Analyze run began: how it was launched and what it was told to do.
    "run_started": ("mode", "input_dir", "argv", "scope"),
    # The same run ended: the run journal's outcome, and the run_check summary
    # the pipeline left in pipeline_info.json (None when it left none).
    "run_finished": ("mode", "outcome", "run_check"),
    # A piece of Review's analysis state reached disk with a different value.
    # ``before`` / ``after`` are block digests (see block_digest), never the
    # blocks themselves: the history says *that* a gate moved, and the
    # persistence files say where it is now.
    "analysis_changed": ("domain", "before", "after"),
    # A reset scope was cleared from the Reset dialog.
    "reset": ("scopes",),
    # A Datasets tool wrote this dataset from one or more others.
    "derived_from": ("tool", "sources"),
    # Validate & Repair changed this dataset's metadata.
    "repaired": ("actions",),
    # Analysis was copied into this dataset from another one. Each block it
    # wrote is also an ``analysis_changed``; this line says where they came
    # from and what did not copy.
    "recipe_applied": ("source", "scopes", "failed"),
    # The headless report drew this dataset's saved plots into a folder.
    "report_generated": ("output", "status", "drawn", "not_drawn"),
}


def log_path(directory: Path | str) -> Path:
    """The log file inside *directory* (the folder holding pipeline_info.json)."""
    return Path(directory) / LOG_FILENAME


def block_digest(block) -> str | None:
    """A short stable fingerprint of a JSON-able block, or ``None`` for no block.

    ``None`` in, ``None`` out: a block that does not exist has no digest, and
    writing the digest of an empty dict in its place would make "never set"
    and "set to nothing" indistinguishable in the history.
    """
    if block is None:
        return None
    blob = json.dumps(block, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.blake2b(blob.encode("utf-8"), digest_size=8).hexdigest()


def _app_version() -> str | None:
    # Imported lazily: ``version.py`` is the single source of the number, and a
    # caller that cannot see it (a stripped-down host) records None, which reads
    # as "unknown" rather than as a version that never existed.
    try:
        from version import __version__
    except ImportError:
        _LOG.debug("version.py not importable; provenance line records no version")
        return None
    return str(__version__)


def _validated(event: str, source: str, detail: dict) -> None:
    if event not in EVENT_FIELDS:
        raise ValueError(
            f"{event!r} is not a provenance event; expected one of "
            f"{', '.join(EVENT_FIELDS)}")
    if source not in SOURCES:
        raise ValueError(
            f"{source!r} is not a provenance source; expected one of "
            f"{', '.join(SOURCES)}")
    missing = [k for k in EVENT_FIELDS[event] if k not in detail]
    if missing:
        raise ValueError(
            f"provenance event {event!r} is missing detail key(s): "
            f"{', '.join(missing)}")


def format_line(event: str, *, source: str, detail: dict,
                now: float | None = None) -> bytes:
    """The bytes :func:`append_event` writes, newline included.

    Separate so a test can assert the exact line without touching a disk.
    """
    _validated(event, source, detail)
    ts = time.time() if now is None else float(now)
    record = {
        "schema_version": SCHEMA_VERSION,
        "ts": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds"),
        "event": event,
        "source": source,
        "app_version": _app_version(),
        "detail": detail,
    }
    # ``default=str`` so a Path or a numpy scalar in a detail is written as
    # text rather than raising mid-save; ``ensure_ascii`` keeps every line
    # pure ASCII, so no platform's text decoding can split one.
    text = json.dumps(record, sort_keys=True, default=str, ensure_ascii=True,
                      separators=(",", ":"))
    return (text + "\n").encode("ascii")


def append_event(directory: Path | str | None, event: str, *, source: str,
                 detail: dict, now: float | None = None) -> bool:
    """Append one event to *directory*'s log. ``True`` when it reached disk.

    *directory* of ``None`` is a caller with no dataset open, and answers
    ``False`` without writing. Raises ``ValueError`` for an event the roster
    does not know or a detail missing a required key; every I/O failure is
    logged at WARNING and answered ``False``.
    """
    line = format_line(event, source=source, detail=detail, now=now)
    if directory is None:
        return False
    path = log_path(directory)
    # ``O_BINARY`` matters on Windows, where a low-level descriptor otherwise
    # opens in text mode and rewrites every "\n" as "\r\n".
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags, 0o644)
    except OSError as exc:
        _LOG.warning("could not open the provenance log %s (%s); the %s event "
                     "was not recorded", path, exc, event)
        return False
    try:
        view = memoryview(line)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    except OSError as exc:
        _LOG.warning("could not append to the provenance log %s (%s); the %s "
                     "event may be missing or partial", path, exc, event)
        return False
    finally:
        os.close(fd)
    return True


@dataclass(frozen=True)
class ProvenanceRead:
    """What :func:`read_events` found.

    ``events`` are the lines this build understands, oldest first.
    ``unreadable`` counts lines that could not be parsed — a torn append, an
    interleaved write, a hand edit. ``foreign`` counts well-formed lines from a
    newer schema. ``missing`` is True when there is no log at all, which is a
    different statement from an empty one.
    """

    events: list[dict] = field(default_factory=list)
    unreadable: int = 0
    foreign: int = 0
    missing: bool = False

    @property
    def complete(self) -> bool:
        """Whether every line on disk is represented in ``events``."""
        return not self.missing and self.unreadable == 0 and self.foreign == 0


def read_events(directory: Path | str) -> ProvenanceRead:
    """Read *directory*'s log, line by line, never raising for its contents.

    An ``OSError`` reading the file is reported as ``missing`` with a WARNING,
    because a reader cannot tell a vanished file from an unreadable one and
    both mean the history is unavailable.
    """
    path = log_path(directory)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return ProvenanceRead(missing=True)
    except OSError as exc:
        _LOG.warning("could not read the provenance log %s (%s)", path, exc)
        return ProvenanceRead(missing=True)
    events: list[dict] = []
    unreadable = 0
    foreign = 0
    for chunk in raw.split(b"\n"):
        if not chunk.strip():
            continue
        try:
            record = json.loads(chunk.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            unreadable += 1
            continue
        if not isinstance(record, dict) or not isinstance(record.get("event"), str):
            unreadable += 1
            continue
        version = record.get("schema_version")
        if not isinstance(version, int):
            unreadable += 1
            continue
        if version > SCHEMA_VERSION:
            foreign += 1
            continue
        events.append(record)
    return ProvenanceRead(events=events, unreadable=unreadable, foreign=foreign)
