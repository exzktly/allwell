"""Pure-Python unit tests for ``SelectionState``.

No Qt, no app — these exercise the plate-selection invariants directly: the
add/remove diff that resolves the last-touched well, and the saved-selections
helpers that compose ``selections_model``.
"""

from __future__ import annotations

from well_viewer.core.selection_state import SelectionState


# ── saved selections ─────────────────────────────────────────────────────────

def test_defaults():
    sel = SelectionState()
    assert sel.selected_wells == set()
    assert sel.rep_mode is False
    assert sel.current_selection() is None


def test_rep_mode_tracks_selections():
    sel = SelectionState()
    assert sel.rep_mode is False
    sel.selections = [{"id": "a", "name": "G1"}]
    assert sel.rep_mode is True


def test_current_selection_lookup():
    sel = SelectionState(
        selections=[{"id": "x", "name": "A"}, {"id": "y", "name": "B"}],
        current_selection_id="y",
    )
    assert sel.current_selection()["name"] == "B"


def test_current_selection_none_when_stale():
    sel = SelectionState(
        selections=[{"id": "x", "name": "A"}],
        current_selection_id="missing",
    )
    assert sel.current_selection() is None


def test_validate_repairs_and_sets_current_id():
    sel = SelectionState(selections=[{"name": "G1", "wells": ["A01"]}])
    sel.validate()
    assert len(sel.selections) == 1
    minted_id = sel.selections[0]["id"]
    assert minted_id
    assert sel.current_selection_id == minted_id


def test_validate_drops_stale_current_id():
    sel = SelectionState(
        selections=[{"id": "keep", "name": "G1", "wells": ["A01"]}],
        current_selection_id="bogus",
    )
    sel.validate()
    assert sel.current_selection_id == sel.selections[0]["id"]


# ── one-condition group invariant: no replicate sub-partitions ──────────────

def test_make_selection_collapses_replicate_partition_to_none():
    """A group is one condition — its wells are its implicit replicate set, so
    any incoming ``replicates`` sub-partition (multiple sub-sets / solo wells)
    is collapsed to ``None`` while the flat ``wells`` are preserved."""
    from well_viewer.domain.selections_model import make_selection
    s = make_selection(name="G", wells=["A01", "A02", "A03"],
                       replicates=[["A01"], ["A02", "A03"]])
    assert s["replicates"] is None
    assert s["wells"] == ["A01", "A02", "A03"]


def test_validate_repair_drops_stored_partition():
    """A legacy on-disk block carrying a multi-sublist partition loads with the
    partition dropped (behaviour-inert — nothing downstream reads it)."""
    from well_viewer.domain.selections_model import validate_repair
    out = validate_repair([
        {"id": "g1", "name": "G", "wells": ["A01", "B01"],
         "replicates": [["A01"], ["B01"]]},
    ])
    assert out[0]["replicates"] is None
    assert out[0]["wells"] == ["A01", "B01"]


# ── plate selection ──────────────────────────────────────────────────────────

def test_select_wells_snapshots():
    sel = SelectionState()
    sel.select_wells(["A01", "A02"])
    assert sel.selected_wells == {"A01", "A02"}
    assert sel.prev_sel == {"A01", "A02"}
    assert sel.last_sel in {"A01", "A02"}


def test_clear_selection():
    sel = SelectionState(
        selected_wells={"A01"}, last_sel="A01", prev_sel={"A01"},
    )
    sel.clear_selection()
    assert sel.selected_wells == set()
    assert sel.last_sel is None
    assert sel.prev_sel == set()


def test_commit_plate_change_addition_wins():
    sel = SelectionState(prev_sel={"A01"})
    sel.selected_wells = {"A01", "B02"}
    sel.commit_plate_change()
    assert sel.last_sel == "B02"
    assert sel.prev_sel == {"A01", "B02"}


def test_commit_plate_change_removal():
    sel = SelectionState(prev_sel={"A01", "B02"})
    sel.selected_wells = {"A01"}
    sel.commit_plate_change()
    assert sel.last_sel == "B02"
    assert sel.prev_sel == {"A01"}


def test_commit_plate_change_removal_to_empty():
    sel = SelectionState(prev_sel={"A01"})
    sel.selected_wells = set()
    sel.commit_plate_change()
    assert sel.last_sel is None
    assert sel.prev_sel == set()
