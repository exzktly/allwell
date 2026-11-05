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


def render_composite(fig, comp, crops: dict, timepoint: str, *, n_total: int, display_range=None,
                     note: str = "") -> list:
    """Draw *comp* (``composite_compute.Composite``) at *timepoint* on *fig* (cleared first); returns
    ``[(x, y, site, rank)]`` for turning a pointer position into a tile.

    *crops* is ``(well, q, r) -> {timepoint: Crop}`` for the nanowells cut so far; *n_total* is how many nanowells the
    window holds (the composite is one page of them), *note* the sentence the caption leads with."""
    from ui.theme import WELL_COLORS_TUPLE

    fig.clear()
    ax = fig.add_axes([0.02, 0.10, 0.96, 0.80])
    ax.set_axis_off()
    ax._aw_image_panel = True                            # pixel for pixel: no typed axis range on it
    _bg, ink, faint = _inks(ax)
    scale = crop_scale([(crops.get(t.source) or {}).get(timepoint) for t in comp.tiles], display_range)
    missing: Counter = Counter()
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
                               facecolor=WELL_COLORS_TUPLE[tile.patch % len(WELL_COLORS_TUPLE)],
                               gid="nw-composite-well"))
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
    _ink(fig.text(0.02, 0.97, comp.title(n_total) + f" · {timepoint}", va="top", fontsize=10, color=ink,
                  gid="nw-composite-title"))
    said = "Each tile is its nanowell's crop at one size; every tile on " + scale.said() + "."
    if missing:
        said += " No image: " + ", ".join(f"{n} {why}" for why, n in missing.most_common()) + "."
    _ink(fig.text(0.02, 0.012, " ".join(filter(None, [note, said])), fontsize=7, color=faint, wrap=True,
                  gid="nw-composite-caption"), muted=True)
    return [(t.x, t.y, t.source, t.rank) for t in comp.tiles]
