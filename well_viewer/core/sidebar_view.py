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
from well_viewer.core.selection_controller import rebuild_all
from well_viewer.core.selection_controller import set_selected_wells
import logging

from well_viewer.core.safe import swallowed
from well_viewer.domain.plot_groups import make_plot_group
from well_viewer.domain.viewer_state import parse_rc

_logger = logging.getLogger("well_viewer")


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
    # Shared sidebar-plate sizing (the single home for it) — scales to the rail
    # width, keeps aspect, no fixed min-height floor. A trailing addStretch below
    # keeps the plate anchored to the top of the sidebar across tabs.
    from well_viewer.core.ui_helpers import apply_sidebar_plate_geometry
    apply_sidebar_plate_geometry(plate)
    layout.addWidget(plate)
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
    from well_viewer.core.ui_helpers import well_plate_tooltip_provider
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
    from well_viewer.core.selection_controller import select_none as _select_none
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
    from well_viewer.features.heatmap.sidebar_view import (
        build_heatmap_layout_sidebar,
    )
    heatmap_frame = build_heatmap_layout_sidebar(win._ctx, parent)
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
        win._ctx, parent, include_add_controls=True,
    )
    layout.addWidget(plot_groups_section)
    win._plot_groups_sidebar_section = plot_groups_section
    win._plot_groups_refresh = lambda: refresh_plot_groups_sidebar(win._ctx)


def build_plot_groups_drawer(ctx, parent, *, include_add_controls: bool = True,
                             wells_source=None):
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

    ``wells_source`` is a zero-argument callable returning the wells **this**
    drawer's Add should capture. A sidebar whose plate is not the shared one
    passes its own — the Image Table's picker keeps its selection in
    ``ctx.state.image_table.active_wells`` and deliberately ignores replicate
    sets, so a drawer sitting under it must read that store rather than the
    plotting sidebar's. Defaults to the plotting sidebar's plate selection
    (:func:`_wells_for_new_plot_group`). Core has no business knowing what a
    feature's sidebar keeps its wells in, so the knowledge travels in from the
    caller rather than being branched on here.
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
            lambda _=False, ne=name_edit, ws=wells_source:
                _plot_groups_add_current(ctx, ne, wells_source=ws))
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


def _group_visibility_mode(ctx) -> bool:
    """Whether the sidebar plate is painting *group visibility* right now.

    Mirrors ``selection_controller._plate_passive_in_group_mode``: replicate
    sets exist and no well-editing pane (Sample Definitions / Review CSV) is on
    screen, so clicks toggle a set's ``hidden`` flag instead of selecting wells.
    Falls back to "replicate sets exist" when the shell isn't reachable — the
    lightweight ``ctx`` used by headless callers and unit tests has no window.
    """
    if not ctx.state.selection.selections:
        return False
    try:
        from well_viewer.core.selection_controller import _plate_passive_in_group_mode
        return bool(_plate_passive_in_group_mode(ctx))
    except Exception:
        _logger.debug(
            "plot_groups: plate-mode probe failed; assuming group-visibility mode",
            exc_info=True,
        )
        return True


def _wells_for_new_plot_group(ctx) -> list[str]:
    """The well set a new Plot Group should capture, sorted by plate position.

    Which store holds "the current selection" depends on the mode the sidebar
    plate is in, so the capture has to branch on the same condition the plate
    does:

    * **group-visibility mode** (replicate sets defined, no well-editing pane on
      screen) — the plate paints set visibility and clicks toggle a set's
      ``hidden`` flag. What is on screen is exactly the union of every visible
      (non-hidden) set's wells plus any active solo wells: the same "active set"
      the plotting tabs draw from (see ``selection_controller``).
      ``selected_wells`` is not consulted at all here, because nothing in this
      mode keeps it current — it holds whatever a per-well tab, or an earlier
      Plot Group load, last wrote. Reading it first meant that once any well
      landed there, "Add Plot Group" saved that stale set instead of the sets
      the user could see, so a group covering several replicate sets came back
      as one.
    * **per-well mode** — the plate is a well selector and ``selected_wells`` is
      the selection the user made.
    """
    sel = ctx.state.selection
    if _group_visibility_mode(ctx):
        loaded = ctx.state.dataset.well_paths
        raw = {
            w
            for s in sel.selections if isinstance(s, dict) and not s.get("hidden")
            for w in (s.get("wells") or []) if w in loaded
        }
        raw |= {w for w in (sel.active_solo_wells or set()) if w in loaded}
    else:
        raw = set(sel.selected_wells or set())
    return sorted(raw, key=parse_rc)


