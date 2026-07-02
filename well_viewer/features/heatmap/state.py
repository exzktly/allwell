"""Heat Map tab domain state.

Per-feature state for the Heat Map tab — the colour-scale mode and the two
display toggles that used to live on the god-object as ``app._heatmap_scale_mode``
/ ``_heatmap_repset_avg`` / ``_heatmap_log_scale`` (A8 state migration). These are
persisted per layout by ``persistence/heatmap_layouts.py``.

Qt-free: imports only stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HeatmapState:
    """Heat-map feature-local display state.

    ``scale_mode`` — ``"Auto"`` (colour range fit to the data each redraw) or
    ``"Fixed"`` (use the vmin/vmax edits). ``repset_avg`` — show the rep-set
    average per cell instead of the single well. ``log_scale`` — colour on a log
    scale (values ≤ 0 masked).
    """

    scale_mode: str = "Auto"
    repset_avg: bool = False
    log_scale: bool = False
