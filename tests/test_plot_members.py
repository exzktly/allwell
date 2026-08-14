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

from well_viewer.domain.plot_members import (
    bar_member_keys,
    line_member_keys,
    member_replicate_sets,
    plotted_member_keys,
)
from well_viewer.features.barplot.collect import collect_bar_items
from well_viewer.features.lineplot.controller import collect_line_series

from tests.conftest import MockApp, make_agg


def _ctx(*, selections=None, pool_reps=True):
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