def _plot_groups_add_current(ctx, name_edit=None, *, wells_source=None) -> None:
    """Save the current selection as a Plot Group.

    ``wells_source`` is the triggering drawer's own selection reader (see
    :func:`build_plot_groups_drawer`); without one this is the plotting
    sidebar's plate.
    """
    wells = (list(wells_source()) if callable(wells_source)
             else _wells_for_new_plot_group(ctx))
    if not wells:
        ctx.notify.set_status(
            "Select wells on the plate above before adding a Plot Group."
            if callable(wells_source) else
            "Select wells (or show a group) before adding a Plot Group."
        )
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
    ctx.notify.set_status(
        f"Added Plot Group “{group['name']}” ({len(group['wells'])} wells)."
    )


def plot_group_visibility_plan(selections, wells) -> tuple[list[bool], set[str]]:
    """How loading a Plot Group of *wells* should repaint replicate visibility.

    Returns ``(hidden_flags, solo_wells)`` — one ``hidden`` flag per entry of
    *selections*, in order, and the subset of *wells* that belongs to no
    replicate set (which the plotting tabs carry as solo wells). A set is shown
    when the group names at least one of its wells.

    Pure, so the mapping is testable without an application. Returns all-hidden
    and every well solo for an empty *selections*, which the caller treats as
    "not in replicate mode" and ignores.
    """
    flags: list[bool] = []
    claimed: set[str] = set()
    for s in selections:
        members = set(s.get("wells") or []) if isinstance(s, dict) else set()
        hit = members & set(wells)
        flags.append(not hit)
        claimed |= hit
    return flags, set(wells) - claimed


def _apply_plot_group_to_rep_mode(ctx, wells) -> None:
    """Repaint replicate visibility from a just-loaded Plot Group. No-op when no
    replicate sets are defined (plain mode already honours ``selected_wells``)."""
    sel = ctx.state.selection
    selections = [s for s in (sel.selections or []) if isinstance(s, dict)]
    if not selections:
        return
    flags, solo = plot_group_visibility_plan(selections, wells)
    changed = False
    for s, hidden in zip(selections, flags, strict=True):
        if bool(s.get("hidden")) != hidden:
            s["hidden"] = hidden
            changed = True
    if set(sel.active_solo_wells or set()) != solo:
        sel.active_solo_wells = set(solo)
        changed = True
    if changed:
        rebuild_all(ctx)


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
    # In replicate mode the plotting tabs read the *visible* replicate sets, not
    # ``selected_wells`` — clicks there toggle each group's ``hidden`` flag so
    # the plate highlight and the plots stay in lock-step (see
    # ``selection_controller.on_plate_sel_change``). Writing ``selected_wells``
    # alone therefore changed nothing on screen, which read as the group failing
    # to load. Mirror the group onto visibility the same way a row / column
    # click does: show every set the group touches, hide the rest, and carry any
    # well the group names that belongs to no set as a solo well. This changes
    # only what is *shown* — no replicate set's membership is touched, so Plot
    # Groups still never mutate Sample Definitions.
    _apply_plot_group_to_rep_mode(ctx, wells)
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
    ctx.notify.set_status(
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
