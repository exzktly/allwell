"""Process-wide LRU cache of open :class:`zipfile.ZipFile` handles.

Used by ``preview_controller.read_member_bytes``,
``preview_controller.scan_zip_members``, ``smfish_worker._process_well``,
and ``image_table_controller`` to share a single open file handle across
all member reads on the same well zip.

Why: an Image Table at e.g. 4 × 6 cells with two channel variants performs
~50 individual zip-member reads. The previous code opened the zip (parsing
the central directory) once per read — multi-MB zips on a network mount
turned that into a measurable lag on every Generate / auto-LUT / export
cycle.

Thread safety
-------------
``zipfile.ZipFile.read(member)`` is not concurrency-safe — two threads
calling ``.read()`` on the same handle corrupt each other. The cache
guards each handle with a per-handle :class:`threading.Lock`, so callers
can use the same handle from multiple threads as long as they go through
``with_handle()``. Concurrent reads on *different* handles run in
parallel.
"""

from __future__ import annotations

import logging
import threading
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from collections import OrderedDict as TOrderedDict
from collections.abc import Iterator
from collections import OrderedDict
from core.safe import swallowed


_logger = logging.getLogger("well_viewer.data.zipfile_cache")


# Tunable: a 96-well plate has at most 96 zips; 32 covers the working set
# for any single Image Table / heatmap render comfortably without growing
# unboundedly when the user navigates through datasets.
DEFAULT_MAX_OPEN = 32


#: Total wall-clock a single close sweep may spend waiting for readers, in
#: seconds. Shared across every victim in the sweep rather than allowed per
#: handle: ``invalidate()`` runs on the GUI thread and closes up to
#: ``DEFAULT_MAX_OPEN`` entries in one pass, so a per-handle budget is a
#: window freeze of that budget times the cap.
CLOSE_WAIT_BUDGET_S = 2.0


class _Entry:
    __slots__ = ("zf", "lock", "mtime_ns", "closed")

    def __init__(self, zf: zipfile.ZipFile, mtime_ns: int) -> None:
        self.zf = zf
        self.lock = threading.Lock()
        self.mtime_ns = mtime_ns
        #: Set under ``lock`` immediately before ``zf.close()``. A reader that
        #: took the entry out of the cache before the close began and reaches
        #: ``lock`` after it finished sees this and re-opens instead of reading
        #: a closed handle.
        self.closed = False


