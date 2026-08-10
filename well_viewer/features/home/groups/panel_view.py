"""The rail plate that edits a group's well membership.

Home's **Groups** tab and this plate are two halves of one editor: the tab
(``groups/tab_view.py``) carries the group list, the header and Quick
Replicates, and this supplies the plate in the left rail that says which wells
are in the group currently selected there. They are separate modules because
they mount in separate places -- ``core/centre_view.build_centre`` puts this one
in the sidebar stack at startup, beside every other feature's rail panel.

That is also why it is a ``*_view.py`` and not part of ``tab_view.py``:
ARCHITECTURE §3 reserves ``tab_view.py`` for a feature's main tab page and gives
sidebars and sub-panels their own ``*_view.py``. The two were briefly in one
file, which put the rail builder under the tab-page name and left the module
docstring describing the panel rather than the tab.

The plate is live only on the three Home tabs that read the well selection --
see ``home/controller.sync_home_sidebar`` -- and greyed elsewhere, so a click
that would have no consequence looks like it has none.
"""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from well_viewer.core.safe import swallowed
from well_viewer.features.home.groups.controller import rep_refresh_map


def build_replicate_panel(ctx, parent: QWidget) -> None:
    """Sample-Definitions LEFT sidebar: the plate map only.

    The GROUPS panel (header + Quick Replicates + SavedSelectionsList +
    hint line) lives in the centre area now; see
    ``build_replicate_groups_centre``.
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
    apply_plate_geometry(plate, role="rail")
    layout.addWidget(plate, 0, PLATE_ALIGNMENT)
    layout.addStretch(1)
    ctx.widgets.register("_rep_map_plate", plate)

    def _commit_plate_to_current(*_a) -> None:
        from well_viewer.core.selection_controller import sel_by_id, sel_set_composition
        from well_viewer.domain.viewer_state import parse_rc
        # Well-membership edits should only land while the user is on Home's
        # GROUPS tab. From Overview / Cell Gating / Well Labels / Ratios /
        # Pipeline Info, this plate's selectionDragFinished can still fire
        # (e.g. from programmatic refresh ticks) and silently rewrite the
        # current group's composition — exactly the "wells get reassigned even
        # though I'm not on the Groups tab" bug. Guard against it here.
        sub_tabs = ctx.runtime.home.subtabs
        if sub_tabs is not None:
            try:
                if sub_tabs.tabText(sub_tabs.currentIndex()) != "Groups":
                    return
            except Exception:
                swallowed()  # was: pass (silent except)
        sid = ctx.state.selection.current_selection_id
        sel = sel_by_id(ctx, sid) if sid else None
        if sel is None:
            rep_refresh_map(ctx)        # no current selection — revert the click
            return
        well_paths = ctx.state.dataset.well_paths
        new = sorted((w for w in plate.selectedWellIds() if w in well_paths),
                     key=parse_rc)
        old = sorted((w for w in (sel.get("wells") or []) if w in well_paths),
                     key=parse_rc)
        if new == old:
            return
        # sel_set_composition enforces well-exclusivity (the current group wins)
        # and runs ctx.coordinator.rebuild_all(), which re-pushes the plate via rep_refresh_map.
        sel_set_composition(ctx, sid, new)
    # selectionDragFinished fires once per click *and* once at the end of a drag.
    plate.selectionDragFinished.connect(_commit_plate_to_current)
