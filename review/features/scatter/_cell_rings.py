"""The focused cell's ring and the highlighted saved cells on the per-cell Scatter (tracker F5, M7).

Every redraw files its points through :func:`store_interaction_points`, which
also resolves each drawn point to its cell — well, field, timepoint token and
nucleus id — in one vectorised read per well (``plotted_cells_for``). Points
carry the row's *index label* (``collect`` stores ``sel.index``), so the rows are
looked up by label; the focused ring used to read them by position, which agreed
only while every cached frame had a default index.

**The focused cell** gets the one large ring (``cell_picking.draw_ring``),
repainted alone on every focus change.

**A highlighted saved set** (``CellSetsState.highlight``, chosen under Display ▾
or from the Saved cells panel) is not drawn as rings: a 14-px ring per cell
merges into a blob at a few hundred and drowns the focused one. Instead the
set's points are redrawn solid in ``Colors.saved_cells_highlight`` on top, as
one artist, and every other point keeps its colouring but is faded; on a
density-coloured cloud the set gets a point-sized outline instead. A note on the
figure says how many of the set are shown, why the rest are not, how many were
lost to re-segmentation, and when the set is also excluded from analysis.
Changing the highlight redraws the plot (``cell_sets.set_highlighted`` emits
``plot_options_changed``), so the fade is always applied to fresh artists.
"""

from __future__ import annotations

import numpy as np

from core.safe import swallowed
from review.core.cell_picking import RING_GID, PlottedCells, clear_rings, draw_ring, focused_indices

__all__ = ["RING_GID", "SET_GID", "paint_focus_ring", "plotted_cells_for",
           "store_interaction_points"]

#: The highlighted set's artist and note.
SET_GID = "saved_cells_highlight"
NOTE_GID = "saved_cells_note"
#: How much an un-highlighted point keeps of its opacity.
FADE = 0.25


def plotted_cells_for(ctx, points) -> PlottedCells:
    """Each drawn point's cell, from the interaction points ``(x, y, (well, filename, nid, row_label))``."""
    from review.data.narrow_rows import narrow_rows

    n = len(points)
    xs = np.fromiter((p[0] for p in points), float, n)
    ys = np.fromiter((p[1] for p in points), float, n)
    wells = np.array([str(p[2][0]) for p in points], dtype=object)
    fovs = np.full(n, "", dtype=object)
    tps = np.full(n, "", dtype=object)
    nids = np.full(n, -1, dtype=np.int64)
    rows = np.array([p[2][3] for p in points], dtype=object)
    for well in sorted(set(wells.tolist())):
        at = np.flatnonzero(wells == well)
        try:
            frame = narrow_rows(ctx, well, {"fov", "timepoint", "nucleus_id"})
            labels = [r for r in rows[at]]
            sub = frame.reindex(labels)
        except Exception:
            swallowed(f"scatter: reading the cells drawn for {well}")
            continue
        if "fov" in sub.columns:
            fovs[at] = sub["fov"].astype(str).to_numpy()
        if "timepoint" in sub.columns:
            tps[at] = sub["timepoint"].astype(str).to_numpy()
        if "nucleus_id" in sub.columns:
            import pandas as pd
            v = pd.to_numeric(sub["nucleus_id"], errors="coerce").to_numpy()
            nids[at] = np.where(np.isfinite(v), v, -1).astype(np.int64)
    return PlottedCells(xs=xs, ys=ys, wells=wells, fovs=fovs, tps=tps, nids=nids)


def _cells_on_axes(ctx):
    cache = ctx.runtime.scatter.interaction_cache or {}
    return cache.get("cells") or []


def paint_focus_ring(ctx, *, draw: bool = True) -> int:
    """Remove the old ring and ring the focused cell's points; return how many."""
    hits = 0
    for ax, cells in _cells_on_axes(ctx):
        clear_rings(ax)
        try:
            idx = focused_indices(ctx, cells)
        except Exception:
            swallowed("scatter: locating the focused cell")
            continue
        for i in idx:
            draw_ring(ax, float(cells.xs[i]), float(cells.ys[i]))
        hits += len(idx)
    if draw:
        canvas = ctx.widgets.get_or_none("_scatter_canvas")
        if canvas is not None:
            canvas.draw_idle()
    return hits


