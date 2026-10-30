"""Threads inside one nanowell worker, and what a worker may hold in memory.

A nanowell run on a GPU node runs few worker processes — each holds a Cellpose model on the GPU, and more than two
on one card earlier lost the run's heartbeat to a shared filesystem they all read from — and gives each the node's
remaining cores as threads (``pipeline.parallelism``, ``OMP_NUM_THREADS``). Almost none of a well's work used them:
locating a timepoint and measuring a tile are numpy, scipy and scikit-image calls run one after another. This module
is what lets :func:`pipeline.nanowell_run.run_well` spread that work over the worker's threads:

* :func:`ordered_map` runs independent items — a well's timepoints while they are located, a timepoint's tiles while
  they are measured — on a thread pool and returns the results **in the items' order**, so everything the caller
  builds from them (dict order, notes, cell rows) is what a serial loop builds. With one thread it *is* the serial
  loop.
* :func:`serialised` puts a segmenter behind the worker's one lock, so a worker never has two Cellpose calls in
  flight: threads overlap each other's CPU work with one thread's GPU work, not GPU work with GPU work.
* :func:`thread_budget` and :func:`memory_available` say how many threads a worker has and how much memory it may
  count on, both read without importing anything heavy.

Qt-free and in the remote manifest, as the rest of ``pipeline/`` the run imports.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor

#: Overrides the threads a nanowell worker uses (a positive integer). Unset, a worker uses its ``OMP_NUM_THREADS``,
#: which ``process_microscopy._worker_init`` sets from the run's plan; ``1`` runs every step serially, as before.
THREADS_ENV = "ALLWELL_NANOWELL_THREADS"

#: The run's worker count, which the main process sets before its pool spawns (``parallelism.export_gpu_sharers``),
#: so a worker can take its share of the node's memory rather than all of it.
POOL_WORKERS_ENV = "ALLWELL_POOL_WORKERS"

#: The share of the memory available when a well starts that its worker plans to fill, split among the workers.
#: The rest is for the other processes on the node and for what the estimates below do not see.
MEMORY_SHARE = 0.6

#: The worker's lock around its segmenter (:func:`serialised`): one per process, since each worker holds one model.
_SEGMENTER_LOCK = threading.Lock()


def _positive_int(raw) -> int | None:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def thread_budget(environ=None) -> int:
    """Threads this worker may use: ``THREADS_ENV`` when set, else ``OMP_NUM_THREADS``, else 1; never more than the
    machine's CPUs."""
    environ = os.environ if environ is None else environ
    for name in (THREADS_ENV, "OMP_NUM_THREADS"):
        value = _positive_int(environ.get(name, ""))
        if value is not None:
            return max(1, min(value, os.cpu_count() or value))
    return 1


def pool_workers(environ=None) -> int:
    """The run's worker processes (``POOL_WORKERS_ENV``); 1 when not told (Test detection, a caller outside a run)."""
    environ = os.environ if environ is None else environ
    return _positive_int(environ.get(POOL_WORKERS_ENV, "")) or 1


def memory_available(meminfo: str = "/proc/meminfo") -> int | None:
    """Bytes the kernel says can be given to a new process without swapping (``MemAvailable``), or ``None`` where
    that cannot be read (not Linux): the caller then plans as it did before it could ask."""
    try:
        with open(meminfo, encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def worker_memory_budget(available: int | None = None, workers: int | None = None) -> int | None:
    """Bytes this worker may plan to hold: ``MEMORY_SHARE`` of the memory available, over the run's workers; ``None``
    when the available memory cannot be read."""
    available = memory_available() if available is None else available
    if available is None:
        return None
    return int(available * MEMORY_SHARE / max(1, pool_workers() if workers is None else workers))


def ordered_results(fn: Callable, items: Iterable, threads: int) -> Iterator:
    """``fn(item)`` for each item, run on up to *threads* threads when there is more than one item, yielded in the
    items' order as each becomes available, so a caller can report progress while later items still run. The first
    exception a call raises is raised from the iteration that reaches it, as a loop would have; the calls already
    running finish before it propagates."""
    items = list(items)
    if threads <= 1 or len(items) <= 1:
        for item in items:
            yield fn(item)
        return
    with ThreadPoolExecutor(max_workers=min(threads, len(items)), thread_name_prefix="nanowell") as pool:
        yield from pool.map(fn, items)


def ordered_map(fn: Callable, items: Iterable, threads: int) -> list:
    """``[fn(item) for item in items]`` by :func:`ordered_results`."""
    return list(ordered_results(fn, items, threads))


def serialised(segment, lock: threading.Lock | None = None):
    """*segment* (a :func:`pipeline.nanowell_measure.cellpose_segmenter`, or a stand-in with the same shape) with each
    call — and each ``segment_many`` call — made under *lock* (the worker's own by default), keeping the attributes
    :func:`pipeline.nanowell_measure.segment_sites` reads."""
    lock = _SEGMENTER_LOCK if lock is None else lock

    def call(*args, **kwargs):
        with lock:
            return segment(*args, **kwargs)

    for name in ("accepts_range", "accepts_size_cap"):
        if hasattr(segment, name):
            setattr(call, name, getattr(segment, name))
    many = getattr(segment, "segment_many", None)
    if many is not None:
        def many_call(*args, **kwargs):
            with lock:
                return many(*args, **kwargs)
        call.segment_many = many_call
    return call


def synchronised(fn: Callable | None) -> Callable | None:
    """*fn* behind a lock of its own (a progress counter called from several threads); ``None`` stays ``None``."""
    if fn is None:
        return None
    lock = threading.Lock()

    def call(*args, **kwargs):
        with lock:
            return fn(*args, **kwargs)
    return call
