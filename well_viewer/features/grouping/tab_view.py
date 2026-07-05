"""GROUPS panel (Sample Definitions tab) — wiring for the unified `_selections`
model rendered by a ``widgets.SavedSelectionsList``."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from well_viewer.core.safe import swallowed


# ─────────────────────────────────────────────────────────────────────────────
# The "GROUPS" panel on the Sample Definitions tab — a widgets.SavedSelectionsList
# over the unified app._app_state.selection.selections model (Phase 8.0 Stage C: _selections is the
# in-memory source of truth; the SavedSelectionsList's signals go straight to the
# app's `_sel_*` mutators, keyed by selection id).
# ─────────────────────────────────────────────────────────────────────────────

def _grp_on_composition(ctx, sel_id) -> None:
    from well_viewer.core.selection_controller import sel_set_composition
    lst = ctx.widgets.get_or_none("_rep_list")
    if lst is None:
        return
    for s in lst.selections():
        if s.get("id") == sel_id:
            sel_set_composition(ctx, sel_id, s.get("wells") or [], s.get("replicates"))
            return


def _grp_on_replicates(ctx, sel_id) -> None:
    """Replicates-only edit (chip moves / un-group / group-solo) — push the
    list's current ``replicates`` for ``sel_id`` into the model without touching
    ``wells`` (so the de-overlap pass in ``sel_set_composition`` runs against the
    unchanged membership)."""
    from well_viewer.core.selection_controller import sel_set_composition
    lst = ctx.widgets.get_or_none("_rep_list")
    if lst is None:
        return
    for s in lst.selections():
        if s.get("id") == sel_id:
            sel_set_composition(ctx, sel_id, None, s.get("replicates"))
            return


def wire_selections_list(ctx, lst) -> None:
    """Connect a SavedSelectionsList's signals to the ctx-native `sel_*` mutators."""
    from well_viewer.core.selection_controller import (
        sel_select, sel_rename, sel_set_hidden, sel_delete, sel_duplicate,
        sel_set_color, sel_export_one, sel_reorder,
    )
    from well_viewer.features.grouping.controller import rep_add
    lst.entryActivated.connect(lambda sid: sel_select(ctx, sid))
    lst.entryRenamed.connect(lambda sid, name: sel_rename(ctx, sid, name))
    lst.entryVisibilityToggled.connect(lambda sid, hidden: sel_set_hidden(ctx, sid, hidden))
    lst.entryDeleted.connect(lambda sid: sel_delete(ctx, sid))
    lst.entryDuplicated.connect(lambda new_id, src_id: sel_duplicate(ctx, src_id))
    lst.entryRecoloured.connect(lambda sid, color: sel_set_color(ctx, sid, color))
    lst.entryExportRequested.connect(lambda sid: sel_export_one(ctx, sid))
    lst.orderChanged.connect(lambda ids: sel_reorder(ctx, ids))
    lst.wellsChanged.connect(lambda sid, _w: _grp_on_composition(ctx, sid))
    lst.replicatesChanged.connect(lambda sid, _r: _grp_on_replicates(ctx, sid))
    lst.addFromSelectionRequested.connect(lambda *_a: rep_add(ctx))
    # Bar-group import is a bar-persistence feature action; connect its bound
    # method off the window (signal wiring, not a behaviour reach-through).
    load_cb = getattr(ctx.window, "_bar_load_groups", None)
    if callable(load_cb):
        lst.importRequested.connect(load_cb)


def rep_panel_refresh(ctx) -> None:
    """Refresh the GROUPS list (a widgets.SavedSelectionsList over ctx.state.selection.selections)."""
    lst = ctx.widgets.get_or_none("_rep_list")
    if lst is None:
        return
    try:
        lst.setEnabledWells(list(ctx.state.dataset.well_paths.keys()))
    except Exception:
        swallowed()  # was: pass (silent except)
    sels = list(ctx.state.selection.selections or [])
    lst.updateSelections(sels)
    cur = ctx.state.selection.current_selection_id
    # When the app has explicitly cleared the active group (e.g. the user
    # just entered the Groups sub-tab), force the list's current row to
    # "" so no row paints with the active stripe. The list otherwise
    # auto-falls-back to the first selection in ``updateSelections``.
    if cur:
        lst.setCurrentId(cur)
    else:
        try:
            lst._current_id = ""
            lst._refresh_current()
        except Exception:
            swallowed()  # was: pass (silent except)
    # honour a pending inline-rename request (set by the "Rename" action — it
    # stores a position into _selections; see _sel_request_inline_rename)
    idx = ctx.runtime.grouping.inline_edit_idx
    idx = idx if isinstance(idx, int) else -1
    if 0 <= idx < len(sels):
        ctx.runtime.grouping.inline_edit_idx = -1
        row = getattr(lst, "_rows", {}).get(sels[idx].get("id"))
        if row is not None and hasattr(row, "trigger_rename"):
            QTimer.singleShot(0, row.trigger_rename)
    from well_viewer.features.grouping.controller import rep_refresh_map
    rep_refresh_map(ctx)
