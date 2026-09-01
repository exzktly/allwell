"""ButtonRow — a row of buttons that wraps instead of forcing its host wider.

A ``QHBoxLayout`` full of buttons reports the sum of their widths as its
minimum, and that minimum propagates: on the Statistics page the three
group actions (**+ Add group**, **Clear All**, **↻ Load from Replicates**) made
the column they sit in about 370px at its narrowest, which was enough to push
the well map out of the row beside it at a pane width where the two still fitted
comfortably. Wrapping, the row's minimum is its widest single button and the
buttons take a second line instead of costing the map its place.

**This is not a control row and must never be used as one.** ``ControlBand``
(``widgets/control_band.py``) owns the one wrapping row of controls a page has,
at the top, and ``tests/test_control_band_adoption.py`` is the guard that keeps
a tab from growing a second one beside it — which is also why a page's furniture
of this kind belongs in a widget here rather than as a ``FlowLayout`` built at
the feature site, where it is indistinguishable from the thing that guard exists
to catch. A ButtonRow takes buttons that act on the section around it; anything
that selects what a page *shows* is a scope control and goes in the band.

No All-Well coupling — a plain reusable widget.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

import theme
from widgets.flow_layout import FlowLayout, sync_flow_height


class ButtonRow(QWidget):
    """A left-aligned row of buttons that wraps onto further lines as needed."""

    def __init__(self, parent: QWidget | None = None, *buttons: QWidget,
                 spacing: int | None = None) -> None:
        super().__init__(parent)
        gap = theme.Spacing.sm if spacing is None else spacing
        FlowLayout(self, h_spacing=gap, v_spacing=theme.Spacing.xs)
        # A ``FlowLayout`` answers ``heightForWidth``, but only a parent that
        # asks acts on it, and a widget nested in a ``QStackedWidget`` page — as
        # every Review tab is — can be sized from its one-line minimum instead,
        # putting the wrapped second line behind whatever is below. This pins
        # the host's minimum height to what the rows actually need on every
        # resize; ``widgets/flow_layout`` documents the case.
        sync_flow_height(self)
        for button in buttons:
            self.add(button)

    def add(self, button: QWidget) -> None:
        """Append *button*, reparenting it into the row."""
        self.layout().addWidget(button)
