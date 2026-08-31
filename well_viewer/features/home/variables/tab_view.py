"""The Variables sub-tab — declare a variable and list its values.

Two columns. On the left the variables the plate has, with **+ Variable**
under them; on the right the selected variable's name, kind, unit and its
**ordered** list of values.

The order is not decoration: it decides the cycle a fill walks, the rows of a
legend, and the direction of a numeric axis. So the values are a list with move
buttons rather than a set, and the two arrows are next to the list rather than
in a menu.

**Every rule this tab enforces lives in ``domain/sample_variables``** — unique
names, no duplicate labels, a rename that keeps the value's id so the wells
follow, a kind switch that re-reads the numbers, a move that clamps. This module
calls those and redraws. That split is deliberate and it is the lesson of §7.6:
the semantics that matter are the ones a fast-lane test can drive, and a rule
implemented in a signal handler is a rule nothing tests.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

import theme
from well_viewer.domain.plate_layout import FILL_ALONG_COLS, FILL_ALONG_ROWS
from well_viewer.domain.sample_variables import NUMBER, TEXT

#: The definition columns' widths. Named rather than written inline: the left
#: list is a fixed rail so the value list beside it does not reflow every time a
#: variable is renamed.
_LIST_W = 200
_UNIT_W = 90

#: What the tab says when a plate has no variables yet. It names the payoff
#: rather than the mechanism, because the mechanism is visible and the payoff is
#: the reason to bother: a variable is not a group, and defining one saves
#: painting a group name per condition.
EMPTY_HINT = (
    "A variable is anything you changed across the plate — cell line, dose, "
    "timepoint of treatment. Name it, list its values, then fill them onto the "
    "plate.\n\nA variable is not a group: two cell lines at six doses is twelve "
    "conditions, and you do not have to paint twelve group names to plot them "
    "separately."
)


def build_variables_subtab(ctx, frame: QWidget) -> None:
    """Build the tab body into *frame*. Registered lazily, like every sub-tab."""
    reg = ctx.widgets
    outer = frame.layout() or QHBoxLayout(frame)
    # No margins of its own: the frame is already inset by ``subtab_inset`` on
    # the descriptor, the way Groups and Well Labels are, and a second set here
    # would double it on one tab only.
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(theme.Spacing.lg)

    outer.addWidget(_build_variable_rail(ctx, frame, reg))

    right = QWidget(frame)
    column = QVBoxLayout(right)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(theme.Spacing.md)
    column.addWidget(_build_definition(ctx, right, reg))
    column.addWidget(_build_fill_row(ctx, right, reg))
    column.addWidget(_build_plate(ctx, right, reg), 1)
    outer.addWidget(right, 1)

    from .controller import refresh_variables, wire_variables
    wire_variables(ctx)
    refresh_variables(ctx)


def _build_variable_rail(ctx, parent: QWidget, reg) -> QWidget:
    """The list of variables, and the button that adds one."""
    host = QWidget(parent)
    column = QVBoxLayout(host)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(theme.Spacing.sm)

    column.addWidget(_section_label(host, "Variables"))
    # No object name: the widget is reached through the registry, and the
    # app-wide QListWidget rule already paints it. A hook that resolves to
    # nothing is a hook that does nothing.
    listing = reg.register("_var_list", QListWidget(host))
    listing.setFixedWidth(_LIST_W)
    column.addWidget(listing, 1)

    add = reg.register("_var_add", QPushButton("+ Variable", host))
    add.setObjectName("HomeAction")
    column.addWidget(add)

    remove = reg.register("_var_remove", QPushButton("Remove", host))
    remove.setObjectName("HomeAction")
    column.addWidget(remove)
    return host


def _build_definition(ctx, parent: QWidget, reg) -> QWidget:
    """Name, kind, unit and the ordered value list for the selected variable."""
    host = QWidget(parent)
    column = QVBoxLayout(host)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(theme.Spacing.sm)

    hint = reg.register("_var_hint", QLabel(EMPTY_HINT, host))
    hint.setObjectName("Muted")
    hint.setWordWrap(True)
    column.addWidget(hint)

    body = reg.register("_var_body", QWidget(host))
    body_column = QVBoxLayout(body)
    body_column.setContentsMargins(0, 0, 0, 0)
    body_column.setSpacing(theme.Spacing.sm)

    row = QHBoxLayout()
    row.setSpacing(theme.Spacing.sm)
    row.addWidget(QLabel("Name", body))
    row.addWidget(reg.register("_var_name", QLineEdit(body)), 1)
    row.addWidget(QLabel("Type", body))
    kind = reg.register("_var_kind", QComboBox(body))
    # The user-facing words, with the stored token on the item. "Number" is what
    # makes a variable usable as an axis, and is the only kind with a unit —
    # which is why the unit field is disabled rather than hidden for text: a
    # field that vanishes reads as a bug, one that greys out reads as a rule.
    for token, label in ((TEXT, "Text"), (NUMBER, "Number")):
        kind.addItem(label, token)
    row.addWidget(kind)
    row.addWidget(QLabel("Unit", body))
    unit = reg.register("_var_unit", QLineEdit(body))
    unit.setFixedWidth(_UNIT_W)
    unit.setPlaceholderText("µM")
    row.addWidget(unit)
    body_column.addLayout(row)

    body_column.addWidget(_section_label(body, "Values, in order"))
    values_row = QHBoxLayout()
    values_row.setSpacing(theme.Spacing.sm)
    values = reg.register("_var_values", QListWidget(body))
    values.setEditTriggers(QListWidget.DoubleClicked | QListWidget.EditKeyPressed)
    values_row.addWidget(values, 1)

    buttons = QVBoxLayout()
    buttons.setSpacing(theme.Spacing.xs)
    for name, text in (("_var_value_up", "▲"), ("_var_value_down", "▼"),
                       ("_var_value_remove", "Remove")):
        button = reg.register(name, QPushButton(text, body))
        button.setObjectName("HomeAction")
        buttons.addWidget(button)
    buttons.addStretch(1)
    values_row.addLayout(buttons)
    body_column.addLayout(values_row)

    add_row = QHBoxLayout()
    add_row.setSpacing(theme.Spacing.sm)
    entry = reg.register("_var_value_entry", QLineEdit(body))
    entry.setPlaceholderText("Add a value and press Enter — 0, 0.1, 0.3, 1, 3, 10")
    add_row.addWidget(entry, 1)
    add = reg.register("_var_value_add", QPushButton("Add", body))
    add.setObjectName("HomeAction")
    add_row.addWidget(add)
    body_column.addLayout(add_row)

    note = reg.register("_var_note", QLabel("", body))
    note.setObjectName("Muted")
    note.setWordWrap(True)
    body_column.addWidget(note)

    column.addWidget(body, 1)
    return host


def _section_label(parent: QWidget, text: str) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("HomeCardTitle")
    label.setAlignment(Qt.AlignLeft)
    return label


def value_item(label: str, value_id: str) -> QListWidgetItem:
    """One row of the value list, carrying its id.

    The id rides on the item rather than being looked up by label, because the
    label is exactly what a rename changes — and reading the row back by its
    *new* label after an edit is how a rename becomes a delete-and-add, taking
    every well that pointed at it.
    """
    item = QListWidgetItem(label)
    item.setData(Qt.UserRole, value_id)
    item.setFlags(item.flags() | Qt.ItemIsEditable)
    return item


def _build_fill_row(ctx, parent: QWidget, reg) -> QWidget:
    """``Fill [Dose] along [Columns] · repeat each [2] · [Apply Fill]``.

    Modelled on Quick Replicates' own row, which is the control this page
    already teaches. Any block, row or column design is one click: six doses
    along the columns repeating each twice fills a 12-column plate, and two cell
    lines along the rows repeating each four fills the 8 rows.

    The variable being filled is the one selected in the rail rather than a
    fourth combo — the definition of it is on screen directly above, and a fill
    that could target a variable the user is not looking at is a fill that lands
    somewhere they will not check.
    """
    host = reg.register("_var_fill_row", QWidget(parent))
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(theme.Spacing.sm)

    row.addWidget(QLabel("Fill along", host))
    axis = reg.register("_var_fill_axis", QComboBox(host))
    for token, label in ((FILL_ALONG_ROWS, "Rows"), (FILL_ALONG_COLS, "Columns")):
        axis.addItem(label, token)
    row.addWidget(axis)

    row.addWidget(QLabel("repeat each", host))
    repeat = reg.register("_var_fill_repeat", QSpinBox(host))
    repeat.setRange(1, 96)
    repeat.setValue(1)
    # The unit, on the control. "repeat each 4" beside "along Rows" reads as
    # four rows to every reader — which is what it means — but nothing said so,
    # and while the cycling counted *wells* the two readings differed by the
    # width of the plate: a user wanting four rows had to type 48. The number
    # now names what it counts, and it follows the axis combo so it cannot go
    # stale against it.
    def _say_unit() -> None:
        repeat.setSuffix(" columns" if axis.currentData() == FILL_ALONG_COLS
                         else " rows")

    _say_unit()
    axis.currentIndexChanged.connect(lambda _i: _say_unit())
    row.addWidget(repeat)

    apply_button = reg.register("_var_fill_apply", QPushButton("Apply fill", host))
    apply_button.setObjectName("HomeAction")
    row.addWidget(apply_button)

    scope = reg.register("_var_fill_scope", QLabel("", host))
    scope.setObjectName("Muted")
    row.addWidget(scope, 1)
    return host


def _build_plate(ctx, parent: QWidget, reg) -> QWidget:
    """The tab's **own** plate, and the line that reads it back.

    Its own rather than the rail's, and that is not a preference. Groups guards
    its commit on the current sub-tab being "Groups" precisely because a second
    meaning on the rail plate silently rewrites memberships — two plates meaning
    different things is a hazard this codebase has already recorded. A plate
    here means "which wells hold which value", and it means only that.
    """
    from widgets.plate_geometry import PLATE_ALIGNMENT, apply_plate_geometry
    from widgets.well_plate_selector import WellPlateSelector

    host = QWidget(parent)
    column = QVBoxLayout(host)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(theme.Spacing.sm)

    chips = reg.register("_var_chips", QWidget(host))
    chip_row = QHBoxLayout(chips)
    chip_row.setContentsMargins(0, 0, 0, 0)
    chip_row.setSpacing(theme.Spacing.xs)
    column.addWidget(chips)

    # A second variable, drawn as a glyph on top of the first one's colour, so a
    # two-factor design can be read in one look instead of by switching back and
    # forth. A combo rather than the planned pair of chips: with three or more
    # variables a chip would have to mean "colour" or "glyph" depending on which
    # one the rail has selected, which is two meanings on one control.
    overlay_row = QHBoxLayout()
    overlay_row.setContentsMargins(0, 0, 0, 0)
    overlay_row.setSpacing(theme.Spacing.sm)
    overlay_row.addWidget(QLabel("Also show", host))
    overlay = reg.register("_var_overlay", QComboBox(host))
    overlay_row.addWidget(overlay)
    legend = reg.register("_var_overlay_legend", QLabel("", host))
    legend.setObjectName("Muted")
    overlay_row.addWidget(legend, 1)
    column.addLayout(overlay_row)

    plate = reg.register("_var_plate", WellPlateSelector(host))
    # "editor" rather than "rail": the rail role is exempt from the size caps
    # because Review's rail picker is that pane's primary interaction surface,
    # and this plate is one control among several on a page that also holds a
    # definition form.
    apply_plate_geometry(plate, role="editor")
    plate.setSelectionModel("set")
    column.addWidget(plate, 0, PLATE_ALIGNMENT)

    readout = reg.register("_var_readout", QLabel("", host))
    readout.setObjectName("Muted")
    readout.setWordWrap(True)
    column.addWidget(readout)

    column.addWidget(reg.register("_var_groups_title",
                                  _section_label(host, "By group")))
    groups = reg.register("_var_groups", QListWidget(host))
    groups.setSelectionMode(QListWidget.NoSelection)
    column.addWidget(groups)
    column.addStretch(1)
    return host


def value_chip(label: str, value_id: str, parent: QWidget, *,
               swatch: str = "") -> QPushButton:
    """One assignable value, as a button carrying its id.

    Select wells, then click a chip — two steps, and the same shape as Well
    Labels' "select cells, then Add Prefix" which this page already teaches.

    Deliberately not the plate's ``"paint"`` model: paint latches an add/remove
    polarity on press, so dragging back over an assigned well *clears* it, and a
    chip would still be needed to say which value is being painted. Two steps
    that always mean the same thing beat one that sometimes means the opposite.
    """
    button = QPushButton(label, parent)
    button.setObjectName("VariableValueChip")
    button.setCheckable(False)
    button.setCursor(Qt.PointingHandCursor)
    button.setProperty("aw_value_id", value_id)
    if swatch:
        # Only the swatch is set here, because only the swatch varies per value.
        # Everything else about a chip is one rule in ``theme.qss`` — a widget
        # stylesheet merges with the app's rather than replacing it, so setting
        # the whole look here would be a second definition that drifts.
        button.setStyleSheet(
            f"QPushButton#VariableValueChip {{ border-left: 3px solid {swatch}; }}")
    return button
