"""The per-well row cache, bounded by a memory budget.

A dataset's measurement frames used to be held in a plain ``dict`` with no limit,
one ``DataFrame`` per well, filled for every well as the dataset opened. A
well of 1.1M cells (a 300 MB CSV) holds about 200 MiB once parsed, so a 96-well
plate of them asked for about 19 GB and the open died with a ``MemoryError``
(``_Docs/Markdowns/LARGE_DATASET_TRACKER.md``, E1–E3).

:class:`RowCache` is a mapping with the same interface that keeps the frames it
holds under a byte budget by evicting the least recently used well. Evicting is
safe because a well is cheap to bring back and comes back identical:

* **Cheap.** Every frame read from a CSV is written to the Parquet cache
  (:mod:`review.data.parquet_cache`), and re-reading a well from it took 0.22 s
  for 1.1M rows against 15.3 s for the CSV parse (tracker E5).
* **Identical.** A frame enters the cache only through
  ``data_loading.load_and_project``, which runs every registered projector over
  it — curation, gating, QC, crop, field quality, focus, field exclusions — from
  their persistent state. So a reloaded well carries exactly the verdicts the
  evicted one did, which is also why eviction does not bump
  ``DatasetState.rows_version``: the memoized per-well aggregates keyed on it
  remain true of the reloaded rows. The tracker's M2 row is what made that
  hold for gating, the one writer that was not a projector.

What eviction changes is what *iterating* the cache means: it yields the wells
that happen to be held, not the plate. Code that wants every well asks for each
one through ``get_rows``; ``tests/test_row_cache_readers.py`` holds the roster
of sites allowed to iterate the cache, each with the reason it only means the
held frames.

Thread-safe, because the gating worker reads rows off the GUI thread and a read
now reorders the cache. Iteration works on a snapshot, so a walk on one thread
cannot be broken by an insert or eviction on another.

The budget defaults to :data:`DEFAULT_SHARE` of physical memory and is set with
the ``ALLWELL_ROW_CACHE_MB`` environment variable. At least one frame is always
held, however small the budget: a well larger than the budget is still
readable, it is just never kept alongside another.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections import OrderedDict
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager

import pandas as pd

_logger = logging.getLogger("review")

#: Share of physical memory the row cache may hold by default. The rest of the
#: app — figures, image tiles, the Qt object graph, a pooled replicate group
#: being drawn — needs the remainder, and a plate small enough to fit (the
#: common case) is held whole either way.
DEFAULT_SHARE = 0.25
#: The budget never defaults below this, so a machine whose memory cannot be
#: read still holds a useful number of ordinary wells.
MIN_DEFAULT_BYTES = 512 * 2**20
#: Environment override, in MiB.
BUDGET_ENV = "ALLWELL_ROW_CACHE_MB"


def physical_memory_bytes() -> int | None:
    """The machine's physical memory, or ``None`` when it cannot be read."""
    try:
        if sys.platform == "win32":
            import ctypes

            class _MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatus()
            status.dwLength = ctypes.sizeof(_MemoryStatus)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return int(status.ullTotalPhys)
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return None


def default_budget_bytes() -> int:
    """The budget a new cache gets: the environment override, else a share of memory."""
    raw = os.environ.get(BUDGET_ENV, "").strip()
    if raw:
        try:
            mib = float(raw)
        except ValueError:
            _logger.warning("%s=%r is not a number of MiB; using the default row-cache budget",
                            BUDGET_ENV, raw)
        else:
            if mib > 0:
                return int(mib * 2**20)
            _logger.warning("%s=%r is not positive; using the default row-cache budget", BUDGET_ENV, raw)
    total = physical_memory_bytes()
    if not total:
        return MIN_DEFAULT_BYTES
    return max(MIN_DEFAULT_BYTES, int(total * DEFAULT_SHARE))


def frame_bytes(frame) -> int:
    """What *frame* holds, counting the strings inside object columns."""
    if not isinstance(frame, pd.DataFrame):
        return sys.getsizeof(frame)
    return int(frame.memory_usage(deep=True, index=True).sum())


def _mib(n: int) -> str:
    return f"{n / 2**20:,.0f} MiB"


