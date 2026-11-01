"""The rail's main frame: the shared well plate and what sits under it.

Saved selections, which the frame also hosts, live in
``review/core/saved_selections.py``.
"""

from __future__ import annotations


from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)
from functools import partial
from review.core.selection_controller import on_sidebar_plate_col_activated
from review.core.selection_controller import on_sidebar_plate_corner_toggled
from review.core.selection_controller import on_sidebar_plate_drag_finished
from review.core.selection_controller import on_sidebar_plate_row_activated
from review.core.selection_controller import on_sidebar_plate_selection_changed
from review.core.selection_controller import on_sidebar_plate_well_activated
from review.core.selection_controller import on_sidebar_plate_well_dropped
from review.core.saved_selections import build_plot_groups_drawer
from review.core.saved_selections import refresh_plot_groups_sidebar


def build_sidebar(win, parent: QWidget) -> None:
    """Build the 8x12 plate-map well selector in the sidebar.

    Creates:
      - "WELLS" header
      - Row/Col quick-select buttons (A-H, 01-12)
      - 8x12 WellButton plate-map grid with drag-to-select bindings
      - All / None buttons
      - Selected-well count label
      - Group-mode hint label
    """
    from review.domain.plate_layout import PLATE_ROWS as _PLATE_ROWS, PLATE_COLS as _PLATE_COLS
    from review.core.rail_plate import mount_rail_plate
    from widgets.plate_geometry import PLATE_ALIGNMENT, apply_plate_geometry
    from widgets.well_plate_selector import WellPlateSelector

    # Ensure parent has a vertical layout
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

    # ── 8×12 well-plate selector (v2 widget) ──────────────────────────────
    # Replaces the legacy WellButton grid for the line picker. Appearance and
    # selection are driven from ReviewApp._refresh_sidebar_map_now via the
    # widget's setEnabledWells / setWellColors / setSelectionMode API; clicks /
    # drags / headers come back through the signal connections below.
    plate = WellPlateSelector(parent)
    plate.setEnabledWells([])             # nothing selectable until a dataset loads
    # The app-wide plate geometry (widgets/plate_geometry.py): same caps, same
    # cell scale and same centring as every other plate map in the app. The
    # "rail" role hides the built-in All / Invert / Clear row, since the rail
    # keeps its own All / None below. A trailing addStretch below keeps the
    # plate anchored to the top of the sidebar across tabs.
    apply_plate_geometry(plate, role="rail")
    mount_rail_plate(win._ctx, parent, plate, alignment=PLATE_ALIGNMENT,
                     fallback_layout=layout)
    win._sidebar_plate = win._ctx.widgets.register("_sidebar_plate", plate)
    win._sidebar_map_outer = plate

    # Select all / Select none are now driven by the small toggle circle
    # the WellPlateSelector paints in the plate's top-left corner (it
    # appears on multi-select plates only; smFISH/Segmentation single-
    # select plates leave the corner blank).

    # Tokens-only stub: a {tok: None} dict that doubles as the "sidebar built"
    # sentinel and the loaded-well token list for _refresh_sidebar_map and the
    # rep-colour map. (set_state calls land on the None values and are skipped.)
    win._sidebar_btns = {f"{r}{c}": None for r in _PLATE_ROWS for c in _PLATE_COLS}

    # Wire the plate to the win's selection / replicate-set / heat-map handlers.
    plate.selectionChanged.connect(partial(on_sidebar_plate_selection_changed, win._ctx))
    plate.selectionDragFinished.connect(partial(on_sidebar_plate_drag_finished, win._ctx))
    plate.cornerToggled.connect(partial(on_sidebar_plate_corner_toggled, win._ctx))
    plate.wellActivated.connect(partial(on_sidebar_plate_well_activated, win._ctx))
    plate.rowHeaderActivated.connect(partial(on_sidebar_plate_row_activated, win._ctx))
    plate.columnHeaderActivated.connect(partial(on_sidebar_plate_col_activated, win._ctx))
    plate.wellDropped.connect(partial(on_sidebar_plate_well_dropped, win._ctx))
    # Hover tooltip surfaces the user's Sample-Definitions label when set,
    # alongside the well id, so the rail plate doubles as a quick label
    # readout — useful on tabs where the sidebar doesn't show the labels.
    from review.core.ui_helpers import well_plate_tooltip_provider
    plate.setWellTooltipProvider(well_plate_tooltip_provider(win._ctx))

    # User request: All 96 / Invert / Clear quick-select buttons removed
    # from the sidebar — row and column header clicks on the plate cover
    # the multi-select cases; ⌘A / Clear are exposed elsewhere. Tip line
    # below the buttons also removed (functionality is discoverable via
    # the plate header hover state).

    # "Deselect all" button. Replaces the old selected-well count indicator
    # (count + pill chip), which was redundant with the plate itself. The
    # selection-count writers in selection_controller guard with hasattr, so
    # omitting ``_sel_count_lbl`` / ``_sel_count_chip`` here is safe — they
    # simply no-op.
    from review.core.selection_controller import select_none as _select_none
    deselect_row = QWidget(parent)
    deselect_row_layout = QHBoxLayout(deselect_row)
    deselect_row_layout.setContentsMargins(0, 0, 0, 0)
    deselect_row_layout.setSpacing(8)
    win._deselect_all_btn = QPushButton("Deselect all", deselect_row)
    win._deselect_all_btn.setProperty("variant", "secondary")
    win._deselect_all_btn.setToolTip("Clear the current well selection.")
    win._deselect_all_btn.clicked.connect(lambda _=False: _select_none(win._ctx))
    deselect_row_layout.addWidget(win._deselect_all_btn)
    deselect_row_layout.addStretch(1)
    layout.addWidget(deselect_row)

    # Group-mode hint
    win._line_group_hint = QLabel("", parent)
    win._line_group_hint.setObjectName("Accent")
    win._line_group_hint.setWordWrap(True)
    layout.addWidget(win._line_group_hint)

    # Heat-map layout configurator (sidebar variant). Hidden by default;
    # ``_on_tab_change`` reveals it when the Heat Map tab is active.
    heatmap_frame = win._ctx.features.query(
        "heatmap.build_layout_sidebar", win._ctx, parent, default=None)
    if heatmap_frame is not None:
        heatmap_frame.setVisible(False)

    # Absorb leftover vertical space so the well picker stays pinned to the
    # top of the sidebar even when the sidebar is taller than its contents.
    # Placed *before* the Saved selections drawer so that drawer is pinned to the
    # bottom of the sidebar and expands upward (see below).
    layout.addStretch(1)

    # Saved selections are quick-load well subsets for plotting only. They live
    # in a collapsible drawer pinned to the bottom of the sidebar so users can
    # save the current selection and restore it later without creating stats /
    # replicate groups. Collapsed by default — only the header shows until the
    # user opens it — so the name field + saved list never push up into (and
    # overlap) the selected-well count bar above the plate. The symbols keep the
    # ``plot_group`` spelling; only what a user reads was renamed.
    plot_groups_section = build_plot_groups_drawer(
        win._ctx, parent, include_add_controls=True,
    )
    layout.addWidget(plot_groups_section)
    win._plot_groups_sidebar_section = plot_groups_section
    win._plot_groups_refresh = lambda: refresh_plot_groups_sidebar(win._ctx)
