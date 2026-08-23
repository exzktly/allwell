"""A well the completeness record does not mention is still on Home's plate.

``plate_cells`` returned only the wells in the record, and ``_paint_plate``
passes its keys straight to ``setEnabledWells``. A well loaded into Review but
absent from the record — a record written before the well was added, or one
covering a subset of a merged dataset — was therefore disabled on Home's plate,
painted nothing, and had no legend row to say why. It could not be selected
there at all, and nothing on screen distinguished "we have no information about
this well" from "this well is not in the dataset".

``unchecked`` is the state that already means exactly that: not fine, not
broken, nothing known. The legend already carries a row for it.
"""

from __future__ import annotations

import types

from well_viewer.features.home import collect as hc  # noqa: E402
from well_viewer.features.home.collect import CELL_COMPLETE, CELL_UNCHECKED  # noqa: E402


def _ctx(record_wells, loaded):
    return types.SimpleNamespace(state=types.SimpleNamespace(
        dataset=types.SimpleNamespace(
            well_paths={w: f"/tmp/{w}.csv" for w in loaded},
            pipeline_info={"run_check": {"wells": record_wells}},
        ),
    ))


def test_a_well_missing_from_the_record_is_unchecked_not_absent():
    ctx = _ctx({"A01": {"health": "complete"}}, ["A01", "A02"])
    cells = hc.plate_cells(ctx)
    assert cells == {"A01": CELL_COMPLETE, "A02": CELL_UNCHECKED}


def test_the_record_still_wins_for_the_wells_it_knows():
    ctx = _ctx({"A01": {"health": "complete"}}, ["A01"])
    assert hc.plate_cells(ctx)["A01"] == CELL_COMPLETE


def test_a_recorded_well_the_dataset_no_longer_holds_is_kept():
    """The record is evidence about the run, not about what is loaded now."""
    ctx = _ctx({"A01": {"health": "complete"}}, [])
    assert hc.plate_cells(ctx) == {"A01": CELL_COMPLETE}


def test_no_record_at_all_leaves_every_loaded_well_unchecked():
    ctx = _ctx({}, ["A01", "A02"])
    assert hc.plate_cells(ctx) == {"A01": CELL_UNCHECKED, "A02": CELL_UNCHECKED}


def test_the_legend_has_a_row_for_every_painted_state():
    ctx = _ctx({"A01": {"health": "complete"}}, ["A01", "A02"])
    cells = hc.plate_cells(ctx)
    rows = {state for state, _label, _count in hc.plate_legend(ctx)}
    unexplained = set(cells.values()) - rows
    assert not unexplained, (
        f"wells painted {sorted(unexplained)} with no legend row to explain them"
    )
