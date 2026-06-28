"""Pure-Python tests for ``sync_preview_well_for_image_tabs``.

Regression for the Image Browser well-picker bug: with replicate sets the
active selection is a rep-set, and the sync helper used to override a valid
explicit preview pick with the rep-set's first well — so clicking a single
well in the Segmentation picker never stuck. The fix honors a valid explicit
pick; it only falls back to the selection / first well when there is none.
"""

from __future__ import annotations

from types import SimpleNamespace

from well_viewer.features.preview.controller import sync_preview_well_for_image_tabs


def _make_app(*, selected_well, sel_wells, well_paths):
    """Minimal stub exposing only what the sync helper touches."""
    state = SimpleNamespace(
        preview=SimpleNamespace(selected_well=selected_well),
        dataset=SimpleNamespace(well_paths={w: object() for w in well_paths}),
        selection=SimpleNamespace(current_selection_id="sel"),
    )
    sel = {"wells": list(sel_wells)}
    return SimpleNamespace(
        _app_state=state,
        _sel_by_id=lambda _id: sel,
        # Sort key only needs to be deterministic for the first-well fallback.
        _parse_rc=lambda label: (label[:1], label[1:]),
    )


def test_honors_valid_explicit_pick_over_rep_set_first_well():
    # User explicitly picked B2; the active selection is a replicate set whose
    # first member is A1. The pick must survive untouched.
    app = _make_app(
        selected_well="B2",
        sel_wells=["A1", "B2"],
        well_paths=["A1", "B2"],
    )
    sync_preview_well_for_image_tabs(app)
    assert app._app_state.preview.selected_well == "B2"


def test_falls_back_to_selection_when_no_valid_pick():
    # No (or stale) current pick: take the active selection's first loaded well.
    app = _make_app(
        selected_well=None,
        sel_wells=["A1", "B2"],
        well_paths=["A1", "B2"],
    )
    sync_preview_well_for_image_tabs(app)
    assert app._app_state.preview.selected_well == "A1"


def test_falls_back_to_first_well_when_selection_empty():
    app = _make_app(
        selected_well="ZZ",   # not a loaded well
        sel_wells=[],
        well_paths=["B2", "A1"],
    )
    sync_preview_well_for_image_tabs(app)
    # Sorted by _parse_rc → A1 sorts before B2.
    assert app._app_state.preview.selected_well == "A1"
