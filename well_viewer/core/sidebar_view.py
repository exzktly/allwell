"""Main well-picker sidebar builder (Qt port)."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QTabWidget,
    QVBoxLayout, QWidget,
)
from functools import partial
from well_viewer.core.selection_controller import on_sidebar_plate_col_activated
from well_viewer.core.selection_controller import on_sidebar_plate_drag_finished
from well_viewer.core.selection_controller import on_sidebar_plate_row_activated
from well_viewer.core.selection_controller import on_sidebar_plate_selection_changed
from well_viewer.core.selection_controller import on_sidebar_plate_well_activated
from well_viewer.core.selection_controller import on_sidebar_plate_well_dropped
from well_viewer.core.safe import swallowed
from well_viewer.domain.plot_groups import make_plot_group


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

    # Plot Groups are quick-load well subsets for plotting only. They live
    # below the main plate selector so users can select wells, save that subset,
    # and later restore it without creating stats/replicate groups.
    plot_groups_tabs = QTabWidget(parent)
    plot_groups_tabs.setObjectName("PlotGroupsSidebarTabs")
    plot_groups_tabs.tabBar().setExpanding(False)
    plot_groups_tab = QWidget(plot_groups_tabs)
    pg_layout = QVBoxLayout(plot_groups_tab)
    pg_layout.setContentsMargins(8, 8, 8, 8)
    pg_layout.setSpacing(6)

    name_row = QWidget(plot_groups_tab)
    nr = QHBoxLayout(name_row)
    nr.setContentsMargins(0, 0, 0, 0)
    nr.setSpacing(6)
    app._plot_group_name_edit = QLineEdit(name_row)
    app._plot_group_name_edit.setPlaceholderText("Name current selection…")
    nr.addWidget(app._plot_group_name_edit, 1)
    add_btn = QPushButton("Add", name_row)
    add_btn.setProperty("variant", "secondary")
    add_btn.setToolTip(
        "Save the currently selected wells as a Plot Group. Plot Groups only "
        "reload well selections; they are not stats or replicate groups."
    )
    add_btn.clicked.connect(lambda _=False: _plot_groups_add_current(app))
    nr.addWidget(add_btn)
    pg_layout.addWidget(name_row)

    app._plot_groups_empty_label = QLabel("No Plot Groups yet.", plot_groups_tab)
    app._plot_groups_empty_label.setObjectName("Muted")
    app._plot_groups_empty_label.setWordWrap(True)
    pg_layout.addWidget(app._plot_groups_empty_label)

    scroll = QScrollArea(plot_groups_tab)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    app._plot_groups_list_widget = QWidget(scroll)
    app._plot_groups_list_layout = QVBoxLayout(app._plot_groups_list_widget)
    app._plot_groups_list_layout.setContentsMargins(0, 0, 0, 0)
    app._plot_groups_list_layout.setSpacing(4)
    scroll.setWidget(app._plot_groups_list_widget)
    pg_layout.addWidget(scroll, 1)

    plot_groups_tabs.addTab(plot_groups_tab, "Plot Groups")
    layout.addWidget(plot_groups_tabs)
    app._plot_groups_sidebar_tabs = plot_groups_tabs
    app._plot_groups_add_btn = add_btn
    app._plot_groups_refresh = lambda: refresh_plot_groups_sidebar(app)

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


def _plot_groups_schedule_save(app) -> None:
    p = getattr(app, "_persistence", None)
    if p is not None:
        p.schedule_save("plot_groups")


def _plot_groups_add_current(app) -> None:
    wells = sorted(
        (app._app_state.selection.selected_wells or set()),
        key=app._parse_rc,
    )
    if not wells:
        try:
            app._set_status("Select wells before adding a Plot Group.")
        except Exception:
            swallowed("plot_groups: set status (no wells selected)")
        return
    edit = getattr(app, "_plot_group_name_edit", None)
    name = edit.text().strip() if edit is not None else ""
    existing = app._app_state.selection.plot_groups or []
    used_names = {g.get("name") for g in existing}
    used_ids = {g.get("id") for g in existing}
    group = make_plot_group(
        name=name or f"Plot Group {len(existing) + 1}",
        wells=wells,
        used_names=used_names,
        used_ids=used_ids,
    )
    app._app_state.selection.plot_groups = list(existing) + [group]
    if edit is not None:
        edit.clear()
    refresh_plot_groups_sidebar(app)
    _plot_groups_schedule_save(app)
    try:
        app._set_status(
            f"Added Plot Group “{group['name']}” ({len(group['wells'])} wells)."
        )
    except Exception:
        swallowed("plot_groups: set status (added group)")


def _plot_groups_load(app, group_id: str) -> None:
    group = next(
        (g for g in app._app_state.selection.plot_groups if g.get("id") == group_id),
        None,
    )
    if group is None:
        return
    valid = set(app._app_state.dataset.well_paths or {})
    wells = {w for w in (group.get("wells") or []) if w in valid}
    if hasattr(app, "_set_selected_wells"):
        app._set_selected_wells(wells)
    else:
        app._app_state.selection.selected_wells = wells
    try:
        app._set_status(
            f"Loaded Plot Group “{group.get('name', 'Plot Group')}” "
            f"({len(wells)} wells)."
        )
    except Exception:
        swallowed("plot_groups: set status (loaded group)")


def _plot_groups_delete(app, group_id: str) -> None:
    before = list(app._app_state.selection.plot_groups or [])
    after = [g for g in before if g.get("id") != group_id]
    if len(after) == len(before):
        return
    app._app_state.selection.plot_groups = after
    refresh_plot_groups_sidebar(app)
    _plot_groups_schedule_save(app)


def refresh_plot_groups_sidebar(app) -> None:
    """Rebuild the Plot Groups quick-load buttons under the well grid."""
    lay = getattr(app, "_plot_groups_list_layout", None)
    if lay is None:
        return
    while lay.count():
        item = lay.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
    groups = list(app._app_state.selection.plot_groups or [])
    empty = getattr(app, "_plot_groups_empty_label", None)
    if empty is not None:
        empty.setVisible(not groups)
    valid = set(app._app_state.dataset.well_paths or {})
    for group in groups:
        row = QWidget(getattr(app, "_plot_groups_list_widget", None))
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        wells = [w for w in (group.get("wells") or []) if w in valid]
        load_btn = QPushButton(
            f"{group.get('name', 'Plot Group')} ({len(wells)})",
            row,
        )
        load_btn.setToolTip(", ".join(wells) or "No loaded wells in this group")
        load_btn.clicked.connect(
            lambda _=False, gid=group.get("id", ""): _plot_groups_load(app, gid)
        )
        rl.addWidget(load_btn, 1)
        del_btn = QPushButton("×", row)
        del_btn.setFixedWidth(28)
        del_btn.setToolTip("Delete this Plot Group")
        del_btn.clicked.connect(
            lambda _=False, gid=group.get("id", ""): _plot_groups_delete(app, gid)
        )
        rl.addWidget(del_btn)
        lay.addWidget(row)
    lay.addStretch(1)
