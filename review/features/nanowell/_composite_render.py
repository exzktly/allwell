"""The window's hex composite on a figure: a tile for each nanowell, drawn at its place in ``composite_compute``'s
layout, with a patch label for each source well.

It is drawn to be read as what it is — an artificial array, not a well. Tiles are apart with a gap between them, the
whole sits in a dashed frame, nothing is drawn under it (no montage, no central-square mark), the title says it is not a
real array, and each tile carries a thin stripe along its foot in its source well's colour — the colour that well's
label above its patch is written in, which the caption says, so the stripe has its key on the figure. Every tile is on **one** brightness
scale. A tile with no image at the timepoint is a hatched hexagon, and the caption counts those by reason: degraded
output must not look like good output.
"""

from __future__ import annotations

from collections import Counter

import numpy as np
from matplotlib.patches import Rectangle, RegularPolygon

from review.features.nanowell.renderer import CELL_DOT_PT2, _ink, _inks, crop_scale

#: Half of a tile's side, in pitch units: tiles are 0.92 across, so a gap shows between neighbours.
TILE = 0.46
#: Height of the mark along a tile's foot, in pitch units.
_MARK = 0.08


def _panel_rects(n: int) -> list:
    """Figure-fraction ``[left, bottom, width, height]`` of *n* panels side by side in one row, inside the band
    between the title and the caption."""
    left, bottom, width, height = 0.02, 0.10, 0.96, 0.80
    cell_w = width / max(1, n)
    return [[left + k * cell_w, bottom, cell_w, height * 0.94] for k in range(n)]


def _tile_cells(tile, crop) -> np.ndarray:
    """*crop*'s counted cells (pixel ``(column, row)``) in the composite's coordinates on *tile*: the crop's pixels
    fill the tile's extent, pixel ``i``'s centre at ``i + 0.5`` of ``2 * TILE / width``."""
    h, w = crop.image.shape[:2]
    cells = np.asarray(crop.cells, dtype=float).reshape(-1, 2)
    return np.column_stack([tile.x - TILE + (cells[:, 0] + 0.5) * (2 * TILE / w),
                            tile.y - TILE + (cells[:, 1] + 0.5) * (2 * TILE / h)])


def _draw_panel(ax, comp, crops: dict, timepoint: str, scale, missing: Counter, colours, cells: Counter | None) -> None:
    """One composite at *timepoint* on *ax*: its tiles (or the hatched placeholder, counted into *missing*), each
    tile's counted cells when *cells* is a tally (``None``: Cells off), the patch stripes and labels, and the dashed
    frame."""
    from ui.nanowell_sites import CELL_COLOUR, CELL_EDGE_COLOUR

    _bg, _ink_colour, faint = _inks(ax)
    dots = []
    for tile in comp.tiles:
        crop = (crops.get(tile.source) or {}).get(timepoint)
        if crop is not None and crop.image is not None:
            lo, hi = scale.of(crop)
            ax.imshow(crop.image, cmap="gray", vmin=lo, vmax=hi, interpolation="nearest", gid="nw-composite-tile",
                      extent=(tile.x - TILE, tile.x + TILE, tile.y + TILE, tile.y - TILE))
            if cells is not None:
                if crop.cells is None:
                    cells["no_table"] += 1               # no cell table: none could be read, not "no cells"
                elif len(crop.cells):
                    dots.append(_tile_cells(tile, crop))
                    cells["drawn"] += len(crop.cells)
        else:
            why = (crop.note if crop is not None else "not cut yet" if tile.source not in crops
                   else f"no image at {timepoint}")
            missing[why] += 1
            ax.add_patch(RegularPolygon((tile.x, tile.y), 6, radius=0.5, fill=False, hatch="////", linewidth=0.6,
                                        edgecolor=faint, gid="nw-composite-missing"))
        ax.add_patch(Rectangle((tile.x - TILE, tile.y + TILE - _MARK), 2 * TILE, _MARK, linewidth=0,
                               facecolor=colours[tile.patch % len(colours)], gid="nw-composite-well"))
    if dots:
        xy = np.vstack(dots)
        ax.scatter(xy[:, 0], xy[:, 1], s=CELL_DOT_PT2 / 3, c=CELL_COLOUR, edgecolors=CELL_EDGE_COLOUR, linewidths=0.3,
                   zorder=5, gid="nw-composite-cells")
    for p, (well, x, y) in enumerate(comp.patches):
        # Written in the colour of its tiles' stripes: the label is the stripe's key. Not theme-inked, so a capture
        # flipped to publication keeps it.
        ax.text(x, y, well, ha="center", va="bottom", fontsize=8, fontweight="bold", color=colours[p % len(colours)],
                gid="nw-composite-patch")
    if comp.tiles:
        xs = [t.x for t in comp.tiles]
        ys = [t.y for t in comp.tiles]
        left, right = min(xs) - 0.9, max(xs) + 0.9
        top = min(ys + [y for _well, _x, y in comp.patches]) - 0.9      # the patch labels sit inside the frame
        bottom = max(ys) + 0.9
        ax.add_patch(Rectangle((left, top), right - left, bottom - top, fill=False, linestyle="--", edgecolor=faint,
                               linewidth=0.8, gid="nw-composite-frame"))
        ax.set_xlim(left - 0.2, right + 0.2)
        ax.set_ylim(bottom + 0.2, top - 0.2)             # y down: the smaller y is the top
    ax.set_aspect("equal")
    ax._aw_composite_home = (tuple(ax.get_xlim()), tuple(ax.get_ylim()))   # the unzoomed view, for the wheel
    ax._aw_composite_tp = timepoint                      # which timepoint a pointer over this panel is at


