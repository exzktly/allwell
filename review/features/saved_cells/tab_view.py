"""The Saved Cells page: every saved cell set and every gate, and how to look at each.

Two lists, because they are two kinds of thing (``collect.py`` says why). Each
row says what the population is and what it does to the analysis now; the
buttons under a list act on its selected row:

* **Saved cell sets** — *View in Cell Browser*, *Highlight on Scatter*,
  *Exclude from analysis* (each a toggle; one set at a time can be highlighted
  and one excluded), rename and delete.
* **Gates** — *View in Cell Browser* (the cells inside that gate alone) and
  *Manage on Cell Gating*, where a gate is applied to the Included or ON cells.
  Below them, the per-channel intensity floors in force, the other way a cell
  leaves the Included population.

The page is rebuilt from the stores whenever it is shown and whenever a set
changes (``core/cell_sets.REFRESH_KEYS``), so it never holds a copy that can
disagree with them.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from review.core.ui_helpers import build_section_header
from review.features.saved_cells import collect
from review.features.saved_cells import controller as ctl
from ui import theme
from widgets.page_body import build_page_body

_SET_COLUMNS = ("Name", "Cells", "From", "In use")
_GATE_COLUMNS = ("Name", "Axes", "Wells", "In use")


def _table(parent: QWidget, columns: tuple) -> QTableWidget:
    table = QTableWidget(0, len(columns), parent)
    table.setHorizontalHeaderLabels(list(columns))
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    table.setWordWrap(True)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setStretchLastSection(True)
    return table


def _fill(table: QTableWidget, rows: list[tuple], keys: list[str], keep: str | None) -> None:
    """Replace *table*'s rows, each carrying its key, and reselect *keep* if it is still there."""
    table.blockSignals(True)
    try:
        table.setRowCount(len(rows))
        for r, cells in enumerate(rows):
            for c, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                item.setData(256, keys[r])
                item.setToolTip(str(text))
                table.setItem(r, c, item)
        table.clearSelection()
        if keep in keys:
            table.selectRow(keys.index(keep))
    finally:
        table.blockSignals(False)
    table.resizeRowsToContents()


def _selected(table: QTableWidget) -> str | None:
    items = table.selectedItems()
    return items[0].data(256) if items else None


def _button(parent: QWidget, text: str, tip: str, *, checkable: bool = False) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setProperty("variant", "secondary")
    btn.setToolTip(tip)
    btn.setCheckable(checkable)
    return btn


