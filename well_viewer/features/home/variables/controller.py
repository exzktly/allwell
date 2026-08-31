"""Variables tab orchestration — every control, wired to a domain call.

The tab holds no rules. Each handler reads the live
:class:`~well_viewer.domain.sample_variables.VariableSet`, calls one pure
function on it, stores the result and redraws. That is what keeps the rules —
unique names, no duplicate labels, a rename that keeps its id so the wells
follow, a kind switch that re-reads the numbers, a move that clamps, what a
selection currently holds, what mark a second variable wears — testable without
a window, and it is the split §7.6 exists to enforce: a rule implemented in a
signal handler is a rule nothing tests.

**Every control is connected here and nowhere else**, so "the button exists" and
"the button does something" cannot come apart. :func:`wire_variables` is called
once by the tab builder and is idempotent.

**Every commit stashes what it replaced.** :func:`_commit` writes the previous
set to ``ctx.state.selection.variables_undo`` before overwriting, which is the
whole of the tab's undo: one step, covering the fill that rewrites ninety-six
wells at once as well as a mis-clicked chip. It is one step because the
expensive mistake here is a single button, not a session's worth of edits.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt

from well_viewer.core.safe import swallow
from well_viewer.domain import sample_variables as sv
from well_viewer.domain import variable_series as vseries
from well_viewer.domain.plate_layout import wells_in_fill_lines

_logger = logging.getLogger("well_viewer")


def _widget(ctx, name):
    return ctx.widgets.get_or_none(name)


def _vset(ctx) -> sv.VariableSet:
    return getattr(ctx.state.selection, "variables", None) or sv.VariableSet()


def _commit(ctx, vset: sv.VariableSet, *, note: str = "") -> None:
    """Store *vset*, stash what it replaced, persist it, and redraw.

    The autosave is scheduled rather than written: the value list is edited a
    keystroke at a time, and a disk write per keystroke on a file the pipeline
    also writes is both slow and a needless collision. The registry's debounce
    coalesces a burst into one write.

    *note* is what the tab says happened, and it is passed through the redraw
    rather than written into a label here — a message set before a repaint is a
    message the repaint overwrites, which is how the old fill confirmation
    managed to appear and then vanish on the next keystroke.
    """
    before = _vset(ctx)
    if vset is not before:
        ctx.state.selection.variables_undo = before
    ctx.state.selection.variables = vset
    with swallow("schedule the sample-variables autosave"):
        ctx.persistence.schedule_save("sample_variables")
    # The plotting band's Group by combo lists these, so defining, renaming or
    # deleting one has to reach it — otherwise the band keeps offering a
    # variable that no longer exists, or fails to offer the one just created.
    with swallow("refresh the Group by control"):
        from well_viewer.core.group_by_controller import refresh_group_by_combo
        refresh_group_by_combo(ctx)
    refresh_variables(ctx, note=note)


def selected_variable_id(ctx) -> str | None:
    """The variable the rail has selected, or ``None``.

    Read off the item's stored id rather than its text, because the text is
    exactly what a rename changes — and it now also carries a coverage count
    that changes on every assignment.
    """
    listing = _widget(ctx, "_var_list")
    item = listing.currentItem() if listing is not None else None
    return item.data(Qt.UserRole) if item is not None else None


def selected_value_id(ctx) -> str | None:
    values = _widget(ctx, "_var_values")
    item = values.currentItem() if values is not None else None
    return item.data(Qt.UserRole) if item is not None else None


def selected_wells(ctx) -> list[str]:
    plate = _widget(ctx, "_var_plate")
    return list(plate.selectedWells or []) if plate is not None else []


def dataset_wells(ctx) -> list[str]:
    """The wells this dataset holds, which is what a fill covers.

    Not all 96: a plate imaged in half is half a plate, and cycling values over
    the empty slots would put every value one place out from the well it was
    meant for, invisibly, because there is nothing on screen where the absent
    well would be.
    """
    return sorted(getattr(ctx.state.dataset, "well_paths", {}) or {})


def _value_colours(variable) -> dict:
    """``value id -> colour`` for this tab's chips, rows and plate.

    **Through** ``domain/variable_series.value_colors``, which is where the
    plots read the same answer. It used to be a second implementation here — the
    same palette walked with a stride of seven rather than one — and the two
    disagreed about every value but the first: a dose that inked green on a bar
    chart painted purple on the plate that assigned it.
    """
    return vseries.value_colors(variable)


# ── redraw ──────────────────────────────────────────────────────────────────

def refresh_variables(ctx, *, note: str = "") -> None:
    """Repaint the whole tab from the live set. Safe before the tab is built.

    *note* is a one-off sentence about what just happened — a fill's tally, a
    refused edit — shown until the next thing happens. It is a parameter rather
    than a label written by whichever handler had something to say, because
    those handlers wrote into labels that a subsequent repaint then cleared.
    """
    listing = _widget(ctx, "_var_list")
    if listing is None:
        return
    vset = _vset(ctx)
    wells = dataset_wells(ctx)
    keep = selected_variable_id(ctx)

    _paint_rail(ctx, listing, vset, wells, keep)

    variable_id = selected_variable_id(ctx)
    variable = vset.by_id(variable_id) if variable_id else None

    body = _widget(ctx, "_var_body")
    hint = _widget(ctx, "_var_hint")
    if body is not None:
        body.setVisible(variable is not None)
    if hint is not None:
        hint.setVisible(variable is None)

    _paint_undo(ctx)
    _say(ctx, note)
    if variable is None:
        # Everything the plate and the chips were showing belonged to a
        # variable that is no longer there — a dataset swap, a reset, or the
        # last one deleted. Left painted, the colours, rings and sentence
        # describe the previous plate while the masthead names the new one, and
        # the stale chips are still connected.
        _clear_plate(ctx, wells)
        return

    _show_plate_surfaces(ctx)
    _paint_definition(ctx, variable)
    _paint_chips(ctx, variable)
    _paint_overlay_combo(ctx, variable)
    _paint_plate(ctx, variable, wells)
    _paint_groups(ctx, variable)


def _say(ctx, note: str) -> None:
    """Put *note* on the tab's one message line, or clear it.

    Cleared on every redraw that carries no note, so a message describes the
    action the user just took rather than lingering over three unrelated ones.
    """
    label = _widget(ctx, "_var_message")
    if label is not None:
        label.setText(note or "")
        label.setVisible(bool(note))


def _paint_rail(ctx, listing, vset, wells, keep) -> None:
    from .editor_view import variable_item

    listing.blockSignals(True)
    listing.clear()
    for variable in vset.variables:
        annotated, total = sv.coverage(vset, variable.id, wells)
        item = variable_item(variable.name, variable.id, annotated, total)
        listing.addItem(item)
        if variable.id == keep:
            listing.setCurrentItem(item)
    if listing.currentItem() is None and listing.count():
        listing.setCurrentRow(0)
    listing.blockSignals(False)


def _paint_definition(ctx, variable) -> None:
    from .editor_view import value_item

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
        unit.setToolTip("" if variable.is_numeric()
                        else "Only a Number variable takes a unit.")
    for widget in (name, kind, unit):
        if widget is not None:
            widget.blockSignals(False)

    values = _widget(ctx, "_var_values")
    if values is not None:
        colours = _value_colours(variable)
        kept_value = selected_value_id(ctx)
        values.blockSignals(True)
        values.clear()
        for value in variable.values:
            item = value_item(value.label, value.id, colours.get(value.id, ""))
            values.addItem(item)
            if value.id == kept_value:
                values.setCurrentItem(item)
        values.blockSignals(False)

    note = _widget(ctx, "_var_note")
    if note is not None:
        note.setText(_note_for(variable))

    _sync_value_buttons(ctx, variable)
    _paint_shortfall(ctx, variable)
    remove = _widget(ctx, "_var_remove")
    if remove is not None:
        remove.setText(f"Delete “{variable.name}”")


def _sync_value_buttons(ctx, variable) -> None:
    """Grey the three value buttons when there is no value for them to act on.

    Always-enabled controls that return at their first guard are the failure
    this tab's own code names — *"a chip that appears inert is indistinguishable
    from one that is broken"* — and it was applying that standard to its chips
    and not to its own buttons. The move buttons additionally go dead at the
    end they cannot move towards, so the clamp is visible rather than being a
    press that does nothing.
    """
    values = list(getattr(variable, "values", ()) or ())
    value_id = selected_value_id(ctx)
    index = next((i for i, v in enumerate(values) if v.id == value_id), None)
    for name, on in (
        ("_var_value_remove", index is not None),
        ("_var_value_up", index is not None and index > 0),
        ("_var_value_down", index is not None and index < len(values) - 1),
    ):
        button = _widget(ctx, name)
        if button is not None:
            button.setEnabled(bool(on))


def _paint_shortfall(ctx, variable) -> None:
    """How many wells this variable is short, on the link that fixes them.

    The count used to be a sentence under the plate that also carried the well
    ids, a rendering fact and a policy statement about a different pane. It is
    one number, and it belongs on the control that selects exactly those wells —
    which turns "eight are missing" from something to read into something to
    press. The link greys out at zero, so a finished plate offers no action that
    would do nothing.
    """
    link = _widget(ctx, "_var_sel_missing")
    if link is None:
        return
    short = sv.unassigned_wells(_vset(ctx), variable.id, dataset_wells(ctx))
    link.setText(f"unassigned ({len(short)})" if short else "unassigned")
    link.setEnabled(bool(short))
    link.setToolTip(
        f"Select the {len(short)} well(s) with no {variable.name}."
        if short else f"Every well has a {variable.name}.")


def _paint_undo(ctx) -> None:
    button = _widget(ctx, "_var_undo")
    if button is None:
        return
    available = getattr(ctx.state.selection, "variables_undo", None) is not None
    button.setEnabled(available)
    button.setToolTip(
        "Put the variables back the way they were before the last change."
        if available else "Nothing to undo yet.")


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
    _widget(ctx, "_var_undo").clicked.connect(lambda: undo(ctx))

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
    values = _widget(ctx, "_var_values")
    values.itemChanged.connect(lambda item: rename_value(ctx, item))
    # The three value buttons follow the row the user is on, so "disabled"
    # tracks what is actually selectable rather than only being recomputed when
    # the whole tab repaints.
    values.currentItemChanged.connect(lambda *_: _sync_value_buttons(
        ctx, _vset(ctx).by_id(selected_variable_id(ctx) or "")
        or sv.Variable(id="", name="")))

    _widget(ctx, "_var_fill_apply").clicked.connect(lambda: apply_fill(ctx))
    _widget(ctx, "_var_fill_preview").clicked.connect(lambda: preview_fill(ctx))
    for name in ("_var_fill_axis", "_var_fill_repeat"):
        widget = _widget(ctx, name)
        signal = (widget.currentIndexChanged if name.endswith("axis")
                  else widget.valueChanged)
        signal.connect(lambda *_: refresh_variables(ctx))
    _widget(ctx, "_var_overlay").currentIndexChanged.connect(
        lambda *_: refresh_variables(ctx))

    # The plate, at last. Fourteen controls were connected here and none of
    # them was the surface the tab is about: selecting wells changed nothing on
    # screen, so a user could not tell a selection had been made, and the
    # "select wells first" instruction stayed up after they had.
    plate = _widget(ctx, "_var_plate")
    plate.selectionChanged.connect(lambda *_: refresh_selection_readout(ctx))
    _widget(ctx, "_var_sel_all").clicked.connect(
        lambda: set_selection(ctx, dataset_wells(ctx)))
    _widget(ctx, "_var_sel_none").clicked.connect(
        lambda: set_selection(ctx, []))
    _widget(ctx, "_var_sel_missing").clicked.connect(
        lambda: select_unassigned(ctx))
    _widget(ctx, "_var_sel_same").clicked.connect(lambda: select_same(ctx))

    _widget(ctx, "_var_groups").itemDoubleClicked.connect(
        lambda item: select_group_wells(ctx, item))


def add_variable(ctx) -> None:
    # Named "Variable" so the id is minted from something, and renamed by the
    # user a keystroke later — which means the id never reflects the name.
    # ``_new_variable_id`` mints a random suffix rather than the first free
    # slot, so a band setting carried across a dataset swap resolves to nothing
    # instead of to a different plate's first variable.
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
    """Delete the selected variable — after asking, when wells would go with it.

    One click destroyed the definition *and* every well→value assignment under
    it, with no confirmation: a filled 96-well dose grid is the most expensive
    thing on this page and was the easiest to lose.

    Only when there is something to lose. A variable nobody has filled yet costs
    nothing to delete, and a dialog for it would be the kind of prompt users
    learn to click through — which is how the prompt that matters gets clicked
    through too.
    """
    from PySide6.QtWidgets import QMessageBox

    variable_id = selected_variable_id(ctx)
    if not variable_id:
        return
    vset = _vset(ctx)
    variable = vset.by_id(variable_id)
    assigned = len(vset.assigned_wells(variable_id))
    if assigned:
        resp = QMessageBox.question(
            # ``getattr``: a parentless message box is still modal and still
            # asks. Reaching straight for ``ctx.window`` raised on a context
            # that has none, and the click handler's swallow turned that into a
            # delete that happened without asking — a confirmation that fails
            # open is worse than none, because it reads as protection.
            getattr(ctx, "window", None), "Delete this variable?",
            f"“{variable.name if variable else variable_id}” is assigned to "
            f"{assigned} well(s). Deleting it removes those assignments too. "
            f"Undo will put it back until you make another change. Continue?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
    name = variable.name if variable is not None else variable_id
    _commit(ctx, sv.remove_variable(vset, variable_id),
            note=f"Deleted “{name}”.")


def undo(ctx) -> None:
    """Put the variables back the way they were before the last change.

    One step. ``_commit`` stashes on every write, so this reverses the fill that
    rewrote the plate, the chip that was clicked with the wrong wells selected,
    and the delete — without the tab keeping a command history for a page whose
    expensive mistake is a single button.

    The stash is cleared afterwards rather than swapped, because a redo would
    need a second button and a second thing for the user to reason about, and
    the pressing case here is "put it back", not "put it back and forth".
    """
    previous = getattr(ctx.state.selection, "variables_undo", None)
    if previous is None:
        return
    ctx.state.selection.variables = previous
    ctx.state.selection.variables_undo = None
    with swallow("schedule the sample-variables autosave"):
        ctx.persistence.schedule_save("sample_variables")
    with swallow("refresh the Group by control"):
        from well_viewer.core.group_by_controller import refresh_group_by_combo
        refresh_group_by_combo(ctx)
    refresh_variables(ctx, note="Undone.")


def commit_name(ctx) -> None:
    """Rename, and say so when the name that lands is not the one typed.

    ``unique_name`` turns a second ``Dose`` into ``Dose 2`` — renaming beats
    refusing, because the user is mid-thought — but the field was then repainted
    from the model and their word was rewritten with nothing said about it.
    """
    variable_id = selected_variable_id(ctx)
    name = _widget(ctx, "_var_name")
    if not variable_id or name is None:
        return
    typed = name.text().strip()
    after = sv.rename_variable(_vset(ctx), variable_id, typed)
    landed = after.by_id(variable_id)
    note = ""
    if landed is not None and typed and landed.name != typed:
        note = (f"Another variable is already called “{typed}”, so this one is "
                f"“{landed.name}”.")
    _commit(ctx, after, note=note)


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
    """Add one value, or several separated by commas — and say what was refused.

    A duplicate label is dropped by ``add_value``, which is the right rule and
    was delivered in silence: the entry cleared itself and nothing appeared, so
    a user who typed a dose they already had saw the same screen as one whose
    button was broken.
    """
    variable_id = selected_variable_id(ctx)
    entry = _widget(ctx, "_var_value_entry")
    if not variable_id or entry is None:
        return
    text = entry.text().strip()
    if not text:
        refresh_variables(ctx, note="Type a value before pressing Add.")
        return
    # Several at once, because that is how a dose series is written down and
    # retyping the commas is the difference between this taking ten seconds and
    # taking a minute. A label containing a comma is not lost — it just has to
    # be added on its own, which the split leaves possible.
    vset = _vset(ctx)
    refused: list[str] = []
    for part in [p.strip() for p in text.split(",")] if "," in text else [text]:
        if not part:
            continue
        if sv.refusal_for_value(vset.by_id(variable_id), part) is not None:
            refused.append(part)
            continue
        vset = sv.add_value(vset, variable_id, part)
    entry.clear()
    note = ""
    if refused:
        note = (f"Already there, so not added again: {', '.join(refused)}.")
    _commit(ctx, vset, note=note)


def remove_value(ctx) -> None:
    variable_id = selected_variable_id(ctx)
    value_id = selected_value_id(ctx)
    if not (variable_id and value_id):
        return
    vset = _vset(ctx)
    variable = vset.by_id(variable_id)
    value = variable.value_by_id(value_id) if variable is not None else None
    held = len(sv.wells_with_value(vset, variable_id, value_id,
                                   dataset_wells(ctx)))
    note = ""
    if held:
        # ``prune_values`` leaves those wells unassigned rather than dangling,
        # which is the right thing and is invisible: the wells simply gain a
        # ring. Saying how many turns a silent cost into a stated one.
        note = (f"Removed “{value.label if value else value_id}”. "
                f"{held} well(s) are now unassigned.")
    _commit(ctx, sv.remove_value(vset, variable_id, value_id), note=note)


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

    A collision is refused by ``rename_value`` and the row then snaps back to
    its old text on the repaint. That is the correct outcome and it used to
    happen without a word, which reads as the edit having been lost.
    """
    variable_id = selected_variable_id(ctx)
    value_id = item.data(Qt.UserRole) if item is not None else None
    if not (variable_id and value_id):
        return
    vset = _vset(ctx)
    reason = sv.refusal_for_value(vset.by_id(variable_id), item.text(),
                                  exclude=value_id)
    if reason is not None:
        refresh_variables(ctx, note=reason)
        return
    _commit(ctx, sv.rename_value(vset, variable_id, value_id, item.text()))


