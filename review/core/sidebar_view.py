"""The rail's main frame: the shared well plate and what sits under it.

Saved selections, which the frame also hosts, live in
``review/core/saved_selections.py``.
"""

from __future__ import annotations


from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
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

    Creates, top to bottom:
      - the 8x12 well-plate selector (mounted in the rail's plate slot)
      - the selection footer: count, **All**, **None**
      - the group-mode hint
      - the Saved selections drawer
      - frames pages build here (``RailSection.build``): Heat Map's layout
        configurator, shown while Heat Map is on screen
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

    # The small toggle circle the WellPlateSelector paints in the plate's
    # top-left corner is a shortcut for the footer's All / None below (it
    # appears on multi-select plates only).

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

    # The selection footer: the count, then **All** and **None** — the same
    # widget and the same words as under the Image Table's picker, so a
    # selection reads and changes the same way on every page. Registered as
    # ``_sel_count_lbl``, the key ``selection_controller.refresh_sidebar_map_now``
    # writes the count to: "3 wells selected" when the plate selects wells,
    # "2/3 sets visible" when it shows groups, where All and None show and hide
    # every set instead.
    from review.core.selection_controller import select_all as _select_all
    from review.core.selection_controller import select_none as _select_none
    from widgets.selection_footer import SelectionFooter
    footer = SelectionFooter(
        parent,
        all_tooltip="Select every loaded well — or, where the plate shows groups, show every group.",
        none_tooltip="Clear the selection — or, where the plate shows groups, hide every group.",
    )
    footer.selectAllRequested.connect(lambda: _select_all(win._ctx))
    footer.selectNoneRequested.connect(lambda: _select_none(win._ctx))
    win._ctx.widgets.register("_sel_count_lbl", footer)
    layout.addWidget(footer)

    # Group-mode hint
    win._line_group_hint = QLabel("", parent)
    win._line_group_hint.setObjectName("Accent")
    win._line_group_hint.setWordWrap(True)
    layout.addWidget(win._line_group_hint)

    # Saved selections: named well subsets the user can reload in one click.
    # Directly under the footer, because saving is something done *to* the
    # selection the footer counts — a drawer pinned at the rail's foot sat a
    # screen's height away from the plate it saves. Collapsed by default; its
    # header carries the count, so a closed drawer still says it holds
    # something.
    plot_groups_section = build_plot_groups_drawer(
        win._ctx, parent, include_add_controls=True,
    )
    layout.addWidget(plot_groups_section)
    win._plot_groups_sidebar_section = plot_groups_section
    win._plot_groups_refresh = lambda: refresh_plot_groups_sidebar(win._ctx)

    # Frames a page builds inside this one (a ``RailSection`` with ``build``):
    # Heat Map's layout configurator. Each is registered under its key and
    # hidden; the tab switch shows it while its page is on screen, with every
    # other rail frame.
    for sec in win._ctx.features.rail_sections():
        if sec.build is None:
            continue
        frame = sec.build()(win._ctx, parent)
        if frame is not None:
            win._ctx.widgets.register(sec.key, frame)
            frame.setVisible(False)

    # Absorb leftover vertical space so everything above stays pinned to the
    # plate rather than spreading down the rail.
    layout.addStretch(1)
