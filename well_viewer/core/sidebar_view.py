"""Main well-picker sidebar builder (Qt port)."""

from __future__ import annotations


from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)
from functools import partial
from well_viewer.core.selection_controller import on_sidebar_plate_col_activated
from well_viewer.core.selection_controller import on_sidebar_plate_corner_toggled
from well_viewer.core.selection_controller import on_sidebar_plate_drag_finished
from well_viewer.core.selection_controller import on_sidebar_plate_row_activated
from well_viewer.core.selection_controller import on_sidebar_plate_selection_changed
from well_viewer.core.selection_controller import on_sidebar_plate_well_activated
from well_viewer.core.selection_controller import on_sidebar_plate_well_dropped
from well_viewer.core.selection_controller import set_selected_wells
import logging

from well_viewer.core.safe import swallowed
from well_viewer.domain.plot_groups import make_plot_group
from well_viewer.domain.viewer_state import parse_rc

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
    app._sidebar_plate = app._ctx.widgets.register("_sidebar_plate", plate)
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
    plate.cornerToggled.connect(partial(on_sidebar_plate_corner_toggled, app))
    plate.wellActivated.connect(partial(on_sidebar_plate_well_activated, app))
    plate.rowHeaderActivated.connect(partial(on_sidebar_plate_row_activated, app))
    plate.columnHeaderActivated.connect(partial(on_sidebar_plate_col_activated, app))
    plate.wellDropped.connect(partial(on_sidebar_plate_well_dropped, app))
    # Hover tooltip surfaces the user's Sample-Definitions label when set,
    # alongside the well id, so the rail plate doubles as a quick label
    # readout — useful on tabs where the sidebar doesn't show the labels.
    from well_viewer.core.ui_helpers import well_plate_tooltip_provider
    plate.setWellTooltipProvider(well_plate_tooltip_provider(app._ctx))

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
        app._ctx, parent, include_add_controls=True,
    )
    layout.addWidget(plot_groups_section)
    app._plot_groups_sidebar_section = plot_groups_section
    app._plot_groups_refresh = lambda: refresh_plot_groups_sidebar(app._ctx)


