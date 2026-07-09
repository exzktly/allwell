"""Image Table tab — sidebar plate-map picker (Qt port).

A multi-select well picker (a ``widgets.WellPlateSelector`` in "select" mode)
that explicitly ignores replicate sets / groups. State is held in
``ctx.state.image_table.active_wells`` (a ``set`` of well tokens).
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from well_viewer.core.safe import swallowed
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from well_viewer.features.image_browser.image_table.controller import image_table_clear_active
from well_viewer.features.image_browser.image_table.controller import image_table_refresh_picker
from well_viewer.features.image_browser.image_table.controller import image_table_select_all
from well_viewer.features.image_browser.image_table.controller import image_table_set_spacing


def build_image_table_picker(ctx, parent: QWidget) -> None:
    """Compact plate-map picker for the Image Table tab sidebar."""
    from widgets.well_plate_selector import WellPlateSelector

    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    plate = WellPlateSelector(parent)
    plate.setActionsVisible(False)          # the rail keeps its own All / Clear below
    plate.setSelectionMode("select")
    plate.setDragSelectEnabled(True)
    plate.setRowColumnSelectable(True)
    plate.setEnabledWells([])               # nothing selectable until a dataset loads
    from well_viewer.core.ui_helpers import well_plate_tooltip_provider
    plate.setWellTooltipProvider(well_plate_tooltip_provider(ctx))
    # Match the main sidebar plate's geometry so the picker stays in the
    # same screen position when the user switches tabs.
    plate.setMinimumHeight(280)
    from PySide6.QtWidgets import QSizePolicy as _SizePolicy
    _sp = _SizePolicy(_SizePolicy.Preferred, _SizePolicy.Preferred)
    _sp.setHeightForWidth(True)
    plate.setSizePolicy(_sp)
    layout.addWidget(plate)
    ctx.runtime.image_table.sidebar_plate = plate

    def _on_plate_changed(ids) -> None:
        new = {w for w in ids if w in ctx.state.dataset.well_paths}
        if new == ctx.state.image_table.active_wells:
            return
        ctx.state.image_table.active_wells = new
        image_table_refresh_picker(ctx)
    plate.selectionChanged.connect(_on_plate_changed)

    btn_row = QWidget(parent)
    bl = QHBoxLayout(btn_row)
    bl.setContentsMargins(6, 2, 6, 2)
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

    layout.addWidget(_build_properties_section(ctx, parent))

    layout.addStretch(1)

    help_lbl = QLabel(
        "Select wells here, choose channels in the Build Layout row, then press "
        "Build table followed by Generate.", parent,
    )
    help_lbl.setObjectName("Muted")
    help_lbl.setWordWrap(True)
    layout.addWidget(help_lbl)

    # Plot Groups drawer — same widget that lives on the plotting sidebar,
    # added here so users can pick a saved well set and have it drive the
    # Image Table grid immediately. ``include_add_controls=False`` because
    # authoring new groups belongs with the plotting workflow.
    from well_viewer.core.sidebar_view import build_plot_groups_drawer
    # build_plot_groups_drawer is a shared core sidebar builder; it takes ctx
    # (drawer registry on ctx.runtime.plot_groups, widgets via ctx.widgets).
    plot_groups_section = build_plot_groups_drawer(
        ctx, parent, include_add_controls=False,
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


def _build_properties_section(ctx, parent: QWidget):
    """A collapsible Properties panel for the rendered table's grouped-view
    spacing + label typography (mirrors the per-property sidebars other tabs
    use). Each row live-re-renders via ``image_table_set_spacing``."""
    from widgets.collapsible_section import CollapsibleSection
    from widgets.stepper import Stepper
    from PySide6.QtWidgets import QGridLayout

    sec = CollapsibleSection("Properties", parent, expanded=True)
    grid = QGridLayout()
    grid.setColumnMinimumWidth(0, 110)
    grid.setColumnStretch(0, 0)
    grid.setColumnStretch(1, 1)
    grid.setHorizontalSpacing(8)
    grid.setVerticalSpacing(5)
    sec.addLayout(grid)
    its = ctx.state.image_table
    row = [0]

    def _caption(text: str) -> None:
        cap = QLabel(text)
        cap.setObjectName("Muted")
        grid.addWidget(cap, row[0], 0, 1, 2)
        row[0] += 1

    def _prop(label: str, key: str, default: int, lo: int, hi: int, tip: str) -> None:
        lab = QLabel(label)
        lab.setToolTip(tip)
        grid.addWidget(lab, row[0], 0)
        st = Stepper(value=float(getattr(its, key, default)),
                     minimum=float(lo), maximum=float(hi), single_step=1.0, decimals=0)
        st.setToolTip(tip)
        st.valueChanged.connect(lambda v, k=key: image_table_set_spacing(ctx, k, v))
        grid.addWidget(st, row[0], 1)
        row[0] += 1

    _caption("Spacing (px)")
    _prop("Cell gap", "cell_gap", 2, 0, 200,
          "Gap between adjacent images within a group.")
    _prop("Row-group gap", "row_group_gap", 18, 0, 400,
          "Vertical gap between row facet groups.")
    _prop("Col-group gap", "col_group_gap", 18, 0, 400,
          "Horizontal gap between column facet groups.")
    _caption("Label font (px)")
    _prop("Axis labels", "label_font_pt", 11, 6, 48,
          "Font size of the row / column axis value labels.")
    _prop("Group labels", "group_font_pt", 11, 6, 48,
          "Font size of the facet-group band labels.")
    return sec
