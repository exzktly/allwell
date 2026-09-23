"""Run a per-file scan across processes, because the work is a parser.

``pipeline.czi_read`` reads a CZI's header by parsing it in Python: the
subblock directory entry by entry, and the plate's metadata document through
``ElementTree``. That is bytecode, and bytecode holds the interpreter lock, so
a pool of *threads* over it does not go N times faster — it contends, and one
of the threads it contends with is the one drawing the window.

Measured on 96 files with no network latency, so that the cost is all
parsing: eight threads took 3922 ms, four processes 1066 ms. With 8 ms of
per-file latency injected the same comparison was 3928 ms against 1241 ms on
eight processes, and at 40 ms it was 3849 ms against 1441 ms. Processes win in
every condition by between 1.8x and 3.7x, and the window's event loop — sampled
as the worst gap between firings of a 10 ms timer — goes from 56 ms to 16 ms,
with the share of the run spent starved falling from measurable to zero.

**Eight workers, as before.** The best width moves with latency: four is best
with none (1066 ms against 1318 for eight), eight is best at 8 ms and at 40 ms
(1441 ms against 2171 for four). Eight is never more than about a quarter off
the best in any of the three, and four is half as good again on a slow share —
so the cap that was chosen for the round trips is still the right one. What was
wrong was the *kind* of worker, not the number.

**Spawn, always, on every platform.** A ``fork`` of a process that is running
a Qt event loop and several threads inherits locks held by threads that do not
exist in the child, which is a deadlock waiting for the right timing rather
than a thing that works on Linux. macOS and Windows use spawn regardless, so
forcing it also means the behaviour under test is the behaviour that ships.
``all_well_launcher`` already handles a spawned child re-execing the frozen
bundle — its dispatcher runs before any heavy import — which is what makes
this usable outside a source checkout.
"""

from __future__ import annotations

import multiprocessing as mp
import os
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from typing import Any

from util.aw_safe import swallowed

#: Below this many files, the scan runs on threads instead.
#:
#: Spawning a worker re-execs the interpreter, and in a PyInstaller bundle it
#: re-execs the bundle — a cost this repository cannot measure from a source
#: checkout, and therefore one to leave room for. Measured from source, the
#: crossover is between four and eight files: at four, eight threads took
#: 167 ms against 276 ms for four spawned processes; at eight it was 321 ms
#: against 245 ms; at ninety-six, 3860 ms against 1039 ms.
#:
#: Twenty-four rather than eight is that room. Below it the thread path
#: finishes in under a second either way — at twenty-four files it is about
#: 900 ms — so the worst this threshold can cost is a fraction of a second
#: that nobody is waiting on, while setting it at the measured crossover
#: would spend a bundle's startup to save a tenth of one.
PROCESS_THRESHOLD = 24

#: Set ``ALLWELL_SCAN_PROCESSES=0`` to force the thread path, or ``1`` to force
#: processes regardless of the file count. For diagnosing a machine where the
#: pool misbehaves — a locked-down Windows host, a sandbox with no shared
#: memory — without shipping a new build to find out.
_OVERRIDE = "ALLWELL_SCAN_PROCESSES"


def _override() -> "bool | None":
    """``True``/``False`` if the environment insists, else ``None``."""
    raw = os.environ.get(_OVERRIDE, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return None


def wants_processes(n_items: int) -> bool:
    """Whether a scan of *n_items* files is worth spawning processes for."""
    forced = _override()
    if forced is not None:
        return forced
    return n_items >= PROCESS_THRESHOLD


def map_ordered(
    fn: Callable[[Any], Any],
    items: Sequence[Any],
    *,
    workers: int,
    on_result: "Callable[[int], None] | None" = None,
) -> list:
    """``[fn(item) for item in items]``, computed in parallel, **in order**.

    The order is not a nicety: it is the order a split export was written in
    and the order a grid mapping is laid over, so a result collected as it
    completed would make a plate's well mapping depend on which file answered
    first. ``Executor.map`` yields by argument order however the work finishes,
    which is exactly that guarantee, and it re-raises each call's exception at
    its own position, so a set whose third file is broken still fails on the
    third file.

    *on_result* is called with each result's index as it is yielded, for a
    caller that wants to count files as the scan reaches them. It lags the
    files actually finished, because the yielding is in argument order rather
    than in completion order — which is honest for a progress line and is the
    same ordering the results themselves depend on.

    *fn* must be importable by name in a fresh interpreter — a module-level
    function, not a closure or a lambda — because that is how a spawned child
    is told what to run. A closure raises ``PicklingError`` at dispatch rather
    than misbehaving quietly, so this is a constraint the first run enforces.
    """
    if not items:
        return []
    if len(items) < 2 or workers <= 1:
        return _serially(fn, items, on_result)
    if wants_processes(len(items)):
        out = _across_processes(fn, items, workers, on_result)
        if out is not None:
            return out
        # Falling back rather than failing: a pool that cannot start is a
        # property of the machine — a locked-down host, a sandbox without
        # shared memory, a frozen bundle whose child dispatch is not wired —
        # and the scan's answer does not depend on how it was computed. The
        # failure is logged rather than swallowed silently.
    return _across_threads(fn, items, workers, on_result)


def _serially(fn, items, on_result) -> list:
    out = []
    for index, item in enumerate(items):
        out.append(fn(item))
        if on_result is not None:
            on_result(index)
    return out


def _collect(pool, fn, items, on_result, **kwargs) -> list:
    out = []
    for index, result in enumerate(pool.map(fn, items, **kwargs)):
        out.append(result)
        if on_result is not None:
            on_result(index)
    return out


def _across_threads(fn, items, workers, on_result) -> list:
    with ThreadPoolExecutor(max_workers=workers,
                            thread_name_prefix="aw-scan") as pool:
        return _collect(pool, fn, items, on_result)


def _across_processes(fn, items, workers, on_result) -> "list | None":
    """The results, or ``None`` when no pool could be had."""
    try:
        context = mp.get_context("spawn")
        with ProcessPoolExecutor(max_workers=workers,
                                 mp_context=context) as pool:
            # Chunked, because a spawned worker is expensive to reach: sending
            # one path at a time makes the round trip per file comparable to
            # the parse it is asking for. Small chunks all the same — a large
            # one would leave the last worker holding a tail of files while
            # the others idle, on a scan whose whole point is to finish.
            chunk = max(1, min(8, len(items) // (workers * 4) or 1))
            return _collect(pool, fn, items, on_result, chunksize=chunk)
    except Exception:
        swallowed("scan process pool; falling back to threads")
        return None


def is_picklable_by_name(fn: Callable) -> bool:
    """Whether *fn* can be sent to a spawned worker at all.

    A closure, a lambda, or a bound method of an instance cannot: the child is
    told a module and a qualified name and imports it. Exposed so a test can
    assert the property of the functions this module is given, rather than
    waiting for a ``PicklingError`` on the first real plate.
    """
    module = getattr(fn, "__module__", None)
    qualname = getattr(fn, "__qualname__", "")
    if not module or not qualname or "<locals>" in qualname:
        return False
    import importlib
    try:
        found: Any = importlib.import_module(module)
        for part in qualname.split("."):
            found = getattr(found, part)
    except (ImportError, AttributeError):
        return False
    return found is fn
