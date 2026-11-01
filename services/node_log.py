"""The granted node's log: every line a host's granted node has said in this session, kept on its handle.

A granted node (``services.remote_hold``) lives longer than the window that asked for it. The lines it says — the
request's queue and boot progress, the heartbeat's missed beats, the release — used to reach only the Granted node
panel's one-line readout, which each new line overwrote and which went away with Manage Remotes. A warning that
scrolled past there could not be read again. This keeps them on the node's process-wide handle, so the Granted node
log window in Manage Remotes can show the whole session's history, including what was said while the dialog was
closed.

Qt-free: the handle that writes here lives in ``services/`` and is written to from worker threads.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

#: How many distinct lines are kept per node; older ones are dropped first. A node polled every ten seconds while
#: unreachable repeats one line, which is collapsed (see :meth:`NodeLog.record`), so this is many hours of events.
MAX_ENTRIES = 1000


@dataclass
class NodeLogEntry:
    """One line, and how often it was said in a row."""

    first: float           # wall-clock time it was first said (``time.time()``)
    text: str
    last: float = 0.0      # wall-clock time of the last repeat; equal to *first* while there is none
    count: int = 1

    def render(self) -> str:
        line = f"{_clock(self.first)}  {self.text}"
        if self.count > 1:
            line += f"  (said {self.count} times in a row, last at {_clock(self.last)})"
        return line


def _clock(t: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t))


class NodeLog:
    """A host's granted-node lines, thread-safe. Consecutive identical lines are kept as one entry with a count, so a
    host that cannot be reached on every poll does not push everything else out."""

    def __init__(self, *, now: Callable[[], float] = time.time, max_entries: int = MAX_ENTRIES) -> None:
        self._now = now
        self._entries: deque[NodeLogEntry] = deque(maxlen=max_entries)
        self._lock = threading.Lock()

    def record(self, text: str) -> None:
        """Keep *text* (surrounding whitespace removed; an empty line is not kept)."""
        text = text.strip()
        if not text:
            return
        t = self._now()
        with self._lock:
            last = self._entries[-1] if self._entries else None
            if last is not None and last.text == text:
                last.count += 1
                last.last = t
            else:
                self._entries.append(NodeLogEntry(first=t, text=text, last=t))

    def entries(self) -> list[NodeLogEntry]:
        """A copy of the entries, oldest first."""
        with self._lock:
            return [NodeLogEntry(e.first, e.text, e.last, e.count) for e in self._entries]

    def render(self) -> str:
        """The whole log as text, one entry per line, oldest first."""
        return "\n".join(e.render() for e in self.entries())
