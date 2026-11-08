"""The *Lasso cells* button: draw round cells on a plot and save them (tracker M5).

Armed, every axes of the plot that carries cells (``core/cell_picking``) takes
a freehand lasso; the cells inside it are saved as a new set of saved cells
(``core/cell_sets.add_set``) and the button disarms. Saving changes no number
anywhere: the set is used from the Cell Browser's source menu, or excluded from
analysis under Cell Gating → Saved cells.

A lasso on a sampled projection says so in the set itself: "drawn on 2,500
sampled cells of 61,000" travels with the set, because cells the sample did
not draw cannot be inside the lasso however they would have plotted.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtWidgets import QPushButton

from review.core.cell_picking import cells_of
from review.core.cell_sets_state import CellSet


def cells_inside(cells, verts) -> np.ndarray:
    """Positions in *cells* whose point lies inside the lasso polygon *verts*."""
    from matplotlib.path import Path

    if len(cells) == 0 or len(verts) < 3:
        return np.empty(0, dtype=int)
    inside = Path(verts).contains_points(np.column_stack([cells.xs, cells.ys]))
    return np.flatnonzero(inside & (cells.nids >= 0))


def save_lassoed(ctx, cells, verts, *, source: str) -> CellSet | None:
    """Save the cells inside *verts* as a new set; return it (or ``None`` for none)."""
    from review.core.cell_sets import add_set

    idx = cells_inside(cells, verts)
    if not len(idx):
        return None
    note = ""
    if cells.sampled:
        n, total = cells.sampled
        note = f"drawn on {n:,} sampled cells of {total:,}"
    return add_set(ctx, [(str(cells.wells[i]), str(cells.fovs[i]), str(cells.tps[i]),
                          int(cells.nids[i])) for i in idx], source=source, note=note)


def make_lasso_button(ctx, parent, canvas_key: str, *, source: str) -> QPushButton:
    """A checkable *Lasso cells* button for the plot whose canvas is registered as *canvas_key*."""
    from matplotlib.widgets import LassoSelector

    btn = QPushButton("Lasso cells", parent)
    btn.setCheckable(True)
    btn.setProperty("variant", "secondary")
    btn.setToolTip("Draw round cells on the plot to save them as a set of saved cells. "
                   "Saving changes no number: use the set in the Cell Browser, or exclude "
                   "it under Cell Gating → Saved cells.")
    selectors: list = []

    def _disarm() -> None:
        for sel in selectors:
            sel.set_active(False)
            sel.disconnect_events()
        selectors.clear()
        if btn.isChecked():
            btn.setChecked(False)

    def _on_select(ax, verts) -> None:
        cells = cells_of(ax)
        saved = save_lassoed(ctx, cells, verts, source=source) if cells is not None else None
        _disarm()
        if saved is None:
            ctx.notify.set_status("No cells inside the lasso; nothing was saved.")
        else:
            ctx.notify.set_status(f"Saved {len(saved.cells):,} cells as “{saved.name}”"
                                  + (f" ({saved.note})" if saved.note else "")
                                  + ". Highlight it on the Scatter (Display ▾, or Cell Gating → "
                                    "Saved cells → Highlight on Scatter), use it as the Cell Browser's "
                                    "source, or rename it under Cell Gating → Saved cells.")

    def _toggled(on: bool) -> None:
        if not on:
            _disarm()
            return
        canvas = ctx.widgets.get_or_none(canvas_key)
        axes = [ax for ax in (canvas.figure.axes if canvas is not None else []) if cells_of(ax)]
        if not axes:
            btn.setChecked(False)
            ctx.notify.set_status("This plot draws no individual cells to lasso.")
            return
        for ax in axes:
            selectors.append(LassoSelector(ax, lambda verts, _ax=ax: _on_select(_ax, verts)))
        ctx.notify.set_status("Lasso armed: draw round the cells to save.")

    btn.toggled.connect(_toggled)
    return btn
