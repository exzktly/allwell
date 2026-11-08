"""Cell Gating → *Saved cells*: the sets saved with a lasso, and the one excluded (tracker M6).

Lists every set — its size, where it was drawn, and, for a set saved before
some of its wells were segmented again, how many of its cells are left out
(``core/cell_sets.describe``). The selected set can be renamed or deleted.
*Exclude from analysis* picks the one set whose cells leave every plot,
statistic and export (``Saved_Cells_Pass``); saving a set never does.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout, QWidget,
)

from review.core import cell_sets as cs
from review.core.ui_helpers import build_section_header

_NONE = "— none —"


class SavedCellsPanel(QWidget):
    def __init__(self, ctx, parent: QWidget) -> None:
        super().__init__(parent)
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(build_section_header(
            self, "Saved cells",
            hint="Sets of cells saved with Lasso cells on Spatial or Multivariate. Saving a "
                 "set changes no number; use it as the Cell Browser's source, or exclude it here."))
        self.empty = QLabel("No saved cells yet — draw a lasso on Spatial or Multivariate.", self)
        self.empty.setObjectName("Muted")
        self.empty.setWordWrap(True)
        lay.addWidget(self.empty)
        self.list = QListWidget(self)
        self.list.setWordWrap(True)
        self.list.currentItemChanged.connect(lambda *_a: self._on_pick())
        lay.addWidget(self.list)
        row = QHBoxLayout()
        self.name = QLineEdit(self)
        self.name.setPlaceholderText("Name of the selected set")
        self.name.editingFinished.connect(self._rename)
        row.addWidget(self.name, 1)
        self.delete = QPushButton("Delete", self)
        self.delete.setProperty("variant", "secondary")
        self.delete.clicked.connect(self._delete)
        row.addWidget(self.delete)
        self.highlight = QPushButton("Highlight on Scatter", self)
        self.highlight.setProperty("variant", "secondary")
        self.highlight.setToolTip("Draw this set's cells solid over the faded cloud on the per-cell "
                                  "Scatter (also under the Scatter's Display ▾). Changes no number.")
        self.highlight.clicked.connect(self._highlight)
        row.addWidget(self.highlight)
        lay.addLayout(row)
        ex = QHBoxLayout()
        ex_lbl = QLabel("Exclude from analysis", self)
        ex_lbl.setObjectName("Muted")
        ex.addWidget(ex_lbl)
        self.exclude = QComboBox(self)
        self.exclude.setToolTip("The one saved set whose cells are left out of every plot, "
                                "statistic and export. Cells of it in wells segmented again "
                                "since it was saved are not excluded: they may be other cells.")
        self.exclude.currentIndexChanged.connect(lambda _i: self._on_exclude())
        ex.addWidget(self.exclude, 1)
        lay.addLayout(ex)
        self.refresh()

    def _selected(self) -> str | None:
        item = self.list.currentItem()
        return item.data(256) if item is not None else None

    def refresh(self) -> None:
        current = self._selected()
        all_sets = cs.sets(self.ctx)
        self.list.blockSignals(True)
        self.exclude.blockSignals(True)
        try:
            self.list.clear()
            for s in all_sets:
                item = QListWidgetItem(cs.describe(self.ctx, s))
                item.setData(256, s.name)
                self.list.addItem(item)
                if s.name == current:
                    self.list.setCurrentItem(item)
            self.exclude.clear()
            self.exclude.addItem(_NONE, None)
            for s in all_sets:
                self.exclude.addItem(s.name, s.name)
            chosen = self.ctx.state.cell_sets.exclude
            self.exclude.setCurrentIndex(max(0, self.exclude.findData(chosen)) if chosen else 0)
        finally:
            self.list.blockSignals(False)
            self.exclude.blockSignals(False)
        has = bool(all_sets)
        self.empty.setVisible(not has)
        for w in (self.list, self.name, self.delete, self.highlight, self.exclude):
            w.setEnabled(has)
        self._on_pick()

    def _on_pick(self) -> None:
        self.name.setText(self._selected() or "")

    def _rename(self) -> None:
        old = self._selected()
        if old and cs.rename_set(self.ctx, old, self.name.text()):
            self.refresh()
        else:
            self._on_pick()

    def _delete(self) -> None:
        name = self._selected()
        if name:
            cs.delete_set(self.ctx, name)
            self.refresh()

    def _highlight(self) -> None:
        name = self._selected()
        if name:
            cs.set_highlighted(self.ctx, name)

    def _on_exclude(self) -> None:
        cs.set_excluded(self.ctx, self.exclude.currentData())


def build_saved_cells_section(ctx, frame: QWidget, layout: QVBoxLayout) -> SavedCellsPanel:
    panel = SavedCellsPanel(ctx, frame)
    ctx.widgets.register("_saved_cells_panel", panel)
    layout.addWidget(panel)
    return panel
