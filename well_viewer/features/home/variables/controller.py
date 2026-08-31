"""Variables tab orchestration — every control, wired to a domain call.

The tab holds no rules. Each handler reads the live
:class:`~well_viewer.domain.sample_variables.VariableSet`, calls one pure
function on it, stores the result and redraws. That is what keeps the rules —
unique names, no duplicate labels, a rename that keeps its id so the wells
follow, a kind switch that re-reads the numbers, a move that clamps — testable
without a window, and it is the split §7.6 exists to enforce: a rule implemented
in a signal handler is a rule nothing tests.

**Every control is connected here and nowhere else**, so "the button exists" and
"the button does something" cannot come apart. ``wire_variables`` is called once
by the tab builder and is idempotent.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt

from well_viewer.core.safe import swallow
from well_viewer.domain import sample_variables as sv
from well_viewer.domain.plate_layout import wells_in_fill_lines

_logger = logging.getLogger("well_viewer")


def _widget(ctx, name):
    return ctx.widgets.get_or_none(name)


def _vset(ctx) -> sv.VariableSet:
    return getattr(ctx.state.selection, "variables", None) or sv.VariableSet()


def _commit(ctx, vset: sv.VariableSet) -> None:
    """Store *vset*, persist it, and redraw.

    The autosave is scheduled rather than written: the value list is edited a
    keystroke at a time, and a disk write per keystroke on a file the pipeline
    also writes is both slow and a needless collision. The registry's debounce
    coalesces a burst into one write.
    """
    ctx.state.selection.variables = vset
    with swallow("schedule the sample-variables autosave"):
        ctx.persistence.schedule_save("sample_variables")
    # The plotting band's Group by combo lists these, so defining, renaming or
    # deleting one has to reach it — otherwise the band keeps offering a
    # variable that no longer exists, or fails to offer the one just created.
    with swallow("refresh the Group by control"):
        from well_viewer.core.group_by_controller import refresh_group_by_combo
        refresh_group_by_combo(ctx)
    refresh_variables(ctx)


def selected_variable_id(ctx) -> str | None:
    """The variable the rail has selected, or ``None``.

    Read off the item's stored id rather than its text, because the text is
    exactly what a rename changes — looking the row back up by its new name is
    how a rename becomes a delete-and-add.
    """
    listing = _widget(ctx, "_var_list")
    item = listing.currentItem() if listing is not None else None
    return item.data(Qt.UserRole) if item is not None else None


def selected_value_id(ctx) -> str | None:
    values = _widget(ctx, "_var_values")
    item = values.currentItem() if values is not None else None
    return item.data(Qt.UserRole) if item is not None else None


# ── redraw ──────────────────────────────────────────────────────────────────

def refresh_variables(ctx) -> None:
    """Repaint both columns from the live set. Safe before the tab is built."""
    from .tab_view import value_item

    listing = _widget(ctx, "_var_list")
    if listing is None:
        return
    vset = _vset(ctx)
    keep = selected_variable_id(ctx)

    listing.blockSignals(True)
    listing.clear()
    for variable in vset.variables:
        item = _variable_item(variable)
        listing.addItem(item)
        if variable.id == keep:
            listing.setCurrentItem(item)
    if listing.currentItem() is None and listing.count():
        listing.setCurrentRow(0)
    listing.blockSignals(False)

    variable_id = selected_variable_id(ctx)
    variable = vset.by_id(variable_id) if variable_id else None

    body = _widget(ctx, "_var_body")
    hint = _widget(ctx, "_var_hint")
    if body is not None:
        body.setVisible(variable is not None)
    if hint is not None:
        hint.setVisible(variable is None)
    if variable is None:
        # Everything the plate and the chips were showing belonged to a
        # variable that is no longer there — a dataset swap, a reset, or the
        # last one deleted. Left painted, the colours, rings and sentence
        # describe the previous plate while the masthead names the new one, and
        # the stale chips are still connected: pressing one reaches
        # ``assign_selected`` and does nothing at all.
        _clear_plate(ctx)
        return

    name = _widget(ctx, "_var_name")
    kind = _widget(ctx, "_var_kind")
    unit = _widget(ctx, "_var_unit")
    for widget in (name, kind, unit):
        if widget is not None:
            widget.blockSignals(True)
    if name is not None:
        name.setText(variable.name)
    if kind is not None:
        kind.setCurrentIndex(max(0, kind.findData(variable.kind)))
    if unit is not None:
        unit.setText(variable.unit)
        # Disabled rather than hidden: a field that vanishes reads as a bug,
        # one that greys out reads as a rule.
        unit.setEnabled(variable.is_numeric())
    for widget in (name, kind, unit):
        if widget is not None:
            widget.blockSignals(False)

    values = _widget(ctx, "_var_values")
    if values is not None:
        kept_value = selected_value_id(ctx)
        values.blockSignals(True)
        values.clear()
        for value in variable.values:
            item = value_item(value.label, value.id)
            values.addItem(item)
            if value.id == kept_value:
                values.setCurrentItem(item)
        values.blockSignals(False)

    note = _widget(ctx, "_var_note")
    if note is not None:
        note.setText(_note_for(variable))

    _paint_chips(ctx, variable)
    _paint_overlay_combo(ctx, variable)
    _paint_plate(ctx, variable)
    _paint_groups(ctx, variable)


def _variable_item(variable):
    from PySide6.QtWidgets import QListWidgetItem

    count = len(variable.values)
    suffix = f"  ({count})" if count else "  (no values yet)"
    item = QListWidgetItem(f"{variable.name}{suffix}")
    item.setData(Qt.UserRole, variable.id)
    return item


def _note_for(variable) -> str:
    """What this variable is short of, said where it is being defined.

    A variable with no values cannot be filled and cannot group anything, and a
    `number` variable whose labels do not parse cannot be an axis. Both are
    states the user can fix in the field right there, so they are said here
    rather than discovered as an empty plot later.
    """
    if not variable.values:
        return "Add at least two values before filling this onto the plate."
    if variable.is_numeric():
        unreadable = [v.label for v in variable.values if v.number is None]
        if unreadable:
            return (
                f"{len(unreadable)} value(s) are not numbers: "
                f"{', '.join(unreadable)}. They can still be assigned to wells "
                "and will still group and colour, but they have no place on a "
                "numeric axis — a response curve leaves them out and says so."
            )
    return ""


# ── the controls ────────────────────────────────────────────────────────────

def wire_variables(ctx) -> None:
    """Connect every control on the tab. Called once, by the builder."""
    listing = _widget(ctx, "_var_list")
    if listing is None or getattr(listing, "_aw_wired", False):
        return
    listing._aw_wired = True

    listing.currentItemChanged.connect(lambda *_: refresh_variables(ctx))
    _widget(ctx, "_var_add").clicked.connect(lambda: add_variable(ctx))
    _widget(ctx, "_var_remove").clicked.connect(lambda: remove_variable(ctx))

    _widget(ctx, "_var_name").editingFinished.connect(lambda: commit_name(ctx))
    _widget(ctx, "_var_kind").currentIndexChanged.connect(
        lambda *_: commit_kind(ctx))
    _widget(ctx, "_var_unit").editingFinished.connect(lambda: commit_unit(ctx))

    entry = _widget(ctx, "_var_value_entry")
    entry.returnPressed.connect(lambda: add_value(ctx))
    _widget(ctx, "_var_value_add").clicked.connect(lambda: add_value(ctx))
    _widget(ctx, "_var_value_remove").clicked.connect(lambda: remove_value(ctx))
    _widget(ctx, "_var_value_up").clicked.connect(lambda: move_value(ctx, -1))
    _widget(ctx, "_var_value_down").clicked.connect(lambda: move_value(ctx, +1))
    _widget(ctx, "_var_values").itemChanged.connect(
        lambda item: rename_value(ctx, item))

    _widget(ctx, "_var_fill_apply").clicked.connect(lambda: apply_fill(ctx))
    _widget(ctx, "_var_overlay").currentIndexChanged.connect(
        lambda *_: refresh_variables(ctx))


def add_variable(ctx) -> None:
    # Named "Variable" so the id is minted from something, and renamed by the
    # user a keystroke later — which means the id never reflects the name. That
    # is harmless for correctness (ids are internal and scoped to one plate's
    # block) but it does make ``group_by_variable``, which stores an id, able to
    # survive a dataset swap onto a *different* variable that happens to share
    # the generic id. ``refresh_group_by_combo`` clears the setting when the id
    # resolves to nothing, and ``plot_replicate_sets`` falls back when it groups
    # nothing, so the failure mode is a wrong-but-visible grouping rather than a
    # silent one.
    vset, variable_id = sv.new_variable(_vset(ctx), "Variable")
    _commit(ctx, vset)
    _select_variable(ctx, variable_id)
    name = _widget(ctx, "_var_name")
    if name is not None:
        # Straight into the name field: the one thing a new variable needs is a
        # name, and the placeholder it was given is not one.
        name.setFocus()
        name.selectAll()


def remove_variable(ctx) -> None:
    variable_id = selected_variable_id(ctx)
    if variable_id:
        _commit(ctx, sv.remove_variable(_vset(ctx), variable_id))


def commit_name(ctx) -> None:
    variable_id = selected_variable_id(ctx)
    name = _widget(ctx, "_var_name")
    if variable_id and name is not None:
        _commit(ctx, sv.rename_variable(_vset(ctx), variable_id, name.text()))


def commit_kind(ctx) -> None:
    variable_id = selected_variable_id(ctx)
    kind = _widget(ctx, "_var_kind")
    unit = _widget(ctx, "_var_unit")
    if variable_id and kind is not None:
        _commit(ctx, sv.set_kind(_vset(ctx), variable_id, kind.currentData(),
                                 unit.text() if unit is not None else ""))


def commit_unit(ctx) -> None:
    variable_id = selected_variable_id(ctx)
    unit = _widget(ctx, "_var_unit")
    if variable_id and unit is not None:
        _commit(ctx, sv.set_unit(_vset(ctx), variable_id, unit.text()))


def add_value(ctx) -> None:
    variable_id = selected_variable_id(ctx)
    entry = _widget(ctx, "_var_value_entry")
    if not variable_id or entry is None:
        return
    text = entry.text().strip()
    if not text:
        return
    # Several at once, because that is how a dose series is written down and
    # retyping the commas is the difference between this taking ten seconds and
    # taking a minute. A label containing a comma is not lost — it just has to
    # be added on its own, which the split leaves possible.
    vset = _vset(ctx)
    for part in [p.strip() for p in text.split(",")] if "," in text else [text]:
        if part:
            vset = sv.add_value(vset, variable_id, part)
    entry.clear()
    _commit(ctx, vset)


def remove_value(ctx) -> None:
    variable_id = selected_variable_id(ctx)
    value_id = selected_value_id(ctx)
    if variable_id and value_id:
        _commit(ctx, sv.remove_value(_vset(ctx), variable_id, value_id))


def move_value(ctx, delta: int) -> None:
    variable_id = selected_variable_id(ctx)
    value_id = selected_value_id(ctx)
    if variable_id and value_id:
        _commit(ctx, sv.move_value(_vset(ctx), variable_id, value_id, delta))


def rename_value(ctx, item) -> None:
    """An in-place edit of a value's label.

    The item carries its id, so this is a rename and every well holding the
    value follows it. Reading the row back by its new text would make this a
    delete-and-add and orphan them.
    """
    variable_id = selected_variable_id(ctx)
    value_id = item.data(Qt.UserRole) if item is not None else None
    if variable_id and value_id:
        _commit(ctx, sv.rename_value(_vset(ctx), variable_id, value_id,
                                     item.text()))


def _select_variable(ctx, variable_id: str) -> None:
    listing = _widget(ctx, "_var_list")
    if listing is None:
        return
    for row in range(listing.count()):
        if listing.item(row).data(Qt.UserRole) == variable_id:
            listing.setCurrentRow(row)
            return


# ── the plate ───────────────────────────────────────────────────────────────

def dataset_wells(ctx) -> list[str]:
    """The wells this dataset holds, which is what a fill covers.

    Not all 96: a plate imaged in half is half a plate, and cycling values over
    the empty slots would put every value one place out from the well it was
    meant for, invisibly, because there is nothing on screen where the absent
    well would be.
    """
    return sorted(getattr(ctx.state.dataset, "well_paths", {}) or {})


def _value_colours(variable) -> dict:
    """``value id -> colour`` for this tab's chips and plate.

    **Through** ``domain/variable_series.value_colors``, which is where the
    plots read the same answer. It used to be a second implementation here — the
    same palette walked with a stride of seven rather than one — and the two
    disagreed about every value but the first: a dose that inked green on a bar
    chart painted purple on the plate that assigned it. ``value_colors``' own
    docstring argues the shared palette exists so there is not "a second
    categorical vocabulary… two unrelated colour languages on one screen", and
    that is exactly what two strides over one palette produced.

    The stride was not an arbitrary difference either. The plots' `i % n` was
    chosen on a measurement — consecutive palette entries are 199 apart in RGB
    at three values against 30 for picks spread across all 96 — and this copy
    kept a third strategy that measurement never covered.
    """
    from well_viewer.domain.variable_series import value_colors

    return value_colors(variable)


def _paint_chips(ctx, variable) -> None:
    """One chip per value, plus a Clear. Select wells, then click a chip."""
    from PySide6.QtWidgets import QPushButton

    from .tab_view import value_chip

    host = _widget(ctx, "_var_chips")
    if host is None:
        return
    layout = host.layout()
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)

    colours = _value_colours(variable)
    for value in variable.values:
        chip = value_chip(variable.display_label(value.id), value.id, host,
                          swatch=colours.get(value.id))
        chip.clicked.connect(
            lambda _checked=False, _v=value.id: assign_selected(ctx, _v))
        layout.addWidget(chip)

    if variable.values:
        clear = QPushButton("Clear", host)
        clear.setObjectName("HomeAction")
        clear.clicked.connect(lambda: assign_selected(ctx, None))
        layout.addWidget(clear)
    layout.addStretch(1)


def _paint_plate(ctx, variable) -> None:
    """Colour, ring and read back the plate for the selected variable.

    Three independent layers, none of them colour alone: the fill says which
    value, a **ring** says a well in this dataset has no value for this
    variable, and the line underneath counts them. The ring is not the empty
    socket — that already means "not in the dataset", and a well that exists but
    is unannotated is a different fact with a different fix.
    """
    plate = _widget(ctx, "_var_plate")
    if plate is None:
        return
    wells = dataset_wells(ctx)
    vset = _vset(ctx)

    plate.setEnabledWells(wells)
    plate.clearWellColors()
    plate.clearWellRings()
    plate.setColoredWellsLit(True)

    colours = _value_colours(variable)
    for well in wells:
        value_id = vset.value_id_for(variable.id, well)
        if value_id in colours:
            plate.setWellColor(well, colours[value_id])

    missing = sv.unassigned_wells(vset, variable.id, wells)
    plate.setWellRings(missing)

    scope = _widget(ctx, "_var_fill_scope")
    if scope is not None:
        scope.setText(f"over the {len(wells)} well(s) in this dataset")

    readout = _widget(ctx, "_var_readout")
    if readout is not None:
        readout.setText(_readout_for(variable, wells, missing))

    _paint_overlay(ctx, variable)

    from well_viewer.core.ui_helpers import well_plate_tooltip_provider
    plate.setWellTooltipProvider(well_plate_tooltip_provider(ctx))


def _readout_for(variable, wells, missing) -> str:
    """What the plate is showing, in words, including what it is short of."""
    if not wells:
        return "No dataset is open, so there are no wells to fill."
    if not variable.values:
        return f"{variable.name} has no values yet, so there is nothing to fill."
    if missing:
        shown = ", ".join(missing[:8])
        more = f" and {len(missing) - 8} more" if len(missing) > 8 else ""
        return (
            f"{len(missing)} of {len(wells)} well(s) have no {variable.name} — "
            f"{shown}{more}. They are ringed. A plot grouped by {variable.name} "
            "leaves out any member that includes one, and says how many."
        )
    return f"All {len(wells)} well(s) have a {variable.name}."


# ── assignment ──────────────────────────────────────────────────────────────

def assign_selected(ctx, value_id: str | None) -> None:
    """Give the plate's selected wells *value_id*, or clear them.

    Nothing happens with no selection, and it says so rather than silently
    doing nothing — a chip that appears inert is indistinguishable from one
    that is broken.
    """
    variable_id = selected_variable_id(ctx)
    plate = _widget(ctx, "_var_plate")
    if not variable_id or plate is None:
        return
    wells = list(plate.selectedWells or [])
    if not wells:
        readout = _widget(ctx, "_var_readout")
        if readout is not None:
            readout.setText(
                "Select wells on the plate first — drag across them, or click a "
                "row letter or column number — then click a value.")
        return
    _commit(ctx, sv.assign(_vset(ctx), variable_id, wells, value_id))


def apply_fill(ctx) -> None:
    """Cycle the selected variable's values over the dataset's wells.

    The grouping is ``plate_layout.wells_in_fill_lines`` and the cycling is
    ``sample_variables.fill``: one module knows the geometry, the other knows
    the values, and neither holds a second definition of the other's job.
    """
    variable_id = selected_variable_id(ctx)
    axis = _widget(ctx, "_var_fill_axis")
    repeat = _widget(ctx, "_var_fill_repeat")
    if not variable_id or axis is None or repeat is None:
        return
    lines = wells_in_fill_lines(dataset_wells(ctx), axis.currentData())
    _commit(ctx, sv.fill(_vset(ctx), variable_id, lines,
                         repeat=repeat.value()))


# ── a second variable, as a glyph ───────────────────────────────────────────

#: A short glyph per value position for the overlay variable. Letters and digits
#: rather than symbols: a well is a few pixels of ink and a user reading
#: "H" / "U" against HeLa / U2OS needs no key, while a triangle against a circle
#: needs one they have to keep in their head.
def _overlay_glyph(variable, value_id) -> str:
    value = variable.value_by_id(value_id) if value_id else None
    if value is None:
        return ""
    label = value.label.strip()
    if not label:
        return ""
    # A number keeps its own digits where they fit, because "10" *is* the
    # shortest true label for a 10 µM dose and an initial letter would be a code
    # the user has to learn. Anything longer is cut to its first character.
    return label if (variable.is_numeric() and len(label) <= 2) else label[0].upper()


def overlay_variable_id(ctx) -> str | None:
    combo = _widget(ctx, "_var_overlay")
    return combo.currentData() if combo is not None else None


def _paint_overlay_combo(ctx, variable) -> None:
    """List the *other* variables, keeping the user's pick where it still exists."""
    combo = _widget(ctx, "_var_overlay")
    if combo is None:
        return
    others = [v for v in _vset(ctx).variables if v.id != variable.id]
    keep = combo.currentData()
    combo.blockSignals(True)
    combo.clear()
    combo.addItem("Nothing", None)
    for other in others:
        combo.addItem(other.name, other.id)
    index = combo.findData(keep)
    combo.setCurrentIndex(index if index >= 0 else 0)
    combo.blockSignals(False)
    # Hidden entirely with nothing to offer, rather than shown holding only
    # "Nothing" — a control whose every option is a no-op is a control that
    # invites a click and answers with silence.
    combo.setEnabled(bool(others))