def _select_variable(ctx, variable_id: str) -> None:
    listing = _widget(ctx, "_var_list")
    if listing is None:
        return
    for row in range(listing.count()):
        if listing.item(row).data(Qt.UserRole) == variable_id:
            listing.setCurrentRow(row)
            return


# ── the selection ───────────────────────────────────────────────────────────

def set_selection(ctx, wells) -> None:
    plate = _widget(ctx, "_var_plate")
    if plate is not None:
        plate.setSelectedWellIds(list(wells))
        refresh_selection_readout(ctx)


def select_unassigned(ctx) -> None:
    variable_id = selected_variable_id(ctx)
    if variable_id:
        set_selection(ctx, sv.unassigned_wells(_vset(ctx), variable_id,
                                               dataset_wells(ctx)))


def select_same(ctx) -> None:
    """Grow the selection to every well holding what it already holds.

    The route to "four of these wells got the wrong dose": select one of them,
    press this, and the rest are found for you. Without it the only way to act
    on "which wells hold 10 µM" was to read the colours off a grid of
    ninety-six by eye.

    When the current selection spans more than one value, every one of them is
    matched — the honest reading of "the same value as what I have picked", and
    the one that does not silently drop wells the user selected on purpose.
    """
    variable_id = selected_variable_id(ctx)
    picked = selected_wells(ctx)
    if not variable_id or not picked:
        refresh_variables(ctx, note="Select at least one well first, then this "
                                    "finds the rest holding the same value.")
        return
    vset = _vset(ctx)
    wells = dataset_wells(ctx)
    wanted = {value_id for value_id, _ in
              sv.selection_summary(vset, variable_id, picked)}
    grown: set[str] = set()
    for value_id in wanted:
        grown.update(sv.wells_with_value(vset, variable_id, value_id, wells))
    set_selection(ctx, sorted(grown))


