"""Faceted placement for the rendered Image Table grid.

Turns the flat rows×cols selector grid into a faceted *small-multiples* layout
that mirrors the builder's nesting: cells that share an outer row/column
dimension value are grouped with tight spacing, groups are separated by a gap,
and each dimension's values are labelled **once** on the top (columns) and left
(rows) axes instead of repeating a full title under every cell. The leftmost
strip carries the outer/group label as a vertical axis (JMP-style facet strip).

Pure-ish: :func:`plan_facets` computes only indices + label text from
``row_dims``/``col_dims`` and the cells' current combo values (no widgets), so it
is unit-testable; :func:`render_facet_chrome` turns a plan into the label widgets
and inter-group spacers. The controller places each cell widget at
``plan.grid_row_of[r]`` / ``plan.grid_col_of[c]``.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from well_viewer.core.ui_helpers import make_overlay_thumb
from well_viewer.features.image_browser.image_table.image_compose import (
    composite_column_rgb,
)

_DIM_CB = {"well": "well_cb", "channel": "chan_cb",
           "timepoint": "tp_cb", "fov": "fov_cb"}
_DIM_LABEL = {"well": "Well", "channel": "Channel",
              "timepoint": "Timepoint", "fov": "FOV"}
_GROUP_GAP = 18          # px gap between facet groups (the non-uniform spacing)

# Scoped label styling — muted per-value labels, a subtle strip behind the
# outer/group labels so they read as facet bands.
_INNER_QSS = "color:#98A2B3; font-size:11px;"
_GROUP_QSS = ("color:#E6E9EF; font-size:11px; font-weight:600;"
              " background:#1A2230; border-radius:5px; padding:2px 6px;")
_CORNER_QSS = "color:#5F6B7C; font-size:10px;"


def _cv(cell, dim: str) -> str:
    cb = cell.get(_DIM_CB.get(dim, ""))
    try:
        return cb.currentText().strip() if cb is not None else ""
    except Exception:
        return ""


def _fmt(dim: str, val: str) -> str:
    if not val:
        return ""
    if dim == "timepoint":
        return f"T:{val}"
    if dim == "fov":
        return f"FOV:{val}"
    if dim == "channel":
        return str(val).upper()
    return str(val)


class _VLabel(QLabel):
    """A QLabel that paints its text rotated 90° — the left vertical facet axis."""

    def sizeHint(self):  # noqa: N802
        return super().sizeHint().transposed()

    def minimumSizeHint(self):  # noqa: N802
        return super().minimumSizeHint().transposed()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        # Rotate −90° about the centre so the text reads bottom-to-top (the
        # conventional vertical facet-axis orientation).
        p.translate(self.width() / 2.0, self.height() / 2.0)
        p.rotate(-90)
        r = QRect(-self.height() // 2, -self.width() // 2, self.height(), self.width())
        p.drawText(r, int(Qt.AlignCenter), self.text())
        p.end()


class FacetPlan:
    """Placement + labelling derived from the layout's nesting."""

    __slots__ = ("grid_row_of", "grid_col_of", "spacer_rows", "spacer_cols",
                 "top", "left", "corner", "first_row", "first_col", "bottom_row",
                 "row_gap", "col_gap")

    def __init__(self) -> None:
        self.grid_row_of: List[int] = []
        self.grid_col_of: List[int] = []
        self.spacer_rows: List[int] = []
        self.spacer_cols: List[int] = []
        # (text, grid_row, grid_col, row_span, col_span, is_group)
        self.top: List[Tuple[str, int, int, int, int, bool]] = []
        self.left: List[Tuple[str, int, int, int, int, bool]] = []
        self.corner: str = ""
        self.first_row = 0
        self.first_col = 0
        self.bottom_row = 0
        self.row_gap = _GROUP_GAP
        self.col_gap = _GROUP_GAP


def _slots(vals, depth: int, base: int):
    """Grid index of each logical item, inserting a spacer slot between groups
    (items whose outer prefix — all dims but the innermost — changes)."""
    grid_of: List[int] = []
    spacers: List[int] = []
    g = base
    for i in range(len(vals)):
        if i > 0 and depth >= 2 and vals[i][:-1] != vals[i - 1][:-1]:
            spacers.append(g)
            g += 1
        grid_of.append(g)
        g += 1
    return grid_of, spacers, g


def _group_spans(vals, grid_of):
    """Contiguous outer-prefix groups → (prefix_value, grid_start, grid_end)."""
    spans = []
    i = 0
    n = len(vals)
    while i < n:
        j = i
        while j + 1 < n and vals[j + 1][:-1] == vals[i][:-1]:
            j += 1
        spans.append((vals[i][:-1], grid_of[i], grid_of[j]))
        i = j + 1
    return spans


