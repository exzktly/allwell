"""Main well-picker sidebar builder (Qt port)."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)
from functools import partial
from well_viewer.core.selection_controller import on_sidebar_plate_col_activated
from well_viewer.core.selection_controller import on_sidebar_plate_drag_finished
from well_viewer.core.selection_controller import on_sidebar_plate_row_activated
from well_viewer.core.selection_controller import on_sidebar_plate_selection_changed
from well_viewer.core.selection_controller import on_sidebar_plate_well_activated
from well_viewer.core.selection_controller import on_sidebar_plate_well_dropped
import logging

from well_viewer.core.safe import swallowed
from well_viewer.domain.plot_groups import make_plot_group

_logger = logging.getLogger("well_viewer")


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

    # "Deselect all" button. Replaces the old selected-well count indicator
    # (count + pill chip), which was redundant with the plate itself. The
    # selection-count writers in selection_controller guard with hasattr, so
    # omitting ``_sel_count_lbl`` / ``_sel_count_chip`` here is safe — they
    # simply no-op.
    from well_viewer.core.selection_controller import select_none as _select_none
    deselect_row = QWidget(parent)
    deselect_row_layout = QHBoxLayout(deselect_row)
    deselect_row_layout.setContentsMargins(0, 0, 0, 0)
    deselect_row_layout.setSpacing(8)
    app._deselect_all_btn = QPushButton("Deselect all", deselect_row)
    app._deselect_all_btn.setProperty("variant", "secondary")
    app._deselect_all_btn.setToolTip("Clear the current well selection.")
    app._deselect_all_btn.clicked.connect(lambda _=False: _select_none(app))
    deselect_row_layout.addWidget(app._deselect_all_btn)
    deselect_row_layout.addStretch(1)
    layout.addWidget(deselect_row)

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
    # Placed *before* the Plot Groups drawer so that drawer is pinned to the
    # bottom of the sidebar and expands upward (see below).
    layout.addStretch(1)

    # Plot Groups are quick-load well subsets for plotting only. They live in a
    # collapsible drawer pinned to the bottom of the sidebar so users can save
    # the current selection and restore it later without creating stats /
    # replicate groups. Collapsed by default — only the header shows until the
    # user opens it — so the name field + saved-group list never push up into
    # (and overlap) the selected-well count bar above the plate.
    plot_groups_section = build_plot_groups_drawer(
        app, parent, include_add_controls=True,
    )
    layout.addWidget(plot_groups_section)
    app._plot_groups_sidebar_section = plot_groups_section
    app._plot_groups_refresh = lambda: refresh_plot_groups_sidebar(app)


def build_plot_groups_drawer(app, parent, *, include_add_controls: bool = True):
    """Build a Plot Groups quick-load drawer and register it for refresh.

    Returns a ``CollapsibleSection`` widget the caller adds to its layout.
    Multiple drawers can co-exist (one on the main plotting sidebar, one on
    the Image Table sidebar) — each is registered in
    ``app._plot_groups_drawers`` and ``refresh_plot_groups_sidebar`` updates
    them all in lock-step.

    ``include_add_controls=False`` omits the "name + Add" row, useful for
    secondary drawers (e.g. Image Table's) where the user only wants to
    pick from existing groups, not author new ones.
    """
    from widgets.collapsible_section import CollapsibleSection

    section = CollapsibleSection("Plot Groups", parent, expanded=False)
    section.setObjectName("PlotGroupsDrawer")

    if include_add_controls:
        name_row = QWidget(section)
        nr = QHBoxLayout(name_row)
        nr.setContentsMargins(0, 0, 0, 0)
        nr.setSpacing(6)
        name_edit = QLineEdit(name_row)
        name_edit.setPlaceholderText("Name current selection…")
        nr.addWidget(name_edit, 1)
        add_btn = QPushButton("Add", name_row)
        add_btn.setProperty("variant", "secondary")
        add_btn.setToolTip(
            "Save the currently selected wells as a Plot Group. Plot Groups only "
            "reload well selections; they are not stats or replicate groups."
        )
        add_btn.clicked.connect(lambda _=False: _plot_groups_add_current(app))
        nr.addWidget(add_btn)
        section.addWidget(name_row)
        # Back-compat: the original main-sidebar API exposed these directly.
        app._plot_group_name_edit = name_edit
        app._plot_groups_add_btn = add_btn

    empty_label = QLabel("No Plot Groups yet.", section)
    empty_label.setObjectName("Muted")
    empty_label.setWordWrap(True)
    section.addWidget(empty_label)

    scroll = QScrollArea(section)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    # Cap the list height so a long set of saved groups scrolls inside the
    # drawer instead of growing it tall enough to cover whatever sits above.
    scroll.setMaximumHeight(200)
    list_widget = QWidget(scroll)
    list_layout = QVBoxLayout(list_widget)
    list_layout.setContentsMargins(0, 0, 0, 0)
    list_layout.setSpacing(4)
    scroll.setWidget(list_widget)
    section.addWidget(scroll)

    drawer = {
        "section": section,
        "list_widget": list_widget,
        "list_layout": list_layout,
        "empty_label": empty_label,
    }
    drawers = getattr(app, "_plot_groups_drawers", None)
    if drawers is None:
        app._plot_groups_drawers = [drawer]
    else:
        drawers.append(drawer)
    # Back-compat: keep the original singular references pointing at the
    # primary (main-sidebar) drawer so older callers still resolve.
    if not hasattr(app, "_plot_groups_list_widget"):
        app._plot_groups_list_widget = list_widget
        app._plot_groups_list_layout = list_layout
        app._plot_groups_empty_label = empty_label

    return section


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
    # Mirror the selection into the Image Table's tab-local well set so a
    # group picked from any sidebar (plotting or Image Table) immediately
    # drives the Image Table grid — without this, the Image Table's plate
    # picker stayed pinned to its own previous selection.
    #
    # The plate's ``setSelectedWellIds`` emits ``selectionChanged``, which
    # the picker view wires to ``_on_plate_changed`` — that handler is the
    # canonical mutator for ``image_table.active_wells`` AND triggers the
    # grid refresh (``image_table_refresh_picker``). So the only thing we
    # need to do here is feed the plate the wells; we don't write
    # ``active_wells`` ourselves (it would race with the signal handler).
    # When the plate widget doesn't exist yet (user hasn't opened Image
    # Table this session, so its sidebar wasn't built), fall back to
    # writing ``active_wells`` directly so the state is correct when the
    # tab does build.
    try:
        plate = getattr(app, "_sidebar_image_table_plate", None)
        if plate is not None:
            try:
                plate.setSelectedWellIds(sorted(wells))
            except Exception:
                swallowed("plot_groups: image table plate sync")
        else:
            image_table_state = getattr(app._app_state, "image_table", None)
            if image_table_state is not None:
                image_table_state.active_wells = set(wells)
    except Exception:
        _logger.debug("plot_groups: image table active_wells sync failed", exc_info=True)
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
    """Rebuild the Plot Groups quick-load buttons across every registered drawer.

    Each sidebar that hosts a Plot Groups drawer registers it via
    ``build_plot_groups_drawer``; this function repaints every one so the
    main plotting sidebar and the Image Table sidebar stay in lock-step.
    """
    drawers = getattr(app, "_plot_groups_drawers", None)
    if not drawers:
        # Fallback for any builder that still uses the original singular refs.
        lay = getattr(app, "_plot_groups_list_layout", None)
        empty = getattr(app, "_plot_groups_empty_label", None)
        if lay is None:
            return
        drawers = [{
            "list_layout": lay,
            "list_widget": getattr(app, "_plot_groups_list_widget", None),
            "empty_label": empty,
        }]
    groups = list(app._app_state.selection.plot_groups or [])
    valid = set(app._app_state.dataset.well_paths or {})
    for drawer in drawers:
        lay = drawer.get("list_layout")
        if lay is None:
            continue
        while lay.count():
            item = lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        empty = drawer.get("empty_label")
        if empty is not None:
            empty.setVisible(not groups)
        list_widget = drawer.get("list_widget")
        for group in groups:
            row = QWidget(list_widget)
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
