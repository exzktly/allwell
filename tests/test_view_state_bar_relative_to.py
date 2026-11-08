"""The Bar tab's *Then relative to* is saved, restored and reset by id."""
from __future__ import annotations

import types

from review.persistence import view_state as vs

from tests.conftest import MockApp, make_agg


def _ctx():
    app = MockApp(per_well_pts={"A01": [make_agg(0.0, 1.0)]}, selections=[])
    ctx = app._ctx
    # The bar block also reads widgets (the timepoint combo, the violin slider);
    # none exist here, which is the headless case it already handles.
    ctx.widgets = types.SimpleNamespace(get_or_none=lambda _n: None)
    return ctx


def test_the_rule_survives_a_save_and_restore_by_id():
    ctx = _ctx()
    bar = ctx.state.feature_slice("bar")
    bar.rel_variable_id, bar.rel_value_id, bar.rel_within = "treat", "veh", ["rna"]
    saved = vs._snapshot_bar(ctx)
    assert saved["relative_to"] == {"variable_id": "treat", "value_id": "veh",
                                    "within": ["rna"]}

    bar.rel_variable_id, bar.rel_value_id, bar.rel_within = "", "", []
    vs._apply_bar(ctx, saved)
    assert (bar.rel_variable_id, bar.rel_value_id, bar.rel_within) == (
        "treat", "veh", ["rna"])


def test_a_view_saved_without_it_restores_as_off():
    ctx = _ctx()
    bar = ctx.state.feature_slice("bar")
    bar.rel_variable_id, bar.rel_value_id = "treat", "veh"
    vs._apply_bar(ctx, {"swarm": False})
    assert not bar.relative_to_on()
    assert "relative_to" not in vs._snapshot_bar(ctx)


def test_reset_turns_it_off():
    # ``_reset_bar`` takes the window and reads ``_app_state``; a stand-in that
    # holds one state object is the window as far as this function is concerned.
    state = _ctx().state
    bar = state.feature_slice("bar")
    bar.rel_variable_id, bar.rel_value_id, bar.rel_within = "treat", "veh", ["rna"]
    vs._reset_bar(types.SimpleNamespace(_app_state=state))
    assert (bar.rel_variable_id, bar.rel_value_id, bar.rel_within) == ("", "", [])
