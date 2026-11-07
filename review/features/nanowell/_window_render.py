"""The Distribution window on the figure: its band on each panel it covers, and a ring on each member's dot inside
it (``window_compute``). Drawn after ``renderer.render_distribution``, from the hit map it returned."""

from __future__ import annotations

from matplotlib.patches import Rectangle

from review.features.nanowell import window_compute as wc
from review.features.nanowell.renderer import DOT_PT

BAND_GID, RING_GID, DRAG_GID = "nw-window-band", "nw-window-ring", "nw-window-drag"


def draw_window(hits, window, result) -> None:
    """Draw *window* (``DistributionWindow``) with its *result* (``WindowResult``) on the axes *hits* records."""
    from ui.theme import Colors

    members = set(result.sites())
    by_panel = dict(hits.panels)
    for ax, index in hits.panels:
        # One band per covered slot: the swarms the window was drawn around, not the whole columns they sit in.
        for lo, hi in wc.merged_spans(result.spans.get(index, [])):
            ax.add_patch(Rectangle((lo, window.lo), hi - lo, window.hi - window.lo,
                                   facecolor=Colors.accent, alpha=0.12, edgecolor=Colors.accent, linewidth=1.0,
                                   zorder=1.5, gid=BAND_GID))
    for (ax, xs, ys, sites), values in zip(hits.entries, hits.values):
        spans = result.spans.get(by_panel.get(ax), [])
        keep = [k for k, (x, s, v) in enumerate(zip(xs, sites, values))
                if tuple(s) in members and wc.in_spans(float(x), spans) and window.lo <= v <= window.hi]
        if keep:
            ax.scatter([xs[k] for k in keep], [ys[k] for k in keep], s=(DOT_PT * 2.0) ** 2, facecolors="none",
                       edgecolors=Colors.accent, linewidths=1.0, zorder=4).set_gid(RING_GID)


def draw_drag(ax, x0, y0, x1, y1):
    """The rectangle a drag is drawing, in data units; returned so the release can remove it."""
    from ui.theme import Colors

    return ax.add_patch(Rectangle((min(x0, x1), min(y0, y1)), abs(x1 - x0), abs(y1 - y0), fill=False,
                                  edgecolor=Colors.accent, linewidth=1.0, linestyle="--", zorder=6, gid=DRAG_GID))
