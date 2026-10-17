"""Plate-wide values computed one well at a time, and remembered small.

The row cache is bounded (``review/data/row_cache.py``), so "every well" is no
longer "every frame in memory": a reader that means the plate reads each well
through ``get_rows``, which may load it from disk. Doing that on every repaint
would stream the whole plate each time, so the per-well result — a handful of
numbers, never the rows — is memoized here.

The memo lives in the statistics cache (``ctx.state.stats_cache``) under a key
that carries ``dataset.rows_version``, exactly as ``stats_compute.
aggregate_well_cached`` does. That borrows the invalidation the aggregates
already depend on: every change to what a well's rows say either bumps the
version (gating, curation, a replaced frame, a dataset swap) or clears the
statistics cache outright (QC, crop and the other verdict layers, through
``recompute_qc_all`` and ``invalidate_stats_cache``). A per-well value is
therefore exactly as fresh as the plotted mean beside it. The version is read
into the key before computing, never after, for the reason that function gives.

A *compute* must return something small and must not keep the frame: a result
holding the rows would put the plate back in memory through the side door.

A caller that can name the columns its *compute* reads passes them as
``columns``: the well is then read through ``narrow_rows.narrow_rows``, which
adds every verdict layer's declared inputs and runs the projectors on the
narrow frame, and never puts it in the row cache. Without ``columns`` (or with
``None``, "I cannot say") the whole frame is read through ``get_rows`` as
before. The columns are not part of the key: they must follow from *params*,
which is what decides the answer.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable

from review.data.data_loading import get_rows
from review.data.narrow_rows import narrow_rows


def per_well(ctx, kind: str, label: str, params: Hashable, compute: Callable,
             *, columns: Iterable[str] | None = None):
    """``compute(rows of label)``, memoized for as long as the rows and *params* hold.

    With *columns*, the rows are a narrow read of those columns plus what the
    verdicts need (see the module docstring); without, the whole frame.
    """
    store = getattr(ctx.state, "stats_cache", None)
    version = getattr(ctx.state.dataset, "rows_version", None)
    if version is None:
        # A context too thin to say when its rows changed: compute, don't guess.
        store = None
    key = ("plate_scan", kind, label, params, version)
    if store is not None:
        hit = store.cache.get(key, _MISSING)
        if hit is not _MISSING:
            return None if hit is _NONE else hit
    if columns is None:
        rows = get_rows(ctx, label)
    else:
        rows = narrow_rows(ctx, label, columns)
    result = compute(rows)
    if store is not None:
        # ``None`` is remembered too: it is the answer for a well with nothing
        # to contribute (no values of the column, no rows), and not remembering
        # it re-read exactly those wells — every well, for a ratio column — on
        # every ask.
        store.cache[key] = _NONE if result is None else result
    return result


#: A key with no entry, and a remembered ``None`` (which ``dict.get`` cannot tell apart).
_MISSING = object()
_NONE = object()


def every_well(ctx, kind: str, params: Hashable, compute: Callable) -> dict:
    """``{label: per_well(...)}`` over every well of the dataset, held or not."""
    return {label: per_well(ctx, kind, label, params, compute)
            for label in list(ctx.state.dataset.well_paths)}
