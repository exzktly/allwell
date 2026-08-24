"""Cache-projector registry — the seam that keeps the data layer feature-free.

``data_loading.get_rows`` loads a well CSV and then projects the composable
inclusion-verdict columns (``QC_Pass`` / ``Crop_Pass``) onto the fresh frame.
Those projectors *live under* ``features/qc`` and ``features/crop``, but the data
layer must not import ``features/`` (the layering contract enforced by
``tests/test_data_layer_no_feature_imports.py`` + ``tests/test_layering.py``).

So instead of importing them, ``get_rows`` iterates the projectors registered
here. A projector is any ``(ctx, label) -> None`` callable that mutates
``ctx.state.dataset.cache[label]`` in place. The defaults (QC, then Crop) are
registered at app construction by
``core/projector_registry.register_default_projectors`` — the same
"core wires the feature callback at startup so the lower layer imports no
feature" pattern as ``core/scope_registry.register_default_scopes``.

Deliberately Qt-free and feature-free — it only holds callables and the one
shared walk (:func:`project_verdict_column`) that every projector performs.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

_logger = logging.getLogger("well_viewer")

# Registered projectors, applied in order to each freshly-loaded frame.
_PROJECTORS: list[Callable] = []


def register_cache_projector(fn: Callable) -> None:
    """Append *fn* — an ``(ctx, label) -> None`` in-place cache projector."""
    _PROJECTORS.append(fn)


def clear_cache_projectors() -> None:
    """Drop all registered projectors (used before re-registering defaults so
    repeated app construction in tests can't stack duplicates)."""
    _PROJECTORS.clear()


def iter_cache_projectors() -> list[Callable]:
    """The registered projectors, in registration order."""
    return list(_PROJECTORS)


def project_verdict_column(
    ctx,
    label: str | None,
    *,
    column: str,
    verdict: Callable,
    layer: str,
) -> list[str]:
    """Write *column* onto cached well frame(s) from *verdict*.

    The walk every inclusion-verdict projector performs: with *label* given,
    only that well; otherwise every cached well. Empty and missing frames are
    skipped. *verdict* is a ``(df, well_label) -> Series`` returning the per-row
    0/1 decision, cast to int here so callers need not. The label is passed
    because a verdict can be scoped to particular wells (the crop's
    :class:`~well_viewer.features.crop.region.FrameScope`) and the frame itself
    need not carry a well column — this walk is the only place that knows which
    well each cached frame belongs to.

    A projector must never take down the data path, so a frame whose columns
    ``verdict`` cannot read is logged against *layer* and stepped over rather
    than raised — the remaining wells still get their column. Fail-open is the
    verdict functions' own job: they return all-pass when their inputs are
    absent, so a frame that simply predates a feature still reads as included.

    **Stepping over a failure drops the column rather than leaving it.** This walk
    is a *re-*projection as often as it is a first one — the user moves a QC
    threshold or a crop region and every cached frame is rewritten. A frame that
    kept the value from the previous parameters would then be filtered by settings
    the UI no longer shows anywhere, and it would look exactly like a frame that
    had just been recomputed and agreed. Dropping it puts the frame in the one
    state the whole stack already handles honestly: every consumer guards its read
    with ``if column in df.columns`` and treats an absent verdict as all-pass, the
    same as a frame that predates the feature. Wrong-and-known beats
    stale-and-invisible.

    Returns the labels whose verdict could not be computed — empty on a clean
    pass. The frame itself is the primary signal (the column is gone, so nothing
    downstream can mistake old parameters for new); the list lets a caller that
    wants to say so on screen name the wells without re-walking the cache.
    """
    cache = ctx.state.dataset.cache
    labels = [label] if label is not None else list(cache.keys())
    failed: list[str] = []
    for lbl in labels:
        df = cache.get(lbl)
        if df is None or df.empty:
            continue
        try:
            df[column] = verdict(df, lbl).astype(int)
        except Exception:
            failed.append(lbl)
            # ``errors="ignore"``: a FIRST projection has no column to remove, and
            # the frame is mutated in place because that is this walk's contract.
            df.drop(columns=[column], inplace=True, errors="ignore")
            _logger.exception(
                "%s: failed to compute %s for well %s — any %s from earlier "
                "parameters has been dropped from that frame, so the well is "
                "treated as fully included until the verdict can be computed "
                "again", layer, column, lbl, column)
    # No ``note_rows_changed`` here, deliberately. This walk has exactly two
    # callers — the QC and crop controllers — and both call
    # ``invalidate_stats_cache`` immediately afterwards, which empties the very
    # dict the per-well aggregation memo lives in. The other route in is
    # ``get_rows``, which projects onto a frame it has just built, and a frame
    # that did not exist a moment ago cannot have a memoized aggregate.
    #
    # Bumping anyway looked like harmless insurance and was not: this function
    # is reached in tests through hand-rolled contexts whose ``dataset`` is a
    # namespace, and requiring the counter here would have meant teaching
    # eighty-one test files about a field none of them is about. A guard that
    # can only be satisfied by rewriting its callers' tests is measuring the
    # wrong thing.
    return failed
