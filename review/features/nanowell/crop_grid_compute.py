"""What the pop-out's crop grid shows and where each panel goes: Qt-free and matplotlib-free.

The **Nanowell crops** pop-out draws one nanowell over time (``renderer.render_crop_grid``). Its own controls choose
which of the timepoints are shown (``NanowellState.popout_tps``), which channels are drawn beside the page's image
channel (``NanowellState.popout_channels``), and the shape of the grid (``NanowellState.popout_grid`` /
``popout_cols``). The rules those choices follow live here, so they can be tested without a figure or a window.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: The pop-out grid's shapes. ``auto``: one channel wraps its timepoints into a near-square grid, several channels
#: take one row each with the timepoints across. ``row``: every timepoint across, one row per channel. ``column``:
#: every timepoint down, one column per channel. ``columns``: the timepoints across, wrapped every
#: ``popout_cols`` panels, each wrapped line one row per channel.
POPOUT_GRIDS = ("auto", "row", "column", "columns")
#: The widest wrapped grid the **Grid** control offers.
POPOUT_MAX_COLS = 12


@dataclass(frozen=True)
class GridShape:
    """A grid of ``rows`` by ``cols`` panels, and where the panel of channel *c* at timepoint *k* goes."""

    rows: int
    cols: int
    n_channels: int
    wrap: int                 # timepoints per line across (unused when ``down``)
    down: bool                # timepoints run down the grid, one column per channel

    def place(self, c: int, k: int) -> tuple[int, int]:
        """``(row, column)`` of the panel of the *c*-th channel at the *k*-th timepoint shown."""
        if self.down:
            return k, c
        return (k // self.wrap) * self.n_channels + c, k % self.wrap


def grid_shape(n_channels: int, n_tps: int, grid: str = "auto", cols: int = 0) -> GridShape:
    """The grid for *n_channels* rows of crops over *n_tps* timepoints, in the shape *grid* (:data:`POPOUT_GRIDS`;
    anything else is ``auto``) — *cols* being the timepoints per line of a ``columns`` grid, at most *n_tps*."""
    n_channels, n_tps = max(1, int(n_channels)), max(1, int(n_tps))
    if grid == "column":
        return GridShape(n_tps, n_channels, n_channels, 1, True)
    if grid == "row":
        wrap = n_tps
    elif grid == "columns" and int(cols or 0) >= 1:
        wrap = min(n_tps, int(cols))
    elif n_channels == 1:
        # The near-square wrap the pop-out has always drawn one channel with: a little wider than tall.
        wrap = int(min(n_tps, max(1, math.ceil(math.sqrt(n_tps * 1.6)))))
    else:
        wrap = n_tps
    return GridShape(math.ceil(n_tps / wrap) * n_channels, wrap, n_channels, wrap, False)


def strip_columns(n: int, width: float, height: float) -> int:
    """How many columns lay *n* square crops out largest in a *width* × *height* area (any units, the same for both).

    The site detail's strip draws one crop per timepoint. At a desk it is wide and short, and one row is largest; at
    phone width twelve crops in one row were a few pixels each, with their captions run together, so the strip wraps
    into as many rows as make each crop biggest. Ties keep the fewer rows."""
    if n <= 0 or width <= 0 or height <= 0:
        return max(1, n)
    best, best_side = n, -1.0
    for cols in range(n, 0, -1):
        side = min(width / cols, height / math.ceil(n / cols))
        if side > best_side * (1 + 1e-9):
            best, best_side = cols, side
    return best


def shown_timepoints(timepoints, chosen) -> list:
    """The timepoints the pop-out shows, in the nanowell's order: those *chosen*, or every one when none is chosen —
    or when none of those chosen is one of this nanowell's (a choice made on another dataset), so the grid is never
    empty because of a stale choice."""
    timepoints = [str(tp) for tp in timepoints]
    chosen = {str(tp) for tp in (chosen or ())}
    kept = [tp for tp in timepoints if tp in chosen]
    return kept or timepoints


def shown_channels(page_channel: str, extras, available) -> list:
    """The channels the pop-out draws, one row (or column) each: the page's image channel first, always, then each
    of *extras* the dataset has, in the dataset's order (*available*: ``""`` for phase, then its fluorescence
    channels). A channel is never drawn twice."""
    page_channel = page_channel or ""
    wanted = {str(c).lower() for c in (extras or ())}
    out = [page_channel]
    for channel in available:
        if channel != page_channel and str(channel).lower() in wanted:
            out.append(channel)
    return out


def grid_label(grid: str, cols: int) -> str:
    """The **Grid** control's text for a shape."""
    if grid == "row":
        return "Grid: one row"
    if grid == "column":
        return "Grid: one column"
    if grid == "columns" and int(cols or 0) >= 1:
        return f"Grid: {int(cols)} across"
    return "Grid: auto"


def save_name(site, channels) -> str:
    """The pop-out's default Save-As file name: the nanowell's address and what it is an image of, e.g.
    ``A01_q3_r-2_phase_EGFP_over_time.png``."""
    well, q, r = site
    said = "_".join((c or "phase") for c in channels) or "phase"
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in f"{well}_q{int(q)}_r{int(r)}_{said}")
    return f"{safe}_over_time.png"