def render_composite(fig, comp, crops: dict, timepoints, *, n_total: int, display_range=None, note: str = "",
                     cells: bool = False, view=None) -> list:
    """Draw *comp* (``composite_compute.Composite``) on *fig* (cleared first), once per timepoint of *timepoints*,
    side by side in one row; returns ``[(x, y, site, rank)]`` for turning a pointer position into a tile — every
    panel puts a tile at the same place. *cells* draws each tile's counted cells as dots; *view* ``(xlim, ylim)`` is
    the zoom the wheel left (``_window_controller.on_popout_scroll``), applied to every panel alike.

    *crops* is ``(well, q, r) -> {timepoint: Crop}`` for the nanowells cut so far; *n_total* is how many nanowells the
    window holds (the composite is one page of them), *note* the sentence the caption leads with. Every tile of every
    panel is on one brightness scale, so a change over time reads as a change in the picture."""
    from ui.theme import WELL_COLORS_TUPLE

    timepoints = [str(timepoints)] if isinstance(timepoints, str) else [str(tp) for tp in timepoints]
    timepoints = timepoints or [""]                      # a figure always has a panel; callers pass at least one
    fig.clear()
    scale = crop_scale([(crops.get(t.source) or {}).get(tp) for tp in timepoints for t in comp.tiles], display_range)
    missing: Counter = Counter()
    tally: Counter | None = Counter() if cells else None
    several = len(timepoints) > 1
    ink = faint = None
    for timepoint, rect in zip(timepoints, _panel_rects(len(timepoints))):
        ax = fig.add_axes(rect)
        ax.set_axis_off()
        ax._aw_image_panel = True                        # pixel for pixel: no typed axis range on it
        _bg, ink, faint = _inks(ax)
        _draw_panel(ax, comp, crops, timepoint, scale, missing, WELL_COLORS_TUPLE, tally)
        if view is not None:
            ax.set_xlim(*view[0])
            ax.set_ylim(*view[1])
        if several:
            _ink(ax.set_title(timepoint, fontsize=9, color=ink, gid="nw-composite-tp"))
    said_tps = timepoints[0] if not several else f"{len(timepoints)} timepoints"
    _ink(fig.text(0.02, 0.97, comp.title(n_total) + f" · {said_tps}", va="top", fontsize=10, color=ink,
                  gid="nw-composite-title"))
    said = ("Each tile is its nanowell's crop at one size; every tile on " + scale.said() + ". The stripe under a "
            "tile is its source well's colour, the colour of that well's name above its patch. Scroll to zoom.")
    if tally is not None:
        said += f" Dots: the {tally['drawn']:,} cells the run counted in these nanowells."
        if tally["no_table"]:
            said += f" {tally['no_table']} tile(s) of a well with no cell table, so no dots there."
    if missing:
        said += " No image: " + ", ".join(f"{n} {why}" for why, n in missing.most_common()) + "."
    _ink(fig.text(0.02, 0.012, " ".join(filter(None, [note, said])), fontsize=7, color=faint, wrap=True,
                  gid="nw-composite-caption"), muted=True)
    return [(t.x, t.y, t.source, t.rank) for t in comp.tiles]
