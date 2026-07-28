"""96-well plate row/column constants and shared well palette.

Extracted from ``well_viewer.runtime_app`` so that controllers and views can
reach plate geometry without importing the GUI monolith.
"""

from __future__ import annotations

import theme

PLATE_ROWS = list("ABCDEFGH")
PLATE_COLS = [f"{c:02d}" for c in range(1, 13)]  # "01" … "12"

# One entry per 96-well plate position (see the length note on
# ``theme.WELL_COLORS_TUPLE``): the per-well rank colour is
# ``WELL_COLORS[well_rank(token) % len(WELL_COLORS)]`` and ranks step by 12 down
# a column, so a shorter (submultiple-of-12) palette would repeat colours down
# each column. 96 distinct colours keep every well unique.
WELL_COLORS = list(theme.WELL_COLORS_TUPLE)