def select_group_wells(ctx, item) -> None:
    """Select the wells of the group whose row was double-clicked."""
    wells = item.data(Qt.UserRole) if item is not None else None
    if wells:
        set_selection(ctx, list(wells))


def refresh_selection_readout(ctx) -> None:
    """Everything that changes when the selection does — which is two lines.

    Repainted from the plate's own signal rather than only when the whole tab
    redraws, so neither is ever behind the selection it describes.

    **The fill's scope is one of them**, and leaving it out was a live defect
    caught in a render: ``_fill_result`` narrows a fill to the selected wells,
    while this label was only repainted by a full redraw — so with four wells
    picked the page read *"Covers all 96 well(s)"* over a button that would have
    filled four. A control whose statement of what it is about to do disagrees
    with what it does is the exact shape of a silent substitution, and here the
    disagreement pointed the safe way round only by luck.
    """
    label = _widget(ctx, "_var_selection")
    if label is None:
        return
    variable_id_for_scope = selected_variable_id(ctx)
    variable_for_scope = _vset(ctx).by_id(variable_id_for_scope or "")
    if variable_for_scope is not None:
        _paint_fill_scope(ctx, variable_for_scope, dataset_wells(ctx))
    variable_id = selected_variable_id(ctx)
    picked = selected_wells(ctx)
    label.setText(_selection_sentence(_vset(ctx), variable_id, picked))


