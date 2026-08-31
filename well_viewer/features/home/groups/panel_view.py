"""The plate that edits a group's well membership.

The **Groups** layer of Home's Sample map tab is two halves: the list, the header
and Quick Replicates in ``groups/tab_view.py``, and this plate, which says which
wells are in the group selected there and edits that membership when dragged.

It used to live in the left rail, mounted by ``core/centre_view.build_centre``
beside every other feature's rail panel, and it is in the tab body now. Two
reasons, and the second is the one that matters. A rail plate on Home was a
second plate on screen the moment a layer carried its own — which Variables
did, greyed behind its live one. And measured rather than assumed: this plate
never wrote ``ctx.state.selection.selected_wells``. It was described as the
picker Cell Gating drew its CDFs from and Add Prefix applied to, and it was
neither; both read the app-wide selection, which only Overview's plate set. So
moving it costs those two tabs nothing and the Sample map tab's own selection
publishing gives them a picker they never had.

It stays a ``*_view.py`` rather than folding into ``tab_view.py``: ARCHITECTURE
§3 reserves ``tab_view.py`` for a feature's main tab page and gives its panels
their own ``*_view.py``, and this is one panel of a layered tab.
"""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from well_viewer.core.safe import swallowed
from well_viewer.features.home.groups.controller import rep_refresh_map
from well_viewer.features.home.sample_map.controller import layer_is_showing


def _plate_order(well: str) -> tuple:
    """Plate-reading sort key for a well id, total over any string.

    ``parse_rc`` answers ``(None, None)`` for a token it cannot read, and a list
    holding both that and a real ``("A", "01")`` cannot be sorted — comparing
    ``None`` with ``str`` raises. That never came up while the sorted list was
    filtered to loaded wells; it can now that a group's wells from outside this
    dataset ride along, and a group edit is not the place to discover it.
    """
    from well_viewer.domain.viewer_state import parse_rc
    row, col = parse_rc(well)
    return (row is None, row or "", col or "", well)


def build_replicate_panel(ctx, parent: QWidget, *, role: str = "editor") -> None:
    """The Groups layer's plate. *role* is the shared plate geometry's role.

    ``"editor"`` — an interactive plate that sits in a page rather than the
    rail and whose actions are supplied by its host — is what this is now, and
    it is what the Variables layer's plate already asked for. ``"rail"`` is kept
    reachable for a caller that mounts it in the sidebar, and there is none.
    """

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

    from widgets.plate_geometry import PLATE_ALIGNMENT, apply_plate_geometry
    from widgets.well_plate_selector import WellPlateSelector
    plate = WellPlateSelector(parent)
    plate.setDragSelectEnabled(True)
    plate.setRowColumnSelectable(True)
    plate.setEnabledWells([])
    from well_viewer.core.ui_helpers import well_plate_tooltip_provider
    plate.setWellTooltipProvider(well_plate_tooltip_provider(ctx))
    # The app-wide plate geometry (widgets/plate_geometry.py): same caps, same
    # cell scale and same centring as every other plate map in the app. The
    # trailing addStretch below pins the plate to the top of the column.
    apply_plate_geometry(plate, role=role)
    layout.addWidget(plate, 0, PLATE_ALIGNMENT)
    layout.addStretch(1)
    ctx.widgets.register("_rep_map_plate", plate)

    def _commit_plate_to_current(*_a) -> None:
        from well_viewer.core.selection_controller import sel_by_id, sel_set_composition
        # Well-membership edits should only land while the user is looking at
        # the **Groups layer**. This plate's ``selectionDragFinished`` can fire
        # from a programmatic refresh tick while another layer or another tab is
        # showing, and would then silently rewrite the current group — the
        # "wells get reassigned even though I'm not on the Groups tab" bug. The
        # guard used to read the Home sub-tab's title, which said "Groups"; the
        # layers of one tab are what say it now, and the question is the same
        # one. Both halves are asked: a layer switch inside a tab nobody is
        # looking at must not commit either.
        sub_tabs = ctx.runtime.home.subtabs
        if sub_tabs is not None:
            try:
                if sub_tabs.tabText(sub_tabs.currentIndex()) != "Sample map":
                    return
            except Exception:
                swallowed()  # was: pass (silent except)
        # ``layer_is_showing``, not ``current_layer(...) == "Groups"``: the
        # latter falls back to the first layer, which *is* Groups, so a context
        # that has recorded nothing would have answered yes — a guard failing
        # open on the one layer that edits.
        if not layer_is_showing(ctx, "Groups"):
            return
        sid = ctx.state.selection.current_selection_id
        sel = sel_by_id(ctx, sid) if sid else None
        if sel is None:
            rep_refresh_map(ctx)        # no current selection — revert the click
            return
        well_paths = ctx.state.dataset.well_paths
        new = sorted((w for w in plate.selectedWellIds() if w in well_paths),
                     key=_plate_order)
        old = sorted((w for w in (sel.get("wells") or []) if w in well_paths),
                     key=_plate_order)
        if new == old:
            return
        # A group can name wells this dataset does not hold — a subset load, a
        # plate re-exported with fewer wells, a definitions file shared between
        # runs. The plate cannot show them, so a membership rebuilt from the
        # plate alone deletes them, and the autosave that follows makes the
        # deletion permanent in the only place they were written down. Only the
        # wells the user could actually see are re-decided here; the rest are
        # carried through untouched.
        absent = [w for w in (sel.get("wells") or []) if w not in well_paths]
        # sel_set_composition enforces well-exclusivity (the current group wins)
        # and runs ctx.coordinator.rebuild_all(), which re-pushes the plate via rep_refresh_map.
        sel_set_composition(ctx, sid, sorted(new + absent, key=_plate_order))
    # selectionDragFinished fires once per click *and* once at the end of a drag.
    plate.selectionDragFinished.connect(_commit_plate_to_current)
