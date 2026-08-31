"""The plate, and everything that acts on it.

The right column of the tab, top to bottom: the plate itself, a line saying
what is selected and what those wells currently hold, the four selection links,
the value chips that assign, the fill sentence, the second-variable overlay,
and the group roll-up. That order is the gesture's order — pick wells, see what
you picked, say what they are — and it used to be interrupted: the chips sat
above the plate with the overlay combo wedged between them, so the two halves
of one action were separated by a control belonging to neither.

**The plate is the tab's own, at ``role="canvas"``.** Its own rather than the
rail's, and that is not a preference: ``groups/panel_view`` guards its commit on
the current sub-tab precisely because a second meaning on the rail plate
silently rewrites memberships. A plate here means "which wells hold which
value", and it means only that. ``"canvas"`` rather than ``"editor"`` because
this plate is what the page is *for* rather than one control on it, and it
carries three independent marks at once — see ``plate_geometry``.

**Three layers, none of them colour alone.** A fill says which value; a **ring**
says a well the dataset holds has no value for this variable (never the empty
socket, which already means "not in the dataset"); a **rim** says the well is in
your selection. All three can be true of one well at once, which is why they are
three radii and three inks rather than three uses of one mark.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QListWidget, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)

import theme
from well_viewer.domain.plate_layout import FILL_ALONG_COLS, FILL_ALONG_ROWS


#: Widest the plate column may render. The plate itself is capped narrower
#: still, and every control under it is a caption or a sentence about the wells
#: above — so the column is bounded and the whole of it is one block, rather
#: than a centred plate with its own controls starting three hundred pixels to
#: the left of its edge. On a page wider than this the block sits at the left,
#: under the header, where the eye already is.
_COLUMN_W = 490


def build_plate_column(ctx, parent: QWidget, reg) -> QWidget:
    from widgets.plate_geometry import PLATE_ALIGNMENT, apply_plate_geometry
    from widgets.well_plate_selector import WellPlateSelector

    from .tab_view import POLICY_NOTE

    outer = QWidget(parent)
    outer_row = QHBoxLayout(outer)
    outer_row.setContentsMargins(0, 0, 0, 0)
    outer_row.setSpacing(0)

    # Registered as one widget so the whole column can be hidden at once. Hiding
    # the plate, the fill row and the chips by name left the selection links,
    # the Preview / Apply fill buttons, the overlay combo and the policy note on
    # screen with nothing to act on — the empty state this was meant to fix,
    # minus the three widgets that happened to come to mind.
    host = reg.register("_var_plate_column", QWidget(outer))
    host.setMaximumWidth(_COLUMN_W)
    outer_row.addWidget(host)
    outer_row.addStretch(1)
    column = QVBoxLayout(host)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(theme.Spacing.sm)

    plate = reg.register("_var_plate", WellPlateSelector(host))
    apply_plate_geometry(plate, role="canvas")
    plate.setSelectionModel("set")
    # Rubber-band rectangle, not freeform paint — the same configuration the
    # Groups plate uses. A block design is what this plate exists to describe,
    # and painting a 4×3 block freehand takes a serpentine drag that de-selects
    # any cell the cursor crosses twice.
    plate.setDragMode("rect")
    # The tab has spent the fill on data, so the plate has nothing left to say
    # selection with unless it is given a mark of its own. Without this a
    # selected assigned well and an unselected one are the same pixels, and the
    # tab's central gesture has no feedback at all.
    plate.setSelectionRim(True)
    column.addWidget(plate, 0, PLATE_ALIGNMENT)

    _build_selection_row(ctx, host, column, reg)
    _build_chip_row(ctx, host, column, reg)
    _build_fill_row(ctx, host, column, reg)
    _build_overlay_row(ctx, host, column, reg)
    _build_groups(ctx, host, column, reg)

    policy = QLabel(POLICY_NOTE, host)
    policy.setObjectName("Muted")
    policy.setWordWrap(True)
    column.addWidget(policy)
    column.addStretch(1)
    return outer


def _build_selection_row(ctx, host: QWidget, column, reg) -> None:
    """What is selected, and the four ways to change it.

    The readout is the fix for a tab that connected fourteen signals and none of
    them to the plate: dragging a box produced no confirmation that anything had
    been picked, and the "select wells on the plate first" instruction — written
    into the status label by the chip handler — never cleared when the user
    complied, because complying triggered no redraw. State that is always on
    screen needs no instruction shouted after the fact.

    The links are the other half. A rubber-band selection accumulates across
    gestures, and ``role="canvas"`` hides the plate's own All / Invert / Clear
    row on the promise that the host supplies its own — which this one did not,
    so a selection could only be undone by finding the eleven-pixel corner
    circle. ``All`` and ``None`` are that row's job done properly (``All`` means
    the wells the dataset holds, not ninety-six); ``Unassigned`` and
    ``Same value`` are the two questions the plate could previously only paint
    the answer to, leaving a user with four wrong wells to find them by eye.
    """
    readout = reg.register("_var_selection", QLabel("", host))
    readout.setObjectName("VariablesSelection")
    readout.setWordWrap(True)
    column.addWidget(readout)

    links = QHBoxLayout()
    links.setSpacing(theme.Spacing.sm)
    links.addWidget(QLabel("Select", host))
    for name, text, tip in (
        ("_var_sel_all", "all", "Select every well in this dataset."),
        ("_var_sel_none", "none", "Clear the selection."),
        ("_var_sel_missing", "unassigned",
         "Select the wells that have no value for this variable."),
        ("_var_sel_same", "same value",
         "Select every well holding the same value as the wells you have "
         "selected."),
    ):
        button = reg.register(name, QPushButton(text, host))
        button.setObjectName("VariablesLink")
        button.setToolTip(tip)
        links.addWidget(button)
    links.addStretch(1)
    column.addLayout(links)


def _build_chip_row(ctx, host: QWidget, column, reg) -> None:
    """``Set the selected wells to  [0] [0.1] [0.3] … [No value]``

    Under the plate and under a caption naming its object, so the verb and the
    thing it acts on are one sentence read downward. The chips used to sit above
    the plate with the overlay combo between them.

    A wrapping layout, because a twelve-value dose series ran off the page.
    """
    from widgets.flow_layout import FlowLayout

    caption = reg.register("_var_chips_caption",
                           QLabel("Set the selected wells to", host))
    caption.setObjectName("VariablesCaption")
    column.addWidget(caption)

    chips = reg.register("_var_chips", QWidget(host))
    FlowLayout(chips, h_spacing=theme.Spacing.xs, v_spacing=theme.Spacing.xs)
    column.addWidget(chips)


def _build_fill_row(ctx, host: QWidget, column, reg) -> None:
    """``Give each [column] the next Dose, [2] columns per value.``

    A sentence rather than a control strip. ``Fill along [Rows] repeat each
    [N] rows`` named its parts correctly and still needed a paragraph, because
    nothing in it said what a "repeat" repeats over or in which direction the
    cycle runs.

    Two things below it that were not there before. The scope line states what
    the fill will **overwrite** — it read ``over the 96 well(s) in this
    dataset``, an accurate statement about the dataset that says nothing about
    the ninety-six assignments about to be replaced. And ``Preview`` paints the
    prospective result on the plate without committing, which is nearly free:
    ``sample_variables.fill`` is pure and returns a new set, so previewing is
    painting from the returned set and storing nothing.
    """
    host_row = reg.register("_var_fill_row", QWidget(host))
    row = QHBoxLayout(host_row)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(theme.Spacing.sm)

    row.addWidget(QLabel("Give each", host_row))
    axis = reg.register("_var_fill_axis", QComboBox(host_row))
    for token, label in ((FILL_ALONG_ROWS, "row"), (FILL_ALONG_COLS, "column")):
        axis.addItem(label, token)
    row.addWidget(axis)

    lead = reg.register("_var_fill_lead", QLabel("the next value,", host_row))
    row.addWidget(lead)

    repeat = reg.register("_var_fill_repeat", QSpinBox(host_row))
    repeat.setRange(1, 96)
    repeat.setValue(1)
    row.addWidget(repeat)
    # The unit follows the axis combo so it cannot go stale against it. While
    # the cycling counted *wells* the two readings differed by the width of the
    # plate: a user wanting four rows had to type 48.
    unit = reg.register("_var_fill_unit", QLabel("", host_row))
    row.addWidget(unit)

    row.addStretch(1)
    column.addWidget(host_row)

    scope = reg.register("_var_fill_scope", QLabel("", host))
    scope.setObjectName("Muted")
    scope.setWordWrap(True)
    column.addWidget(scope)

    # The two buttons on their own line under the sentence they act on, rather
    # than trailing off the end of it. It keeps the widest row on this half of
    # the page narrow enough that the plate above and the controls below are one
    # aligned block instead of a centred card with its own controls starting a
    # hundred pixels to its left.
    buttons = QHBoxLayout()
    buttons.setSpacing(theme.Spacing.sm)
    preview = reg.register("_var_fill_preview", _action(host, "Preview"))
    preview.setToolTip("Paint what Apply fill would do, without saving it.")
    buttons.addWidget(preview)
    apply_button = reg.register("_var_fill_apply", _action(host, "Apply fill"))
    buttons.addWidget(apply_button)
    buttons.addStretch(1)
    column.addLayout(buttons)


def _build_overlay_row(ctx, host: QWidget, column, reg) -> None:
    """A second variable, as a glyph on top of the first one's colour.

    A combo rather than the planned pair of chips: with three or more variables
    a chip would have to mean "colour" or "glyph" depending on which one the
    rail had selected, which is two meanings on one control.

    The key gets its own line rather than sharing the combo's. A six-value
    series' key is six pairs long and was being squeezed into whatever width
    was left beside the dropdown.
    """
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(theme.Spacing.sm)
    row.addWidget(QLabel("Also show", host))
    row.addWidget(reg.register("_var_overlay", QComboBox(host)))
    row.addStretch(1)
    column.addLayout(row)

    legend = reg.register("_var_overlay_legend", QLabel("", host))
    legend.setObjectName("Muted")
    legend.setWordWrap(True)
    column.addWidget(legend)


def _build_groups(ctx, host: QWidget, column, reg) -> None:
    """The saved groups, and what each one rolls up to.

    Selectable, and a double-click selects the wells the row is complaining
    about. The list knows exactly which wells are inconsistent and could only
    print them into a sentence, leaving the one surface that has the answer
    unable to tell the plate 40 px above it.
    """
    from .tab_view import section_label

    column.addWidget(reg.register("_var_groups_title",
                                  section_label(host, "By group")))
    groups = reg.register("_var_groups", QListWidget(host))
    groups.setSelectionMode(QListWidget.SingleSelection)
    groups.setToolTip("Double-click a row to select that group's wells on the "
                      "plate.")
    column.addWidget(groups)


def _action(parent: QWidget, text: str) -> QPushButton:
    button = QPushButton(text, parent)
    button.setObjectName("HomeAction")
    return button


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
        # A block of the value's ink rather than a three-pixel stripe. The chip
        # row is where a user reads "this colour means this dose", and a hairline
        # is not enough of the colour to match against a plate. Only the swatch
        # is set here because only the swatch varies per value; everything else
        # about a chip is one rule in ``theme.qss``, and a widget stylesheet
        # merges with the app's rather than replacing it.
        button.setStyleSheet(
            f"QPushButton#VariableValueChip {{ border-left: 14px solid {swatch}; }}")
    return button
