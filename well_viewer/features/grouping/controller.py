"""Quick-replicate helpers — drives the "Quick Replicates" dropdowns on the
sample-definitions panel. (The rep-map plate is now a ``widgets.WellPlateSelector``
wired in ``views/replicate_panel_view.py``; its drag edits the current selection
via ``WellViewerApp._sel_set_composition`` directly — no controller helpers.)"""

from __future__ import annotations


from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
)

from well_viewer.domain.batch_models import ReplicateSet
from well_viewer.domain.viewer_state import extract_well_token as _extract_well_token
from well_viewer.domain.plate_layout import PLATE_COLS, PLATE_ROWS
from well_viewer.core.safe import swallowed
from well_viewer.domain.selections_model import color_for_wells


def make_replicate_pairs(toks, size=2):
    """Chunk adjacent tokens into ReplicateSets of ``size`` wells each.

    Consecutive wells are grouped ``size`` at a time; a trailing remainder
    smaller than ``size`` becomes its own (smaller) set. Each set holds one
    condition's replicate wells — a group of N wells, not just pairs. ``size=1``
    yields one solo set per well; ``size=2`` reproduces the historical pairing.
    """
    size = max(1, int(size))
    sets = []
    for i in range(0, len(toks), size):
        chunk = list(toks[i:i + size])
        if chunk:
            sets.append(ReplicateSet("/".join(chunk), chunk))
    return sets


