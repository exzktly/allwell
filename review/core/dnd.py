"""Shared drag-and-drop protocol constants.

Neutral home for the MIME token exchanged between the drag *source* (the
well-plate widget, armed from ``core/tab_controller``) and the drop *sink* (the
Heat-Map layout sidebar, ``features/heatmap/sidebar_view``). Living here — below
both — lets each side reference the token without ``core`` importing a feature
or the feature importing a controller.
"""

from __future__ import annotations

#: MIME type carrying a single well token (e.g. ``"A01"``) during a plate→heatmap
#: drag. Both the drag source and the drop sink must agree on this exact string.
WELL_MIME = "application/x-well-token"
