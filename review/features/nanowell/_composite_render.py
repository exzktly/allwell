"""The window's hex composite on a figure: a tile for each nanowell, drawn at its place in ``composite_compute``'s
layout, with a patch label for each source well.

It is drawn to be read as what it is — an artificial array, not a well. Tiles are apart with a gap between them, the
whole sits in a dashed frame, nothing is drawn under it (no montage, no central-square mark), the title says it is not a
real array, and each tile carries a thin stripe along its foot coloured by what the pop-out's **Colour** chose — its
source well, or its well's value of a sample variable — with a key under the title naming each colour drawn (or no
stripe at all). Every tile is on **one** brightness
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


def _panel_rects(n: int, top: float = 0.852) -> list:
    """Figure-fraction ``[left, bottom, width, height]`` of *n* panels side by side in one row, between the caption
    and *top* (lowered under a key of more than one row)."""
    left, bottom, width = 0.02, 0.10, 0.96
    cell_w = width / max(1, n)
    return [[left + k * cell_w, bottom, cell_w, max(top - bottom, 0.2)] for k in range(n)]


#: The most entries in one row of the stripes' key, and the height a row takes, as a fraction of the figure.
_KEY_COLUMNS, _KEY_ROW = 8, 0.04


def _draw_key(fig, key, title: str, ink) -> float:
    """The stripes' key under the title — a swatch and a label per colour drawn — and the top the panels keep below
    it."""
    import math

    from matplotlib.patches import Patch

    if not key:
        return 0.852
    columns = min(len(key), _KEY_COLUMNS)
    rows = math.ceil(len(key) / columns)
    legend = fig.legend([Patch(facecolor=colour, edgecolor="none") for _label, colour in key],
                        [label for label, _colour in key], loc="upper left", bbox_to_anchor=(0.02, 0.935),
                        ncol=columns, fontsize=7, frameon=False, handlelength=1.2, handleheight=0.6,
                        columnspacing=1.0, title=f"Stripe: {title}", title_fontsize=7, alignment="left")
    legend.set_gid("nw-composite-key")
    for text in legend.get_texts() + [legend.get_title()]:
        _ink(text)
        text.set_color(ink)
    return 0.852 - rows * _KEY_ROW


def _to_tile(tile, shape, points) -> np.ndarray:
    """Crop pixel positions ``(column, row)`` (N × 2) in the composite's coordinates on *tile*: the crop's pixels fill
    the tile's extent, pixel ``i``'s centre at ``i + 0.5`` of ``2 * TILE / width``."""
    h, w = shape[:2]
    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    return np.column_stack([tile.x - TILE + (pts[:, 0] + 0.5) * (2 * TILE / w),
                            tile.y - TILE + (pts[:, 1] + 0.5) * (2 * TILE / h)])


def _tile_cells(tile, crop) -> np.ndarray:
    """*crop*'s counted cells' centres in the composite's coordinates on *tile* (:func:`_to_tile`)."""
    return _to_tile(tile, crop.image.shape, crop.cells)


def _tile_outlines(tile, crop) -> tuple[list, np.ndarray]:
    """``(lines, dotted)`` for *crop*'s counted cells on *tile*: each traced outline (``Crop.outlines``, the pixels the
    run counted the cell on) as composite-coordinate lines, and a mask of the cells drawn as a dot instead — every one
    when the well kept no outlines, else those it kept none of — as ``renderer._draw_crop_cells`` draws a crop."""
    n = len(crop.cells)
    outlines = crop.outlines if crop.outlines is not None and len(crop.outlines) == n else None
    if outlines is None:
        return [], np.ones(n, dtype=bool)
    lines = [_to_tile(tile, crop.image.shape, line) for o in outlines if o for line in o]
    return lines, np.array([not o for o in outlines], dtype=bool)