def build_plot_groups_drawer(ctx, parent, *, include_add_controls: bool = True):
    """Build a Plot Groups quick-load drawer and register it for refresh.

    Returns a ``CollapsibleSection`` widget the caller adds to its layout.
    Multiple drawers can co-exist (one on the main plotting sidebar, one on
    the Image Table sidebar) — each is registered in
    ``ctx.runtime.plot_groups.drawers`` and ``refresh_plot_groups_sidebar``
    updates them all in lock-step.

    Takes ``ctx``: the drawer registry lives on ``ctx.runtime.plot_groups`` and
    the per-drawer Qt widgets register through ``ctx.widgets`` (which still
    stamps ``app._<name>`` for the legacy singular back-compat readers).

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
        # Pass THIS drawer's own name field into the handler (closure) so several
        # author-capable drawers (main sidebar + Image Table) don't fight over the
        # single shared ``_plot_group_name_edit`` registry key — each Add reads the
        # name typed into its own drawer.
        add_btn.clicked.connect(
            lambda _=False, ne=name_edit: _plot_groups_add_current(ctx, ne))
        nr.addWidget(add_btn)
        section.addWidget(name_row)
        # Back-compat: the original main-sidebar API exposed these directly
        # (register() stamps app._plot_group_name_edit / _plot_groups_add_btn).
        # First drawer wins the singular key (the main sidebar builds first).
        if not ctx.widgets.has("_plot_group_name_edit"):
            ctx.widgets.register("_plot_group_name_edit", name_edit)
            ctx.widgets.register("_plot_groups_add_btn", add_btn)

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
    drawers = ctx.runtime.plot_groups.drawers
    if drawers is None:
        ctx.runtime.plot_groups.drawers = [drawer]
    else:
        drawers.append(drawer)
    # Back-compat: keep the original singular references pointing at the
    # primary (main-sidebar) drawer so older callers still resolve.
    if not ctx.widgets.has("_plot_groups_list_widget"):
        ctx.widgets.register("_plot_groups_list_widget", list_widget)
        ctx.widgets.register("_plot_groups_list_layout", list_layout)
        ctx.widgets.register("_plot_groups_empty_label", empty_label)

    return section


def _plot_groups_schedule_save(ctx) -> None:
    p = ctx.persistence
    if p is not None:
        p.schedule_save("plot_groups")


def _wells_for_new_plot_group(ctx) -> "list[str]":
    """The well set a new Plot Group should capture, sorted by plate position.

    Prefers the raw plate selection (``selected_wells``). But once replicate
    sets exist the sidebar plate runs in passive *group-visibility* mode on
    plotting tabs — clicks toggle a group's ``hidden`` flag instead of selecting
    wells, so ``selected_wells`` is empty. In that mode the "current selection"
    for plotting is exactly the union of every visible (non-hidden) group's
    wells plus any active solo wells (mirrors the rep-mode "active set" the plots
    use, see ``selection_controller``). Falling back to that lets "Add Plot
    Group" save whatever is currently on screen — the feature was previously
    unusable whenever any replicate set was defined.
    """
    sel = ctx.state.selection
    raw = set(sel.selected_wells or set())
    if not raw and sel.selections:
        loaded = ctx.state.dataset.well_paths
        raw = {
            w
            for s in sel.selections if not s.get("hidden")
            for w in (s.get("wells") or []) if w in loaded
        }
        raw |= {w for w in (sel.active_solo_wells or set()) if w in loaded}
    return sorted(raw, key=parse_rc)


def _plot_groups_add_current(ctx, name_edit=None) -> None:
    wells = _wells_for_new_plot_group(ctx)
    if not wells:
        ctx.set_status("Select wells (or show a group) before adding a Plot Group.")
        return
    # Prefer the drawer that triggered this (its own name field); fall back to the
    # shared registry key for any other caller.
    edit = name_edit if name_edit is not None else ctx.widgets.get_or_none("_plot_group_name_edit")
    name = edit.text().strip() if edit is not None else ""
    existing = ctx.state.selection.plot_groups or []
    used_names = {g.get("name") for g in existing}
    used_ids = {g.get("id") for g in existing}
    group = make_plot_group(
        name=name or f"Plot Group {len(existing) + 1}",
        wells=wells,
        used_names=used_names,
        used_ids=used_ids,
    )
    ctx.state.selection.plot_groups = list(existing) + [group]
    if edit is not None:
        edit.clear()
    refresh_plot_groups_sidebar(ctx)
    _plot_groups_schedule_save(ctx)
    ctx.set_status(
        f"Added Plot Group “{group['name']}” ({len(group['wells'])} wells)."
    )


def _plot_groups_load(ctx, group_id: str) -> None:
    group = next(
        (g for g in ctx.state.selection.plot_groups if g.get("id") == group_id),
        None,
    )
    if group is None:
        return
    valid = set(ctx.state.dataset.well_paths or {})
    wells = {w for w in (group.get("wells") or []) if w in valid}
    # ``set_selected_wells`` is the single mutation point (prev-sel tracking +
    # redraw fan-out); thread ctx.
    set_selected_wells(ctx, wells)
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
        plate = ctx.runtime.image_table.sidebar_plate
        if plate is not None:
            try:
                plate.setSelectedWellIds(sorted(wells))
            except Exception:
                swallowed("plot_groups: image table plate sync")
        else:
            image_table_state = getattr(ctx.state, "image_table", None)
            if image_table_state is not None:
                image_table_state.active_wells = set(wells)
    except Exception:
        _logger.debug("plot_groups: image table active_wells sync failed", exc_info=True)
    ctx.set_status(
        f"Loaded Plot Group “{group.get('name', 'Plot Group')}” "
        f"({len(wells)} wells)."
    )


def _plot_groups_delete(ctx, group_id: str) -> None:
    before = list(ctx.state.selection.plot_groups or [])
    after = [g for g in before if g.get("id") != group_id]
    if len(after) == len(before):
        return
    ctx.state.selection.plot_groups = after
    refresh_plot_groups_sidebar(ctx)
    _plot_groups_schedule_save(ctx)


def refresh_plot_groups_sidebar(ctx) -> None:
    """Rebuild the Plot Groups quick-load buttons across every registered drawer.

    Each sidebar that hosts a Plot Groups drawer registers it via
    ``build_plot_groups_drawer``; this function repaints every one so the
    main plotting sidebar and the Image Table sidebar stay in lock-step.
    """
    drawers = ctx.runtime.plot_groups.drawers
    if not drawers:
        # Fallback for any builder that still uses the original singular refs.
        lay = ctx.widgets.get_or_none("_plot_groups_list_layout")
        empty = ctx.widgets.get_or_none("_plot_groups_empty_label")
        if lay is None:
            return
        drawers = [{
            "list_layout": lay,
            "list_widget": ctx.widgets.get_or_none("_plot_groups_list_widget"),
            "empty_label": empty,
        }]
    groups = list(ctx.state.selection.plot_groups or [])
    valid = set(ctx.state.dataset.well_paths or {})
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
                lambda _=False, gid=group.get("id", ""): _plot_groups_load(ctx, gid)
            )
            rl.addWidget(load_btn, 1)
            del_btn = QPushButton("×", row)
            del_btn.setFixedWidth(28)
            del_btn.setToolTip("Delete this Plot Group")
            del_btn.clicked.connect(
                lambda _=False, gid=group.get("id", ""): _plot_groups_delete(ctx, gid)
            )
            rl.addWidget(del_btn)
            lay.addWidget(row)
        lay.addStretch(1)
