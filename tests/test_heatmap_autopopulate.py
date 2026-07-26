"""Heat Map "Auto-populate from selection" reads the right selection.

The plate expresses a selection in two different places depending on mode. In
plain mode it is ``selection.selected_wells``; in replicate mode (Sample
Definition groups defined) it is ``selection.selections``, and
``selected_wells`` is empty or holds whatever individual wells were left in it
from an earlier plain-mode click. Auto-populate read only ``selected_wells``,
so with replicate sets chosen it built a grid of those stale individual wells —
the wrong count, and one cell per well rather than one per set.

In replicate mode the layout then follows the **Rep-set avg** toggle, since
that is what decides whether a cell stands for a set or for a well: checked
gives one multi-well cell per set (the map averages it), unchecked gives one
cell per well. Grouping regardless of the toggle put a whole set in a single
cell that the sidebar grid could only render as its first well, so the other
members looked dropped and the toggle looked inert.

These exercise the pure cell-planner, so they need no application fixture.
"""

from __future__ import annotations

import types

from well_viewer.features.heatmap.sidebar_view import _autopopulate_cells


def _ctx(*, selections, selected_wells, well_paths, repset_avg=True):
    """Minimal stand-in for the frozen ViewerContext this planner reads."""
    return types.SimpleNamespace(
        state=types.SimpleNamespace(
            selection=types.SimpleNamespace(
                selections=selections,
                selected_wells=selected_wells,
            ),
            dataset=types.SimpleNamespace(well_paths=well_paths),
            heatmap=types.SimpleNamespace(repset_avg=repset_avg),
        ),
        window=types.SimpleNamespace(),
    )


WELLS = {f"{row}{col:02d}": f"/data/{row}{col:02d}"
         for row in "AB" for col in range(1, 7)}


def test_replicate_sets_become_one_cell_each_when_averaging():
    """Five replicate sets produce five cells, each holding its own wells."""
    selections = [
        {"name": f"set{i}", "wells": [f"A{i:02d}", f"B{i:02d}"]}
        for i in range(1, 6)
    ]
    ctx = _ctx(selections=selections,
               # Stale leftovers from an earlier plain-mode click — the exact
               # thing that used to be laid out instead of the sets.
               selected_wells={"A01", "A02"},
               well_paths=WELLS,
               repset_avg=True)

    cells = _autopopulate_cells(ctx)

    assert len(cells) == 5
    assert cells[0] == ["A01", "B01"]
    assert cells[4] == ["A05", "B05"]


def test_without_averaging_every_member_well_gets_its_own_cell():
    """Regression: with Rep-set avg off a set is not collapsed into one cell.

    Grouping anyway left a cell holding four wells that the sidebar grid drew as
    a single ``A01`` — every other member of the set looked ignored — while the
    map pooled all four despite the toggle being off.
    """
    selections = [
        {"name": "ctrl", "wells": ["A01", "B01"]},
        {"name": "drug", "wells": ["A02", "B02"]},
    ]
    ctx = _ctx(selections=selections, selected_wells=set(),
               well_paths=WELLS, repset_avg=False)

    assert _autopopulate_cells(ctx) == [["A01"], ["A02"], ["B01"], ["B02"]]


def test_without_averaging_hidden_sets_still_contribute_nothing():
    selections = [
        {"name": "shown", "wells": ["A01", "B01"]},
        {"name": "hidden", "wells": ["A02", "B02"], "hidden": True},
    ]
    ctx = _ctx(selections=selections, selected_wells=set(),
               well_paths=WELLS, repset_avg=False)

    assert _autopopulate_cells(ctx) == [["A01"], ["B01"]]


def test_without_averaging_unloaded_wells_are_dropped():
    selections = [{"name": "set", "wells": ["A01", "H12", "B01"]}]
    ctx = _ctx(selections=selections, selected_wells=set(),
               well_paths=WELLS, repset_avg=False)

    assert _autopopulate_cells(ctx) == [["A01"], ["B01"]]


def test_hidden_replicate_sets_are_skipped():
    """Only visible sets are laid out — hidden ones draw no trace."""
    selections = [
        {"name": "shown", "wells": ["A01", "B01"]},
        {"name": "hidden", "wells": ["A02", "B02"], "hidden": True},
    ]
    ctx = _ctx(selections=selections, selected_wells=set(), well_paths=WELLS)

    cells = _autopopulate_cells(ctx)

    assert cells == [["A01", "B01"]]


def test_replicate_set_cells_drop_unloaded_wells():
    """A set may name wells this dataset does not have; those are not laid out."""
    selections = [{"name": "set", "wells": ["A01", "H12", "B01"]}]
    ctx = _ctx(selections=selections, selected_wells=set(), well_paths=WELLS)

    assert _autopopulate_cells(ctx) == [["A01", "B01"]]


def test_plain_mode_lays_out_one_cell_per_selected_well():
    """With no replicate sets defined, each selected well is its own cell."""
    ctx = _ctx(selections=[], selected_wells={"B02", "A01", "A03"},
               well_paths=WELLS)

    assert _autopopulate_cells(ctx) == [["A01"], ["A03"], ["B02"]]


def test_empty_selection_builds_nothing():
    ctx = _ctx(selections=[], selected_wells=set(), well_paths=WELLS)

    assert _autopopulate_cells(ctx) == []
