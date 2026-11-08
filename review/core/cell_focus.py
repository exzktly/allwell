"""Set, clear and read the focused cell (``review/core/focus_state.py``).

Every surface that names a cell calls :func:`set_focused_cell`; every surface
that can draw one listens for ``ctx.bus.focused_cell_changed`` and asks
:func:`focused_nucleus_in` whether the focused cell is in the frame it shows.
"""

from __future__ import annotations

from review.core.focus_state import FocusedCell


def _norm(fov: str, tp: str) -> tuple[str, str]:
    from domain.image_identity import normalize_fov, normalize_tp
    return normalize_fov(fov), normalize_tp(tp)


def focused_cell(ctx) -> FocusedCell | None:
    focus = getattr(ctx.state, "focus", None)
    return None if focus is None else focus.cell


def set_focused_cell(ctx, cell: FocusedCell | None) -> None:
    """Focus *cell* (``None`` clears). Emits ``focused_cell_changed`` on a change."""
    focus = ctx.state.focus
    if focus.cell == cell:
        return
    focus.cell = cell
    bus = getattr(ctx, "bus", None)
    if bus is not None:
        bus.focused_cell_changed.emit(focus)


def focus_cell(ctx, well: str, fov, tp, nucleus_id) -> FocusedCell | None:
    """Focus the cell these row tokens name; ``None`` (and nothing changed) when
    the nucleus id is not a number."""
    try:
        nid = int(float(nucleus_id))
    except (TypeError, ValueError):
        return None
    cell = FocusedCell(well=str(well or "").strip(), fov=str(fov or "").strip(),
                       tp=str(tp or "").strip(), nucleus_id=nid)
    set_focused_cell(ctx, cell)
    return cell


def same_frame(cell: FocusedCell, well: str, fov: str, tp: str) -> bool:
    """Whether *cell* lies in the field (*well*, *fov*, *tp*), tokens normalised."""
    if str(well or "").strip() != cell.well:
        return False
    return _norm(fov, tp) == _norm(cell.fov, cell.tp)


def focused_nucleus_in(ctx, well: str, fov: str, tp: str) -> int | None:
    """The focused cell's nucleus id when it is in this field, else ``None``."""
    cell = focused_cell(ctx)
    if cell is None or not same_frame(cell, well, fov, tp):
        return None
    return cell.nucleus_id
