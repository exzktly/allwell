"""Cell Browser sort builder: the interactive "Sort by" rows in the sidebar.

Each row is ``(field, ascending?)`` over any sortable field — well, timepoint,
FOV, or any measured/filter/metric value (``collect.sort_field_options``). Rows
are evaluated in order (first row = primary key); the **Apply filters** button
applies them through ``controller.apply_filters``. Mirrors the criteria-row
add/remove pattern in ``criteria_view``.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from well_viewer.core.safe import swallowed
from well_viewer.features.image_browser.cell_browser.collect import sort_field_options


def build_cell_browser_sort_section(app, parent_layout) -> None:
    """Build the "Sort by" heading + rows host + add button into *parent_layout*."""
    title = QLabel("Sort by")
    title.setObjectName("Heading")
    parent_layout.addWidget(title)

    host = QWidget()
    rows_lay = QVBoxLayout(host)
    rows_lay.setContentsMargins(0, 0, 0, 0)
    rows_lay.setSpacing(4)
    rows_lay.addStretch(1)
    parent_layout.addWidget(host)
    app._cell_browser_sort_host = rows_lay
    app._cell_browser_sort_rows = []

    add_btn = QPushButton("+ Add sort key")
    add_btn.setProperty("variant", "secondary")
    add_btn.clicked.connect(lambda _=False: add_sort_row(app))
    parent_layout.addWidget(add_btn)


def add_sort_row(app, field: str = "", ascending: bool = True) -> None:
    rows_lay = getattr(app, "_cell_browser_sort_host", None)
    if rows_lay is None:
        return
    row = QWidget()
    rl = QHBoxLayout(row)
    rl.setContentsMargins(0, 0, 0, 0)
    rl.setSpacing(3)

    combo = QComboBox(row)
    for col, label in sort_field_options(app):
        combo.addItem(label, userData=col)
    if field:
        idx = combo.findData(field)
        if idx >= 0:
            combo.setCurrentIndex(idx)
    combo.setMinimumWidth(120)

    # Direction toggle: checked = descending (▼), unchecked = ascending (▲).
    dir_btn = QPushButton("▲ Asc", row)
    dir_btn.setCheckable(True)
    dir_btn.setFixedWidth(64)
    dir_btn.setToolTip("Toggle ascending / descending")

    def _sync_dir(_checked=None) -> None:
        dir_btn.setText("▼ Desc" if dir_btn.isChecked() else "▲ Asc")
    dir_btn.setChecked(not ascending)
    _sync_dir()
    dir_btn.toggled.connect(_sync_dir)

    rm = QPushButton("✕", row)
    rm.setFixedWidth(24)
    rm.setObjectName("Ghost")

    rl.addWidget(combo, 1)
    rl.addWidget(dir_btn)
    rl.addWidget(rm)
    # insert before the trailing stretch
    rows_lay.insertWidget(rows_lay.count() - 1, row)
    entry = (combo, dir_btn)
    app._cell_browser_sort_rows.append(entry)

    def _remove(_=False) -> None:
        try:
            app._cell_browser_sort_rows.remove(entry)
        except ValueError:
            swallowed()
        row.deleteLater()
    rm.clicked.connect(_remove)


def cell_browser_sort_refresh(app) -> None:
    """Repopulate each sort row's field combo from the current dataset (called
    alongside the criteria refresh on tab show / dataset load)."""
    opts = sort_field_options(app)
    for combo, _dir in getattr(app, "_cell_browser_sort_rows", []):
        cur = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for col, label in opts:
            combo.addItem(label, userData=col)
        if cur:
            idx = combo.findData(cur)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        combo.blockSignals(False)
