"""Image Table tab — sidebar plate-map picker (Qt port).

A multi-select well picker (a ``widgets.WellPlateSelector`` in "select" mode)
that explicitly ignores replicate sets / groups. State is held in
``ctx.state.image_table.active_wells`` (a ``set`` of well tokens).
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from well_viewer.core.safe import swallowed
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from well_viewer.features.image_browser.image_table.controller import image_table_active_wells
from well_viewer.features.image_browser.image_table.controller import image_table_clear_active
from well_viewer.features.image_browser.image_table.controller import image_table_refresh_picker
from well_viewer.features.image_browser.image_table.controller import image_table_select_all


def build_image_table_picker(ctx, parent: QWidget) -> None:
    """Compact plate-map picker for the Image Table tab sidebar."""
    from widgets.well_plate_selector import WellPlateSelector

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    # No horizontal inset: the plate spans the full rail width, identical to every
    # other sidebar tab's plate (the main / Stats / Preview / Groups pickers are all
    # flush at 0). A left/right gutter here used to make *only* this tab's plate
    # render ~24px narrower — the aspect-locked plate then shrank in both dimensions,
    # so the well picker looked smaller on the Image Table tab than anywhere else.
    layout.setContentsMargins(0, 0, 0, 8)
    layout.setSpacing(0)

    plate = WellPlateSelector(parent)
    plate.setDragSelectEnabled(True)
    plate.setRowColumnSelectable(True)
    plate.setEnabledWells([])               # nothing selectable until a dataset loads
    from well_viewer.core.ui_helpers import well_plate_tooltip_provider
    plate.setWellTooltipProvider(well_plate_tooltip_provider(ctx))
    # The app-wide plate geometry (widgets/plate_geometry.py): same caps, same
    # cell scale and same centring as every other plate map in the app. The
    # "rail" role hides the built-in action row — the rail keeps its own
    # Select All / Clear buttons below.
    from widgets.plate_geometry import PLATE_ALIGNMENT, apply_plate_geometry
    apply_plate_geometry(plate, role="rail")
    layout.addWidget(plate, 0, PLATE_ALIGNMENT)
    ctx.runtime.image_table.sidebar_plate = plate

    def _on_plate_changed(ids) -> None:
        new = {w for w in ids if w in ctx.state.dataset.well_paths}
        if new == ctx.state.image_table.active_wells:
            return
        ctx.state.image_table.active_wells = new
        image_table_refresh_picker(ctx)
        # Re-run the layout so a Wells axis tracks the plate — otherwise the grid
        # stays frozen at the selection it had when the shelf was last edited. The
        # shelf registers this callback on build (no cross-module import here).
        cb = getattr(ctx.runtime.image_table, "selection_rebuild_cb", None)
        if callable(cb):
            cb()
    plate.selectionChanged.connect(_on_plate_changed)

    btn_row = QWidget(parent)
    bl = QHBoxLayout(btn_row)
    bl.setContentsMargins(0, 4, 0, 4)
    bl.setSpacing(4)

    select_all = QPushButton("Select All", btn_row)
    select_all.setProperty("variant", "secondary")
    select_all.clicked.connect(lambda _=False: image_table_select_all(ctx))
    bl.addWidget(select_all)

    clear_btn = QPushButton("Clear", btn_row)
    clear_btn.setProperty("variant", "secondary")
    clear_btn.clicked.connect(lambda _=False: image_table_clear_active(ctx))
    bl.addWidget(clear_btn)
    bl.addStretch(1)
    layout.addWidget(btn_row)

    ctx.runtime.image_table.count_lbl = QLabel("0 wells active", parent)
    ctx.runtime.image_table.count_lbl.setObjectName("Muted")
    ctx.runtime.image_table.count_lbl.setAlignment(Qt.AlignLeft)
    layout.addWidget(ctx.runtime.image_table.count_lbl)

    layout.addStretch(1)

    help_lbl = QLabel(
        "Select wells here, then build the table on the shelf — drag a root "
        "(Well/Timepoint/FOV) onto Rows or Columns and nest channels under it — "
        "and press Generate.", parent,
    )
    help_lbl.setObjectName("Muted")
    help_lbl.setWordWrap(True)
    layout.addWidget(help_lbl)

    # Plot Groups drawer — same widget that lives on the plotting sidebar, added
    # here so users can save the current plate selection as a group and pick a
    # saved group to drive the Image Table grid, without leaving the tab. The
    # drawer's Add uses its own name field (see build_plot_groups_drawer), so it
    # doesn't collide with the main sidebar's Add.
    from well_viewer.core.sidebar_view import build_plot_groups_drawer
    # build_plot_groups_drawer is a shared core sidebar builder; it takes ctx
    # (drawer registry on ctx.runtime.plot_groups, widgets via ctx.widgets).
    #
    # It also takes the reader for "the current selection", because that differs
    # per sidebar: the plate above is this tab's own selector and writes
    # ``state.image_table.active_wells``, never ``selection.selected_wells``.
    # Without passing it, Add here saved the *plotting* sidebar's selection —
    # whatever was chosen on another tab — instead of the wells the user had just
    # clicked right above the button.
    plot_groups_section = build_plot_groups_drawer(
        ctx, parent, include_add_controls=True,
        wells_source=lambda: image_table_active_wells(ctx),
    )
    layout.addWidget(plot_groups_section)

    image_table_refresh_picker(ctx)
    # Repaint both Plot Groups drawers now that the Image Table's drawer
    # has registered itself — otherwise the first paint of the sidebar
    # shows an empty group list even when groups exist in ctx state.
    try:
        from well_viewer.core.sidebar_view import refresh_plot_groups_sidebar
        refresh_plot_groups_sidebar(ctx)
    except Exception:
        swallowed("image table picker: plot groups refresh on build")