class SavedCellsPage(QWidget):
    """The page body; ``refresh`` re-reads both stores."""

    def __init__(self, ctx, parent: QWidget) -> None:
        super().__init__(parent)
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(theme.Spacing.md)
        self._build_sets(lay)
        self._build_gates(lay)
        lay.addStretch(1)
        self.refresh()

    # ── saved sets ──────────────────────────────────────────────────────────
    def _build_sets(self, lay: QVBoxLayout) -> None:
        lay.addWidget(build_section_header(
            self, "Saved cell sets",
            hint="Cells saved by name with Lasso cells on Spatial or Multivariate. A set is a "
                 "fixed list of cells: saving one changes no number."))
        self.sets_empty = QLabel(
            "No saved cells yet. Draw round cells with Lasso cells on a Spatial map or a "
            "Multivariate projection to save them here.", self)
        self.sets_empty.setObjectName("Muted")
        self.sets_empty.setWordWrap(True)
        lay.addWidget(self.sets_empty)
        self.sets = _table(self, _SET_COLUMNS)
        self.sets.itemSelectionChanged.connect(self._sync_set_buttons)
        lay.addWidget(self.sets)
        self.set_detail = QLabel("", self)
        self.set_detail.setObjectName("Muted")
        self.set_detail.setWordWrap(True)
        lay.addWidget(self.set_detail)
        row = QHBoxLayout()
        self.view_set = _button(self, "View in Cell Browser",
                                "Open Image Browser → Cell Browser showing this set's cells.")
        self.view_set.clicked.connect(lambda: self._on_set(ctl.view_set))
        self.highlight = _button(
            self, "Highlight on Scatter",
            "Draw this set's cells solid over a faded cloud on the per-cell Scatter. "
            "Changes no number. Click again to stop.", checkable=True)
        self.highlight.clicked.connect(lambda: self._on_set(ctl.toggle_highlight, refresh=True))
        self.exclude = _button(
            self, "Exclude from analysis",
            "Leave this set's cells out of every plot, statistic and export. One set at a time; "
            "click again to bring them back.", checkable=True)
        self.exclude.clicked.connect(lambda: self._on_set(ctl.toggle_exclude, refresh=True))
        for btn in (self.view_set, self.highlight, self.exclude):
            row.addWidget(btn)
        row.addStretch(1)
        self.name = QLineEdit(self)
        self.name.setPlaceholderText("Rename the selected set")
        self.name.editingFinished.connect(self._rename)
        row.addWidget(self.name)
        self.delete = _button(self, "Delete", "Delete the selected set. Its cells are not changed.")
        self.delete.clicked.connect(lambda: self._on_set(ctl.delete, refresh=True))
        row.addWidget(self.delete)
        lay.addLayout(row)

    def _on_set(self, action, *, refresh: bool = False) -> None:
        name = _selected(self.sets)
        if name:
            action(self.ctx, name)
            if refresh:
                self.refresh()

    def _rename(self) -> None:
        old = _selected(self.sets)
        new = self.name.text().strip()
        if old and new and new != old and ctl.rename(self.ctx, old, new):
            self._keep_set = new
            self.refresh()
        else:
            self._sync_set_buttons()

    def _sync_set_buttons(self) -> None:
        name = _selected(self.sets)
        row = next((r for r in self._set_rows if r.name == name), None)
        for btn in (self.view_set, self.highlight, self.exclude, self.name, self.delete):
            btn.setEnabled(row is not None)
        self.highlight.setChecked(bool(row and row.highlighted))
        self.exclude.setChecked(bool(row and row.excluded))
        self.name.setText(row.name if row else "")
        detail = []
        if row is not None:
            if row.created:
                detail.append(f"Saved {row.created.replace('T', ' ')}")
            if row.note:
                detail.append(row.note)
            detail.append("Wells: " + ", ".join(row.wells))
            if row.stale:
                detail.append(row.stale)
        self.set_detail.setText(" · ".join(detail))
        self.set_detail.setVisible(bool(detail))

    # ── gates ───────────────────────────────────────────────────────────────
    def _build_gates(self, lay: QVBoxLayout) -> None:
        lay.addWidget(build_section_header(
            self, "Gates",
            hint="Rectangles drawn on the per-cell Scatter. A gate is a rule over two "
                 "measurements, so it takes whatever cells are loaded; it changes the analysis "
                 "only where Cell Gating applies it."))
        self.gates_empty = QLabel(
            "No gates yet. Draw one on the Scatter Plot's per-cell view with Draw gate.", self)
        self.gates_empty.setObjectName("Muted")
        self.gates_empty.setWordWrap(True)
        lay.addWidget(self.gates_empty)
        self.gates = _table(self, _GATE_COLUMNS)
        self.gates.itemSelectionChanged.connect(self._sync_gate_buttons)
        lay.addWidget(self.gates)
        row = QHBoxLayout()
        self.view_gate = _button(self, "View in Cell Browser",
                                 "Open Image Browser → Cell Browser showing the cells inside "
                                 "this gate alone.")
        self.view_gate.clicked.connect(self._on_view_gate)
        row.addWidget(self.view_gate)
        self.manage = _button(self, "Manage on Cell Gating",
                              "Go to Home → Cell Gating, where gates are applied to the Included "
                              "or ON cells.")
        self.manage.clicked.connect(lambda: ctl.open_cell_gating(self.ctx))
        row.addWidget(self.manage)
        row.addStretch(1)
        lay.addLayout(row)
        self.intensity = QLabel("", self)
        self.intensity.setObjectName("Muted")
        self.intensity.setWordWrap(True)
        lay.addWidget(self.intensity)

    def _on_view_gate(self) -> None:
        name = _selected(self.gates)
        if name:
            ctl.view_gate(self.ctx, name)

    def _sync_gate_buttons(self) -> None:
        self.view_gate.setEnabled(_selected(self.gates) is not None)

    # ── both ────────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        keep = getattr(self, "_keep_set", None) or _selected(self.sets)
        self._keep_set = None
        self._set_rows = collect.set_rows(self.ctx)
        _fill(self.sets, [(r.name, r.size_text, r.source or "—", r.status_text)
                          for r in self._set_rows],
              [r.name for r in self._set_rows], keep)
        has_sets = bool(self._set_rows)
        self.sets_empty.setVisible(not has_sets)
        self.sets.setVisible(has_sets)
        if has_sets and _selected(self.sets) is None:
            self.sets.selectRow(0)
        self._sync_set_buttons()

        keep_gate = _selected(self.gates)
        gate_rows = collect.gate_rows(self.ctx)
        _fill(self.gates, [(g.name, g.axes_text, ", ".join(g.wells) or "all", g.status_text)
                           for g in gate_rows],
              [g.name for g in gate_rows], keep_gate)
        has_gates = bool(gate_rows)
        self.gates_empty.setVisible(not has_gates)
        self.gates.setVisible(has_gates)
        if has_gates and _selected(self.gates) is None:
            self.gates.selectRow(0)
        self._sync_gate_buttons()
        floors = collect.intensity_gates(self.ctx)
        self.intensity.setText(
            "Intensity gates in force: " + ", ".join(f"{ch} ≥ {v:g}" for ch, v in floors)
            if floors else "No intensity gate is in force.")


def build_saved_cells_tab(ctx, parent: QWidget) -> None:
    """Build the page into *parent* and register it for refreshes."""
    _band, body = build_page_body(parent, content_margins=(theme.Spacing.lg,) * 3)
    page = SavedCellsPage(ctx, body)
    body.layout().addWidget(page)
    ctx.widgets.register(ctl.PAGE_KEY, page)
