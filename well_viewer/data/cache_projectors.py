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
from typing import Callable, List, Optional

_logger = logging.getLogger("well_viewer")

# Registered projectors, applied in order to each freshly-loaded frame.
_PROJECTORS: List[Callable] = []


def register_cache_projector(fn: Callable) -> None:
    """Append *fn* — an ``(ctx, label) -> None`` in-place cache projector."""
    _PROJECTORS.append(fn)


def clear_cache_projectors() -> None:
    """Drop all registered projectors (used before re-registering defaults so
    repeated app construction in tests can't stack duplicates)."""
    _PROJECTORS.clear()


def iter_cache_projectors() -> "List[Callable]":
    """The registered projectors, in registration order."""
    return list(_PROJECTORS)


def project_verdict_column(
    ctx,
    label: Optional[str],
    *,
    column: str,
    verdict: Callable,
    layer: str,
) -> None:
    """Write *column* onto cached well frame(s) from *verdict*.

    The walk every inclusion-verdict projector performs: with *label* given,
    only that well; otherwise every cached well. Empty and missing frames are
    skipped. *verdict* is a ``(df) -> Series`` returning the per-row 0/1
    decision, cast to int here so callers need not.

    A projector must never take down the data path, so a frame whose columns
    ``verdict`` cannot read is logged against *layer* and stepped over rather
    than raised — the remaining wells still get their column. Fail-open is the
    verdict functions' own job: they return all-pass when their inputs are
    absent, so a frame that simply predates a feature still reads as included.
    """
    cache = ctx.state.dataset.cache
    labels = [label] if label is not None else list(cache.keys())
    for lbl in labels:
        df = cache.get(lbl)
        if df is None or df.empty:
            continue
        try:
            df[column] = verdict(df).astype(int)
        except Exception:
            _logger.exception(
                "%s: failed to compute %s for well %s", layer, column, lbl)
