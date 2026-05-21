"""Quick-replicate helpers — drives the "Quick Replicates" dropdowns on the
Sample Definitions panel. (The rep-map plate is now a ``widgets.WellPlateSelector``
wired in ``views/replicate_panel_view.py``; its drag edits the current selection
via ``WellViewerApp._sel_set_composition`` directly — no controller helpers.)"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox

from well_viewer.batch_models import ReplicateSet
from well_viewer.viewer_state import extract_well_token as _extract_well_token
from well_viewer.plate_layout import PLATE_COLS, PLATE_ROWS


def make_replicate_pairs(toks, prefix: str):
    """Pair adjacent tokens into ReplicateSets; singletons become solo sets."""
    sets = []
    i = 0
    while i < len(toks):
        if i + 1 < len(toks):
            t1, t2 = toks[i], toks[i + 1]
            sets.append(ReplicateSet(f"{t1}/{t2}", [t1, t2]))
            i += 2
        else:
            t = toks[i]
            sets.append(ReplicateSet(t, [t]))
            i += 1
    return sets


def rep_quick_pairs(app) -> None:
    """Generate quick replicate pairs using current dropdown selections."""
    pair_dir = app._rep_quick_pair_dir
    iter_order = app._rep_quick_iter_order
    new_sets = []

    if pair_dir == "row":
        if iter_order == "row":
            for row_ltr in PLATE_ROWS:
                loaded = [f"{row_ltr}{col}" for col in PLATE_COLS
                          if f"{row_ltr}{col}" in app._app_state.dataset.well_paths]
                new_sets.extend(make_replicate_pairs(loaded, row_ltr))
        else:
            by_col = {}
            for row_ltr in PLATE_ROWS:
                loaded = [f"{row_ltr}{col}" for col in PLATE_COLS
                          if f"{row_ltr}{col}" in app._app_state.dataset.well_paths]
                row_sets = make_replicate_pairs(loaded, row_ltr)
                for s in row_sets:
                    if s.wells:
                        col = _extract_well_token(s.wells[0])
                        if col and len(col) > 1:
                            col = col[1:]
                            by_col.setdefault(col, []).append(s)
            for col in PLATE_COLS:
                if col in by_col:
                    new_sets.extend(by_col[col])
    else:
        if iter_order == "col":
            for col in PLATE_COLS:
                loaded = [f"{row_ltr}{col}" for row_ltr in PLATE_ROWS
                          if f"{row_ltr}{col}" in app._app_state.dataset.well_paths]
                new_sets.extend(make_replicate_pairs(loaded, col))
        else:
            by_row = {}
            for col in PLATE_COLS:
                loaded = [f"{row_ltr}{col}" for row_ltr in PLATE_ROWS
                          if f"{row_ltr}{col}" in app._app_state.dataset.well_paths]
                col_sets = make_replicate_pairs(loaded, col)
                for s in col_sets:
                    if s.wells:
                        tok = _extract_well_token(s.wells[0])
                        if tok and len(tok) > 0:
                            row = tok[0]
                            by_row.setdefault(row, []).append(s)
            for row in PLATE_ROWS:
                if row in by_row:
                    new_sets.extend(by_row[row])

    if not new_sets:
        return
    if app._app_state.selection.selections:
        resp = QMessageBox.question(
            app, "Replace groups?",
            f"This will replace the current {len(app._app_state.selection.selections)} group(s). Continue?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
    from well_viewer.selections_model import make_selection
    used_names: set = set()
    used_ids: set = set()
    sels = [make_selection(name=s.name, wells=list(s.wells),
                           replicates=[list(s.wells)] if s.wells else None,
                           source="rep_set", used_names=used_names, used_ids=used_ids,
                           fallback_color_idx=i)
            for i, s in enumerate(new_sets)]
    app._app_state.selection.selections = sels
    app._app_state.selection.current_selection_id = sels[0]["id"] if sels else None
    app._rebuild_all()
