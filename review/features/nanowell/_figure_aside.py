"""The one owner of whether the Nanowells page's figure stack is shown.

The site detail asks the figure stack to step aside: on a phone it takes the page's column instead of the figure
(``_phone.detail_placement``), and it writes on every redraw. A writer records its reason here rather than setting
the stack's visibility outright, and the stack is shown only while no reason holds — so a second reason, when one
is added, cannot be undone by the first one's next redraw.

The reasons are Qt dynamic properties on the stack, not Python attributes, so they live exactly as long as the
widget does whichever wrapper reads them.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

#: Why the figure stack may be aside: the site detail alone on a phone.
REASONS = ("detail",)


def _key(reason: str) -> str:
    if reason not in REASONS:
        raise ValueError(f"unknown reason for the figure to step aside: {reason!r}")
    return f"_aw_figures_aside_{reason}"


def set_aside(figures: QWidget, reason: str, aside: bool) -> bool:
    """Record whether *reason* holds the figure stack aside, show or hide the stack accordingly, and return whether
    the stack was hidden before and is shown now (the caller then draws it: a hidden figure is not drawn)."""
    was_hidden = figures.isHidden()
    figures.setProperty(_key(reason), bool(aside))
    shown = not any(bool(figures.property(_key(r))) for r in REASONS)
    if shown == was_hidden:
        figures.setVisible(shown)
    return was_hidden and shown
