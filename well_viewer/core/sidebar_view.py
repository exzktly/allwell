"""Main well-picker sidebar builder (Qt port)."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)
from functools import partial
from well_viewer.core.selection_controller import on_sidebar_plate_col_activated
from well_viewer.core.selection_controller import on_sidebar_plate_drag_finished
from well_viewer.core.selection_controller import on_sidebar_plate_row_activated
from well_viewer.core.selection_controller import on_sidebar_plate_selection_changed
from well_viewer.core.selection_controller import on_sidebar_plate_well_activated
from well_viewer.core.selection_controller import on_sidebar_plate_well_dropped


def _drag_info(tok, pos):
    return SimpleNamespace(tok=tok, pos=pos, x=pos.x(), y=pos.y())


def _row_col_select_disabled(app) -> bool:
    """Tabs that don't support multi-well selection (smFISH) suppress the
    row/column header click handlers. smFISH lives as a sub-tab inside
    the Segmentation parent now, so we descend via ``_current_centre_tab`` to
    recover the leaf name rather than reading the top-level notebook
    directly."""
    cur = getattr(app, "_current_centre_tab", None)
    if cur is None:
        return False
    try:
        return cur() == "smFISH"
    except Exception:
        return False


def build_sidebar(app, parent: QWidget) -> None:
    """Build the 8x12 plate-map well selector in the sidebar.

    Creates:
      - "WELLS" header
      - Row/Col quick-select buttons (A-H, 01-12)
      - 8x12 WellButton plate-map grid with drag-to-select bindings
      - All / None buttons
      - Selected-well count label
      - Group-mode hint label
    """
    from well_viewer.domain.plate_layout import PLATE_ROWS as _PLATE_ROWS, PLATE_COLS as _PLATE_COLS
    from widgets.well_plate_selector import WellPlateSelector

    # Ensure parent has a vertical layout
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

    # ── 8×12 well-plate selector (v2 widget) ──────────────────────────────
    # Replaces the legacy WellButton grid for the line picker. Appearance and
    # selection are driven from WellViewerApp._refresh_sidebar_map_now via the
    # widget's setEnabledWells / setWellColors / setSelectionMode API; clicks /
    # drags / headers come back through the signal connections below.
    plate = WellPlateSelector(parent)
    plate.setActionsVisible(False)        # the rail keeps its own All / None below
    plate.setEnabledWells([])             # nothing selectable until a dataset loads
    # Fixed size + placement so the plate looks identical no matter which
    # section is active (every sidebar tab uses the same Preferred/Preferred
    # size policy + 280-px min height + heightForWidth, with a trailing
    # ``layout.addStretch(1)`` absorbing the remaining vertical space —
    # the plate stays anchored to the top of the sidebar across tabs).
    plate.setMinimumHeight(280)
    from PySide6.QtWidgets import QSizePolicy as _SizePolicy
    _sp = _SizePolicy(_SizePolicy.Preferred, _SizePolicy.Preferred)
    _sp.setHeightForWidth(True)
    plate.setSizePolicy(_sp)
    layout.addWidget(plate)
    app._sidebar_plate = plate
    app._sidebar_map_outer = plate

    # Select all / Select none are now driven by the small toggle circle
    # the WellPlateSelector paints in the plate's top-left corner (it
    # appears on multi-select plates only; smFISH/Segmentation single-
    # select plates leave the corner blank).

    # Tokens-only stub: a {tok: None} dict that doubles as the "sidebar built"
    # sentinel and the loaded-well token list for _refresh_sidebar_map and the
    # rep-colour map. (set_state calls land on the None values and are skipped.)
    app._sidebar_btns = {f"{r}{c}": None for r in _PLATE_ROWS for c in _PLATE_COLS}

    # Wire the plate to the app's selection / replicate-set / heat-map handlers.
    plate.selectionChanged.connect(partial(on_sidebar_plate_selection_changed, app))
    plate.selectionDragFinished.connect(partial(on_sidebar_plate_drag_finished, app))
    plate.wellActivated.connect(partial(on_sidebar_plate_well_activated, app))
    plate.rowHeaderActivated.connect(partial(on_sidebar_plate_row_activated, app))
    plate.columnHeaderActivated.connect(partial(on_sidebar_plate_col_activated, app))
    plate.wellDropped.connect(partial(on_sidebar_plate_well_dropped, app))

    # User request: All 96 / Invert / Clear quick-select buttons removed
    # from the sidebar — row and column header clicks on the plate cover
    # the multi-select cases; ⌘A / Clear are exposed elsewhere. Tip line
    # below the buttons also removed (functionality is discoverable via
    # the plate header hover state).

    # Selected-well count status text (legacy plain QLabel kept so existing
    # call sites in runtime_app keep writing to it). The mockup's pill-shaped
    # SelectionChip in the plate header (Phase 10 B6) wraps the same value
    # via _sel_count_chip; both render concurrently — the chip in the
    # header, the long-form caption below it.
    from widgets.selection_chip import SelectionChip as _SelectionChip
    app._sel_count_lbl = QLabel("", parent)
    app._sel_count_lbl.setObjectName("Caption")
    chip_row = QWidget(parent)
    chip_row_layout = QHBoxLayout(chip_row)
    chip_row_layout.setContentsMargins(0, 0, 0, 0)
    chip_row_layout.setSpacing(8)
    chip_row_layout.addWidget(app._sel_count_lbl, 1)
    app._sel_count_chip = _SelectionChip("0 / 96", icon="check", variant="accent",
                                          parent=chip_row)
    chip_row_layout.addWidget(app._sel_count_chip, 0)
    layout.addWidget(chip_row)

    # Group-mode hint
    app._line_group_hint = QLabel("", parent)
    app._line_group_hint.setObjectName("Accent")
    app._line_group_hint.setWordWrap(True)
    layout.addWidget(app._line_group_hint)

    # Heat-map layout configurator (sidebar variant). Hidden by default;
    # ``_on_tab_change`` reveals it when the Heat Map tab is active.
    from well_viewer.features.heatmap.sidebar_view import (
        build_heatmap_layout_sidebar,
    )
    heatmap_frame = build_heatmap_layout_sidebar(app, parent)
    heatmap_frame.setVisible(False)

    # Absorb leftover vertical space so the well picker stays pinned to the
    # top of the sidebar even when the sidebar is taller than its contents.
    layout.addStretch(1)
