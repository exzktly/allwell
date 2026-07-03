"""Audit X14 — a subset load must not prune saved wells from other plates.

``restore`` narrows ``selected_wells`` / ``solo_wells`` to the current dataset
before applying them; ``snapshot`` used to persist only that narrowed set, so
loading a subset and saving irreversibly dropped every well the current dataset
didn't contain. ``restore`` now stashes the absent (out-of-dataset) wells and
``snapshot`` unions them back.
"""
from __future__ import annotations

from well_viewer.persistence import view_state as vs


def test_absent_wells_preserved_across_subset_load(loaded_app):
    # mini dataset contains A01, A02, B01, B02. The saved state references wells
    # from a plate that isn't currently loaded (Z99 selected, Y88 solo).
    app = loaded_app
    vs.restore(app, {
        "selected_wells": ["A01", "Z99"],
        "selection_visibility": {"solo_wells": ["B01", "Y88"]},
    })

    # The live selection only holds the wells the dataset actually has.
    assert "Z99" not in (app._app_state.selection.selected_wells or set())

    snap = vs.snapshot(app)
    # …but the persisted snapshot preserves the out-of-dataset wells.
    assert "Z99" in snap["selected_wells"]
    assert "A01" in snap["selected_wells"]
    assert "Y88" in snap["selection_visibility"]["solo_wells"]
    assert "B01" in snap["selection_visibility"]["solo_wells"]


def test_no_absent_wells_when_all_present(loaded_app):
    app = loaded_app
    vs.restore(app, {"selected_wells": ["A01", "A02"]})
    snap = vs.snapshot(app)
    # Nothing spurious added; only real dataset wells.
    assert set(snap["selected_wells"]) <= {"A01", "A02", "B01", "B02"}
