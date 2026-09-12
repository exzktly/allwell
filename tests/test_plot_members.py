"""The member list the editor shows must be the member list the plot draws.

``domain/plot_members.py`` exists so the per-sample fold-change editor and the
collectors answer "which members, in what order" the same way. The extraction
is not the guard — this equality is. Each test below collects real items and
asserts the keys match, so a change to either predicate that is not made to the
other fails here rather than showing up as a table row for a bar nobody drew.

The case that motivated it is ``test_the_keys_flip_to_wells_when_pooling_is_off``:
``ctx.runtime.barplot.current_keys()`` returns replicate-set names whenever any
set is active, but the collector emits well tokens once pooling is switched off.
An editor built on the runtime's list would have listed two rows while the
renderer normalized four members.
"""
from __future__ import annotations

import pytest

from review.domain.plot_members import (
    bar_member_keys,
    line_member_keys,
    member_wells,
    member_replicate_sets,
    plotted_member_keys,
)
from review.features.barplot.collect import collect_bar_items
from review.features.lineplot.controller import collect_line_series

from tests.conftest import MockApp, make_agg


def _ctx(*, selections=None, pool_reps=True, selected=None):
    """A context whose typed state is stable across accesses.

    ``MockApp._app_state`` rebuilds itself on every read, so a test that sets
    ``bar_pool_replicates`` on a fresh read and then hands ``app._ctx`` to the
    collector would be handing it the default. Grab one ctx and mutate that.
    """
    app = MockApp(
        per_well_pts={
            "A01": [make_agg(0.0, 100.0), make_agg(1.0, 150.0)],
            "A02": [make_agg(0.0, 110.0), make_agg(1.0, 160.0)],
            "B01": [make_agg(0.0, 200.0), make_agg(1.0, 250.0)],
            "B02": [make_agg(0.0, 210.0), make_agg(1.0, 260.0)],
        },
        selections=selections or [],
    )
    ctx = app._ctx
    ctx.state.plot.bar_pool_replicates = pool_reps
    if selected is not None:
        ctx.state.selection.selected_wells = set(selected)
    return ctx


_TWO_SETS = [
    {"name": "CTRL", "wells": ["A01", "A02"], "hidden": False},
    {"name": "TREAT", "wells": ["B01", "B02"], "hidden": False},
]


def _bar_keys_drawn(ctx):
    _use_groups, items, _band = collect_bar_items(ctx, 0.0)
    return [i.key for i in items]


def _line_keys_drawn(ctx):
    series, _msg = collect_line_series(ctx, threshold=0.0, use_sem=True)
    return [s.key for s in series]


# ── the equality, in all four combinations ───────────────────────────────────

@pytest.mark.parametrize("selections,pool_reps", [
    (_TWO_SETS, True),
    (_TWO_SETS, False),
    ([], True),
    ([], False),
])
def test_bar_member_keys_equal_the_keys_the_collector_emits(selections, pool_reps):
    ctx = _ctx(selections=selections, pool_reps=pool_reps)
    assert bar_member_keys(ctx) == _bar_keys_drawn(ctx)


@pytest.mark.parametrize("selections", [_TWO_SETS, []])
def test_line_member_keys_equal_the_keys_the_collector_emits(selections):
    ctx = _ctx(selections=selections)
    assert line_member_keys(ctx) == _line_keys_drawn(ctx)


# ── the specific shapes, stated rather than merely matched ───────────────────

def test_the_keys_are_set_names_when_pooling_is_on():
    ctx = _ctx(selections=_TWO_SETS, pool_reps=True)
    assert bar_member_keys(ctx) == ["CTRL", "TREAT"]


def test_the_keys_flip_to_wells_when_pooling_is_off():
    """The flip the runtime's own key list does not make."""
    ctx = _ctx(selections=_TWO_SETS, pool_reps=False)
    assert list(ctx.runtime.barplot.current_keys()) == ["CTRL", "TREAT"]
    assert bar_member_keys(ctx) == ["A01", "A02", "B01", "B02"]


def test_the_keys_are_wells_when_no_set_is_active():
    ctx = _ctx(selections=[], pool_reps=True)
    assert bar_member_keys(ctx) == ["A01", "A02", "B01", "B02"]


