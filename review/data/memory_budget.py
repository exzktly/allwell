"""How much memory Review's dataset caches may hold, and when they must give it back.

Review holds three caches that grow with the dataset: the row cache (``row_cache.RowCache``, each well's
measurement frame), the population store (``population_store.PopulationStore``, the per-well populations the
statistics are computed from) and the decoded-pixel cache (``image_discovery._DecodedArrayCache``). Each is bounded
by a byte budget, and each used to size that budget on its own, as a share of the machine's physical memory, with no
knowledge of the others: a quarter, an eighth and 15% (at most 2 GiB). On an 8 GB machine that let the three claim
about 4.2 GB between them before Windows, Qt, matplotlib and the cell pool had taken anything, and a size-class edit
on a nanowell plate — which allocates whole-column copies of every held well while it runs — then failed with a
``MemoryError`` and, once the window could no longer get memory to paint itself, ``CreateDIBSection failed``.

Two rules replace that, and both live here so that no cache can drift from them:

**One set of shares.** :data:`SHARES` names each cache's share of physical memory, and :data:`TOTAL_SHARE` is
their sum: the most the caches together may plan to hold. Each cache's ``default_budget_bytes`` reads its share
through :func:`share_bytes`; the environment overrides each cache already documents still win.

**A floor of free memory.** A share of *physical* memory says nothing about what is *free*: another program, a
large figure or an edit's temporary copies can leave the machine short while every cache is inside its budget.
So each cache, whenever it takes something in, asks :func:`shortfall_bytes` how far the machine's free memory is
below :func:`free_floor_bytes` (:data:`FLOOR_SHARE` of physical memory, never below :data:`MIN_FLOOR_BYTES`) and
evicts that much more, least recently used first, beyond its own budget — but never below :data:`PRESSURE_KEEP` of
that budget, since a shortage another program causes can last all session and a cache emptied to its last entry
would then re-read the plate on every redraw. The work no cache counts — the transient
copies of an edit, a redraw, the window's own painting — therefore always starts with that much room.

Giving memory back costs time and never numbers: an evicted well is read back from its Parquet cache with every
verdict re-applied, a population is rebuilt from the rows, and a frame is decoded again. That is what makes the
floor safe to enforce aggressively.

Where free memory cannot be read (macOS, which has no cheap call for it, or an unreadable ``/proc``),
:func:`available_memory_bytes` returns ``None``, there is no shortfall, and the shares alone bound the caches.

Qt-free; read by the three caches in this package.
"""

from __future__ import annotations

import logging
import os
import sys

_logger = logging.getLogger("review")

#: Each cache's default share of physical memory. The row cache keeps the largest because a well missing from it
#: costs a disk read on every redraw that wants it; the other two rebuild from the rows.
SHARES = {
    "rows": 0.25,
    "populations": 0.10,
    "pixels": 0.10,
}
#: The most the caches together may plan to hold, as a share of physical memory.
TOTAL_SHARE = sum(SHARES.values())

#: Free memory every cache keeps for the work no cache counts, as a share of physical memory…
FLOOR_SHARE = 0.15
#: …and never less than this.
MIN_FLOOR_BYTES = 2**30
#: Environment override for the floor, in MiB; ``0`` turns the floor off.
FLOOR_ENV = "ALLWELL_MEMORY_FLOOR_MB"
#: The share of its own budget a cache keeps however short the machine is. The floor is measured against the whole
#: machine, and a cache can free only its own entries: with another program holding memory for as long as the app
#: runs, a floor enforced to the last entry would leave one well held and every redraw re-reading the plate from
#: disk, for the rest of the session. A quarter of the budget is what the shortage can take.
PRESSURE_KEEP = 0.25


def pressure_keep_bytes(budget: int) -> int:
    """What a cache with *budget* keeps however short free memory is (:data:`PRESSURE_KEEP` of it)."""
    return int(budget * PRESSURE_KEEP)


class _MemoryStatus:
    """``MEMORYSTATUSEX``, built lazily so importing this module on another platform touches no ``ctypes``."""

    _type = None

    @classmethod
    def read(cls) -> object | None:
        import ctypes

        if cls._type is None:
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            cls._type = MEMORYSTATUSEX
        status = cls._type()
        status.dwLength = ctypes.sizeof(cls._type)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return status


def physical_memory_bytes() -> int | None:
    """The machine's physical memory, or ``None`` when it cannot be read."""
    try:
        if sys.platform == "win32":
            status = _MemoryStatus.read()
            return int(status.ullTotalPhys) if status is not None else None
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return None


def available_memory_bytes() -> int | None:
    """Memory a new allocation can have without the machine paging, or ``None`` when it cannot be read.

    On Windows the smaller of the free physical memory and the commit charge still available: a ``MemoryError``
    there is the commit limit being reached, which a machine with physical memory to spare can still hit when its
    page file is small. On Linux ``MemAvailable`` from ``/proc/meminfo``. Elsewhere (macOS) ``None``.
    """
    try:
        if sys.platform == "win32":
            status = _MemoryStatus.read()
            if status is None:
                return None
            return int(min(status.ullAvailPhys, status.ullAvailPageFile))
        if sys.platform.startswith("linux"):
            with open("/proc/meminfo", encoding="ascii") as fh:
                for line in fh:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) * 1024
        return None
    except (AttributeError, OSError, ValueError, IndexError):
        return None


def share_bytes(name: str, *, minimum: int, maximum: int | None = None, total: int | None = None) -> int:
    """Cache *name*'s default budget: its :data:`SHARES` of physical memory, within *minimum* and *maximum*.

    *total* is the physical memory to take the share of; ``None`` reads it. A machine whose memory cannot be read
    gets *minimum*.
    """
    total = physical_memory_bytes() if total is None else total
    if not total:
        return int(minimum)
    budget = max(int(minimum), int(total * SHARES[name]))
    return min(budget, int(maximum)) if maximum is not None else budget


def free_floor_bytes() -> int:
    """The free memory the caches keep: the environment override, else :data:`FLOOR_SHARE` of physical memory."""
    raw = os.environ.get(FLOOR_ENV, "").strip()
    if raw:
        try:
            mib = float(raw)
        except ValueError:
            _logger.warning("%s=%r is not a number of MiB; using the default free-memory floor", FLOOR_ENV, raw)
        else:
            if mib >= 0:
                return int(mib * 2**20)
            _logger.warning("%s=%r is negative; using the default free-memory floor", FLOOR_ENV, raw)
    total = physical_memory_bytes()
    return max(MIN_FLOOR_BYTES, int(total * FLOOR_SHARE)) if total else MIN_FLOOR_BYTES


def shortfall_bytes() -> int:
    """How far the machine's free memory is below :func:`free_floor_bytes`; ``0`` when it is not, or cannot be
    told. A cache evicts this much beyond its own budget when it takes something in."""
    floor = free_floor_bytes()
    if floor <= 0:
        return 0
    available = available_memory_bytes()
    if available is None:
        return 0
    return max(0, floor - available)


def note_pressure(cache: str, freed: int, wanted: int) -> None:
    """Log that *cache* gave memory back because the machine was short, once per call that did."""
    _logger.info("Free memory is %.0f MiB below the %.0f MiB floor; the %s gave back %.0f MiB (least recently used "
                 "first; it is rebuilt when next needed)", wanted / 2**20, free_floor_bytes() / 2**20, cache,
                 freed / 2**20)
