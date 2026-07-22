"""Heat Map view-state serialization — the feature owns its own snapshot /
restore so ``persistence/view_state.py`` never names (or imports) Heat Map.

The registry exposes this pair via the Heat Map ``FeatureDescriptor``'s
``view_state_io`` hook; ``view_state`` fans that out (exactly like Image Table).
A profiled-out Heat Map isn't in the registry, so this module is never imported.

The display settings (colormap, Auto/Fixed scale + vmin/vmax, rep-set-average and
log-scale toggles, timepoint-slider position) are (de)serialized by the SAME pair
the dataset-level layout sidecar uses (``persistence.heatmap_layouts._collect_settings``
/ ``apply_persisted_settings``), so nothing new is invented. The active layout name
is carried alongside so a restored Heat Map reproduces the exact arrangement —
``heatmap.controller.active_layout`` reads it live at redraw time.
"""
from __future__ import annotations

import logging

_logger = logging.getLogger("well_viewer")


def snapshot_heatmap(ctx) -> dict:
    """The Heat Map display settings + active-layout name for the view-state blob."""
    from well_viewer.persistence.heatmap_layouts import _collect_settings

    blk = dict(_collect_settings(ctx.window))
    hm = ctx.state.feature_slice("heatmap")
    if hm is not None and hm.active_layout_name:
        blk["active_layout_name"] = str(hm.active_layout_name)
    return blk


def apply_heatmap(ctx, state: dict) -> None:
    """Re-apply a saved Heat Map block: the active layout + the display settings.

    Sets the active layout name first (the renderer resolves it live via
    ``active_layout``), then hands the block to ``apply_persisted_settings`` which
    seeds the typed slice + widgets (safe before the tab is built). The figure is
    redrawn by the caller's post-restore/tab-activation redraw."""
    if not isinstance(state, dict):
        return
    hm = ctx.state.feature_slice("heatmap")
    if hm is None:
        return
    name = state.get("active_layout_name")
    if name:
        hm.active_layout_name = str(name)
    hm.persisted_settings = dict(state)
    try:
        from well_viewer.persistence.heatmap_layouts import apply_persisted_settings
        apply_persisted_settings(ctx)
    except Exception:
        _logger.debug("view_state: heatmap apply failed", exc_info=True)