def _draw_panel(ax, comp, crops: dict, timepoint: str, scale, missing: Counter, inks: dict,
                cells: Counter | None) -> None:
    """One composite at *timepoint* on *ax*: its tiles (or the hatched placeholder, counted into *missing*), each
    tile's counted cells when *cells* is a tally (``None``: Cells off), each tile's stripe in its well's colour of
    *inks* (``well -> colour``; none drawn for a well it lacks), the wells' names and the dashed frame."""
    from ui.nanowell_sites import CELL_COLOUR, CELL_EDGE_COLOUR

    from matplotlib.collections import LineCollection

    _bg, ink, faint = _inks(ax)
    dots, outlines = [], []
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
                    lines, dotted = _tile_outlines(tile, crop)
                    outlines.extend(lines)
                    if dotted.any():
                        dots.append(_tile_cells(tile, crop)[dotted])
                    cells["drawn"] += len(crop.cells)
                    cells["outlined"] += int((~dotted).sum())
        else:
            why = (crop.note if crop is not None else "not cut yet" if tile.source not in crops
                   else f"no image at {timepoint}")
            missing[why] += 1
            ax.add_patch(RegularPolygon((tile.x, tile.y), 6, radius=0.5, fill=False, hatch="////", linewidth=0.6,
                                        edgecolor=faint, gid="nw-composite-missing"))
        colour = inks.get(comp.patches[tile.patch][0])
        if colour is not None:
            ax.add_patch(Rectangle((tile.x - TILE, tile.y + TILE - _MARK), 2 * TILE, _MARK, linewidth=0,
                                   facecolor=colour, gid="nw-composite-well"))
    if outlines:
        # The rim colour under the fill colour, so an outline reads on a bright cell and a dark floor alike.
        ax.add_collection(LineCollection(outlines, colors=CELL_EDGE_COLOUR, linewidths=1.2, zorder=5,
                                         gid="nw-composite-outlines-rim"))
        ax.add_collection(LineCollection(outlines, colors=CELL_COLOUR, linewidths=0.6, zorder=5,
                                         gid="nw-composite-outlines"))
    if dots:
        xy = np.vstack(dots)
        ax.scatter(xy[:, 0], xy[:, 1], s=CELL_DOT_PT2 / 3, c=CELL_COLOUR, edgecolors=CELL_EDGE_COLOUR, linewidths=0.3,
                   zorder=5, gid="nw-composite-cells")
    for well, x, y in comp.patches:
        _ink(ax.text(x, y, well, ha="center", va="bottom", fontsize=8, color=ink, gid="nw-composite-patch"))
    if comp.tiles:
        xs = [t.x for t in comp.tiles]
        ys = [t.y for t in comp.tiles]
        left, right = min(xs) - 0.9, max(xs) + 0.9
        top = min(ys + [y for _well, _x, y in comp.patches]) - 0.9      # the patch labels sit inside the frame
        bottom = max(ys) + 0.9
        ax.set_xlim(left, right)
        ax.set_ylim(bottom, top)                         # y down: the smaller y is the top
    ax.set_aspect("equal")
    # The frame is the panel's own edge, in axes coordinates: drawn around the data's extent it left the view as soon
    # as the wheel zoomed in, and a panel without it no longer read as one timepoint's composite. Unzoomed the panel
    # is exactly that extent (the limits above, the aspect fitting the box to them).
    ax.add_patch(Rectangle((0, 0), 1, 1, transform=ax.transAxes, fill=False, linestyle="--", edgecolor=faint,
                           linewidth=0.8, clip_on=False, gid="nw-composite-frame"))
    ax._aw_composite_home = (tuple(ax.get_xlim()), tuple(ax.get_ylim()))   # the unzoomed view, for the wheel
    ax._aw_composite_tp = timepoint                      # which timepoint a pointer over this panel is at