def _selection_sentence(vset, variable_id, picked) -> str:
    if not variable_id:
        return ""
    if not picked:
        return ("No wells selected — drag a box on the plate, or click a row "
                "letter or column number.")
    variable = vset.by_id(variable_id)
    parts = []
    for value_id, count in sv.selection_summary(vset, variable_id, picked):
        parts.append(f"{count} hold {vseries.value_label(variable, value_id)}")
    return f"{len(picked)} well(s) selected — {', '.join(parts)}."


# ── the plate ───────────────────────────────────────────────────────────────

def _paint_chips(ctx, variable) -> None:
    """One chip per value, plus a No value. Select wells, then click a chip."""
    from PySide6.QtWidgets import QPushButton

    from .plate_view import value_chip

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
        # "No value", not "Clear". It is the state the plots already name
        # ``no value``, and "Clear" beside a "Delete this variable" and a
        # "Remove value" was a third destructive word for a third scope.
        clear = QPushButton("No value", host)
        clear.setObjectName("HomeAction")
        clear.setToolTip("Unassign the selected wells — they go back to being "
                         "ringed and uncounted.")
        clear.clicked.connect(lambda: assign_selected(ctx, None))
        layout.addWidget(clear)


def _paint_plate(ctx, variable, wells) -> None:
    """Colour, ring and read back the plate for the selected variable.

    *preview* aside, everything drawn here comes from the stored set. The
    preview path passes the prospective set instead, which is the whole of what
    ``Preview`` is: paint from what ``fill`` returned and store nothing.
    """
    plate = _widget(ctx, "_var_plate")
    if plate is None:
        return
    vset = _vset(ctx)
    _paint_plate_from(ctx, plate, vset, variable, wells)

    _paint_header(ctx, variable)
    _set_enabled_with_reason(ctx, bool(wells),
                             "" if wells else
                             "No dataset is open, so there are no wells to fill.")
    _paint_fill_scope(ctx, variable, wells)
    refresh_selection_readout(ctx)


