"""Heat Map tab domain state.

Per-feature state for the Heat Map tab — the colour-scale mode and the two
display toggles that used to live on the god-object as ``app._heatmap_scale_mode``
/ ``_heatmap_repset_avg`` / ``_heatmap_log_scale`` (A8 state migration). These are
persisted per layout by ``persistence/heatmap_layouts.py``.

Qt-free: imports only stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
    # Name of the active heat-map layout (``None`` ⇒ first / plate default).
    # Was the loose ``app._active_heatmap_layout_name`` — a poor fit for the
    # ``_heatmap_`` runtime prefix, so it lives here as typed state (A8).
    active_layout_name: str | None = None
    # Fixed-scale colour limits + the selected colormap name. These were the
    # loose ``app._heatmap_vmin`` / ``_vmax`` / ``_cmap_name`` runtime stamps —
    # but they are *persisted* display settings (serialised per layout by
    # ``persistence/heatmap_layouts.py``), so they belong on the typed slice, not
    # in the never-serialised ``AppRuntime`` namespace (D-persist). ``vmin`` /
    # ``vmax`` are ``None`` until the user sets a Fixed range (``nan`` while a
    # field is blank); ``cmap_name`` mirrors the LUT selector for persistence.
    vmin: float | None = None
    vmax: float | None = None
    cmap_name: str = ""
    # Settings dict loaded from disk by ``load_from_data_dir`` and applied to the
    # (possibly not-yet-built) heat-map widgets by ``apply_persisted_settings``.
    # Was the loose ``app._heatmap_persisted_settings`` bridge; kept typed so the
    # persistence loader/applier thread it through ``ctx.state`` (D-persist).
    persisted_settings: dict[str, Any] | None = field(default=None)
    # Timepoint values for the current session (the slider's tick → hours map),
    # recomputed from the dataset each ``refresh_heatmap_timepoints``. **Session-
    # transient — never serialised** (``_collect_settings`` builds its dict
    # explicitly and does not include it). It lives on the slice, not the runtime
    # namespace, so ``apply_persisted_settings`` can label the restored timepoint
    # via ``ctx.state`` without persistence reaching into ``AppRuntime`` (D-persist).
    tp_values: list = field(default_factory=list)
