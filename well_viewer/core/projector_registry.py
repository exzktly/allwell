"""Register the default cache projectors (QC, Crop) at app construction.

``data/cache_projectors.py`` holds the projector list but, as a data-layer
module, must not import ``features/``. This core module does the wiring: it
imports each projector's feature controller and registers it, so
``data_loading.get_rows`` can project ``QC_Pass`` / ``Crop_Pass`` onto freshly
loaded frames without the data layer ever naming a feature.

Mirrors ``core/scope_registry.register_default_scopes``; called once from
``core/bus_wiring.wire_event_bus``.
"""

from __future__ import annotations


def register_default_projectors() -> None:
    """Register the QC and Crop cache projectors (QC first, matching the prior
    inline order in ``get_rows``).

    Idempotent — clears first, so constructing several apps in one test session
    can't stack duplicate projectors.
    """
    from well_viewer.data.cache_projectors import (
        clear_cache_projectors, register_cache_projector,
    )
    from well_viewer.features.qc.controller import apply_qc_to_cache
    from well_viewer.features.crop.controller import apply_crop_to_cache

    clear_cache_projectors()
    register_cache_projector(apply_qc_to_cache)
    register_cache_projector(apply_crop_to_cache)
