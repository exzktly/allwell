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
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait
from itertools import islice

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
#: Re-entrant, so a segmenter wrapped twice, or one that calls back into itself, does not wait on itself.
_SEGMENTER_LOCK = threading.RLock()

#: The process's thread pool for a well's items (:func:`ordered_results`) and its one background thread (:func:`ahead`),
#: made once and kept for the worker's life: a pool per timepoint started fresh threads for every timepoint, each of
#: which opened its own handles and, calling Cellpose, its own Torch state.
_POOL: ThreadPoolExecutor | None = None
_POOL_SIZE = 0
_AHEAD: ThreadPoolExecutor | None = None
_POOL_LOCK = threading.Lock()
_POOL_PREFIX = "nanowell"


def _positive_int(raw) -> int | None:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _usable_cpus() -> int:
    """The CPUs this process may run on: its affinity mask where the platform has one (a Slurm job's), else all."""
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def thread_budget(environ=None) -> int:
    """Threads this worker may use: ``THREADS_ENV`` when set, else ``OMP_NUM_THREADS``, else 1; never more than the
    CPUs the process may run on."""
    environ = os.environ if environ is None else environ
    for name in (THREADS_ENV, "OMP_NUM_THREADS"):
        value = _positive_int(environ.get(name, ""))
        if value is not None:
            return max(1, min(value, _usable_cpus()))
    return 1


def pool_workers(environ=None) -> int:
    """The run's worker processes (``POOL_WORKERS_ENV``); 1 when not told (Test detection, a caller outside a run)."""
    environ = os.environ if environ is None else environ
    return _positive_int(environ.get(POOL_WORKERS_ENV, "")) or 1


def gpu_in_use(environ=None) -> bool:
    """Whether the run put this worker's Cellpose on a GPU: the main process exports how many workers share each GPU
    (``parallelism.GPU_SHARERS_ENV``) only when it found one. Without one, Cellpose runs on the CPU on Torch's own
    threads, and threads of ours beside it would only compete with them for the same cores."""
    from pipeline.parallelism import GPU_SHARERS_ENV

    environ = os.environ if environ is None else environ
    return _positive_int(environ.get(GPU_SHARERS_ENV, "")) is not None


def _read_int(path: str) -> int | None:
    try:
        with open(path, encoding="ascii") as handle:
            text = handle.read().strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None        # "max" (no limit) and anything unreadable: none


#: A cgroup v1 limit at or above this is the kernel's "unlimited" (a page-rounded LONG_MAX).
_V1_UNLIMITED = 1 << 60


def cgroup_headroom(proc_cgroup: str = "/proc/self/cgroup", root: str = "/sys/fs/cgroup") -> int | None:
    """Bytes this process's cgroups still allow before the kernel kills inside them — the least ``limit - usage`` over
    its memory cgroup and every ancestor that sets a limit (a Slurm job, step or container sets one; ``/proc/meminfo``
    shows the whole node regardless) — or ``None`` when no limit is set or none can be read."""
    try:
        with open(proc_cgroup, encoding="utf-8") as handle:
            lines = [ln.strip().split(":", 2) for ln in handle if ln.strip()]
    except OSError:
        return None
    least = None
    for parts in lines:
        if len(parts) != 3:
            continue
        _hier, controllers, path = parts
        if controllers == "":                                              # cgroup v2
            files, base = ("memory.max", "memory.current"), root
        elif "memory" in controllers.split(","):                           # cgroup v1
            files, base = ("memory.limit_in_bytes", "memory.usage_in_bytes"), os.path.join(root, "memory")
        else:
            continue
        here = path.strip("/")
        while True:
            folder = os.path.join(base, here) if here else base
            limit, usage = _read_int(os.path.join(folder, files[0])), _read_int(os.path.join(folder, files[1]))
            if limit is not None and limit < _V1_UNLIMITED and usage is not None:
                room = max(0, limit - usage)
                least = room if least is None else min(least, room)
            if not here:
                break
            here = os.path.dirname(here)
    return least


