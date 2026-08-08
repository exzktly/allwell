"""Line-plot draw-order persistence — ``line_order`` section of ``persistence.json``.

Legacy ``line_order.json`` sidecars are migrated on first load by ``_doc``.
"""

from __future__ import annotations

import logging

from well_viewer.persistence import _doc

_logger = logging.getLogger("well_viewer")


def save_to_data_dir(ctx) -> None:
    if not ctx.state.dataset.data_dir:
        return
    payload = {
        "version": 1,
        "rsets": list(ctx.state.plot.line_order_rsets),
        "wells": list(ctx.state.plot.line_order_wells),
    }
    _doc.set_section(ctx, "line_order", payload)


def load_from_data_dir(ctx) -> None:
    if not ctx.state.dataset.data_dir:
        return
    data = _doc.get_section(ctx, "line_order")
    if not isinstance(data, dict):
        return
    rsets = data.get("rsets") or []
    wells = data.get("wells") or []
    ctx.state.plot.line_order_rsets = [str(x) for x in rsets if isinstance(x, str)]
    ctx.state.plot.line_order_wells = [str(x) for x in wells if isinstance(x, str)]
    _reorder_selections_by_line_order(ctx)


def _reorder_selections_by_line_order(ctx) -> None:
    """Re-apply the saved line-graph order (§3.5) to the unified ``selections``
    model — it loads *after* sample_definitions, so the initial parse can't see
    it. Best-effort; a no-op when there's no model. The legacy ``_rep_sets`` /
    ``_bar_groups`` shadow is left alone here — the legacy renderers already
    re-order at draw time via ``_line_order_rsets`` / ``_line_order_wells``.
    """
    sels = ctx.state.selection.selections
    if not sels:
        return
    try:
        from well_viewer.domain import selections_model as _sel
        new = _sel.reorder_by_line_order(
            sels,
            rset_order=ctx.state.plot.line_order_rsets,
            well_order=ctx.state.plot.line_order_wells,
        )
        if [s["id"] for s in new] != [s["id"] for s in sels]:
            ctx.state.selection.selections = new
    except Exception:  # pragma: no cover - never break loading over a re-sort
        _logger.exception("Couldn't re-apply line order to selections")
