"""A menu of saved-cell sets bound to the Scatter highlight (tracker M7).

``— none —`` then every set; choosing one highlights it on the per-cell
Scatter (``cell_sets.set_highlighted``), choosing none clears it. It rebuilds
itself whenever the sets change (``cell_sets._changed`` refreshes every widget
registered under one of ``REFRESH_KEYS``), so a lasso saved on another page
appears here without reopening anything.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox

from review.core import cell_sets as cs

_NONE = "— none —"


class SavedSetHighlightCombo(QComboBox):
    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setToolTip("Draw one saved set's cells solid over the faded cloud. Changes no number; "
                        "the plot's title and a note under it say which set and how many are shown.")
        self.currentIndexChanged.connect(lambda _i: cs.set_highlighted(self.ctx, self.currentData()))
        self.refresh()

    def refresh(self) -> None:
        chosen = self.ctx.state.cell_sets.highlight
        blocked = self.blockSignals(True)
        try:
            self.clear()
            self.addItem(_NONE, None)
            for s in cs.sets(self.ctx):
                self.addItem(s.name, s.name)
            self.setCurrentIndex(max(0, self.findData(chosen)) if chosen else 0)
        finally:
            self.blockSignals(blocked)
