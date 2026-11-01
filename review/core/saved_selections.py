"""Saved selections — named well subsets the user can reload in one click.

A saved selection only restores which wells are lit: it pools nothing, so it is
neither a group nor a replicate set. The drawer that lists them sits in the
rail's main frame on the plotting pages (``review/core/sidebar_view.build_sidebar``)
and under the Image Table's own picker (``image_table/picker_view``); every drawer is registered on
``ctx.runtime.plot_groups.drawers`` and repainted together by
:func:`refresh_plot_groups_sidebar`.

The symbols keep the ``plot_group`` spelling of the on-disk block
(``domain/plot_groups``); only what a user reads says "saved selection".
"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from core.safe import swallowed
from domain.plot_groups import make_plot_group
from review.core.selection_controller import rebuild_all
from review.core.selection_controller import set_selected_wells
from review.domain.viewer_state import parse_rc
from ui import theme

_logger = logging.getLogger("review")


def build_plot_groups_drawer(ctx, parent, *, include_add_controls: bool = True,
                             wells_source=None):
    """Build a Saved selections quick-load drawer and register it for refresh.

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

    section = CollapsibleSection("Saved selections", parent, expanded=False)
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
            "Save the wells currently selected under a name you can get back. "
            "A saved selection only restores which wells are lit — it pools "
            "nothing, so it is neither a group nor a replicate set."
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

    empty_label = QLabel("No saved selections yet.", section)
    empty_label.setObjectName("Muted")
    empty_label.setWordWrap(True)
    section.addWidget(empty_label)

    scroll = QScrollArea(section)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    # Cap the list height so a long set of saved groups scrolls inside the
    # drawer instead of growing it tall enough to cover whatever sits above.
    scroll.setMaximumHeight(theme.Metrics.scroll_max_h)
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
    sets exist and no well-editing pane (Home / Review CSV) is on
    screen, so clicks toggle a set's ``hidden`` flag instead of selecting wells.
    Falls back to "replicate sets exist" when the shell isn't reachable — the
    lightweight ``ctx`` used by headless callers and unit tests has no window.
    """
    from review.domain.plot_members import variable_grouping_in_force
    if not ctx.state.selection.selections and not variable_grouping_in_force(ctx):
        return False
    try:
        from review.core.selection_controller import _plate_passive_in_group_mode
        return bool(_plate_passive_in_group_mode(ctx))
    except Exception:
        _logger.debug(
            "plot_groups: plate-mode probe failed; assuming group-visibility mode",
            exc_info=True,
        )
        return True


def _wells_for_new_plot_group(ctx) -> list[str]:
    """The well set a new saved selection should capture, in plate position order.

    Which store holds "the current selection" depends on the mode the sidebar
    plate is in, so the capture has to branch on the same condition the plate
    does:

    * **group-visibility mode** (replicate sets defined or the plots grouped by
      a variable, no well-editing pane on screen) — the plate paints what the
      plots draw and clicks toggle it. What is on screen is exactly the wells of
      ``plot_replicate_sets``: every visible set plus any active solo wells, or
      every visible value of the Group-by variable.
      ``selected_wells`` is not consulted at all here, because nothing in this
      mode keeps it current — it holds whatever a per-well tab, or an earlier
      saved-selection load, last wrote. Reading it first meant that once any well
      landed there, "Add Saved selection" saved that stale set instead of the sets
      the user could see, so a group covering several replicate sets came back
      as one.
    * **per-well mode** — the plate is a well selector and ``selected_wells`` is
      the selection the user made.
    """
    sel = ctx.state.selection
    if _group_visibility_mode(ctx):
        # What the plots draw, from the seam they draw it from: the visible
        # groups and solo wells, or the visible values under a variable
        # grouping. Re-deriving it from the groups' ``hidden`` flags captured
        # the user's groups while the plots showed doses.
        from review.domain.plot_members import plot_replicate_sets
        loaded = ctx.state.dataset.well_paths
        raw = {w for r in plot_replicate_sets(ctx) for w in r.wells if w in loaded}
    else:
        raw = set(sel.selected_wells or set())
    return sorted(raw, key=parse_rc)


def _plot_groups_add_current(ctx, name_edit=None, *, wells_source=None) -> None:
    """Save the current selection as a Saved selection.

    ``wells_source`` is the triggering drawer's own selection reader (see
    :func:`build_plot_groups_drawer`); without one this is the plotting
    sidebar's plate.
    """
    wells = (list(wells_source()) if callable(wells_source)
             else _wells_for_new_plot_group(ctx))
    if not wells:
        ctx.notify.set_status(
            "Select wells on the plate above before saving a selection."
            if callable(wells_source) else
            "Select wells (or show a group) before saving a selection."
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
        name=name or f"Selection {len(existing) + 1}",
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
        f"Added saved selection “{group['name']}” ({len(group['wells'])} wells)."
    )


def plot_group_visibility_plan(selections, wells) -> tuple[list[bool], set[str]]:
    """How loading a Saved selection of *wells* should repaint replicate visibility.

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
    """Repaint replicate visibility from a just-loaded Saved selection. No-op when no
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
    # Groups still never mutate the sample definitions.
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
        f"Loaded saved selection “{group.get('name', 'Selection')}” "
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
    """Rebuild the Saved selections quick-load buttons across every registered drawer.

    Each sidebar that hosts a Saved selections drawer registers it via
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
                f"{group.get('name', 'Selection')} ({len(wells)})",
                row,
            )
            load_btn.setToolTip(", ".join(wells) or "No loaded wells in this group")
            load_btn.clicked.connect(
                lambda _=False, gid=group.get("id", ""): _plot_groups_load(ctx, gid)
            )
            rl.addWidget(load_btn, 1)
            del_btn = QPushButton("×", row)
            del_btn.setFixedWidth(theme.Metrics.icon_btn_w)
            del_btn.setToolTip("Delete this saved selection")
            del_btn.clicked.connect(
                lambda _=False, gid=group.get("id", ""): _plot_groups_delete(ctx, gid)
            )
            rl.addWidget(del_btn)
            lay.addWidget(row)
        lay.addStretch(1)