def _paint_plate_from(ctx, plate, vset, variable, wells) -> None:
    plate.setEnabledWells(wells)
    plate.clearWellColors()
    plate.clearWellRings()
    plate.setColoredWellsLit(True)

    colours = _value_colours(variable)
    for well in wells:
        value_id = vset.value_id_for(variable.id, well)
        if value_id in colours:
            plate.setWellColor(well, colours[value_id])

    plate.setWellRings(sv.unassigned_wells(vset, variable.id, wells))
    _paint_overlay(ctx, variable)

    from well_viewer.core.ui_helpers import well_plate_tooltip_provider
    plate.setWellTooltipProvider(well_plate_tooltip_provider(ctx))


def _paint_header(ctx, variable) -> None:
    """Which variable the plate is showing, and which is drawn on top of it.

    One rail selection decides the definition on screen, the chips' target, the
    plate's colours and what a fill rewrites. Four questions, one answer, and
    the answer was written down nowhere but the rail's own highlight.
    """
    label = _widget(ctx, "_var_showing")
    if label is None:
        return
    unit = f" ({variable.unit})" if variable.unit else ""
    text = f"{variable.name}{unit} as colour"
    other = _vset(ctx).by_id(overlay_variable_id(ctx) or "")
    if other is not None:
        text += f"   ·   {other.name} as letters"
    label.setText(text)