def plan_facets(ctx, cells) -> Optional[FacetPlan]:
    """Compute the faceted placement, or None when there's nothing to lay out."""
    st = ctx.state.image_table
    row_dims = [d for d in (getattr(st, "row_dims", None) or []) if d in _DIM_CB]
    col_dims = [d for d in (getattr(st, "col_dims", None) or []) if d in _DIM_CB]
    nrows = len(cells)
    ncols = len(cells[0]) if cells and cells[0] else 0
    if not nrows or not ncols:
        return None

    tl, rl = len(col_dims), len(row_dims)
    row_vals = [tuple(_cv(cells[r][0], d) for d in row_dims) for r in range(nrows)]
    col_vals = [tuple(_cv(cells[0][c], d) for d in col_dims) for c in range(ncols)]

    grid_row_of, spacer_rows, next_row = _slots(row_vals, rl, tl)
    grid_col_of, spacer_cols, next_col = _slots(col_vals, tl, rl)

    plan = FacetPlan()
    plan.grid_row_of, plan.grid_col_of = grid_row_of, grid_col_of
    plan.spacer_rows, plan.spacer_cols = spacer_rows, spacer_cols
    plan.first_row, plan.first_col = tl, rl
    plan.bottom_row = next_row
    plan.row_gap = max(0, int(getattr(st, "row_group_gap", _GROUP_GAP)))
    plan.col_gap = max(0, int(getattr(st, "col_group_gap", _GROUP_GAP)))

    # Column axis (top): inner value per column, outer group spanning its columns.
    if tl >= 1:
        for c in range(ncols):
            plan.top.append((_fmt(col_dims[-1], col_vals[c][-1]),
                             tl - 1, grid_col_of[c], 1, 1, False))
    if tl >= 2:
        for prefix, gs, ge in _group_spans(col_vals, grid_col_of):
            plan.top.append((_fmt(col_dims[0], prefix[0]),
                             0, gs, 1, ge - gs + 1, True))

    # Row axis (left): inner value per row, outer group as a vertical strip.
    if rl >= 1:
        for r in range(nrows):
            plan.left.append((_fmt(row_dims[-1], row_vals[r][-1]),
                              grid_row_of[r], rl - 1, 1, 1, False))
    if rl >= 2:
        for prefix, gs, ge in _group_spans(row_vals, grid_row_of):
            plan.left.append((_fmt(row_dims[0], prefix[0]),
                              gs, 0, ge - gs + 1, 1, True))

    # Corner: the dimensions pinned off both axes (constant for every cell).
    fixed = []
    for dim in ("well", "timepoint", "fov"):
        if dim not in row_dims and dim not in col_dims:
            v = _cv(cells[0][0], dim)
            if v:
                fixed.append(_fmt(dim if dim != "well" else "well", v)
                             if dim != "well" else v)
    plan.corner = "  ·  ".join(fixed)
    return plan


def render_facet_chrome(grid, plan: FacetPlan) -> None:
    """Add the axis labels + inter-group spacers described by *plan* to *grid*."""
    for text, gr, gc, rs, cs, is_group in plan.top:
        if not text:
            continue
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter | Qt.AlignBottom)
        lbl.setStyleSheet(_GROUP_QSS if is_group else _INNER_QSS)
        grid.addWidget(lbl, gr, gc, rs, cs)

    for text, gr, gc, rs, cs, is_group in plan.left:
        if not text:
            continue
        if is_group:
            lbl = _VLabel(text)
            lbl.setStyleSheet(_GROUP_QSS)
        else:
            lbl = QLabel(text)
            lbl.setStyleSheet(_INNER_QSS)
        lbl.setAlignment(Qt.AlignCenter)
        grid.addWidget(lbl, gr, gc, rs, cs)

    if plan.corner and plan.first_row and plan.first_col:
        c = QLabel(plan.corner)
        c.setStyleSheet(_CORNER_QSS)
        c.setAlignment(Qt.AlignCenter)
        grid.addWidget(c, 0, 0, plan.first_row, plan.first_col)

    for sr in plan.spacer_rows:
        grid.setRowMinimumHeight(sr, plan.row_gap)
    for sc in plan.spacer_cols:
        grid.setColumnMinimumWidth(sc, plan.col_gap)


def append_composite_row(ctx, cells, grid, cache, crop_tool, sz, rendered,
                         plan: Optional[FacetPlan] = None) -> None:
    """Append a bottom row that additively merges the LUT-colored RGB of every
    row above it in each column. Sits below the faceted grid, aligned to the
    facet columns, labelled once on the left axis."""
    comp_row = (plan.bottom_row + 1) if plan is not None else len(cells)
    if plan is not None:
        grid.setRowMinimumHeight(plan.bottom_row, plan.row_gap)
        if plan.first_col:
            lbl = QLabel("Composite")
            lbl.setStyleSheet(_INNER_QSS)
            lbl.setAlignment(Qt.AlignCenter)
            grid.addWidget(lbl, comp_row, 0, 1, plan.first_col)
    n_cols = max(len(row) for row in cells)
    for c in range(n_cols):
        comp = composite_column_rgb(ctx, cache, cells, crop_tool, c, rendered)
        cell_widget = QWidget()
        cl = QVBoxLayout(cell_widget)
        cl.setContentsMargins(2, 2, 2, 2)
        cl.setSpacing(2)
        img_label = QLabel(cell_widget)
        img_label.setToolTip("Composite")
        img_label.setAlignment(Qt.AlignCenter)
        img_label.setFixedSize(sz, sz)
        pix = make_overlay_thumb(comp, sz, sz, 0.0, 255.0) if comp is not None else None
        if pix is not None:
            img_label.setPixmap(pix)
        else:
            img_label.setText("(no image)")
        cl.addWidget(img_label)
        gc = plan.grid_col_of[c] if plan is not None else c
        grid.addWidget(cell_widget, comp_row, gc)