def membership(cells: PlottedCells, wanted: frozenset) -> np.ndarray:
    """Which drawn points are cells of *wanted* (normalised ``CellKey`` tuples)."""
    from domain.image_identity import normalize_fov, normalize_nucleus_id, normalize_tp

    if not wanted or len(cells) == 0:
        return np.zeros(len(cells), dtype=bool)
    in_wells = {c[0] for c in wanted}
    out = np.zeros(len(cells), dtype=bool)
    cand = np.flatnonzero(np.isin(cells.wells.astype(str), list(in_wells)) & (cells.nids >= 0))
    memo_f: dict = {}
    memo_t: dict = {}
    for i in cand:
        f, t = cells.fovs[i], cells.tps[i]
        nf = memo_f.get(f) or memo_f.setdefault(f, normalize_fov(f))
        nt = memo_t.get(t) or memo_t.setdefault(t, normalize_tp(t))
        out[i] = (str(cells.wells[i]), nf, nt, normalize_nucleus_id(int(cells.nids[i]))) in wanted
    return out


def _is_cloud(artist) -> bool:
    if artist.get_gid() in (SET_GID, RING_GID):
        return False
    if getattr(artist, "_aw_density_collection", False) or getattr(artist, "_aw_marker_collection", False):
        return True
    marker = getattr(artist, "get_marker", lambda: None)()
    style = getattr(artist, "get_linestyle", lambda: "-")()
    return marker not in (None, "None", "none", "", " ") and style in ("None", "none", "", " ")


def highlight_note(ctx, cell_set, shown: int, in_plotted_wells: int) -> str:
    """The sentence under the plot: shown, not shown and why, lost, excluded."""
    from review.core.cell_sets import usable_cells

    usable = len(usable_cells(ctx, cell_set))
    lost = len(cell_set.cells) - usable
    parts = [f"Saved cells: {cell_set.name} — {shown:,} shown"]
    absent = usable - shown
    if absent:
        outside = usable - in_plotted_wells
        reasons = []
        if outside:
            reasons.append(f"{outside:,} in wells not plotted")
        if in_plotted_wells - shown:
            reasons.append(f"{in_plotted_wells - shown:,} filtered out here "
                           f"(timepoints, axis floor, inclusion, gates or sampling)")
        parts.append(f"{absent:,} not on this plot: " + ", ".join(reasons))
    if lost:
        parts.append(f"{lost:,} lost to re-segmentation")
    if ctx.state.cell_sets.exclude == cell_set.name:
        parts.append("excluded from analysis")
    return " · ".join(parts)


def paint_saved_cells(ctx, cells_on_axes) -> int:
    """Fade the cloud and redraw the highlighted set's points on top; return how many."""
    from review.core.cell_sets import highlighted_set, usable_cells
    from review.core.plot_style import figure_prefs, follows_theme, tokens_for
    from ui import theme

    cell_set = highlighted_set(ctx)
    if cell_set is None or not cells_on_axes:
        return 0
    wanted = usable_cells(ctx, cell_set)
    fig = cells_on_axes[0][0].figure
    size = float(figure_prefs(ctx, fig).get("marker_size", 5.0) or 5.0)
    shown = 0
    plotted_wells: set = set()
    for ax, cells in cells_on_axes:
        plotted_wells |= set(cells.wells.astype(str).tolist())
        mask = membership(cells, wanted)
        density = any(getattr(c, "_aw_density_collection", False) for c in ax.collections)
        for artist in list(ax.lines) + list(ax.collections):
            if _is_cloud(artist):
                artist.set_alpha((artist.get_alpha() if artist.get_alpha() is not None else 1.0) * FADE)
        if not mask.any():
            continue
        ink = theme.Colors.saved_cells_highlight
        (line,) = ax.plot(cells.xs[mask], cells.ys[mask], linestyle="none", marker="o",
                          markersize=size, markeredgewidth=1.0,
                          markerfacecolor="none" if density else ink, markeredgecolor=ink,
                          gid=SET_GID, zorder=9)
        line._aw_no_style_override = True
        shown += int(mask.sum())
    in_wells = sum(1 for c in wanted if c[0] in plotted_wells)
    ax0 = cells_on_axes[0][0]
    follows_theme(ax0.text(
        0.01, 0.06, highlight_note(ctx, cell_set, shown, in_wells), transform=ax0.transAxes,
        ha="left", va="bottom", fontsize=7, color=tokens_for(ax0)[2], zorder=6, clip_on=False,
        gid=NOTE_GID), muted=True)
    return shown


def store_interaction_points(ctx, cache: dict) -> None:
    """Keep a redraw's click-interaction points, resolve their cells, and mark them.

    The one place a redraw files its points, so a layout cannot file them and
    forget the marks; the canvas is drawn by the redraw itself.
    """
    try:
        cache["cells"] = [(ax, plotted_cells_for(ctx, pts)) for ax, pts in cache.get("axes_points") or []]
    except Exception:
        swallowed("scatter: resolving the drawn cells")
        cache["cells"] = []
    ctx.runtime.scatter.interaction_cache = cache
    try:
        paint_saved_cells(ctx, cache["cells"])
    except Exception:
        swallowed("scatter: highlighting the saved cells")
    paint_focus_ring(ctx, draw=False)
