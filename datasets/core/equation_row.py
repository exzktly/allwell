"""The ``dataset 1 + dataset 2 = destination`` row, shared by the two tools
that take two datasets and write a third.

Merge Plates and Concatenate ask the user the same question in the same
picture: two dataset folders, an operator between them, an equals sign, and a
destination. Both pages had built it themselves, in twenty statements that
differed only in two field labels and the name of the slot they connected the
``changed`` signal to — including a byte-identical nested ``_operator`` helper
and a second copy of the operator column's width.

That is the duplication ``core/dataset_fields.py`` and ``core/log_panel.py``
were each written to end, arriving a third time. The wording, the geometry and
the cross-plate hint underneath now have one definition, so the two pages
cannot drift apart one small inconsistency at a time.

What stays at the call site is what genuinely differs: the two dataset labels
(Concatenate names its inputs "base" and "appended" because the operation is
ordered, Merge Plates does not because its is not), and each page's own
destination validator, which is bound to that tool's own in-progress marker and
must not be shared.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ui import theme
from widgets.path_field import PathField, folder_picker

#: Width of the ``+`` / ``=`` column between the fields. Narrow enough that the
#: three fields keep the width, wide enough that the glyph is not clipped.
#: Named rather than written inline so the number is stated once
#: (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_EQUATION_OP_W = 20


@dataclass(frozen=True)
class EquationRow:
    """The four widgets the row builds, handed back for the page to hold.

    The page keeps its own attribute names for these — the fields are what its
    validation, its run spec and its reset all read — so this returns them
    rather than assigning them, and a reader grepping for ``_ds1_field`` still
    finds it in the page that uses it.
    """

    ds1: PathField
    ds2: PathField
    dest: PathField
    #: The red line under the row, hidden until the page has something to say
    #: about the two datasets not fitting together.
    cross_hint: QLabel


def build_equation_row(
    parent: QWidget,
    outer: QVBoxLayout,
    *,
    ds1_label: str,
    ds2_label: str,
    is_dataset: Callable[[Path], str],
    is_destination: Callable[[Path], str],
    on_changed: Callable[[], None],
) -> EquationRow:
    """Build the equation row into *outer* and return its four widgets.

    *is_dataset* and *is_destination* are the page's own validators, called on
    every keystroke; *on_changed* is the slot all three fields report to.
    """
    row = QWidget(parent)
    hl = QHBoxLayout(row)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(theme.Spacing.sm)

    ds1 = PathField(ds1_label, pick=folder_picker("Select dataset 1 folder"),
                    validator=is_dataset)
    ds2 = PathField(ds2_label, pick=folder_picker("Select dataset 2 folder"),
                    validator=is_dataset)
    dest = PathField("Destination",
                     pick=folder_picker("Select destination folder"),
                     validator=is_destination)

    def _operator(text: str) -> QLabel:
        lbl = QLabel(text, row)
        lbl.setObjectName("Heading")
        lbl.setContentsMargins(0, theme.Spacing.lg, 0, 0)
        lbl.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        lbl.setFixedWidth(_EQUATION_OP_W)
        return lbl

    hl.addWidget(ds1, 1)
    hl.addWidget(_operator("+"))
    hl.addWidget(ds2, 1)
    hl.addWidget(_operator("="))
    hl.addWidget(dest, 1)
    outer.addWidget(row)

    cross_hint = QLabel("", parent)
    cross_hint.setObjectName("Danger")
    cross_hint.setWordWrap(True)
    cross_hint.setVisible(False)
    outer.addWidget(cross_hint)

    for field in (ds1, ds2, dest):
        field.changed.connect(on_changed)

    return EquationRow(ds1=ds1, ds2=ds2, dest=dest, cross_hint=cross_hint)
