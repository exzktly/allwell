"""The *Resolve* walk's mark on the Array view (pure matplotlib): a ring round the place its guide card points at —
where the central square most likely is (``resolve_compute.hint_for``) — so "click the square" comes with a where.

The ring is drawn at a fixed size on screen, not in micrometres, so it reads the same at any zoom, and it is hollow
and dashed, so the square under it stays visible: it marks a place to look, not the answer.
"""

from __future__ import annotations

#: The ring's radius, in figure pixels: wider than a nanowell at the walk's opening zoom, so the square sits inside.
TARGET_RADIUS_PX = 26


def draw_resolve_target(ax, region, size_px, point_um) -> None:
    """Ring *point_um* (well frame) on the Array view's *ax*, which shows *region* at *size_px* pixels."""
    from matplotlib.patches import Ellipse

    from ui.theme import Colors

    if point_um is None or region is None or not size_px or not size_px[0] or not size_px[1]:
        return
    left, top, right, bottom = region
    rx = TARGET_RADIUS_PX * abs(right - left) / size_px[0]
    ry = TARGET_RADIUS_PX * abs(bottom - top) / size_px[1]
    x, y = point_um
    for width, colour, z in ((4.0, Colors.surface, 8), (2.0, Colors.accent, 9)):
        ax.add_patch(Ellipse((x, y), 2 * rx, 2 * ry, fill=False, edgecolor=colour, linewidth=width,
                             linestyle=(0, (4, 3)) if z == 9 else "-", zorder=z, gid="nw-resolve-target"))
