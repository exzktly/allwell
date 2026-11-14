"""The one owner of whether the Nanowells page's figure stack is shown.

Two things ask the figure stack to step aside, for unrelated reasons: the site detail, which on a phone takes the
page's column instead of the figure (``_phone.detail_placement``), and the Subset editor, which takes a page too
short for both (``subset_view.share_page``). Each used to set the stack's visibility outright, so whichever wrote
last won — and the site detail writes on every redraw, which brought the figure back under an open editor the moment
a condition was applied. Each writer now records its own reason here, and the stack is shown only while no reason
holds.

The reasons are Qt dynamic properties on the stack, not Python attributes, so they live exactly as long as the
widget does whichever wrapper reads them.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

#: Why the figure stack may be aside: the site detail alone on a phone, the Subset editor on a short page.
REASONS = ("detail", "subset")


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