class ZipFileCache:
    """Bounded LRU of open ``ZipFile`` handles, keyed on resolved path."""

    def __init__(self, max_open: int = DEFAULT_MAX_OPEN) -> None:
        self._max_open = int(max_open)
        self._entries: TOrderedDict[Path, _Entry] = OrderedDict()
        # Mutex for cache-level ops (insertion / eviction). Per-handle
        # operations use the entry's own lock.
        self._mutex = threading.Lock()

    def _stat_mtime_ns(self, path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return 0

    @staticmethod
    def _close_entry(entry: "_Entry | None", deadline: float | None = None) -> None:
        """Close a handle, waiting for any in-flight read to finish first.

        ``with_handle`` yields the ``ZipFile`` while holding the entry's own
        lock, because ZipFile is not safe for concurrent reads. Closing the
        handle without taking that lock could land in the middle of one of
        those reads — and the readers are off-thread (the Cell Browser loader,
        the smFISH worker) — surfacing as a "seek of closed file" from a thread
        that had done nothing wrong.

        *deadline* is an absolute :func:`time.monotonic` instant shared by
        every victim of one close sweep. Waiting for the reader is what makes
        the close safe, but ``invalidate()`` runs on the GUI thread from
        ``load_directory`` and closes up to ``DEFAULT_MAX_OPEN`` entries in one
        pass, so a budget spent per handle would be a window freeze of that
        budget times the cap. Past the deadline the remaining handles are
        acquired without waiting at all.

        A handle whose reader still holds the lock when the budget runs out is
        left to the garbage collector, which closes it once that reader is
        finished with it: a descriptor held a moment longer is a smaller harm
        than a frozen window, and the entry is out of the cache either way.

        **Never call this while holding ``self._mutex``.** A caller inside
        ``with_handle`` may reach back into the cache for another path and want
        the mutex, so taking an entry lock under the mutex inverts the order
        and can deadlock. Every call site here collects victims under the mutex
        and closes them after releasing it.
        """
        if entry is None:
            return
        remaining = -1.0 if deadline is None else deadline - time.monotonic()
        if remaining > 0:
            got = entry.lock.acquire(timeout=remaining)
        else:
            got = entry.lock.acquire(blocking=False)
        try:
            if not got:
                _logger.debug(
                    "zip cache: handle still in use at close; leaving it to be "
                    "collected once the reader finishes")
                return
            # Flagged before the close and under the lock, so the next reader
            # to take this lock knows the handle behind it is dead.
            entry.closed = True
            entry.zf.close()
        except Exception:
            swallowed()  # was: pass (silent except)
        finally:
            if got:
                entry.lock.release()

    def _close_all(self, victims: "list[_Entry]") -> None:
        """Close a sweep of victims against one shared wait budget."""
        deadline = time.monotonic() + CLOSE_WAIT_BUDGET_S
        for victim in victims:
            self._close_entry(victim, deadline)

    def _evict_locked(self) -> list["_Entry"]:
        """Pop entries over the cap and return them for the caller to close."""
        victims: list[_Entry] = []
        while len(self._entries) > self._max_open:
            _, victim = self._entries.popitem(last=False)
            victims.append(victim)
        return victims

    def _open_entry(self, path: Path) -> _Entry | None:
        try:
            zf = zipfile.ZipFile(path, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            _logger.warning("Cannot open zip %s: %s", path, exc)
            return None
        return _Entry(zf, self._stat_mtime_ns(path))

    def _get_or_open(self, path: Path) -> _Entry | None:
        """Return a cached entry for *path*, opening a fresh handle when
        the file has been modified since the cached one was opened.
        """
        path = Path(path).resolve()
        current_mtime = self._stat_mtime_ns(path)
        with self._mutex:
            entry = self._entries.get(path)
            if entry is not None and entry.mtime_ns == current_mtime:
                # Move to MRU end without rebuilding the dict.
                self._entries.move_to_end(path, last=True)
                return entry
            # Either fresh open or mtime changed (pipeline re-ran).
            # Close the stale handle outside the mutex so we don't
            # serialise IO behind it.
            stale = entry
        self._close_all([stale] if stale is not None else [])
        new_entry = self._open_entry(path)
        if new_entry is None:
            return None
        with self._mutex:
            self._entries[path] = new_entry
            self._entries.move_to_end(path, last=True)
            evicted = self._evict_locked()
        self._close_all(evicted)
        return new_entry

    @contextmanager
    def with_handle(self, path: Path) -> Iterator[zipfile.ZipFile | None]:
        """Yield a thread-safe, exclusively-held ZipFile for *path*.

        Yields ``None`` when the zip can't be opened (caller should
        treat that as "file missing / corrupt"). Holds the per-handle
        lock for the body so concurrent reads on the same zip are
        serialised (required by ZipFile).
        """
        entry = self._get_or_open(path)
        if entry is None:
            yield None
            return
        with entry.lock:
            if not entry.closed:
                yield entry.zf
                return
        # The handle was closed between ``_get_or_open`` returning it and this
        # thread taking its lock — a dataset swap or an eviction landed in that
        # window. Ask for the path again: the cache has either re-opened it or
        # can, and the retry is bounded because the entry handed back is a
        # fresh one that this thread now holds the lock on.
        entry = self._get_or_open(path)
        if entry is None:
            yield None
            return
        with entry.lock:
            yield None if entry.closed else entry.zf

    def invalidate(self, path: Path | None = None) -> None:
        """Drop one (or all) cached handles. Call from load_controller on
        dataset swap so a new dataset's writes aren't read through a
        stale handle from the previous one."""
        with self._mutex:
            if path is None:
                victims = list(self._entries.values())
                self._entries.clear()
            else:
                resolved = Path(path).resolve()
                victim = self._entries.pop(resolved, None)
                victims = [victim] if victim is not None else []
        self._close_all(victims)


# Module-level singleton. Process-wide; bounded so we never accumulate
# more open file descriptors than DEFAULT_MAX_OPEN.
GLOBAL_CACHE = ZipFileCache()


def with_zipfile(path: Path):
    """Convenience: equivalent to ``GLOBAL_CACHE.with_handle(path)``.

    Use inside a ``with`` block::

        with with_zipfile(zip_path) as zf:
            if zf is None:
                return None
            data = zf.read(member)
    """
    return GLOBAL_CACHE.with_handle(path)


def invalidate(path: Path | None = None) -> None:
    """Drop cached handle(s). See :meth:`ZipFileCache.invalidate`."""
    GLOBAL_CACHE.invalidate(path)