def rep_quick_pairs(ctx) -> None:
    """Generate quick replicate pairs using current dropdown selections."""
    pair_dir = ctx.runtime.grouping.pair_dir or "row"
    iter_order = ctx.runtime.grouping.iter_order or "row"
    size = ctx.runtime.grouping.rep_size or 2
    well_paths = ctx.state.dataset.well_paths
    sel_state = ctx.state.selection
    new_sets = []

    if pair_dir == "row":
        if iter_order == "row":
            for row_ltr in PLATE_ROWS:
                loaded = [f"{row_ltr}{col}" for col in PLATE_COLS
                          if f"{row_ltr}{col}" in well_paths]
                new_sets.extend(make_replicate_pairs(loaded, size))
        else:
            by_col = {}
            for row_ltr in PLATE_ROWS:
                loaded = [f"{row_ltr}{col}" for col in PLATE_COLS
                          if f"{row_ltr}{col}" in well_paths]
                row_sets = make_replicate_pairs(loaded, size)
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
                          if f"{row_ltr}{col}" in well_paths]
                new_sets.extend(make_replicate_pairs(loaded, size))
        else:
            by_row = {}
            for col in PLATE_COLS:
                loaded = [f"{row_ltr}{col}" for row_ltr in PLATE_ROWS
                          if f"{row_ltr}{col}" in well_paths]
                col_sets = make_replicate_pairs(loaded, size)
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
    if sel_state.selections:
        resp = QMessageBox.question(
            ctx.window, "Replace groups?",
            f"This will replace the current {len(sel_state.selections)} group(s). Continue?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
    from well_viewer.domain.selections_model import make_selection
    used_names: set = set()
    used_ids: set = set()
    # A group is one condition; its wells are implicitly its replicate set, so
    # no explicit ``replicates`` partition is stored (see selections_model).
    sels = [make_selection(name=s.name, wells=list(s.wells),
                           replicates=None,
                           source="rep_set", used_names=used_names, used_ids=used_ids,
                           fallback_color_idx=i)
            for i, s in enumerate(new_sets)]
    sel_state.selections = sels
    sel_state.current_selection_id = sels[0]["id"] if sels else None
    ctx.coordinator.rebuild_all()


def rep_add(ctx) -> None:
    """Open a dialog to create a new named ReplicateSet, picking its wells on the
    same graphical plate the "+ wells…" popover uses to edit an existing group's
    membership — not a text list of well names. Configured identically to
    ``SavedSelectionsList._open_compose_popover`` so the two flows look and feel
    the same (rubber-band rectangle select, row/column headers, multi-select)."""
    from well_viewer.core.selection_controller import sel_add
    from well_viewer.domain.viewer_state import parse_rc
    from widgets.plate_geometry import PLATE_ALIGNMENT, apply_plate_geometry
    from widgets.well_plate_selector import WellPlateSelector
    sel_state = ctx.state.selection
    well_paths = ctx.state.dataset.well_paths
    dlg = QDialog(ctx.window)
    dlg.setWindowTitle("New Replicate Set")
    dlg.setModal(True)
    dlg.setMinimumSize(_DLG_MIN_W, _DLG_MIN_H)
    v = QVBoxLayout(dlg)
    v.addWidget(QLabel("Name:"))
    name_edit = QLineEdit(f"Rep {len(sel_state.selections)+1}")
    v.addWidget(name_edit)
    v.addWidget(QLabel("Choose wells for this group:"))

    enabled = sorted(well_paths.keys(), key=lambda l: parse_rc(l))
    plate = WellPlateSelector()
    try:
        plate.setEnabledWells(enabled)
        plate.setRowColumnSelectable(True)
        plate.setSingleSelectionMode(False)
        plate.setDragMode("rect")   # rubber-band rectangle, not freeform paint
        # The app-wide plate geometry (widgets/plate_geometry.py): same caps,
        # same cell scale and same centring as every other plate map in the app.
        # The "rail" role hides the built-in action row — this dialog has its own
        # Create / Cancel buttons and no use for All / Invert / Clear.
        apply_plate_geometry(plate, role="rail")
    except Exception:
        swallowed()
    v.addWidget(plate, 1, PLATE_ALIGNMENT)

    btn_row = QHBoxLayout()
    v.addLayout(btn_row)
    ok_btn = QPushButton("Create")
    ok_btn.setProperty("variant", "primary")
    cancel_btn = QPushButton("Cancel")
    btn_row.addWidget(ok_btn)
    btn_row.addWidget(cancel_btn)
    btn_row.addStretch(1)

    def _ok():
        picked = sorted((w for w in plate.selectedWellIds() if w in well_paths),
                        key=parse_rc)
        if not picked:
            QMessageBox.warning(dlg, "No wells", "Select at least one well.")
            return
        name = name_edit.text().strip() or f"Rep {len(sel_state.selections) + 1}"
        dlg.accept()
        # sel_add enforces well-exclusivity (the new group takes the wells).
        # A group is one condition; its wells are implicitly its replicate set.
        sel_add(ctx, name, list(picked), replicates=None, source="rep_set")

    ok_btn.clicked.connect(_ok)
    cancel_btn.clicked.connect(dlg.reject)
    dlg.exec()



# A8 teardown: the display-label leaves now live in the domain layer (shared by
# core / every plot renderer without a feature import). Re-exported here so the
# god-method delegates + existing ``grouping.controller`` importers resolve.
from well_viewer.domain.selections_model import (  # noqa: F401
    replicate_display_label,
    well_display_label,
)

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_DLG_MIN_W = 420
_DLG_MIN_H = 480



def rep_refresh_map(ctx) -> None:
    """Push selection state onto the GROUPS-panel plate (a WellPlateSelector):
    every well takes its **group's** colour (so all wells in a group read as one
    condition and an explicit recolour is honoured — matching the group row's
    swatch and the main sidebar's group mode); the *current* selection's wells
    are shown as the plate's selection (sunken/accent), which is also what a drag
    on the plate edits."""
    from well_viewer.domain.selections_model import color_for_selection
    plate = ctx.widgets.get_or_none("_rep_map_plate")
    if plate is None:
        return
    sel_state = ctx.state.selection
    well_paths = ctx.state.dataset.well_paths
    plate.setEnabledWells(list(well_paths.keys()))
    colors: dict[str, str] = {}
    for s in sel_state.selections:
        group_color = color_for_selection(s)
        for tok in (s.get("wells") or []):
            if tok in well_paths:
                colors[tok] = group_color
    plate.clearWellColors()
    plate.setWellColors(colors)
    cur_id = sel_state.current_selection_id
    cur = next((s for s in sel_state.selections if s.get("id") == cur_id), None)
    cur_wells = [w for w in (cur.get("wells") or []) if w in well_paths] if cur else []
    plate.setSelectedWellIds(cur_wells)



def rank_color_rset(ctx, rset) -> str:
    """Colour for a ReplicateSet / BarGroup (or anything with a ``wells``
    attribute) — honours the per-selection ``color`` override when an entry
    in ``_selections`` shares its ``name``, otherwise falls back to the
    rank colour of its lowest well.

    A8 teardown: takes ``ctx`` — delegates to the pure domain colour resolver
    over ``ctx.state.selection`` (no god-object)."""
    from well_viewer.domain.selections_model import color_for_replicate_set
    return color_for_replicate_set(rset, ctx.state.selection.selections)



def rep_quick_pairs_from_dropdowns(ctx) -> None:
    """Read dropdown values and update state, then call rep_quick_pairs()."""
    grp = ctx.runtime.grouping
    pair_cb = ctx.widgets.get_or_none("_rep_quick_pair_dir_var")
    iter_cb = ctx.widgets.get_or_none("_rep_quick_iter_order_var")
    size_sb = ctx.widgets.get_or_none("_rep_quick_size_var")
    if pair_cb is None or iter_cb is None:
        return
    # Map dropdown display values to internal values
    grp.pair_dir = "row" if "Rows" in pair_cb.currentText() else "col"
    grp.iter_order = "row" if "Across" in iter_cb.currentText() else "col"
    grp.rep_size = size_sb.value() if size_sb is not None else 2
    rep_quick_pairs(ctx)



def rep_sets_loaded(ctx) -> list[ReplicateSet]:
    """One ReplicateSet per selection that has ≥1 loaded well (ignores hidden),
    in ``_selections`` order — derived straight from the unified model.

    A8 teardown: reads typed ``ctx.state.selection`` / ``ctx.state.dataset`` so
    it takes ``ctx`` and names no god-object."""
    out: list[ReplicateSet] = []
    for s in ctx.state.selection.selections:
        wells = s.get("wells") or []
        if any(w in ctx.state.dataset.well_paths for w in wells):
            out.append(ReplicateSet(s.get("name") or "", list(wells)))
    return out



def rep_color_for(ctx, lbl: str) -> str | None:
    """Return the colour for the selection that owns *lbl*, or None if the
    well is not in any selection."""
    for s in ctx.state.selection.selections:
        if lbl in (s.get("wells") or []):
            return ctx.window._sel_color(s)
    return None



def rank_color_wells(ctx, wells) -> str:
    """Colour for a set of wells — the rank colour of its lowest well, so all
    of its wells (and its line/bar trace) share one colour.

    A8 teardown: the pure implementation now lives in ``domain.selections_model``
    (``color_for_wells``); this thin ctx-signature wrapper stays for the legacy
    ``WellViewerApp._rank_color_wells`` god-method delegate (the arg is unused)."""
    return color_for_wells(wells)