def _paint_overlay(ctx, variable) -> None:
    """Draw the overlay variable's glyphs, and say what they mean."""
    plate = _widget(ctx, "_var_plate")
    legend = _widget(ctx, "_var_overlay_legend")
    if plate is None:
        return
    plate.clearWellMarkers()

    vset = _vset(ctx)
    other = vset.by_id(overlay_variable_id(ctx) or "")
    if other is None:
        if legend is not None:
            legend.setText("")
        return

    for well in dataset_wells(ctx):
        glyph = _overlay_glyph(other, vset.value_id_for(other.id, well))
        if glyph:
            plate.setWellMarker(well, glyph)

    if legend is not None:
        pairs = ", ".join(f"{_overlay_glyph(other, v.id)} = {v.label}"
                          for v in other.values if _overlay_glyph(other, v.id))
        legend.setText(f"{other.name}: {pairs}" if pairs else "")


# ── group roll-up ───────────────────────────────────────────────────────────

def _paint_groups(ctx, variable) -> None:
    """One row per saved group, showing the value its wells agree on.

    **A group never lends a value to a well, and a well never takes one.** A
    group whose wells disagree reads ``Mixed (2)``; a group holding a well with
    no value reads ``no value`` and names how many are short. Neither is
    resolved to the group's first well or its commonest value, because a plotted
    member built that way would carry a dose the wells were never given, in a
    bar indistinguishable from a correct one.

    That is the state the user reaches by adding a well to a group *after*
    filling — which is ordinary, and is why this list exists rather than the
    tab trusting the plate alone.
    """
    from PySide6.QtWidgets import QListWidgetItem

    listing = _widget(ctx, "_var_groups")
    title = _widget(ctx, "_var_groups_title")
    if listing is None:
        return
    selections = list(getattr(ctx.state.selection, "selections", None) or [])
    known = set(dataset_wells(ctx))
    rows = []
    vset = _vset(ctx)
    for selection in selections:
        wells = [w for w in (selection.get("wells") or []) if w in known]
        if not wells:
            continue
        rows.append((selection.get("name") or "(unnamed)",
                     _group_summary(vset, variable, wells)))

    listing.clear()
    for name, summary in rows:
        listing.addItem(QListWidgetItem(f"{name} — {summary}"))
    for widget in (listing, title):
        if widget is not None:
            # No groups is not an empty list to stare at: this tab works
            # perfectly well without any, which is most of its point.
            widget.setVisible(bool(rows))


