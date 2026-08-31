"""The definition column — which variables exist, and what one of them *is*.

A fixed rail down the left of the tab, holding the variable list, the name /
type / unit form, and the ordered value list. Typed once per variable and then
rarely touched, which is why it is a column beside the plate rather than a band
above it: it was taking the top third of the page for four fields while the
surface the user actually works on was squeezed underneath.

Two things here are load-bearing rather than cosmetic.

**Every row carries its id in ``Qt.UserRole``.** The variable rows and the value
rows both do, and an in-place edit is read from there rather than from the row's
new text — reading it back by text turns a rename into a delete-and-add and
silently orphans every well that held the value.

**The value rows carry their colour.** The list is the authoritative statement
of what values exist and what order they are in, and it used to be the one place
on the tab with no colour in it: the plate and the chips were inked and the
definition was plain text, so the link between "the third row of my list" and
"the yellow wells" had to be made by counting. It also makes the reorder buttons
honest — moving a value changes its ink, on the plate and in every plot, and
with a swatch on the row that is something the user watches happen.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

import theme
from well_viewer.domain.sample_variables import NUMBER, TEXT

#: Side of the square swatch on a value row, in pixels.
_SWATCH = 11

_UNIT_W = 96

#: Tallest the variable rail renders — about six rows.
_RAIL_H = 150

#: Tallest the value list renders before it scrolls — about twelve values,
#: which is a full row of a plate and more than any real series this annotates.
_VALUES_H = 290


def build_editor_column(ctx, parent: QWidget, reg) -> QWidget:
    from .tab_view import EDITOR_W, EMPTY_HINT, section_label

    host = QWidget(parent)
    host.setFixedWidth(EDITOR_W)
    column = QVBoxLayout(host)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(theme.Spacing.sm)

    column.addWidget(section_label(host, "Variables"))
    listing = reg.register("_var_list", QListWidget(host))
    listing.setToolTip("Each row shows how much of the plate that variable "
                       "annotates.")
    # A plate carries two or three variables, not twenty, so the rail is sized
    # to the handful it will hold rather than being handed the column's stretch
    # — which left a mostly-empty box with + Variable stranded halfway down.
    listing.setMaximumHeight(_RAIL_H)
    column.addWidget(listing)

    add = reg.register("_var_add", QPushButton("+ Variable", host))
    add.setObjectName("HomeAction")
    column.addWidget(add)

    hint = reg.register("_var_hint", QLabel(EMPTY_HINT, host))
    hint.setObjectName("Muted")
    hint.setWordWrap(True)
    column.addWidget(hint)

    body = reg.register("_var_body", QWidget(host))
    body_column = QVBoxLayout(body)
    body_column.setContentsMargins(0, 0, 0, 0)
    body_column.setSpacing(theme.Spacing.sm)
    _build_definition_form(ctx, body, body_column, reg)
    _build_value_list(ctx, body, body_column, reg)

    # The delete lives at the foot of the definition it deletes, and names what
    # it takes. Two buttons reading "Remove" a few hundred pixels apart — one
    # dropping a value, one destroying a variable and every well assignment
    # under it — was one word for two scopes of damage.
    remove = reg.register("_var_remove", QPushButton("Delete this variable",
                                                     body))
    remove.setObjectName("HomeAction")
    body_column.addWidget(remove)

    column.addWidget(body)
    # The block sits at the top of the rail. Handing the definition the column's
    # stretch pushed the value buttons and the delete to the foot of an
    # eight-hundred-pixel page, a long way from the list they act on.
    column.addStretch(1)
    return host


def _build_definition_form(ctx, body: QWidget, column, reg) -> None:
    """Name, type and unit, as a form rather than a run-on row.

    A ``QFormLayout`` because the three fields are labelled inputs of very
    different widths, and the row they used to share stretched the name field
    across seven hundred pixels to keep the unit box on the same line.
    """
    form = QFormLayout()
    form.setContentsMargins(0, 0, 0, 0)
    form.setSpacing(theme.Spacing.xs)
    form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

    form.addRow("Name", reg.register("_var_name", QLineEdit(body)))

    kind = reg.register("_var_kind", QComboBox(body))
    # The user-facing words, with the stored token on the item. "Number" is what
    # makes a variable usable as an axis, and is the only kind with a unit.
    for token, label in ((TEXT, "Text"), (NUMBER, "Number")):
        kind.addItem(label, token)
    kind.setToolTip("Number is what lets this variable be a plot's x axis, and "
                    "is the only type that takes a unit.")
    form.addRow("Type", kind)

    unit = reg.register("_var_unit", QLineEdit(body))
    unit.setFixedWidth(_UNIT_W)
    unit.setPlaceholderText("µM")
    # Disabled rather than hidden on a text variable: a field that vanishes
    # reads as a bug, one that greys out reads as a rule.
    form.addRow("Unit", unit)
    column.addLayout(form)


def _build_value_list(ctx, body: QWidget, column, reg) -> None:
    from .tab_view import section_label

    column.addWidget(section_label(body, "Values, in order"))

    values = reg.register("_var_values", QListWidget(body))
    values.setEditTriggers(QListWidget.DoubleClicked | QListWidget.EditKeyPressed)
    values.setToolTip("Double-click to correct a label — that is a rename, so "
                      "every well already holding it follows.\nThe order sets "
                      "a fill's cycle, a legend's rows and a numeric axis's "
                      "direction.")
    values.setMaximumHeight(_VALUES_H)
    column.addWidget(values)

    buttons = QHBoxLayout()
    buttons.setSpacing(theme.Spacing.xs)
    for name, text, tip in (
        ("_var_value_up", "▲", "Move this value earlier in the order."),
        ("_var_value_down", "▼", "Move this value later in the order."),
        ("_var_value_remove", "Remove value",
         "Drop this value. Wells holding it become unassigned."),
    ):
        button = reg.register(name, QPushButton(text, body))
        button.setObjectName("HomeAction")
        button.setToolTip(tip)
        buttons.addWidget(button)
    buttons.addStretch(1)
    column.addLayout(buttons)

    add_row = QHBoxLayout()
    add_row.setSpacing(theme.Spacing.sm)
    entry = reg.register("_var_value_entry", QLineEdit(body))
    # The comma rule, stated on the control that applies it. It was only ever in
    # the README and in the handler, and the placeholder's example read equally
    # well as "here is what one value looks like".
    entry.setPlaceholderText("0, 0.1, 1 — commas add several")
    add_row.addWidget(entry, 1)
    add = reg.register("_var_value_add", QPushButton("Add", body))
    add.setObjectName("HomeAction")
    add_row.addWidget(add)
    column.addLayout(add_row)

    note = reg.register("_var_note", QLabel("", body))
    note.setObjectName("Muted")
    note.setWordWrap(True)
    column.addWidget(note)


def swatch_icon(colour: str) -> QIcon:
    """A small filled square of *colour*, for a list row.

    An icon rather than a styled delegate: the rows stay ordinary editable
    ``QListWidgetItem``s, which is what keeps the double-click rename — and its
    read-the-id-not-the-text rule — exactly as it was.
    """
    pixmap = QPixmap(QSize(_SWATCH, _SWATCH))
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setBrush(QColor(colour))
    painter.setPen(QColor(theme.Colors.border))
    painter.drawRoundedRect(0, 0, _SWATCH - 1, _SWATCH - 1, 2, 2)
    painter.end()
    return QIcon(pixmap)


def value_item(label: str, value_id: str, colour: str = "") -> QListWidgetItem:
    """One row of the value list, carrying its id and wearing its ink.

    The id rides on the item rather than being looked up by label, because the
    label is exactly what a rename changes — and reading the row back by its
    *new* label after an edit is how a rename becomes a delete-and-add, taking
    every well that pointed at it.
    """
    item = QListWidgetItem(label)
    item.setData(Qt.UserRole, value_id)
    item.setFlags(item.flags() | Qt.ItemIsEditable)
    if colour:
        item.setIcon(swatch_icon(colour))
    return item


def variable_item(name: str, variable_id: str, annotated: int,
                  total: int) -> QListWidgetItem:
    """One row of the variable rail: its name, and how much of the plate it covers.

    Coverage rather than a value count. The count was a number the user had just
    typed in and could read off the list beside it, while "is this plate
    finished?" — the question the whole tab is asked — could only be answered
    one variable at a time by selecting each and reading a sentence under the
    plate. A pair per row puts every answer on screen at once.
    """
    if not total:
        detail = "no wells"
    elif annotated == total:
        detail = f"{annotated}/{total} ✓"
    else:
        detail = f"{annotated}/{total}"
    item = QListWidgetItem(f"{name}    {detail}")
    item.setData(Qt.UserRole, variable_id)
    if total and annotated < total:
        item.setToolTip(f"{total - annotated} well(s) have no value for "
                        f"“{name}”.")
    return item
