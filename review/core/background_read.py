"""Plate-wide reads off the window's thread, for the views that read wells not in memory.

The row cache is bounded (``review/data/row_cache.py``), so on a plate larger than it a view that draws every
selected well reads some of them back from disk each time it is shown. On a fast local disk that is seconds; from a
network share or a slow USB drive it is minutes, and it used to happen on the window's thread, so the window froze
and looked hung. A user reported exactly that: Sample Map → Distribution → Sample Map → Cell Gating on a 60-well
nanowell plate on a network share.

:func:`start` runs a view's data step — the part that reads wells and computes arrays, which touches no widget — on
a worker (``core.worker_bridge.run_off_thread``) and hands its result back on the window's thread. A view uses it
only when :func:`wells_not_held` says the step would read from disk; when everything is in memory it draws
synchronously exactly as before, so a plate that fits is unchanged.

**Only the newest result is drawn.** Each key (one per view) carries a generation; a newer :func:`start` or a
:func:`cancel` supersedes the one in flight, and a result that arrives superseded — or after the dataset changed
underneath it — is handed to *discard* (which releases anything it holds, such as a spilled cell pool) instead of
being drawn. A view that changed while its read ran is redrawn by whatever changed it, so nothing is left stale.

The worker reads the same frames the gating worker and the window's thread read; the row cache takes its own lock,
and a frame is only ever read here, never written.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable

from core.safe import swallow
from core.worker_bridge import run_off_thread

_logger = logging.getLogger("review")


def wells_not_held(ctx, wells: Iterable[str]) -> list[str]:
    """The wells of *wells* the row cache does not hold: the ones a draw over them would read from disk."""
    cache = getattr(ctx.state.dataset, "cache", None)
    paths = getattr(ctx.state.dataset, "well_paths", None) or {}
    if cache is None:
        return []
    return [w for w in dict.fromkeys(wells) if w in paths and cache.get(w) is None]


def _jobs(ctx) -> dict | None:
    runtime = getattr(getattr(ctx, "runtime", None), "reads", None)
    if runtime is None:
        return None
    jobs = runtime.jobs
    if jobs is None:
        jobs = {}
        runtime.jobs = jobs
    return jobs


def running(ctx, key: str) -> bool:
    """Whether a read under *key* is in flight."""
    jobs = _jobs(ctx)
    return bool(jobs and jobs.get(key, (0, None))[1] is not None)


def cancel(ctx, key: str) -> None:
    """Supersede the read under *key*, if any: its result will be discarded."""
    jobs = _jobs(ctx)
    if jobs is not None and key in jobs:
        jobs[key] = (jobs[key][0] + 1, None)


def start(ctx, key: str, compute: Callable[[], object], *, on_done: Callable[[object], None],
          discard: Callable[[object], None] | None = None,
          on_failed: Callable[[str], None] | None = None, title: str | None = None) -> bool:
    """Run *compute()* on a worker and call *on_done(result)* on the window's thread, if still the newest.

    *title* names the tab whose plots *on_done* draws: when drawing the result fails — a ``MemoryError`` most
    often — those plots are marked not current (``review.core.plot_failure``) rather than left showing the drawing
    from before.

    Returns False (and runs nothing) when this context has no runtime to keep the worker alive on — a bare test
    context — so the caller draws synchronously instead.
    """
    jobs = _jobs(ctx)
    if jobs is None:
        return False
    generation = jobs.get(key, (0, None))[0] + 1
    data_dir = getattr(ctx.state.dataset, "data_dir", None)

    def current() -> bool:
        return (jobs.get(key, (0, None))[0] == generation
                and getattr(ctx.state.dataset, "data_dir", None) == data_dir)

    def done(result) -> None:
        if not current():
            if discard is not None:
                with swallow(f"discarding a superseded background read ({key})"):
                    discard(result)
            return
        jobs[key] = (generation, None)
        # Nothing may escape a queued slot. The window may have been closed while the read ran (a draw into a
        # deleted widget), and the drawing itself can fail — for want of memory, after a large read — in which case
        # the plot must not go on showing the drawing from before as if it were this one.
        try:
            on_done(result)
        except Exception as exc:
            _logger.exception("drawing a background read's result (%s) failed", key)
            if title:
                with swallow(f"marking the {title} plot as not drawn"):
                    from review.core.plot_failure import show_redraw_failed
                    show_redraw_failed(ctx, (title,), exc)

    def failed(message: str) -> None:
        if not current():
            return
        jobs[key] = (generation, None)
        _logger.warning("Reading wells for %s failed: %s", key, message)
        if on_failed is not None:
            on_failed(message)

    bridge = run_off_thread(compute, on_done=done, on_failed=failed)
    jobs[key] = (generation, bridge)
    return True
