"""The window's hex composite on a figure: a tile for each nanowell, drawn at its place in ``composite_compute``'s
layout, with a patch label for each source well.

It is drawn to be read as what it is — an artificial array, not a well. Tiles are apart with a gap between them, the
whole sits in a dashed frame, nothing is drawn under it (no montage, no central-square mark), the title says it is not a
real array, and each tile carries a thin mark along its foot in its patch's colour. Every tile is on **one** brightness
scale. A tile with no image at the timepoint is a hatched hexagon, and the caption counts those by reason: degraded
output must not look like good output.
"""

from __future__ import annotations

from collections import Counter

from matplotlib.patches import Rectangle, RegularPolygon

from review.features.nanowell.renderer import _ink, _inks, crop_scale

#: Half of a tile's side, in pitch units: tiles are 0.92 across, so a gap shows between neighbours.
TILE = 0.46
#: Height of the mark along a tile's foot, in pitch units.
_MARK = 0.08


def _panel_rects(shape, n: int) -> list:
    """Figure-fraction ``[left, bottom, width, height]`` of each of *n* panels on *shape*
    (``crop_grid_compute.GridShape`` of one channel), inside the band between the title and the caption."""
    left, bottom, width, height = 0.02, 0.10, 0.96, 0.80
    cell_w, cell_h = width / shape.cols, height / shape.rows
    rects = []
    for k in range(n):
        row, col = shape.place(0, k)
        rects.append([left + col * cell_w, bottom + (shape.rows - 1 - row) * cell_h, cell_w, cell_h * 0.94])
    return rects


def _draw_panel(ax, comp, crops: dict, timepoint: str, scale, missing: Counter, colours) -> None:
    """One composite at *timepoint* on *ax*: its tiles (or the hatched placeholder, counted into *missing*), the
    patch marks and labels, and the dashed frame."""
    _bg, ink, faint = _inks(ax)
    for tile in comp.tiles:
        crop = (crops.get(tile.source) or {}).get(timepoint)
        if crop is not None and crop.image is not None:
            lo, hi = scale.of(crop)
            ax.imshow(crop.image, cmap="gray", vmin=lo, vmax=hi, interpolation="nearest", gid="nw-composite-tile",
                      extent=(tile.x - TILE, tile.x + TILE, tile.y + TILE, tile.y - TILE))
        else:
            why = (crop.note if crop is not None else "not cut yet" if tile.source not in crops
                   else f"no image at {timepoint}")
            missing[why] += 1
            ax.add_patch(RegularPolygon((tile.x, tile.y), 6, radius=0.5, fill=False, hatch="////", linewidth=0.6,
                                        edgecolor=faint, gid="nw-composite-missing"))
        ax.add_patch(Rectangle((tile.x - TILE, tile.y + TILE - _MARK), 2 * TILE, _MARK, linewidth=0,
                               facecolor=colours[tile.patch % len(colours)], gid="nw-composite-well"))
    for well, x, y in comp.patches:
        _ink(ax.text(x, y, well, ha="center", va="bottom", fontsize=8, color=ink, gid="nw-composite-patch"))
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


def render_composite(fig, comp, crops: dict, timepoints, *, n_total: int, display_range=None, note: str = "",
                     grid: str = "auto", cols: int = 0) -> list:
    """Draw *comp* (``composite_compute.Composite``) on *fig* (cleared first), once per timepoint of *timepoints*,
    side by side on the pop-out's grid shape *grid* / *cols* (``crop_grid_compute.grid_shape``); returns
    ``[(x, y, site, rank)]`` for turning a pointer position into a tile — every panel puts a tile at the same place.

    *crops* is ``(well, q, r) -> {timepoint: Crop}`` for the nanowells cut so far; *n_total* is how many nanowells the
    window holds (the composite is one page of them), *note* the sentence the caption leads with. Every tile of every
    panel is on one brightness scale, so a change over time reads as a change in the picture."""
    from review.features.nanowell.crop_grid_compute import grid_shape
    from ui.theme import WELL_COLORS_TUPLE

    timepoints = [str(timepoints)] if isinstance(timepoints, str) else [str(tp) for tp in timepoints]
    timepoints = timepoints or [""]                      # a figure always has a panel; callers pass at least one
    fig.clear()
    scale = crop_scale([(crops.get(t.source) or {}).get(tp) for tp in timepoints for t in comp.tiles], display_range)
    missing: Counter = Counter()
    several = len(timepoints) > 1
    shape = grid_shape(1, len(timepoints), grid, cols)
    ink = faint = None
    for timepoint, rect in zip(timepoints, _panel_rects(shape, len(timepoints))):
        ax = fig.add_axes(rect)
        ax.set_axis_off()
        ax._aw_image_panel = True                        # pixel for pixel: no typed axis range on it
        _bg, ink, faint = _inks(ax)
        _draw_panel(ax, comp, crops, timepoint, scale, missing, WELL_COLORS_TUPLE)
        if several:
            _ink(ax.set_title(timepoint, fontsize=9, color=ink, gid="nw-composite-tp"))
    said_tps = timepoints[0] if not several else f"{len(timepoints)} timepoints"
    _ink(fig.text(0.02, 0.97, comp.title(n_total) + f" · {said_tps}", va="top", fontsize=10, color=ink,
                  gid="nw-composite-title"))
    said = "Each tile is its nanowell's crop at one size; every tile on " + scale.said() + "."
    if missing:
        said += " No image: " + ", ".join(f"{n} {why}" for why, n in missing.most_common()) + "."
    _ink(fig.text(0.02, 0.012, " ".join(filter(None, [note, said])), fontsize=7, color=faint, wrap=True,
                  gid="nw-composite-caption"), muted=True)
    return [(t.x, t.y, t.source, t.rank) for t in comp.tiles]