def _paint_fill_scope(ctx, variable, wells) -> None:
    """What ``Apply fill`` is about to do, in the future tense.

    It read ``over the 96 well(s) in this dataset`` — a true statement about the
    dataset that says nothing about the ninety-six assignments the button is
    about to replace. And when a fill changed nothing it reported nothing at
    all, so re-running one to check it took looked exactly like a broken button.
    """
    label = _widget(ctx, "_var_fill_scope")
    unit = _widget(ctx, "_var_fill_unit")
    lead = _widget(ctx, "_var_fill_lead")
    axis = _widget(ctx, "_var_fill_axis")
    if lead is not None:
        lead.setText(f"the next {variable.name},")
    if unit is not None and axis is not None:
        from well_viewer.domain.plate_layout import FILL_ALONG_COLS
        repeat = _widget(ctx, "_var_fill_repeat")
        n = repeat.value() if repeat is not None else 1
        # Singular at one, because the control reads as a sentence now and
        # "1 rows per value" is the sort of thing that makes a reader stop and
        # wonder whether they have misunderstood the control.
        word = "column" if axis.currentData() == FILL_ALONG_COLS else "row"
        unit.setText(f"{word} per value." if n == 1
                     else f"{word}s per value.")
    if label is None:
        return
    if not wells:
        label.setText("No dataset is open, so there are no wells to fill.")
        return
    if not variable.values:
        label.setText(f"{variable.name} has no values yet, so there is nothing "
                      f"to fill.")
        return
    scope = selected_wells(ctx) or wells
    already = len(sv.wells_with_value(_vset(ctx), variable.id, None, scope))
    replaced = len(scope) - already
    where = ("the selected" if selected_wells(ctx) else "all")
    tail = (f" and replaces the {variable.name} on {replaced} of them"
            if replaced else "")
    label.setText(f"Covers {where} {len(scope)} well(s){tail}.")


