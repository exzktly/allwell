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

def _grp_on_composition(app, sel_id) -> None:
    lst = getattr(app, "_rep_list", None)
    if lst is None:
        return
    for s in lst.selections():
        if s.get("id") == sel_id:
            app._sel_set_composition(sel_id, s.get("wells") or [], s.get("replicates"))
            return


def _grp_on_replicates(app, sel_id) -> None:
    """Replicates-only edit (chip moves / un-group / group-solo) — push the
    list's current ``replicates`` for ``sel_id`` into the app model without
    touching ``wells`` (so the de-overlap pass in ``_sel_set_composition`` runs
    against the unchanged membership)."""
    lst = getattr(app, "_rep_list", None)
    if lst is None:
        return
    for s in lst.selections():
        if s.get("id") == sel_id:
            app._sel_set_composition(sel_id, None, s.get("replicates"))
            return


def wire_selections_list(app, lst) -> None:
    """Connect a SavedSelectionsList's signals to the app's `_sel_*` mutators."""
    lst.entryActivated.connect(lambda sid: app._sel_select(sid))
    lst.entryRenamed.connect(lambda sid, name: app._sel_rename(sid, name))
    lst.entryVisibilityToggled.connect(lambda sid, hidden: app._sel_set_hidden(sid, hidden))
    lst.entryDeleted.connect(lambda sid: app._sel_delete(sid))
    lst.entryDuplicated.connect(lambda new_id, src_id: app._sel_duplicate(src_id))
    lst.entryRecoloured.connect(lambda sid, color: app._sel_set_color(sid, color))
    lst.entryExportRequested.connect(lambda sid: app._sel_export_one(sid))
    lst.orderChanged.connect(lambda ids: app._sel_reorder(ids))
    lst.wellsChanged.connect(lambda sid, _w: _grp_on_composition(app, sid))
    lst.replicatesChanged.connect(lambda sid, _r: _grp_on_replicates(app, sid))
    lst.addFromSelectionRequested.connect(app._rep_add)
    load_cb = getattr(app, "_bar_load_groups", None)
    if callable(load_cb):
        lst.importRequested.connect(load_cb)


def rep_panel_refresh(app) -> None:
    """Refresh the GROUPS list (a widgets.SavedSelectionsList over app._app_state.selection.selections)."""
    lst = getattr(app, "_rep_list", None)
    if lst is None:
        # >>> TEMP DEBUG (group-creation bug)
        import sys
        print("=== ALLWELL DEBUG: rep_panel_refresh — _rep_list is None "
              "(GROUPS list widget not built) ===", file=sys.stderr, flush=True)
        # <<< TEMP DEBUG
        return
    try:
        lst.setEnabledWells(list(app._app_state.dataset.well_paths.keys()))
    except Exception:
        # >>> TEMP DEBUG (group-creation bug): was swallowed at DEBUG.
        import sys
        import traceback
        print("\n=== ALLWELL DEBUG: setEnabledWells FAILED ===", file=sys.stderr)
        traceback.print_exc()
        print("=== end ALLWELL DEBUG ===\n", file=sys.stderr, flush=True)
        # <<< TEMP DEBUG
    sels = list(app._app_state.selection.selections or [])
    # >>> TEMP DEBUG (group-creation bug): the new group renders into the list
    # here. Surface any failure instead of letting a caller swallow it.
    try:
        lst.updateSelections(sels)
    except Exception:
        import sys
        import traceback
        print(f"\n=== ALLWELL DEBUG: updateSelections FAILED (n_groups={len(sels)}) ===",
              file=sys.stderr)
        traceback.print_exc()
        print("=== end ALLWELL DEBUG ===\n", file=sys.stderr, flush=True)
        raise
    # <<< TEMP DEBUG
    cur = app._app_state.selection.current_selection_id
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
    idx = getattr(app, "_grp_inline_edit_idx", -1)
    if 0 <= idx < len(sels):
        app._grp_inline_edit_idx = -1
        row = getattr(lst, "_rows", {}).get(sels[idx].get("id"))
        if row is not None and hasattr(row, "trigger_rename"):
            QTimer.singleShot(0, row.trigger_rename)
    if hasattr(app, "_rep_refresh_map"):
        app._rep_refresh_map()
