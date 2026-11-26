"""When has a folder stopped receiving files? The Qt-free half of Watch folder.

A watch job waits on a folder that a microscope, or a copy from another
machine, is still writing to, and succeeds once the writing has stopped. Two
questions decide that, and both are answered here so they can be tested with
no event loop and no clock:

* **What is in the folder right now** — :func:`snapshot`, every file below it
  by relative path with its size and modification time. Polling rather than
  asking the operating system for change notifications is deliberate: the
  folders this is for are usually on a network share or a USB drive, where
  notifications are unreliable or absent, and a watch that silently never fires
  is worse than one that looks every few seconds.
* **Has it been quiet long enough** — :class:`SettleTracker`, fed one snapshot
  per poll with the time it was taken. The folder is *settled* once it holds at
  least one file and nothing has been added, removed, grown or touched for the
  quiet period. With ``require_change`` (the default) the folder must also have
  changed at least once since the watch began, so a watch set up before an
  acquisition does not fire on the folder's previous contents; without it, a
  folder that is already complete and quiet settles after one quiet period.

Files that operating systems and file browsers leave behind (``.DS_Store``,
AppleDouble ``._`` sidecars, ``Thumbs.db``, Office ``~$`` lock files) and
anything inside a hidden folder are left out of the snapshot. Finder writes
``.DS_Store`` whenever someone opens the folder to look at it, and a watch that
restarts its countdown every time a person checks on the acquisition would
never settle while anybody was watching.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from datasets.core.watched_source import is_ignored

#: The default quiet period, in minutes, offered on the page.
DEFAULT_QUIET_MINUTES = 5.0
#: How often the folder is looked at, in seconds, unless a scan of it takes
#: longer than half of this — then the interval stretches to twice the scan.
DEFAULT_POLL_SECONDS = 5.0

#: A file's identity for change detection: its size and modification time.
FileStamp = tuple[int, int]
#: Relative path → stamp. ``None`` stands for a folder that does not exist.
Snapshot = dict[str, FileStamp]

WAITING = "waiting"
SETTLING = "settling"
SETTLED = "settled"
MISSING = "missing"


def snapshot(folder: str | os.PathLike) -> Snapshot | None:
    """Every data file below *folder*, as relative path → (size, mtime_ns).

    ``None`` when *folder* does not exist or is not a directory, which is a
    different answer from an empty folder: a watch can be set up on a folder
    the acquisition software has not created yet. A file that disappears
    between being listed and being stat-ed is left out, since the next poll
    will see the folder as it settles.
    """
    root = Path(folder)
    if not root.is_dir():
        return None
    found: Snapshot = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in filenames:
            if is_ignored(name):
                continue
            path = Path(dirpath) / name
            try:
                st = path.stat()
            except OSError:
                continue
            found[path.relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns)
    return found


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


@dataclass(frozen=True)
class WatchStatus:
    """What one poll found, and what the watch is waiting for next."""

    phase: str
    files: int
    total_bytes: int
    #: Seconds since the last change seen (or since the watch began).
    quiet_for: float
    quiet_needed: float
    #: How many polls saw the folder differ from the poll before.
    changes: int

    @property
    def remaining(self) -> float:
        return max(0.0, self.quiet_needed - self.quiet_for)

    def sentence(self) -> str:
        """The status line a person reads on the page and the queue row."""
        count = f"{self.files} file{'s' if self.files != 1 else ''}"
        if self.phase == MISSING:
            return "Waiting for the folder to be created…"
        if self.phase == WAITING:
            return f"Waiting for new files ({count} there now)…"
        if self.phase == SETTLED:
            return (f"Settled — {count}, no change for "
                    f"{_format_duration(self.quiet_for)}.")
        if self.files == 0:
            return "The folder changed and is now empty — waiting for files…"
        return (f"Receiving files — {count}, quiet for "
                f"{_format_duration(self.quiet_for)} of "
                f"{_format_duration(self.quiet_needed)}.")


class SettleTracker:
    """Decides, one snapshot at a time, whether a folder has settled.

    *quiet_seconds* is the length of the quiet period. Feed :meth:`observe`
    each snapshot with a monotonic time; once it reports :data:`SETTLED` it
    keeps reporting it, since the watch is over.
    """

    def __init__(self, quiet_seconds: float, *, require_change: bool = True) -> None:
        self.quiet_seconds = max(0.0, float(quiet_seconds))
        self.require_change = bool(require_change)
        self._previous: Snapshot | None = None
        self._started = False
        self._last_change = 0.0
        self._changes = 0
        self._settled: WatchStatus | None = None

    def observe(self, snap: Snapshot | None, now: float) -> WatchStatus:
        if self._settled is not None:
            return self._settled
        if not self._started:
            self._started = True
            self._last_change = now
        elif snap != self._previous:
            self._changes += 1
            self._last_change = now
        self._previous = None if snap is None else dict(snap)

        files = len(snap or {})
        total = sum(size for size, _mtime in (snap or {}).values())
        quiet_for = now - self._last_change
        if snap is None:
            phase = MISSING
        elif self.require_change and self._changes == 0:
            phase = WAITING
        elif files and quiet_for >= self.quiet_seconds:
            phase = SETTLED
        else:
            phase = SETTLING
        status = WatchStatus(phase=phase, files=files, total_bytes=total,
                             quiet_for=quiet_for, quiet_needed=self.quiet_seconds,
                             changes=self._changes)
        if phase == SETTLED:
            self._settled = status
        return status


def poll_interval(base_seconds: float, last_scan_seconds: float) -> float:
    """How long to wait before the next look at the folder.

    A scan of a large tree on a slow share can take seconds; polling faster
    than twice the scan time would keep a thread busy listing the folder all
    the time, so the interval stretches to fit.
    """
    return max(float(base_seconds), 2.0 * float(last_scan_seconds))


def spec_quiet_seconds(spec: dict) -> float:
    """The quiet period a queued watch job asks for, in seconds."""
    try:
        minutes = float(spec.get("quiet_minutes", DEFAULT_QUIET_MINUTES))
    except (TypeError, ValueError):
        minutes = DEFAULT_QUIET_MINUTES
    return max(0.0, minutes) * 60.0