# ── assignment ──────────────────────────────────────────────────────────────

def assign_selected(ctx, value_id: str | None) -> None:
    """Give the plate's selected wells *value_id*, or clear them.

    Nothing happens with no selection, and the readout above already says so
    permanently — which is why this no longer writes an instruction into a
    status label that the user's compliance could not clear.
    """
    variable_id = selected_variable_id(ctx)
    plate = _widget(ctx, "_var_plate")
    if not variable_id or plate is None:
        return
    wells = selected_wells(ctx)
    if not wells:
        refresh_variables(ctx, note="Select wells on the plate first — drag "
                                    "across them, or click a row letter or "
                                    "column number — then click a value.")
        return
    vset = _vset(ctx)
    variable = vset.by_id(variable_id)
    named = (vseries.value_label(variable, value_id) if value_id
             else vseries.UNASSIGNED_LABEL)
    _commit(ctx, sv.assign(vset, variable_id, wells, value_id),
            note=f"Set {len(wells)} well(s) to {named}.")


# ── filling ─────────────────────────────────────────────────────────────────

def _fill_result(ctx, variable_id: str) -> tuple | None:
    """``(before, after, scope)`` for the fill the controls currently describe.

    One function, read by both ``Preview`` and ``Apply fill``, so the plate the
    user is shown and the plate they get cannot be computed two different ways.
    """
    axis = _widget(ctx, "_var_fill_axis")
    repeat = _widget(ctx, "_var_fill_repeat")
    if axis is None or repeat is None:
        return None
    # The selection, when there is one. The fill read the dataset and ignored
    # the plate entirely, so the most destructive button on the page rewrote all
    # ninety-six wells for a user who had carefully picked twenty-four.
    scope = selected_wells(ctx) or dataset_wells(ctx)
    lines = wells_in_fill_lines(scope, axis.currentData())
    before = _vset(ctx)
    return before, sv.fill(before, variable_id, lines,
                           repeat=repeat.value()), scope


def preview_fill(ctx) -> None:
    """Paint what ``Apply fill`` would do, and store nothing.

    Free, because ``sample_variables.fill`` is pure: the preview is the returned
    set painted onto the plate. The next repaint — any click anywhere — restores
    the stored one, which is what makes this safe to press without a way out.
    """
    variable_id = selected_variable_id(ctx)
    result = _fill_result(ctx, variable_id) if variable_id else None
    plate = _widget(ctx, "_var_plate")
    if result is None or plate is None:
        return
    before, after, scope = result
    variable = after.by_id(variable_id)
    if variable is None:
        return
    _paint_plate_from(ctx, plate, after, variable, dataset_wells(ctx))
    changed = _changed_wells(before, after, variable_id, dataset_wells(ctx))
    label = _widget(ctx, "_var_fill_scope")
    if label is not None:
        label.setText(
            f"Preview — Apply fill would change {changed} well(s). Nothing has "
            f"been saved; click anywhere to put the plate back.")


def apply_fill(ctx) -> None:
    """Cycle the selected variable's values over the wells in scope.

    The grouping is ``plate_layout.wells_in_fill_lines`` and the cycling is
    ``sample_variables.fill``: one module knows the geometry, the other knows
    the values, and neither holds a second definition of the other's job.
    """
    variable_id = selected_variable_id(ctx)
    result = _fill_result(ctx, variable_id) if variable_id else None
    if result is None:
        return
    before, after, scope = result
    changed = _changed_wells(before, after, variable_id, dataset_wells(ctx))
    filled = after.by_id(variable_id)
    named = filled.name if filled is not None else "the variable"
    # Reported either way. A fill that changed nothing used to report nothing,
    # so a user re-running one to check it had taken saw the same screen as one
    # whose button was broken.
    note = (f"Filled {changed} well(s) with {named}." if changed
            else f"Nothing changed — those wells already had that {named}.")
    _commit(ctx, after, note=note)


