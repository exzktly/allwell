"""Line-plot draw-order persistence — ``line_order`` section of ``persistence.json``.

Legacy ``line_order.json`` sidecars are migrated on first load by ``_doc``.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QTimer

from well_viewer.persistence import _doc

_logger = logging.getLogger("well_viewer")


def save_to_data_dir(app) -> None:
    if not app._app_state.dataset.data_dir:
        return
    payload = {
        "version": 1,
        "rsets": list(app._line_order_rsets),
        "wells": list(app._line_order_wells),
    }
    _doc.set_section(app, "line_order", payload)


def load_from_data_dir(app) -> None:
    if not app._app_state.dataset.data_dir:
        return
    data = _doc.get_section(app, "line_order")
    if not isinstance(data, dict):
        return
    rsets = data.get("rsets") or []
    wells = data.get("wells") or []
    app._line_order_rsets = [str(x) for x in rsets if isinstance(x, str)]
    app._line_order_wells = [str(x) for x in wells if isinstance(x, str)]
    _reorder_selections_by_line_order(app)


def _reorder_selections_by_line_order(app) -> None:
    """Re-apply the saved line-graph order (§3.5) to the unified ``selections``
    model — it loads *after* sample_definitions, so the initial parse can't see
    it. Best-effort; a no-op when there's no model. The legacy ``_rep_sets`` /
    ``_bar_groups`` shadow is left alone here — the legacy renderers already
    re-order at draw time via ``_line_order_rsets`` / ``_line_order_wells``.
    """
    sels = app._app_state.selection.selections
    if not sels:
        return
    try:
        from well_viewer import selections_model as _sel
        new = _sel.reorder_by_line_order(
            sels,
            rset_order=getattr(app, "_line_order_rsets", None),
            well_order=getattr(app, "_line_order_wells", None),
        )
        if [s["id"] for s in new] != [s["id"] for s in sels]:
            app._app_state.selection.selections = new
    except Exception:  # pragma: no cover - never break loading over a re-sort
        _logger.exception("Couldn't re-apply line order to selections")


def schedule_save(app) -> None:
    if app._line_order_save_pending:
        return
    if not app._app_state.dataset.data_dir:
        return
    app._line_order_save_pending = True
    QTimer.singleShot(500, lambda: _flush(app))


def _flush(app) -> None:
    app._line_order_save_pending = False
    save_to_data_dir(app)
