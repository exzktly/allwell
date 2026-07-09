"""Faceted render layout — plan_facets groups + labels the grid from the nesting.

Fast + Qt-light: drives the pure placement planner on fake cells (combo stubs),
asserting the group spacing, axis labels, and the vertical outer strip that the
rendered Image Table uses instead of a title under every cell.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from well_viewer.features.image_browser.image_table import facet_view as F  # noqa: E402


class _CB:
    def __init__(self, t):
        self._t = t

    def currentText(self):
        return self._t


def _cell(well, chan, tp, fov="1"):
    return {"well_cb": _CB(well), "chan_cb": _CB(chan),
            "tp_cb": _CB(tp), "fov_cb": _CB(fov)}


def _ctx(row_dims, col_dims):
    st = SimpleNamespace(row_dims=row_dims, col_dims=col_dims)
    return SimpleNamespace(state=SimpleNamespace(image_table=st))


def test_facet_plan_nested_rows_group_space_and_axes():
    # rows = well › channel, cols = timepoint  → (2 wells × 2 channels) × 3 tps
    cells = []
    for well in ("A01", "A02"):
        for chan in ("GFP", "DAPI"):
            cells.append([_cell(well, chan, tp) for tp in ("0h", "24h", "48h")])
    plan = F.plan_facets(_ctx(["well", "channel"], ["timepoint"]), cells)

    assert plan.first_row == 1 and plan.first_col == 2   # 1 top label row, 2 left cols
    assert plan.spacer_rows == [3]                       # one gap between the well groups
    assert plan.spacer_cols == []
    assert plan.grid_row_of == [1, 2, 4, 5]              # rows skip the spacer slot
    assert plan.grid_col_of == [2, 3, 4]

    groups = [(t, rs) for (t, _gr, _gc, rs, _cs, grp) in plan.left if grp]
    assert ("A01", 2) in groups and ("A02", 2) in groups  # vertical strip spans its group
    inners = [t for (t, *_rest, grp) in plan.left if not grp]
    assert inners == ["GFP", "DAPI", "GFP", "DAPI"]
    tops = [t for (t, *_rest, grp) in plan.top if not grp]
    assert tops == ["T:0h", "T:24h", "T:48h"]
    assert plan.corner == "FOV:1"                         # the one fixed dim, shown once


def test_facet_plan_flat_axes_no_group_spacing():
    cells = [[_cell("A01", chan, tp) for tp in ("0h", "24h")]
             for chan in ("GFP", "DAPI")]
    plan = F.plan_facets(_ctx(["channel"], ["timepoint"]), cells)
    assert plan.spacer_rows == [] and plan.spacer_cols == []   # nothing nested → uniform
    assert plan.first_row == 1 and plan.first_col == 1
    assert [t for (t, *_rest, grp) in plan.left if not grp] == ["GFP", "DAPI"]
    assert not any(grp for (*_x, grp) in plan.left)            # no group strips


def test_facet_plan_reads_independent_gaps():
    # The row-group and column-group gaps are separate knobs — one number can't
    # cover the different spaces in the grouped view.
    cells = [[_cell("A01", chan, tp) for tp in ("0h", "24h")]
             for chan in ("GFP", "DAPI")]
    ctx = _ctx(["channel"], ["timepoint"])
    ctx.state.image_table.row_group_gap = 40
    ctx.state.image_table.col_group_gap = 6
    plan = F.plan_facets(ctx, cells)
    assert plan.row_gap == 40 and plan.col_gap == 6


def test_facet_plan_reads_font_sizes():
    cells = [[_cell("A01", "GFP", tp) for tp in ("0h", "24h")]]
    ctx = _ctx([], ["timepoint"])
    ctx.state.image_table.label_font_pt = 20
    ctx.state.image_table.group_font_pt = 24
    plan = F.plan_facets(ctx, cells)
    assert plan.label_pt == 20 and plan.group_pt == 24


def test_facet_plan_nested_columns_group_space():
    # cols = well › timepoint  → column groups get the horizontal spacer
    cells = [[_cell(well, "GFP", tp) for well in ("A01", "A02") for tp in ("0h", "24h")]]
    plan = F.plan_facets(_ctx([], ["well", "timepoint"]), cells)
    assert plan.spacer_cols == [2]                            # gap between the well column groups
    assert plan.first_row == 2                               # two top label rows (well over tp)
    assert any(grp for (*_x, grp) in plan.top)               # a spanning well group label
