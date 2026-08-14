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
from well_viewer.domain import fold_change as _fc
from well_viewer.domain.control_ref import ControlRef
from well_viewer.domain.fold_change import resolve_control_wells
from well_viewer.features.barplot.collect import collect_bar_items
from well_viewer.features.lineplot.controller import collect_line_series

from tests.conftest import MockApp, make_agg


#: The well and the replicate set carry different values on purpose, so a bar
#: normalised against one cannot be mistaken for a bar normalised against the
#: other. 400 / 100 = 4.0; picking the wrong denominator gives 1.0.
_WELL_VALUE = 100.0
_SET_VALUE = 400.0
_EXPECTED_RATIO = _SET_VALUE / _WELL_VALUE


@pytest.fixture
def colliding_app():
    """A replicate set named `A01` whose wells are B01/B02, plus a real `A01`."""
    app = MockApp(
        per_well_pts={
            "A01": [make_agg(0.0, _WELL_VALUE)],
            "B01": [make_agg(0.0, _SET_VALUE)],
            "B02": [make_agg(0.0, _SET_VALUE)],
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
                          control=ControlRef.for_well("A01"))
    stored = colliding_app.state.plot.fc_control
    assert resolve_control_wells(colliding_app, stored) == ["A01"]


def test_the_stored_value_states_its_kind(colliding_app):
    """The stored value states its kind; the display text is not parsed back."""
    set_fold_change_state(colliding_app, vs_control_on=True,
                          control=ControlRef.for_well("A01"))
    assert colliding_app.state.plot.fc_control == ControlRef.for_well("A01")


def test_picking_the_repset_entry_still_resolves_to_the_repset(colliding_app):
    set_fold_change_state(colliding_app, vs_control_on=True,
                          control=ControlRef.for_replicate_set("A01"))
    stored = colliding_app.state.plot.fc_control
    assert resolve_control_wells(colliding_app, stored) == ["B01", "B02"]


def test_the_two_entries_resolve_differently(colliding_app):
    """The point of the suffix: same displayed token, different denominators."""
    set_fold_change_state(colliding_app, vs_control_on=True, control=ControlRef.for_well("A01"))
    as_well = resolve_control_wells(colliding_app, colliding_app.state.plot.fc_control)
    set_fold_change_state(colliding_app, vs_control_on=True,
                          control=ControlRef.for_replicate_set("A01"))
    as_set = resolve_control_wells(colliding_app, colliding_app.state.plot.fc_control)
    assert as_well != as_set


def test_a_well_with_no_name_collision_is_unaffected(colliding_app):
    """No suffix is offered when nothing collides, and nothing is invented."""
    set_fold_change_state(colliding_app, vs_control_on=True,
                          control=ControlRef.for_well("B01"))
    assert colliding_app.state.plot.fc_control == ControlRef.for_well("B01")
    assert resolve_control_wells(colliding_app, "B01") == ["B01"]


# ── the denominator the plot actually divides by ─────────────────────────────
#
# Everything above asserts the resolver. The resolver was never the broken
# part: it understood the suffix all along, while the denominator builders
# classified the label themselves and returned nothing for it. So the Bar tab
# NaNed every bar and the Line tab drew RAW values under a fold-change axis,
# and a suite that only ever asked the resolver stayed green through all of it.
# These assert the outbound value instead (CLAUDE.md §7.4 #1).

def test_the_denominator_series_resolves_the_suffixed_label(colliding_app):
    """``member_stats_series`` is what every fold-change path divides by."""
    series = _fc.member_stats_series(
        colliding_app, "A01 (well)", threshold=0.0,
        val_col="mean_gfp_intensity", use_sem=True,
        per_fov_spread=False, fluor_gates=None)
    assert series, "empty series means no denominator — every bar goes NaN"
    assert series[0.0][0] == _WELL_VALUE


def test_bars_divide_by_the_well_not_the_same_named_set(colliding_app):
    set_fold_change_state(colliding_app, vs_control_on=True,
                          control=ControlRef.for_well("A01"))
    _ug, items, _b = collect_bar_items(
        colliding_app, 0.0,
        fc_state=(True, colliding_app.state.plot.fc_control, False))
    assert [(i.key, i.mean, i.has_mean) for i in items] \
        == [("A01", _EXPECTED_RATIO, True)]


def test_curves_divide_by_the_well_not_the_same_named_set(colliding_app):
    """The Line tab's failure was the worse one — it drew raw values."""
    set_fold_change_state(colliding_app, vs_control_on=True,
                          control=ControlRef.for_well("A01"))
    series, _msg = collect_line_series(
        colliding_app, threshold=0.0, use_sem=True,
        fc_state=(True, colliding_app.state.plot.fc_control, False))
    point = series[0].points[0]
    assert point.mean == _EXPECTED_RATIO, (
        f"drew {point.mean} — the raw value is {_SET_VALUE}, "
        "so an un-normalised trace under a fold-change axis reads like this"
    )


def test_an_empty_set_does_not_shadow_a_loaded_well_of_the_same_name():
    """A replicate set whose wells are all unloaded must not claim the label.

    It falls through to the well lookup, which is what the resolver has always
    done; centralising the classification is only allowed to keep that.
    """
    app = MockApp(
        per_well_pts={"A01": [make_agg(0.0, _WELL_VALUE)]},
        selections=[{"name": "A01", "wells": ["Z11", "Z12"], "hidden": False}],
    )
    ctx = getattr(app, "_ctx", app)
    assert _fc.classify_control_label(ctx, "A01") == ("well", "A01")
    assert resolve_control_wells(ctx, "A01") == ["A01"]


def test_the_three_resolvers_agree_on_every_spelling(colliding_app):
    """The property whose absence caused this: one label, one meaning.

    ``resolve_control_wells``, ``_label_to_replicate_set`` and the denominator
    series builder each used to classify a label themselves. Any future fourth
    caller that re-decides it fails here.
    """
    for label in ("A01 (well)", "A01", "B01", "NOPE", ""):
        kind, _name = _fc.classify_control_label(colliding_app, label)
        wells = resolve_control_wells(colliding_app, label)
        rset = _fc._label_to_replicate_set(colliding_app, label)
        series = _fc.member_stats_series(
            colliding_app, label, threshold=0.0,
            val_col="mean_gfp_intensity", use_sem=True,
            per_fov_spread=False, fluor_gates=None)
        assert bool(wells) == (kind != ""), label
        assert (rset is not None) == (kind == "set"), label
        assert bool(series) == (kind != ""), label


# ── what a structured reference closes that a label cannot ───────────────────
#
# The suffix scheme could only ever be a convention, because replicate-set
# names are unrestricted free text: `_unique_name` strips whitespace, ensures
# uniqueness, and reserves no character. So the two cases below are not exotic
# — they are what the encoding permits. A `ControlRef` carries its kind beside
# the name instead of inside it, and neither case can arise.

def test_a_set_named_with_the_suffix_can_still_be_addressed():
    """A group literally called `A01 (well)`.

    As a label it is unreachable: the suffix branch fires first, so picking the
    set resolves to the well `A01` and the set can never be chosen at all.
    """
    app = MockApp(
        per_well_pts={"A01": [make_agg(0.0, _WELL_VALUE)],
                      "B01": [make_agg(0.0, _SET_VALUE)]},
        selections=[{"name": "A01 (well)", "wells": ["B01"], "hidden": False}],
    )
    ctx = getattr(app, "_ctx", app)

    # The label form, inverted — kept as a statement of what it does.
    assert _fc.resolve_control(ctx, "A01 (well)") == ("well", "A01")

    # The reference form says which it means, and is believed.
    assert _fc.resolve_control(
        ctx, ControlRef.for_replicate_set("A01 (well)")) == ("set", "A01 (well)")
    assert _fc.resolve_control_wells(
        ctx, ControlRef.for_replicate_set("A01 (well)")) == ["B01"]
    assert _fc.resolve_control_wells(ctx, ControlRef.for_well("A01")) == ["A01"]


def test_a_hidden_set_does_not_capture_a_well_of_the_same_name():
    """Hiding a group removes it from the picker but not from the resolvers.

    So the combo offered a bare `A01` the user read as the well, and it
    resolved to the hidden group's wells instead.
    """
    app = MockApp(
        per_well_pts={"A01": [make_agg(0.0, _WELL_VALUE)],
                      "B01": [make_agg(0.0, _SET_VALUE)],
                      "B02": [make_agg(0.0, _SET_VALUE)]},
        selections=[{"name": "A01", "wells": ["B01", "B02"], "hidden": True}],
    )
    ctx = getattr(app, "_ctx", app)

    assert _fc.resolve_control(ctx, "A01") == ("set", "A01")   # the label form
    assert _fc.resolve_control_wells(ctx, ControlRef.for_well("A01")) == ["A01"]


def test_the_combo_hands_back_a_reference_not_a_label():
    """The entries carry their kind, which is what makes the choice binding."""
    from well_viewer.core.fold_change_controls_view import _all_member_entries

    app = MockApp(
        per_well_pts={"A01": [make_agg(0.0, _WELL_VALUE)],
                      "B01": [make_agg(0.0, _SET_VALUE)]},
        selections=[{"name": "A01", "wells": ["B01"], "hidden": False}],
    )
    ctx = getattr(app, "_ctx", app)
    by_text = dict(_all_member_entries(ctx))
    assert by_text["A01"] == ControlRef.for_replicate_set("A01")
    assert by_text["A01 (well)"] == ControlRef.for_well("A01")


def test_the_suffix_never_reaches_a_figure_or_a_csv():
    """It is combo chrome. It used to be part of the stored value and leaked."""
    ref = ControlRef.for_well("A01")
    assert ref.combo_text(disambiguate=True) == "A01 (well)"
    assert ref.display_name == "A01"
