"""Picking the well entry in the Control combo must address the well.

`_all_member_labels` appends `" (well)"` to a well token whenever a replicate
set carries the same name, so the combo can offer both unambiguously.
`resolve_control_wells` honours it by trying the suffixed spelling against the
well tokens *before* it tries a replicate-set name — that ordering is the whole
mechanism.

The UI used to strip the suffix before storing the label, which made that first
branch unreachable: a user who picked the well entry `A01 (well)` got the
replicate set named `A01` as their denominator, with nothing on screen saying
so. Every bar in the figure was then divided by the wrong wells.

These tests pin the round trip, not just the resolver, because the resolver was
always correct in isolation — the defect lived in what the UI handed it.
"""
from __future__ import annotations

import types

import pytest

from well_viewer.core.fold_change_controls_view import (
    _all_member_labels,
    set_fold_change_state,
)
from well_viewer.domain.fold_change import resolve_control_wells

from tests.conftest import MockApp, make_agg


@pytest.fixture
def colliding_app():
    """A replicate set named `A01` whose wells are B01/B02, plus a real `A01`."""
    app = MockApp(
        per_well_pts={
            "A01": [make_agg(0.0, 111.0)],
            "B01": [make_agg(0.0, 222.0)],
            "B02": [make_agg(0.0, 222.0)],
        },
        selections=[{"name": "A01", "wells": ["B01", "B02"], "hidden": False}],
    )
    ctx = getattr(app, "_ctx", app)
    # ``set_fold_change_state`` announces on the bus and mirrors the change
    # into every registered scope's widgets. MockApp has neither, and the scope
    # registry is module-global — so whether the widget sync runs at all
    # depends on which other tests registered a scope earlier in the session.
    # Stub both, or these tests pass alone and fail in the full suite.
    ctx.bus = types.SimpleNamespace(
        plot_options_changed=types.SimpleNamespace(emit=lambda *_a: None))
    ctx.widgets = types.SimpleNamespace(get_or_none=lambda _name: None)
    return ctx


def test_the_combo_offers_both_spellings(colliding_app):
    members = _all_member_labels(colliding_app)
    assert "A01" in members, "the replicate set"
    assert "A01 (well)" in members, "the well, disambiguated"


def test_picking_the_well_entry_resolves_to_the_well(colliding_app):
    """The regression. Stored bare, this resolved to ['B01', 'B02']."""
    set_fold_change_state(colliding_app, vs_control_on=True,
                          control_label="A01 (well)")
    stored = colliding_app.state.plot.fc_control_label
    assert resolve_control_wells(colliding_app, stored) == ["A01"]


def test_the_suffix_survives_into_state(colliding_app):
    """Storing the bare form is what broke it, so pin the stored spelling."""
    set_fold_change_state(colliding_app, vs_control_on=True,
                          control_label="A01 (well)")
    assert colliding_app.state.plot.fc_control_label == "A01 (well)"


def test_picking_the_repset_entry_still_resolves_to_the_repset(colliding_app):
    set_fold_change_state(colliding_app, vs_control_on=True, control_label="A01")
    stored = colliding_app.state.plot.fc_control_label
    assert resolve_control_wells(colliding_app, stored) == ["B01", "B02"]


def test_the_two_entries_resolve_differently(colliding_app):
    """The point of the suffix: same displayed token, different denominators."""
    set_fold_change_state(colliding_app, vs_control_on=True, control_label="A01 (well)")
    as_well = resolve_control_wells(colliding_app, colliding_app.state.plot.fc_control_label)
    set_fold_change_state(colliding_app, vs_control_on=True, control_label="A01")
    as_set = resolve_control_wells(colliding_app, colliding_app.state.plot.fc_control_label)
    assert as_well != as_set


def test_a_well_with_no_name_collision_is_unaffected(colliding_app):
    """No suffix is offered when nothing collides, and nothing is invented."""
    set_fold_change_state(colliding_app, vs_control_on=True, control_label="B01")
    assert colliding_app.state.plot.fc_control_label == "B01"
    assert resolve_control_wells(colliding_app, "B01") == ["B01"]
