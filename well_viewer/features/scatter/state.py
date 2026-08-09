"""Scatter tab domain state.

Per-feature scalar display state for the Scatter tab — the active sub-tab
(per-cell vs per-well aggregate), the per-cell "colour by timepoint" toggle,
and the "hide gates" toggle. These used to live on the god-object as
``app._scatter_mode`` / ``_scatter_color_by_tp`` / ``_scatter_gates_hidden``
(A8 state migration).

Only the flat display scalars move here. The gate *geometry* list, per-timepoint
selection dicts, matplotlib figures/canvases, and the interaction caches stay on
the app for now — they are runtime objects, not typed-state candidates.

Qt-free: imports only stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScatterState:
    """Scatter feature-local display state.

    ``mode`` — ``"cells"`` (per-cell points) or ``"aggregate"`` (per-well
    aggregate) sub-tab. ``color_by_tp`` — colour per-cell points by timepoint.
    ``gates_hidden`` — hide the gate overlays (inverse of the sidebar
    "Show gates" toggle). ``apply_gates_to_points`` — plot only the cells inside
    the scatter gates, instead of the full cloud with the gates drawn over it
    (the per-cell scatter is the one view that does not filter by its own gates
    unless asked; default off keeps that). ``cut_status_posted`` — this tab put
    the "every cell was cut" notice on the status bar and still owns it, so a
    later successful redraw knows to take its own message down without
    clobbering one another part of the app has since posted there.
    """

    mode: str = "cells"
    color_by_tp: bool = False
    gates_hidden: bool = False
    apply_gates_to_points: bool = False
    cut_status_posted: bool = False