def test_a_well_in_two_sets_is_listed_once():
    ctx = _ctx(
        selections=[
            {"name": "CTRL", "wells": ["A01", "A02"], "hidden": False},
            {"name": "OVERLAP", "wells": ["A02", "B01"], "hidden": False},
        ],
        pool_reps=False,
    )
    keys = bar_member_keys(ctx)
    assert keys == ["A01", "A02", "B01"]
    assert keys == _bar_keys_drawn(ctx)


def test_an_unloaded_well_is_not_a_member():
    ctx = _ctx(
        selections=[{"name": "CTRL", "wells": ["A01", "Z99"], "hidden": False}],
        pool_reps=False,
    )
    assert bar_member_keys(ctx) == ["A01"]


def test_a_hidden_set_is_not_a_member():
    """Hidden sets are absent from the plot, so absent from the table."""
    ctx = _ctx(
        selections=[
            {"name": "CTRL", "wells": ["A01", "A02"], "hidden": False},
            {"name": "GONE", "wells": ["B01", "B02"], "hidden": True},
        ],
    )
    assert bar_member_keys(ctx) == ["CTRL"]
    assert line_member_keys(ctx) == ["CTRL"]


def test_the_bar_drag_order_is_honoured():
    ctx = _ctx(selections=_TWO_SETS)
    ctx.app._bar_order = ["TREAT", "CTRL"]
    ctx.state.plot.bar_order = ["TREAT", "CTRL"]
    assert bar_member_keys(ctx) == _bar_keys_drawn(ctx)


def test_the_line_drag_order_is_honoured():
    ctx = _ctx(selections=_TWO_SETS)
    ctx.state.plot.line_order_rsets = ["TREAT", "CTRL"]
    assert line_member_keys(ctx) == ["TREAT", "CTRL"] == _line_keys_drawn(ctx)


# ── dispatch and inheritance ─────────────────────────────────────────────────

def test_the_scope_name_dispatches():
    ctx = _ctx(selections=_TWO_SETS)
    assert plotted_member_keys(ctx, "bar") == bar_member_keys(ctx)
    assert plotted_member_keys(ctx, "line") == line_member_keys(ctx)


def test_an_unknown_scope_is_empty_rather_than_a_guess():
    assert plotted_member_keys(_ctx(selections=_TWO_SETS), "heatmap") == []


def test_member_wells_is_keyed_exactly_like_the_key_list():
    for selections in (_TWO_SETS, []):
        for pool in (True, False):
            ctx = _ctx(selections=selections, pool_reps=pool)
            assert list(member_wells(ctx, "bar")) == bar_member_keys(ctx)
    ctx = _ctx(selections=_TWO_SETS)
    assert list(member_wells(ctx, "line")) == line_member_keys(ctx)


def test_a_pooled_member_is_made_of_its_sets_wells():
    ctx = _ctx(selections=_TWO_SETS, pool_reps=True)
    assert member_wells(ctx, "bar") == {
        "CTRL": ["A01", "A02"], "TREAT": ["B01", "B02"]}


def test_an_unpooled_member_is_made_of_itself():
    ctx = _ctx(selections=_TWO_SETS, pool_reps=False)
    assert member_wells(ctx, "bar") == {
        "A01": ["A01"], "A02": ["A02"], "B01": ["B01"], "B02": ["B02"]}


def test_a_set_named_like_a_well_is_made_of_the_sets_wells_not_the_wells():
    """The whole reason the caller supplies this rather than a resolver
    guessing: the key ``A01`` is the set here, and its wells are B01/B02."""
    app = MockApp(
        per_well_pts={"A01": [make_agg(0.0, 1.0)],
                      "B01": [make_agg(0.0, 2.0)],
                      "B02": [make_agg(0.0, 3.0)]},
        selections=[{"name": "A01", "wells": ["B01", "B02"], "hidden": False}],
    )
    ctx = app._ctx
    ctx.state.plot.bar_pool_replicates = True
    assert member_wells(ctx, "bar") == {"A01": ["B01", "B02"]}


def test_member_wells_for_an_unknown_scope_is_empty():
    assert member_wells(_ctx(selections=_TWO_SETS), "heatmap") == {}


def test_member_replicate_sets_maps_each_well_to_its_owner():
    """What lets a well inherit its set's control once pooling is off."""
    ctx = _ctx(selections=_TWO_SETS, pool_reps=False)
    assert member_replicate_sets(ctx) == {
        "A01": "CTRL", "A02": "CTRL", "B01": "TREAT", "B02": "TREAT",
    }