def slurm_allocation(environ=None) -> int | None:
    """Bytes Slurm gave this job on the node (``SLURM_MEM_PER_NODE``, or ``SLURM_MEM_PER_CPU`` times the job's CPUs on
    it, both in MB), or ``None`` outside Slurm or when it set neither. An upper bound: what is already used counts
    against it, which :func:`cgroup_headroom` sees where Slurm enforces the limit with a cgroup."""
    environ = os.environ if environ is None else environ
    per_node = _positive_int(environ.get("SLURM_MEM_PER_NODE", ""))
    if per_node is not None:
        return per_node << 20
    per_cpu = _positive_int(environ.get("SLURM_MEM_PER_CPU", ""))
    cpus = _positive_int(environ.get("SLURM_CPUS_ON_NODE", "")) or _positive_int(environ.get("SLURM_CPUS_PER_TASK", ""))
    if per_cpu is not None and cpus is not None:
        return (per_cpu * cpus) << 20
    return None


def memory_available(meminfo: str = "/proc/meminfo", *, proc_cgroup: str = "/proc/self/cgroup",
                     cgroup_root: str = "/sys/fs/cgroup", environ=None) -> int | None:
    """Bytes this process can still be given without being killed or swapping: the least of the kernel's
    ``MemAvailable``, what its cgroups still allow (:func:`cgroup_headroom`) and what Slurm allocated
    (:func:`slurm_allocation`); ``None`` where none of them can be read (not Linux), and the caller then plans as it
    did before it could ask."""
    found = [value for value in (_meminfo_available(meminfo), cgroup_headroom(proc_cgroup, cgroup_root),
                                 slurm_allocation(environ)) if value is not None]
    return min(found) if found else None


def _meminfo_available(meminfo: str) -> int | None:
    """``MemAvailable`` from *meminfo* in bytes; ``None`` where the file cannot be read or holds no such line."""
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


def _pool(size: int) -> ThreadPoolExecutor:
    """The process's pool, grown to *size* threads if it is smaller (never shrunk: :func:`ordered_results` bounds how
    many of its threads one call uses)."""
    global _POOL, _POOL_SIZE
    with _POOL_LOCK:
        if _POOL is None or _POOL_SIZE < size:
            old, _POOL, _POOL_SIZE = _POOL, ThreadPoolExecutor(max_workers=size, thread_name_prefix=_POOL_PREFIX), size
            if old is not None:
                old.shutdown(wait=False)
        return _POOL


def ahead() -> ThreadPoolExecutor:
    """The process's one background thread, for work that runs beside the pool (the next timepoint's preparation)."""
    global _AHEAD
    with _POOL_LOCK:
        if _AHEAD is None:
            _AHEAD = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"{_POOL_PREFIX}-ahead")
        return _AHEAD


def _on_pool_thread() -> bool:
    return threading.current_thread().name.startswith(_POOL_PREFIX)


def ordered_results(fn: Callable, items: Iterable, threads: int) -> Iterator:
    """``fn(item)`` for each item, at most *threads* at a time on the process's pool when there is more than one item,
    yielded in the items' order as each becomes available, so a caller can report progress while later items still
    run. The first exception a call raises is raised from the iteration that reaches it, as a loop would have. When
    the iteration ends early — that exception, or the caller raising or stopping — the items not yet started are
    cancelled and the ones running are waited for, so nothing is still running once it returns. Called from one of
    the pool's own threads it runs serially, since a pool waiting on itself can deadlock."""
    items = list(items)
    if threads <= 1 or len(items) <= 1 or _on_pool_thread():
        for item in items:
            yield fn(item)
        return
    pool = _pool(threads)
    pending: deque = deque()
    rest = iter(items)
    try:
        for item in islice(rest, threads):
            pending.append(pool.submit(fn, item))
        while pending:
            result = pending.popleft().result()
            following = next(rest, _END)
            if following is not _END:
                pending.append(pool.submit(fn, following))
            yield result
    finally:
        for future in pending:
            future.cancel()
        wait(pending)


_END = object()


def ordered_map(fn: Callable, items: Iterable, threads: int) -> list:
    """``[fn(item) for item in items]`` by :func:`ordered_results`."""
    return list(ordered_results(fn, items, threads))


def serialised(segment, lock=None):
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
