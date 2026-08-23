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
import zipfile
from contextlib import contextmanager
from pathlib import Path
from collections import OrderedDict as TOrderedDict
from collections.abc import Iterator
from collections import OrderedDict
from well_viewer.core.safe import swallowed


_logger = logging.getLogger("well_viewer.data.zipfile_cache")


# Tunable: a 96-well plate has at most 96 zips; 32 covers the working set
# for any single Image Table / heatmap render comfortably without growing
# unboundedly when the user navigates through datasets.
DEFAULT_MAX_OPEN = 32


class _Entry:
    __slots__ = ("zf", "lock", "mtime_ns")

    def __init__(self, zf: zipfile.ZipFile, mtime_ns: int) -> None:
        self.zf = zf
        self.lock = threading.Lock()
        self.mtime_ns = mtime_ns


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
    def _close_entry(entry: "_Entry | None") -> None:
        """Close a handle, waiting for any in-flight read to finish first.

        ``with_handle`` yields the ``ZipFile`` while holding the entry's own
        lock, because ZipFile is not safe for concurrent reads. Eviction, the
        stale-mtime replacement and ``invalidate`` all closed the handle
        without taking that lock — and the readers are off-thread (the Cell
        Browser loader, the smFISH worker), so a close could land in the middle
        of one and surface as a "seek of closed file" from a thread that had
        done nothing wrong.

        **Never call this while holding ``self._mutex``.** A caller inside
        ``with_handle`` may reach back into the cache for another path and want
        the mutex, so taking an entry lock under the mutex inverts the order
        and can deadlock. Every call site here collects victims under the mutex
        and closes them after releasing it.
        """
        if entry is None:
            return
        # Bounded, not indefinite. Waiting for the reader is what makes the
        # close safe, but ``invalidate()`` runs on the GUI thread from
        # ``load_directory`` and a reader can be part-way through a member
        # decode — so a hard ``with entry.lock`` would stall opening a dataset
        # behind up to 32 of them. On timeout the handle is left to the garbage
        # collector, which closes it once the reader is finished with it: a
        # leaked descriptor for a moment is a smaller harm than a frozen
        # window, and the entry is already out of the cache either way.
        got = entry.lock.acquire(timeout=2.0)
        try:
            if not got:
                _logger.debug(
                    "zip cache: handle still in use at close; leaving it to be "
                    "collected once the reader finishes")
                return
            entry.zf.close()
        except Exception:
            swallowed()  # was: pass (silent except)
        finally:
            if got:
                entry.lock.release()

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
        self._close_entry(stale)
        new_entry = self._open_entry(path)
        if new_entry is None:
            return None
        with self._mutex:
            self._entries[path] = new_entry
            self._entries.move_to_end(path, last=True)
            evicted = self._evict_locked()
        for victim in evicted:
            self._close_entry(victim)
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
            yield entry.zf

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
        for v in victims:
            self._close_entry(v)


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
