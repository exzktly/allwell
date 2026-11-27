"""The Statistics tab's results as two tables — Groups and Comparisons — above the full text report (design
language row D15: a formatted table, not preformatted text).

The rows come from :mod:`review.features.stats._result_rows`; this module only builds and fills the widgets. A table
with no rows is hidden, so a run that produced no comparisons (Dunnett, a paired test, a two-way ANOVA — whose
blocks are prose in the report) shows no empty grid.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from review.features.stats._result_rows import GROUP_COLUMNS, PAIR_COLUMNS
from ui import text_roles, theme

GROUPS_KEY = "_stats_groups_table"
PAIRS_KEY = "_stats_pairs_table"


def _table(columns, parent: QWidget) -> QTableWidget:
    table = QTableWidget(0, len(columns), parent)
    table.setHorizontalHeaderLabels(list(columns))
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    table.setWordWrap(False)
    header = table.horizontalHeader()
    header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setStretchLastSection(True)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.hide()
    return table


def build_results_tables(ctx, parent: QWidget) -> QWidget:
    """The Groups and Comparisons tables, each under its heading, hidden until a run fills them."""
    host = QWidget(parent)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(theme.Spacing.sm)
    for key, title, columns in ((GROUPS_KEY, "Groups", GROUP_COLUMNS), (PAIRS_KEY, "Comparisons", PAIR_COLUMNS)):
        heading = ctx.widgets.register(key + "_heading", text_roles.section_heading(title, host))
        heading.hide()                                   # shown and hidden with its table
        table = ctx.widgets.register(key, _table(columns, host))
        lay.addWidget(heading)
        lay.addWidget(table)
    return host


def fill_table(ctx, key: str, rows) -> None:
    """Put *rows* in the table registered as *key*; hide it (and its heading) when there are none."""
    table = ctx.widgets.get_or_none(key)
    if table is None:
        return
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            item = QTableWidgetItem(value)
            if c > 0:
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            table.setItem(r, c, item)
    shown = bool(rows)
    table.setVisible(shown)
    heading = ctx.widgets.get_or_none(key + "_heading")
    if heading is not None:
        heading.setVisible(shown)
    if shown:
        # Every row on screen: the page scrolls, the table does not.
        height = table.horizontalHeader().height() + sum(table.rowHeight(r) for r in range(len(rows)))
        table.setFixedHeight(height + 2 * table.frameWidth())
