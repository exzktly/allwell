"""The project's two divider builders — one horizontal, one vertical.

A divider is a `QFrame` whose `Separator` object name is what makes it paint:
`ui.theme.qss` gives that name a `background-color` of `border_subtle` and — the
load-bearing half — `border: none`. A frame without one of the sanctioned names
keeps Qt's default etched border instead, which is derived from the widget
palette rather than from the theme: a 1 px line of near-white on this
application's surfaces, inside a frame that reserves 3 px for it unless the
caller pins a height.

These live in `widgets/` rather than in a pane because a separator knows nothing
about All-Well — it takes a parent and a thickness and returns a frame. They sat
in the Review pane's `ui_helpers` while three of their call sites were in
`analyze/` and `allwell.py`, which is a shared widget wearing a pane's address.

`tests/test_divider_naming.py` is the guard that sends people here: it rejects a
hand-built `QFrame.HLine` anywhere in the tree and names these two functions as
the way to build one. That is why `build_vline_separator` stays even though
nothing calls it today — it is the sanctioned answer for a vertical divider, and
a guard that recommends a function which does not exist is worse than an unused
one.
"""
from __future__ import annotations

from PySide6.QtWidgets import QFrame, QWidget

from ui import theme


def build_hline_separator(parent: QWidget, *,
                          thickness: int = theme.Metrics.hairline) -> QFrame:
    """Return the project's standard horizontal separator frame.

    *thickness* exists for the one divider that is deliberately heavier than a
    hairline (the Image Table's shelf rule); everything else takes the default.
    """
    sep = QFrame(parent)
    sep.setObjectName("Separator")
    sep.setFrameShape(QFrame.HLine)
    sep.setFixedHeight(thickness)
    return sep


def build_vline_separator(parent: QWidget, *,
                          thickness: int = theme.Metrics.hairline) -> QFrame:
    """The vertical twin of :func:`build_hline_separator`."""
    sep = QFrame(parent)
    sep.setObjectName("Separator")
    sep.setFrameShape(QFrame.VLine)
    sep.setFixedWidth(thickness)
    return sep
