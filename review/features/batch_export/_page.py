"""Batch Export's page skeleton: a page title, the Groups card, and the card holding the Output and Plot forms.

The design language (``_Docs/Markdowns/DESIGN_LANGUAGE_TRACKER.md`` §6, row D26) draws every Batch Export mode the
same way: a title naming the mode ("Batch Export · Bar") with a quiet *Sync from app* and the primary *Export* at
its right; a *Groups* card whose heading carries Clear all, Quick setup and Add group; and one card with two form
grids, *Output* (where the files go) and *Plot* (what each figure draws). Each panel's row builders lay their
controls out as they always have, in a row; the helpers here hand that row to a form grid under its label, so a
row's own stretch and spacing survive and only the label moves into the grid's column.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QLayout, QWidget

from ui import theme
from widgets.form_grid import FormGrid


def field(*parts: QWidget | QLayout) -> QWidget:
    """One widget holding *parts* side by side: a lone widget is returned as it is, a lone row layout becomes the
    layout of a bare host, and several parts go into a row with the first taking the slack."""
    if len(parts) == 1 and isinstance(parts[0], QWidget):
        return parts[0]
    host = QWidget()
    if len(parts) == 1:
        layout = parts[0]
        layout.setContentsMargins(0, 0, 0, 0)
        host.setLayout(layout)
        return host
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(theme.Spacing.sm)
    for i, part in enumerate(parts):
        if isinstance(part, QLayout):
            part.setContentsMargins(0, 0, 0, 0)
            row.addLayout(part, 1 if i == 0 else 0)
        else:
            row.addWidget(part, 1 if i == 0 else 0)
    return host


def hug(*widgets: QWidget) -> QHBoxLayout:
    """*widgets* at their own widths, from the left, the slack after them."""
    row = QHBoxLayout()
    row.setSpacing(theme.Spacing.sm)
    for w in widgets:
        row.addWidget(w)
    row.addStretch(1)
    return row


def form_row(form: FormGrid, label: str, *parts: QWidget | QLayout, help: str = "") -> QLabel:  # noqa: A002
    """A labelled row of *form*; returns the label."""
    return form.add_row(label, field(*parts), help=help)


def form_wide(form: FormGrid, *parts: QWidget | QLayout) -> QWidget:
    """A row of *form* that labels itself (a checkbox), across both columns; returns its widget."""
    widget = field(*parts)
    form.add_wide(widget)
    return widget