def test_a_well_in_two_sets_is_owned_by_the_first():
    """One hop, and a deterministic one — the set order the plot walks."""
    ctx = _ctx(
        selections=[
            {"name": "CTRL", "wells": ["A01", "A02"], "hidden": False},
            {"name": "OVERLAP", "wells": ["A02", "B01"], "hidden": False},
        ],
        pool_reps=False,
    )
    assert member_replicate_sets(ctx)["A02"] == "CTRL"


def test_no_sets_means_no_inheritance():
    assert member_replicate_sets(_ctx(selections=[])) == {}


# ── the response scope ──────────────────────────────────────────────────────
#
# Response Curves always pools: a dose is a property of a condition, so one
# point per well at the same x would be a scatter of replicates rather than a
# curve. The half that is easy to leave un-swept is not the collector — it is
# ``_keys_are_replicate_sets``, which decides what a *key* resolves to when the
# per-sample control editor asks. A scope missing from that predicate answers
# "wells", and a key that resolves to no well simply loses its denominator: the
# control silently stops applying, with nothing red anywhere.

def test_the_response_keys_are_the_replicate_sets():
    ctx = _ctx(selections=_TWO_SETS, pool_reps=True)
    assert plotted_member_keys(ctx, "response") == ["CTRL", "TREAT"]


def test_the_response_keys_stay_sets_when_bar_pooling_is_off():
    """``bar_pool_replicates`` is the *Bar* tab's switch. This tab has no
    per-well mode, so its keys do not flip with it."""
    ctx = _ctx(selections=_TWO_SETS, pool_reps=False)
    assert bar_member_keys(ctx) == ["A01", "A02", "B01", "B02"]
    assert plotted_member_keys(ctx, "response") == ["CTRL", "TREAT"]


def test_a_response_key_resolves_to_its_sets_wells_not_to_a_well_of_that_name():
    """The assertion the ``scope == "line"`` spelling could not make.

    That spelling answered *False* for every scope it had not heard of, so each
    response key resolved to "the well with this name" — and ``CTRL`` is not a
    well, so it resolved to nothing at all. A member whose wells come back
    empty cannot be matched to a control, and the whole fold-change axis goes
    quiet on a plate that is perfectly well formed.
    """
    ctx = _ctx(selections=_TWO_SETS, pool_reps=True)
    wells = member_wells(ctx, "response")
    assert wells == {"CTRL": ["A01", "A02"], "TREAT": ["B01", "B02"]}


def test_the_response_scope_resolves_the_same_wells_with_bar_pooling_off():
    ctx = _ctx(selections=_TWO_SETS, pool_reps=False)
    assert member_wells(ctx, "response") == {
        "CTRL": ["A01", "A02"], "TREAT": ["B01", "B02"]}


def test_a_scope_nobody_registered_still_answers_empty():
    """Unknown scopes get no members rather than a guess."""
    ctx = _ctx(selections=_TWO_SETS, pool_reps=True)
    assert plotted_member_keys(ctx, "no-such-tab") == []


# ── the response scope with nothing grouped ─────────────────────────────────
#
# Response used to have no fallback here, on the reasoning that a loose well
# has no dose until it is in something the plate annotates. A sample variable
# is assigned per *well*, so that reasoning was wrong about where the
# annotation lives, and the tab drew nothing on a fully-annotated plate.

def test_the_response_keys_fall_back_to_the_lit_wells_with_no_groups():
    ctx = _ctx(selections=[], selected={"B01", "A02", "A01"})
    assert plotted_member_keys(ctx, "response") == ["A01", "A02", "B01"]


def test_an_ungrouped_response_key_resolves_to_its_own_well():
    """``_keys_are_replicate_sets`` has to answer *wells* here.

    Answering "sets" would resolve every key against a set roster that is
    empty, so each member would come back with no wells — and a member whose
    wells come back empty loses its fold-change denominator silently.
    """
    ctx = _ctx(selections=[], selected={"A01", "A02"})
    assert member_wells(ctx, "response") == {"A01": ["A01"], "A02": ["A02"]}


def test_a_group_still_wins_over_the_lit_wells_in_the_response_scope():
    ctx = _ctx(selections=_TWO_SETS, selected={"A01", "A02", "B01", "B02"})
    assert plotted_member_keys(ctx, "response") == ["CTRL", "TREAT"]
    assert member_wells(ctx, "response") == {
        "CTRL": ["A01", "A02"], "TREAT": ["B01", "B02"]}
