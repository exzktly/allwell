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

Deliberately Qt-free and feature-free — it only holds callables.
"""

from __future__ import annotations

from typing import Callable, List

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
