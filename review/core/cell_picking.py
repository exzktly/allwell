"""Cells drawn on a plot: pick one with a click, ring the focused one (tracker M1–M3).

Multivariate's projections and Spatial's well maps draw one point per cell but
carried no cell identity — a point said which well it came from and nothing
else — so nothing drawn there could be opened, and a cell focused elsewhere
could not be found on them. A renderer that draws cells now hands this module
their identity with :func:`attach_cells`; :func:`wire_cell_picking` makes a
left click on such an axes focus the nearest drawn cell
(``review/core/cell_focus.py``) and keeps a ring on the focused cell through
every redraw and every focus change.

**A sampled plot says so.** UMAP and t-SNE draw a seeded sample of the cells in
scope. When the focused cell belongs to a well the plot covers but is not among
the drawn sample, the axes say that in words rather than show no ring — no ring
alone would read as "the cell is not in this analysis".

Qt-free apart from the canvas the caller hands in; the artists are matplotlib's.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from review.core.cell_focus import focus_cell, focused_cell, same_frame

#: Attribute an axes carries its :class:`PlottedCells` on.
CELLS_ATTR = "_aw_plotted_cells"
#: Artist ids, so a repaint removes exactly what it drew.
RING_GID = "focused_cell_ring"
NOTE_GID = "focused_cell_note"
#: How near a click must land to a point, in screen pixels.
PICK_RADIUS_PX = 12.0


@dataclass
class PlottedCells:
    """The cells drawn on one axes, in draw order, with their positions.

    ``sampled`` is ``(drawn, total)`` when the plot drew a sample of the cells
    in scope, ``scope_wells`` the wells that sample was drawn from.
    """

    xs: np.ndarray
    ys: np.ndarray
    wells: np.ndarray
    fovs: np.ndarray
    tps: np.ndarray
    nids: np.ndarray
    sampled: tuple[int, int] | None = None
    scope_wells: frozenset = field(default_factory=frozenset)

    def __len__(self) -> int:
        return int(len(self.xs))


def identity_arrays(df, sel) -> dict[str, np.ndarray]:
    """``well``-free identity of rows *sel* of a cell frame: field, timepoint token, nucleus id.

    A frame without one of the columns gives empty strings (or -1 ids) for it;
    such cells are drawn but cannot be picked or ringed.
    """
    import pandas as pd

    def _text(col):
        if col not in df.columns:
            return np.full(len(sel), "", dtype=object)
        return df[col].astype(str).to_numpy()[sel]

    if "nucleus_id" in df.columns:
        nid = pd.to_numeric(df["nucleus_id"], errors="coerce").to_numpy()[sel]
        nid = np.where(np.isfinite(nid), nid, -1).astype(np.int64)
    else:
        nid = np.full(len(sel), -1, dtype=np.int64)
    return {"fov": _text("fov"), "tp_token": _text("timepoint"), "nucleus_id": nid}


def attach_cells(ax, cells: PlottedCells) -> None:
    setattr(ax, CELLS_ATTR, cells)


def cells_of(ax) -> PlottedCells | None:
    return getattr(ax, CELLS_ATTR, None)


def clear_rings(ax) -> None:
    for artist in list(ax.lines) + list(ax.texts):
        if artist.get_gid() in (RING_GID, NOTE_GID):
            artist.remove()


def draw_ring(ax, x: float, y: float) -> None:
    from ui import theme
    (ring,) = ax.plot([x], [y], marker="o", markersize=14, markerfacecolor="none",
                      markeredgecolor=theme.Colors.focused_cell, markeredgewidth=2.0,
                      linestyle="none", gid=RING_GID, zorder=10)
    # The Properties pass restyles every marker line to the plot's point size
    # and fill; the ring is a mark about one cell, not a data point.
    ring._aw_no_style_override = True


def focused_indices(ctx, cells: PlottedCells) -> np.ndarray:
    """Positions in *cells* that are the focused cell (field compared normalised)."""
    cell = focused_cell(ctx)
    if cell is None or len(cells) == 0:
        return np.empty(0, dtype=int)
    cand = np.flatnonzero((cells.wells.astype(str) == cell.well) & (cells.nids == cell.nucleus_id))
    return np.asarray([i for i in cand
                       if same_frame(cell, cell.well, str(cells.fovs[i]), str(cells.tps[i]))],
                      dtype=int)


def ring_focused(ctx, fig) -> int:
    """Re-ring the focused cell on every cell-carrying axes of *fig*; return rings drawn."""
    from ui import theme

    drawn = 0
    cell = focused_cell(ctx)
    for ax in list(getattr(fig, "axes", ())):
        cells = cells_of(ax)
        if cells is None:
            continue
        clear_rings(ax)
        if cell is None:
            continue
        hits = focused_indices(ctx, cells)
        for i in hits:
            draw_ring(ax, float(cells.xs[i]), float(cells.ys[i]))
        drawn += len(hits)
        if not len(hits) and cells.sampled and cell.well in cells.scope_wells:
            n, total = cells.sampled
            ax.text(0.01, 0.01, f"The focused cell ({cell.caption}) is not among the "
                                f"{n:,} sampled cells of {total:,}.",
                    transform=ax.transAxes, ha="left", va="bottom", fontsize=7,
                    color=theme.Colors.focused_cell, gid=NOTE_GID, zorder=10)
    return drawn


def pick_cell(event, radius_px: float = PICK_RADIUS_PX) -> tuple[str, str, str, int] | None:
    """The drawn cell nearest a mouse event, as ``(well, fov, tp, nucleus_id)``, or ``None``."""
    ax = getattr(event, "inaxes", None)
    cells = cells_of(ax) if ax is not None else None
    if cells is None or not len(cells) or event.x is None or event.y is None:
        return None
    coords = np.column_stack([cells.xs, cells.ys]).astype(float)
    with np.errstate(invalid="ignore"):
        px = ax.transData.transform(coords)
    d = np.hypot(px[:, 0] - float(event.x), px[:, 1] - float(event.y))
    d = np.where(np.isfinite(d), d, np.inf)
    i = int(np.argmin(d))
    if not np.isfinite(d[i]) or d[i] > radius_px or int(cells.nids[i]) < 0:
        return None
    return (str(cells.wells[i]), str(cells.fovs[i]), str(cells.tps[i]), int(cells.nids[i]))


def wire_cell_picking(ctx, canvas, fig_of) -> None:
    """Click-to-focus on *canvas*, and the focused cell's ring on the figure ``fig_of()`` returns.

    A click while the toolbar's zoom or pan is armed belongs to the toolbar.
    """
    def _on_press(event) -> None:
        if getattr(event, "button", None) != 1:
            return
        toolbar = getattr(canvas, "toolbar", None)
        if toolbar is not None and getattr(toolbar, "mode", ""):
            return
        picked = pick_cell(event)
        if picked is not None:
            focus_cell(ctx, *picked)

    def _repaint(*_args) -> None:
        fig = fig_of()
        if fig is not None:
            ring_focused(ctx, fig)
            canvas.draw_idle()

    canvas.mpl_connect("button_press_event", _on_press)
    ctx.bus.focused_cell_changed.connect(_repaint)
