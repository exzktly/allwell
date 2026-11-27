"""Cell Browser sort builder: the interactive "Sort by" rows in the sidebar.

Each row is ``(field, ascending?)`` over any sortable field — well, timepoint,
FOV, or any measured/filter/metric value (``collect.sort_field_options``). Rows
are evaluated in order (first row = primary key); the **Apply filters** button
applies them through ``controller.apply_filters``. Mirrors the criteria-row
add/remove pattern in ``criteria_view``.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget,
)

from core.safe import swallowed
from review.features.image_browser.cell_browser.collect import sort_field_options
from ui import theme
from ui import buttons

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_RM_W = 24


def build_cell_browser_sort_section(ctx, parent_layout) -> None:
    """Build the "Sort by" heading + rows host + add button into *parent_layout*."""
    from review.core.ui_helpers import build_section_header
    parent_layout.addWidget(build_section_header(None, "Sort by"))

    # Rows live in a capped, frameless scroll (mirrors the Plot Groups drawer) so
    # several sort keys scroll inside the drawer instead of growing the rail.
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setMaximumHeight(theme.Metrics.scroll_max_h)
    host = QWidget()
    rows_lay = QVBoxLayout(host)
    rows_lay.setContentsMargins(0, 0, 0, 0)
    rows_lay.setSpacing(theme.Spacing.xs)
    rows_lay.addStretch(1)   # add_sort_row inserts before this trailing stretch
    scroll.setWidget(host)
    parent_layout.addWidget(scroll)
    ctx.runtime.cell_browser.sort_host = rows_lay
    ctx.runtime.cell_browser.sort_rows = []

    add_btn = buttons.default("Add sort key")
    add_btn.clicked.connect(lambda _=False: add_sort_row(ctx))
    parent_layout.addWidget(add_btn)


def add_sort_row(ctx, field: str = "", ascending: bool = True) -> None:
    rows_lay = getattr(ctx.runtime.cell_browser, "sort_host", None)
    if rows_lay is None:
        return
    row = QWidget()
    rl = QHBoxLayout(row)
    rl.setContentsMargins(0, 0, 0, 0)
    rl.setSpacing(theme.Spacing.xs)

    combo = QComboBox(row)
    for col, label in sort_field_options(ctx):
        combo.addItem(label, userData=col)
    if field:
        idx = combo.findData(field)
        if idx >= 0:
            combo.setCurrentIndex(idx)
    combo.setMinimumWidth(theme.Metrics.combo_min_w_sm)

    # Direction: one of two, a segmented control with the drawn arrows, each named by its tooltip (§2.5).
    from widgets import icons
    from widgets.segmented_control import SegmentedControl
    dir_btn = SegmentedControl(row)
    dir_btn.addSegment(icon=icons.make_icon("chevron-up", 14), data="asc")
    dir_btn.addSegment(icon=icons.make_icon("chevron-down", 14), data="desc")
    dir_btn.setSegmentToolTip(0, "Ascending")
    dir_btn.setSegmentToolTip(1, "Descending")
    dir_btn.setCurrentByData("asc" if ascending else "desc")

    rm = buttons.icon_button("x", "Remove this sort key", row)
    rm.setFixedWidth(_RM_W)

    rl.addWidget(combo, 1)
    rl.addWidget(dir_btn)
    rl.addWidget(rm)
    # insert before the trailing stretch
    rows_lay.insertWidget(rows_lay.count() - 1, row)
    entry = (combo, dir_btn)
    ctx.runtime.cell_browser.sort_rows.append(entry)

    def _remove(_=False) -> None:
        try:
            ctx.runtime.cell_browser.sort_rows.remove(entry)
        except ValueError:
            swallowed()
        row.deleteLater()
    rm.clicked.connect(_remove)


def cell_browser_sort_refresh(ctx) -> None:
    """Repopulate each sort row's field combo from the current dataset (called
    alongside the criteria refresh on tab show / dataset load)."""
    opts = sort_field_options(ctx)
    for combo, _dir in (ctx.runtime.cell_browser.sort_rows or []):
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