def _changed_wells(before, after, variable_id, wells) -> int:
    return sum(1 for w in wells
               if before.value_id_for(variable_id, w)
               != after.value_id_for(variable_id, w))


# ── a second variable, as a glyph ───────────────────────────────────────────

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
    # Disabled rather than hidden, with the rule stated on it. There *is* a rule
    # the user can act on — define a second variable — which is the repo's own
    # test for disabling over hiding; *Group by* is hidden instead because an
    # empty one states no rule, only an absence.
    combo.setEnabled(bool(others))
    combo.setToolTip(
        "" if others else "Define a second variable to overlay it on this plate.")


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

    # One map, read by both the plate and the key, so the two cannot disagree
    # about what a value's mark is.
    glyphs = vseries.overlay_glyphs(other)
    for well in dataset_wells(ctx):
        glyph = glyphs.get(vset.value_id_for(other.id, well))
        if glyph:
            plate.setWellMarker(well, glyph)

    if legend is not None:
        legend.setText(vseries.glyph_key(other))


# ── group roll-up ───────────────────────────────────────────────────────────

def _paint_groups(ctx, variable) -> None:
    """One row per saved group, showing the value its wells agree on.

    **A group never lends a value to a well, and a well never takes one.** A
    group whose wells disagree reads ``—mixed—``; a group holding a well with no
    value reads ``no value`` and names how many are short. Neither is resolved
    to the group's first well or its commonest value, because a plotted member
    built that way would carry a dose the wells were never given, in a bar
    indistinguishable from a correct one.

    Each row carries its wells, so double-clicking one selects them: the list
    knows exactly which wells are inconsistent and could previously only print
    them into a sentence for the user to find by eye on the plate above it.
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
                     _group_summary(vset, variable, wells), wells))

    listing.clear()
    for name, summary, wells in rows:
        item = QListWidgetItem(f"{name} — {summary}")
        item.setData(Qt.UserRole, wells)
        item.setToolTip("Double-click to select this group's wells.")
        listing.addItem(item)
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
        return (f"{vseries.MIXED_LABEL} ({len(distinct)} values across "
                f"{len(wells)} wells)")
    if value_id is None:
        short = sv.unassigned_wells(vset, variable.id, wells)
        return (f"no value — {len(short)} of {len(wells)} wells unassigned "
                f"({', '.join(short[:4])}{'…' if len(short) > 4 else ''})")
    return f"{variable.display_label(value_id)} ({len(wells)} wells)"


def _set_enabled_with_reason(ctx, on: bool, reason: str) -> None:
    """Enable or disable the fill row and the plate, stating why when off.

    Disabled with a reason rather than hidden: a control that cannot work *yet*
    is different from one that has nothing to say, and the difference is whether
    the user can do something about it.
    """
    for name in ("_var_fill_row", "_var_plate"):
        widget = _widget(ctx, name)
        if widget is None:
            continue
        widget.setEnabled(bool(on))
        widget.setToolTip("" if on else reason)


def _clear_plate(ctx, wells) -> None:
    """Hide every surface that described a variable, when there is none.

    Hidden rather than greyed, which is the opposite of the call made for a
    control whose rule the user can act on — and right for the same reason.
    With no variable defined there is no plate to show: a full ninety-six well
    grid greyed out around a two-paragraph explainer reads as software that has
    broken, not as software waiting to be told what the plate is about. The one
    thing a user can do here is press ``+ Variable``, and that is what is left
    on screen.
    """
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
    for name in ("_var_selection", "_var_note", "_var_overlay_legend",
                 "_var_fill_scope", "_var_showing"):
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

    _set_plate_column_shown(ctx, False)


def _show_plate_surfaces(ctx) -> None:
    _set_plate_column_shown(ctx, True)


def _set_plate_column_shown(ctx, on: bool) -> None:
    """Show or hide the plate and everything that acts on it, as one widget.

    One widget rather than a list of names, because a list is a thing to keep
    up to date and this one was already out of date the day it was written: it
    named the plate, the fill row and the chips, and left the selection links,
    the Preview / Apply fill buttons, the overlay combo and the standing policy
    note live on an empty tab with nothing to act on.
    """
    column = _widget(ctx, "_var_plate_column")
    if column is not None:
        column.setVisible(bool(on))
    caption = _widget(ctx, "_var_showing_caption")
    if caption is not None:
        # The header's "SHOWING" label with nothing after it.
        caption.setVisible(bool(on))