def render_composite(fig, comp, crops: dict, timepoints, *, n_total: int, display_range=None, note: str = "",
                     cells: bool = False, view=None, labels: bool = True, inks: dict | None = None, key=None,
                     key_title: str = "Source well") -> list:
    """Draw *comp* (``composite_compute.Composite``) on *fig* (cleared first), once per timepoint of *timepoints*,
    side by side in one row; returns ``[(x, y, site, rank)]`` for turning a pointer position into a tile — every
    panel puts a tile at the same place. *cells* draws each tile's counted cells — traced outlines, a dot where the run
    kept none; *view* ``(xlim, ylim)`` is the zoom or pan the pointer left (``_window_controller``), applied to every
    panel alike. *labels* off (the pop-out's **Labels**) leaves out each panel's timepoint and the caption; the title,
    the wells' names and the stripes' key stay. *inks* (``well -> colour``) colours each tile's stripe and *key*
    (``(label, colour)`` pairs, titled *key_title*) is their key under the title; with *inks* ``None`` each well takes
    its rank colour in patch order and the key names the wells, and with *inks* empty no stripe is drawn.

    *crops* is ``(well, q, r) -> {timepoint: Crop}`` for the nanowells cut so far; *n_total* is how many nanowells the
    window holds (the composite is one page of them), *note* the sentence the caption leads with. Every tile of every
    panel is on one brightness scale, so a change over time reads as a change in the picture."""
    from ui.theme import WELL_COLORS_TUPLE

    if inks is None:
        inks = {well: WELL_COLORS_TUPLE[i % len(WELL_COLORS_TUPLE)] for i, (well, _x, _y) in enumerate(comp.patches)}
        key = tuple((well, inks[well]) for well, _x, _y in comp.patches)
    timepoints = [str(timepoints)] if isinstance(timepoints, str) else [str(tp) for tp in timepoints]
    timepoints = timepoints or [""]                      # a figure always has a panel; callers pass at least one
    fig.clear()
    scale = crop_scale([(crops.get(t.source) or {}).get(tp) for tp in timepoints for t in comp.tiles], display_range)
    missing: Counter = Counter()
    tally: Counter | None = Counter() if cells else None
    several = len(timepoints) > 1
    _bg, ink, faint = _inks(fig)
    top = _draw_key(fig, key if inks else (), key_title, ink)
    for timepoint, rect in zip(timepoints, _panel_rects(len(timepoints), top)):
        ax = fig.add_axes(rect)
        ax.set_axis_off()
        ax._aw_image_panel = True                        # pixel for pixel: no typed axis range on it
        _bg, ink, faint = _inks(ax)
        _draw_panel(ax, comp, crops, timepoint, scale, missing, inks, tally)
        if view is not None:
            ax.set_xlim(*view[0])
            ax.set_ylim(*view[1])
        if several and labels:
            _ink(ax.set_title(timepoint, fontsize=9, color=ink, gid="nw-composite-tp"))
    said_tps = timepoints[0] if not several else f"{len(timepoints)} timepoints"
    _ink(fig.text(0.02, 0.97, comp.title(n_total) + f" · {said_tps}", va="top", fontsize=10, color=ink,
                  gid="nw-composite-title"))
    said = "Each tile is its nanowell's crop at one size; every tile on " + scale.said() + "."
    if inks:
        what = "source well" if key_title == "Source well" else f"well's {key_title}"
        said += f" The stripe under a tile is its {what}, keyed under the title."
    said += " Scroll to zoom, drag to pan."
    if tally is not None:
        outlined, drawn = tally["outlined"], tally["drawn"]
        what = ("Outlines" if drawn and outlined == drawn else "Outlines and dots" if outlined else "Dots")
        said += f" {what}: the {drawn:,} cells the run counted in these nanowells"
        if drawn and outlined < drawn:
            said += f" ({drawn - outlined:,} a dot: the run kept no outline of it)"
        said += "."
        if tally["no_table"]:
            said += f" {tally['no_table']} tile(s) of a well with no cell table, so no dots there."
    if missing:
        said += " No image: " + ", ".join(f"{n} {why}" for why, n in missing.most_common()) + "."
    if labels:
        _ink(fig.text(0.02, 0.012, " ".join(filter(None, [note, said])), fontsize=7, color=faint, wrap=True,
                      gid="nw-composite-caption"), muted=True)
    return [(t.x, t.y, t.source, t.rank) for t in comp.tiles]
