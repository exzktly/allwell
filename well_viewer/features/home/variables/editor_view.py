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
    from .tab_view import EDITOR_W, section_label

    host = QWidget(parent)
    host.setFixedWidth(EDITOR_W)
    column = QVBoxLayout(host)
    # A left inset, because the rail sits hard against the pane edge and the
    # sidebar's show/hide toggle floats over that edge — landing on top of the
    # right-aligned "Name" and "Type" labels of the form below. The column is
    # the right place for it rather than the form: every row in it starts at
    # the same x, and insetting one of them would leave the list and the
    # buttons under the toggle instead.
    #
    # Every Sample Map layer owes the same inset for the same reason, and each
    # pays it where its own leftmost content is — the Replicates page on its
    # outer layout, Well Labels inside its grid card. What holds them together
    # is not a shared constant but
    # ``test_home_page.test_every_sample_map_layer_clears_the_rail_toggle``,
    # which measures the three and fails on whichever one starts at the edge.
    column.setContentsMargins(theme.Spacing.md, 0, 0, 0)
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

    body = reg.register("_var_body", QWidget(host))
    body_column = QVBoxLayout(body)
    body_column.setContentsMargins(0, 0, 0, 0)
    body_column.setSpacing(theme.Spacing.sm)
    _build_definition_form(ctx, body, body_column, reg)
    _build_value_list(ctx, body, body_column, reg)

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

    # The entry and its Add sit directly under the heading, above the list they
    # append to — so the reading order is "Values, in order / type one / here
    # they are", and the button a user reaches for most is the one nearest the
    # label that names the section. Underneath the box it sat below the three
    # reorder buttons, four controls away from the heading.
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


#: Side of the × on a variable row. Small enough not to crowd the coverage
#: count, large enough to be a comfortable target at typical densities.
_ROW_X = 18


def variable_row(name: str, variable_id: str, annotated: int,
                 total: int, parent: QWidget) -> tuple[QListWidgetItem, QWidget]:
    """A rail row and the widget that paints it: the label, and its own ×.

    The delete used to be one button at the foot of the column, several hundred
    pixels below the list, reading "Delete “Dose”" and acting on whatever the
    rail happened to have selected. Two problems in one control: it was nowhere
    near the thing it destroys, and the thing it destroys was named only in its
    own label — so the row a user was looking at and the variable the button
    would delete were separate facts they had to keep in agreement themselves.
    An × on the row cannot disagree with the row it is on.

    The item keeps its text and its ``Qt.UserRole`` id even though the widget
    is what gets painted: the text is what a screen reader and every existing
    caller read, and the id is what an edit is resolved through — reading a row
    back by its label is how a rename becomes a delete-and-add.
    """
    item = variable_item(name, variable_id, annotated, total)

    row = QWidget(parent)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(theme.Spacing.xs, 0, theme.Spacing.xs, 0)
    layout.setSpacing(theme.Spacing.xs)
    label = QLabel(item.text(), row)
    layout.addWidget(label, 1)

    close = QPushButton("✕", row)
    close.setObjectName("VariableRowDelete")
    close.setFixedSize(QSize(_ROW_X, _ROW_X))
    close.setCursor(Qt.PointingHandCursor)
    close.setToolTip(f"Delete “{name}” and every well assignment under it.")
    layout.addWidget(close, 0)

    # The delegate paints every index even when it carries an index widget, and
    # the row has no opaque background — so the item's own text rendered
    # *underneath* the label, one pixel off, and the row read double-struck.
    # Blanked here rather than never set: ``variable_item`` is what stamps the
    # id and the tooltip, and the accessible text keeps the string a screen
    # reader needs.
    item.setData(Qt.AccessibleTextRole, item.text())
    item.setText("")

    # **Add the delegate's padding back.** Qt hands an index widget the item
    # rect *minus* ``QListWidget::item``'s padding, so a hint taken from the row
    # alone comes out short by exactly that much: the × is a fixed 18 px, the
    # row asked for 18, the list gave the row 10, and the button rendered as its
    # top half. Nothing else here needs a floor — the button's fixed size is
    # already what makes the row's own hint tall enough.
    hint = row.sizeHint()
    item.setSizeHint(QSize(hint.width(),
                           hint.height() + 2 * theme.Metrics.list_item_pad_v))
    return item, row


def row_delete_button(listing, row: int) -> QPushButton | None:
    """The × on rail row *row*, or ``None``.

    A named accessor rather than each caller reaching through
    ``itemWidget(...).findChild(...)``: the tests drive this button, and a
    lookup spelled five different ways is five places to fix when the row's
    shape changes.
    """
    item = listing.item(row)
    widget = listing.itemWidget(item) if item is not None else None
    if widget is None:
        return None
    return widget.findChild(QPushButton, "VariableRowDelete")


def row_label_text(listing, row: int) -> str:
    """What rail row *row* actually displays.

    The row's text lives on the widget, not on the item — the item's own text
    is blanked so the delegate does not paint it underneath. A caller reading
    ``item.text()`` gets "" and, if it asserts on that, is asserting something
    no user can see.
    """
    item = listing.item(row)
    widget = listing.itemWidget(item) if item is not None else None
    if widget is None:
        return item.text() if item is not None else ""
    label = widget.findChild(QLabel)
    return label.text() if label is not None else ""
