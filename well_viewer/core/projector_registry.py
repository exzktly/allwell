"""Register the default cache projectors (curation, QC, Crop) at app construction.

``data/cache_projectors.py`` holds the projector list but, as a data-layer
module, must not import ``features/``. This core module does the wiring: it
imports each projector's feature controller and registers it, so
``data_loading.load_and_project`` can project ``QC_Pass`` / ``Crop_Pass`` onto
freshly loaded frames without the data layer ever naming a feature. Manual
curation needs no such indirection — it lives in ``data/curation.py`` — but it is
registered here alongside the other two because the value of this list is that it
is the *whole* roster of what a new frame gets. A projection applied somewhere
else instead is one a reloaded well can miss.

Mirrors ``core/scope_registry.register_default_scopes``; called once from
``core/bus_wiring.wire_event_bus``.
"""

from __future__ import annotations


def register_default_projectors() -> None:
    """Register the curation, QC and Crop cache projectors.

    Order matches the dataset loader's own sequence (curation, then QC, then
    crop) though nothing depends on it: the three write different columns, and
    QC and crop document that they never touch the ``Included`` curation owns.

    Idempotent — clears first, so constructing several apps in one test session
    can't stack duplicate projectors.
    """
    from well_viewer.data.cache_projectors import (
        clear_cache_projectors, register_cache_projector,
    )
    from well_viewer.data.curation import apply_curation_to_cache
    from well_viewer.features.qc.controller import apply_qc_to_cache
    from well_viewer.features.crop.controller import apply_crop_to_cache

    clear_cache_projectors()
    register_cache_projector(apply_curation_to_cache)
    register_cache_projector(apply_qc_to_cache)
    register_cache_projector(apply_crop_to_cache)