def _group_summary(vset, variable, wells) -> str:
    value_id = sv.roll_up(vset, variable.id, wells)
    if value_id is sv.MIXED:
        # Every well has a value here — ``roll_up`` returns ``None`` otherwise —
        # so this set cannot contain one, and counting it as a "value" is what
        # made this line report three across a group of two doses and one blank.
        distinct = {vset.value_id_for(variable.id, w) for w in wells}
        return f"Mixed ({len(distinct)} values across {len(wells)} wells)"
    if value_id is None:
        short = sv.unassigned_wells(vset, variable.id, wells)
        return (f"no value — {len(short)} of {len(wells)} wells unassigned "
                f"({', '.join(short[:4])}{'…' if len(short) > 4 else ''})")
    return f"{variable.display_label(value_id)} ({len(wells)} wells)"


def _clear_plate(ctx) -> None:
    """Blank every surface that described a variable, when there is none."""
    from PySide6.QtWidgets import QListWidget

    plate = _widget(ctx, "_var_plate")
    if plate is not None:
        plate.clearWellColors()
        plate.clearWellRings()
        plate.clearWellMarkers()
    chips = _widget(ctx, "_var_chips")
    if chips is not None:
        layout = chips.layout()
        while layout.count():
            widget = layout.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
    for name in ("_var_readout", "_var_note", "_var_overlay_legend",
                 "_var_fill_scope"):
        label = _widget(ctx, name)
        if label is not None:
            label.setText("")
    for name in ("_var_groups", "_var_groups_title"):
        widget = _widget(ctx, name)
        if widget is not None:
            if isinstance(widget, QListWidget):
                widget.clear()
            widget.setVisible(False)
    overlay = _widget(ctx, "_var_overlay")
    if overlay is not None:
        overlay.blockSignals(True)
        overlay.clear()
        overlay.setEnabled(False)
        overlay.blockSignals(False)
