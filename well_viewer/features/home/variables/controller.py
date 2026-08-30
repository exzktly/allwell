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


def add_variable(ctx) -> None:
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