class RowCache(MutableMapping):
    """Well label → measurement frame, least recently used evicted past a byte budget."""

    def __init__(self, budget_bytes: int | None = None) -> None:
        self._frames: OrderedDict[str, object] = OrderedDict()
        self._sizes: dict[str, int] = {}
        self._held = 0
        self._lock = threading.RLock()
        self.budget_bytes = int(budget_bytes) if budget_bytes is not None else default_budget_bytes()
        #: Wells evicted since the cache was last cleared — what the status and
        #: a memory error report, so "the plate does not fit" is visible.
        self.evictions = 0
        self._reported_full = False
        self._pins: dict[str, int] = {}

    # ── the mapping ──────────────────────────────────────────────────────────

    def __getitem__(self, label: str):
        with self._lock:
            frame = self._frames[label]
            self._frames.move_to_end(label)
            return frame

    def __setitem__(self, label: str, frame) -> None:
        size = frame_bytes(frame)
        with self._lock:
            if label in self._frames:
                self._held -= self._sizes.get(label, 0)
            self._frames[label] = frame
            self._frames.move_to_end(label)
            self._sizes[label] = size
            self._held += size
            self._evict(keep=label)

    def __delitem__(self, label: str) -> None:
        with self._lock:
            del self._frames[label]
            self._held -= self._sizes.pop(label, 0)

    def __contains__(self, label: object) -> bool:
        with self._lock:
            return label in self._frames

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            return iter(list(self._frames))

    def __len__(self) -> int:
        with self._lock:
            return len(self._frames)

    def __bool__(self) -> bool:
        return len(self) > 0

    def keys(self) -> list[str]:  # type: ignore[override]
        with self._lock:
            return list(self._frames)

    def values(self) -> list:  # type: ignore[override]
        """The held frames, as a snapshot. Iterating does not count as use."""
        with self._lock:
            return list(self._frames.values())

    def items(self) -> list[tuple[str, object]]:  # type: ignore[override]
        """The held ``(label, frame)`` pairs, as a snapshot. Iterating does not count as use."""
        with self._lock:
            return list(self._frames.items())

    def clear(self) -> None:
        # Pins are left alone: a block that pinned a label unpins it on exit.
        with self._lock:
            self._frames.clear()
            self._sizes.clear()
            self._held = 0
            self.evictions = 0
            self._reported_full = False

    # ── the budget ───────────────────────────────────────────────────────────

    @property
    def held_bytes(self) -> int:
        with self._lock:
            return self._held

    def remeasure(self, label: str) -> None:
        """Re-take *label*'s size after its frame grew in place (projected columns)."""
        with self._lock:
            frame = self._frames.get(label)
            if frame is None:
                return
            size = frame_bytes(frame)
            self._held += size - self._sizes.get(label, 0)
            self._sizes[label] = size
            self._evict(keep=label)

    def set_budget(self, budget_bytes: int) -> None:
        with self._lock:
            self.budget_bytes = int(budget_bytes)
            self._evict(keep=None)

    @contextmanager
    def pinned(self, label: str):
        """Hold *label* against eviction for the duration of the block.

        For a frame that is being worked on in place by code that finds it by
        label: the projectors run over a just-loaded well by looking it up in
        the cache, and one that found it evicted meanwhile — by a read on the
        gating worker's thread — would skip its verdict and leave the frame
        looking fully included.
        """
        with self._lock:
            self._pins[label] = self._pins.get(label, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                left = self._pins.get(label, 0) - 1
                if left > 0:
                    self._pins[label] = left
                else:
                    self._pins.pop(label, None)
                self._evict(keep=None)

    def describe(self) -> str:
        """One line naming what is held against the budget."""
        with self._lock:
            text = (f"row cache: {len(self._frames)} well(s) held, {_mib(self._held)} of a "
                    f"{_mib(self.budget_bytes)} budget")
            if self.evictions:
                text += (f"; {self.evictions} well(s) evicted so far and re-read from disk when next "
                         f"needed (set {BUDGET_ENV} to change the budget)")
            return text

    def _evict(self, keep: str | None) -> None:
        while self._held > self.budget_bytes:
            # Oldest first, never the most recently used frame (so one well is
            # always held, however small the budget), the frame just stored, or
            # one pinned while it is worked on.
            newest = next(reversed(self._frames), None)
            victim = next((label for label in self._frames
                           if label not in (keep, newest) and label not in self._pins), None)
            if victim is None:
                break
            del self._frames[victim]
            self._held -= self._sizes.pop(victim, 0)
            self.evictions += 1
        if self.evictions and not self._reported_full:
            self._reported_full = True
            _logger.info("The plate does not fit in the row-cache budget; the least recently used "
                         "wells are dropped and re-read from disk when needed — %s", self.describe())
