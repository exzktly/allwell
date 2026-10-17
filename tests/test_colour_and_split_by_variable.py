"""Colour by and Split by: decoration that moves no numbers.

These two band controls change the ink a bar takes and the labelled band it
sits in, and nothing else. ``group_by_variable`` alone decides what a member
*is*, so a figure with both of these set draws exactly the bars it drew before
either was — which is the property most worth pinning, because a "decoration"
that quietly re-pooled the wells would be the worst possible version of this
feature.

The other half is §7.5 #5. ``collect_bar_items`` builds items in four branches
and the decoration is applied once over the finished list, so no branch can be
left out. The test that matters there is not that one branch works; it is that
every branch does.
"""

from __future__ import annotations

import pytest

from review.domain import sample_variables as sv
from tests import _cvd
from review.domain import variable_series as vs
from review.features.barplot._decorate import (
    bar_legend_entries, decorate_bar_items,
)
from review.features.barplot.collect import (
    clustered_x_positions, cluster_centers, collect_bar_items,
)

from tests.conftest import MockApp, make_agg

_WELLS = ("A01", "A02", "B01", "B02")

_TWO_SETS = [
    {"name": "CTRL", "wells": ["A01", "A02"], "hidden": False},
    {"name": "TREAT", "wells": ["B01", "B02"], "hidden": False},
]


def _line(name="Cell line", pairs=(("HeLa", None), ("U2OS", None))):
    return sv.Variable(id=name.lower().replace(" ", "_"), name=name,
                       kind=sv.TEXT, unit="",
                       values=tuple(sv.VariableValue(f"{name[0]}{i}", lab, num)
                                    for i, (lab, num) in enumerate(pairs)))


def _ctx(*, selections=None, variable=None, assignments=None,
         colour_by="", split_by="", pool_reps=True):
    app = MockApp(
        per_well_pts={w: [make_agg(0.0, 100.0 + 10 * i)]
                      for i, w in enumerate(_WELLS)},
        selections=selections if selections is not None else _TWO_SETS,
    )
    ctx = app._ctx
    ctx.state.plot.bar_pool_replicates = pool_reps
    ctx.state.plot.colour_by_variable = colour_by
    ctx.state.plot.split_by_variable = split_by
    if variable is not None:
        ctx.state.selection.variables = sv.VariableSet(
            variables=(variable,),
            assignments={variable.id: dict(assignments or {})})
    return ctx


def _items(ctx):
    _use_groups, items, _band = collect_bar_items(ctx, 0.0)
    return items


# ── neither control moves a number ──────────────────────────────────────────

def test_colouring_by_a_variable_changes_no_value():
    """The property the whole design rests on: decoration is not grouping."""
    variable = _line()
    kwargs = dict(selections=_TWO_SETS, variable=variable,
                  assignments={"A01": "C0", "A02": "C0",
                               "B01": "C1", "B02": "C1"})
    plain = _items(_ctx(**kwargs))
    coloured = _items(_ctx(**kwargs, colour_by=variable.id))

    assert [i.key for i in plain] == [i.key for i in coloured]
    assert [i.mean for i in plain] == [i.mean for i in coloured]


def test_splitting_by_a_variable_changes_no_value():
    variable = _line()
    kwargs = dict(selections=_TWO_SETS, variable=variable,
                  assignments={"A01": "C0", "A02": "C0",
                               "B01": "C1", "B02": "C1"})
    plain = _items(_ctx(**kwargs))
    split = _items(_ctx(**kwargs, split_by=variable.id))

    assert sorted(i.key for i in plain) == sorted(i.key for i in split)
    assert ({i.key: i.mean for i in plain}
            == {i.key: i.mean for i in split})


# ── colour ──────────────────────────────────────────────────────────────────

def test_members_sharing_a_value_share_an_ink():
    variable = _line()
    items = _items(_ctx(variable=variable, colour_by=variable.id,
                        assignments={"A01": "C0", "A02": "C0",
                                     "B01": "C1", "B02": "C1"}))
    by_key = {i.key: i.color for i in items}
    assert by_key["CTRL"] != by_key["TREAT"], by_key


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _separation(colours) -> float:
    """The closest two of *colours* are in RGB. Distinct is not distinguishable.

    The first version of this test asserted only that the colours differed,
    which every strategy satisfies — including the one that gave three values
    ``#E56AFB`` and ``#DC81EA``, two strings a reader sees as one colour. A
    mutation swapping the strategy left it green, which is how the strategy
    that was actually *worse* came to be in the code with a comment explaining
    why it was better.
    """
    values = list(colours)
    return min(
        sum((a - b) ** 2 for a, b in zip(_rgb(x), _rgb(y))) ** 0.5
        for i, x in enumerate(values) for y in values[i + 1:])


@pytest.mark.parametrize("n,floor", [(2, 150.0), (3, 150.0), (6, 100.0)])
def test_the_palette_entries_are_far_enough_apart(n, floor):
    """Measured, not assumed. The plate palette is already ordered so that
    consecutive entries are far apart; spreading picks across all 96 of them
    to avoid "adjacent hues" collapses three values to 30 apart."""
    variable = _line(pairs=tuple((f"v{i}", None) for i in range(n)))
    palette = vs.value_colors(variable)

    assert len(set(palette.values())) == n
    assert _separation(palette.values()) >= floor, sorted(palette.values())


#: The floor a set of *n* value inks must clear under **every** view — normal
#: and the three dichromacies — measured the way ``tests/_cvd.py`` measures.
#: Lower as *n* grows because the space is finite; a set that fell below the
#: last row would be a legend the reader has to guess at.
CVD_FLOORS = ((2, 250.0), (3, 180.0), (4, 160.0), (6, 90.0), (8, 75.0),
              (16, 45.0))


@pytest.mark.parametrize("n,floor", CVD_FLOORS)
@pytest.mark.parametrize("view", _cvd.VIEWS)
def test_the_value_inks_stay_apart_for_a_dichromat_too(n, floor, view):
    """The separation test above asks whether a trichromat can tell the inks
    apart, which is one reader in twelve short of the question.

    The palette's own head — the first n entries, which this used to take —
    measures 116 apart at six values for a trichromat and **28** under
    protanopia, below the 30 this file records as one colour to any reader; at
    eight values deuteranopia collapses it to **10**. *Colour by* carries no
    second channel saying the same thing, so those are not two values drawn
    badly, they are two values drawn identically with nothing on screen to say
    so. ``VALUE_PICKS`` is what the measurement chose.
    """
    variable = _line(pairs=tuple((f"v{i}", None) for i in range(n)))
    inks = list(vs.value_colors(variable).values())

    assert len(set(inks)) == n
    assert _cvd.separation(inks, view) >= floor, (
        view, sorted(inks), _cvd.separation(inks, view))


def test_more_values_than_picks_keeps_going_rather_than_repeating_an_ink():
    """``VALUE_PICKS`` is sixteen long and the palette is ninety-six.

    Cycling the picks would repeat an ink at the seventeenth value — the exact
    failure the picks exist to prevent, reintroduced by the mechanism meant to
    prevent it. The order runs on through the rest of the palette instead.
    """
    variable = _line(pairs=tuple((f"v{i}", None) for i in range(40)))
    inks = list(vs.value_colors(variable).values())

    assert len(set(inks)) == 40, "an ink repeated inside one variable"


def test_the_picks_are_a_roster_of_real_palette_entries():
    """A pick past the end of the palette would be dropped silently, shortening
    the measured set to something no test above would notice."""
    from review.domain.plate_layout import WELL_COLORS

    assert len(set(vs.VALUE_PICKS)) == len(vs.VALUE_PICKS), "a pick is repeated"
    assert all(0 <= i < len(WELL_COLORS) for i in vs.VALUE_PICKS)


def test_the_two_ways_of_having_no_value_are_different_inks():
    """"You filled these inconsistently" and "these were never filled" are
    different mistakes with different fixes, so they are not one grey.

    Measured, not merely compared. This asserted `!=` twenty-five lines below
    `_separation`, whose own docstring says distinct is not distinguishable —
    and the two greys it passed were **23** apart in RGB, less than the 30 the
    workplan records as "one colour to any reader" and a fifth of the floor this
    file holds six real values to. §7.8 face two, in the file that documents it.
    """
    assert vs.MIXED_LABEL != vs.UNASSIGNED_LABEL
    assert _separation([vs.MIXED_COLOR, vs.UNASSIGNED_COLOR]) >= 100, (
        "the two non-values are one grey to a reader"
    )


def test_neither_non_value_ink_is_mistakable_for_a_real_value():
    """The other half: far apart from each other is not enough if either lands
    on top of a value's own colour."""
    variable = _line()
    values = list(vs.value_colors(variable).values())

    for ink, name in ((vs.MIXED_COLOR, "MIXED"), (vs.UNASSIGNED_COLOR, "UNASSIGNED")):
        assert _separation(values + [ink]) >= 60, name


def _two_variable_ctx():
    """A plate carrying two variables, which is what the reuse rule is about."""
    cell = _line()
    dose = _line("Dose", (("0", 0.0), ("1", 1.0), ("10", 10.0)))
    ctx = _ctx(variable=cell)
    ctx.state.selection.variables = sv.VariableSet(
        variables=(cell, dose), assignments={})
    return ctx, cell, dose


def test_the_variables_tab_and_the_plots_ink_a_value_the_same():
    """One colour vocabulary, asserted across the two modules that draw it.

    The tab had its own implementation — the same palette walked with a stride
    of seven rather than one — so every value but the first was one colour on
    the plate that assigned it and a different colour on the bar chart. Asserted
    on the mapping rather than on "both are non-empty", because two wrong
    mappings are both non-empty.

    Driven on the **second** variable of a set. Both modules agreed while both
    indexed from zero, so a one-variable fixture passes whether or not either
    honours the plate's other values — the shape §7.8 calls a fixture in which
    the right and the wrong answer are the same bytes.
    """
    from review.features.home.variables import controller as vc

    ctx, _cell, dose = _two_variable_ctx()
    vset = ctx.state.selection.variables

    assert vc._value_colours(ctx, dose) == vs.colors_for(vset, dose.id)


def test_no_two_values_on_a_plate_share_an_ink():
    """Reported: "colors should not be reused between variables. They currently
    are."

    ``value_colors`` indexed every variable from zero, so `Cell line`'s two
    values took the same two inks as `Dose`'s first two — one blue meaning
    `HeLa` in the chip row and `0 µM` on the plate a click later. The offset is
    the count of values every earlier variable took, so the palette is shared
    out across the whole set.
    """
    _ctx_, cell, dose = _two_variable_ctx()
    vset = sv.VariableSet(variables=(cell, dose), assignments={})

    cell_inks = list(vs.colors_for(vset, cell.id).values())
    dose_inks = list(vs.colors_for(vset, dose.id).values())
    assert len(cell_inks) == 2 and len(dose_inks) == 3, "premise: both are inked"
    assert not (set(cell_inks) & set(dose_inks)), (
        f"{sorted(set(cell_inks) & set(dose_inks))} means two different values")
    every = cell_inks + dose_inks
    assert len(set(every)) == len(every), "an ink repeats within the plate"


def test_a_variables_inks_still_start_at_the_measured_picks():
    """The offset must not cost the first variable the picks.

    ``VALUE_PICKS`` is a measured roster — the entries a dichromat can still
    tell apart — and a per-variable *stride* would have pushed every variable
    after the first past it into the unmeasured tail. Cumulative keeps the head
    in play for the plate as a whole.
    """
    cell = _line()
    vset = sv.VariableSet(variables=(cell,), assignments={})
    assert (list(vs.colors_for(vset, cell.id).values())
            == list(vs.value_colors(cell, offset=0).values()))
    assert vs.color_offset(vset, cell.id) == 0


def test_a_member_whose_wells_disagree_takes_the_mixed_ink():
    variable = _line()
    ctx = _ctx(variable=variable, colour_by=variable.id,
               assignments={"A01": "C0", "A02": "C1",
                            "B01": "C1", "B02": "C1"})
    inks = vs.member_inks(ctx, "bar", variable.id)
    assert inks["CTRL"] == vs.MIXED_COLOR
    assert inks["TREAT"] != vs.MIXED_COLOR


def test_a_member_holding_an_unfilled_well_takes_the_unassigned_ink():
    variable = _line()
    ctx = _ctx(variable=variable, colour_by=variable.id,
               assignments={"A01": "C0", "B01": "C1", "B02": "C1"})
    inks = vs.member_inks(ctx, "bar", variable.id)
    assert inks["CTRL"] == vs.UNASSIGNED_COLOR


def test_no_variable_chosen_leaves_the_plate_colours_alone():
    """``{}`` rather than a dict of defaults, so a caller can tell the two
    apart without comparing every entry."""
    ctx = _ctx()
    assert vs.member_inks(ctx, "bar", "") == {}
    plain = _items(ctx)
    assert len({i.color for i in plain}) == 2


# ── the legend ──────────────────────────────────────────────────────────────

def test_the_legend_lists_only_the_values_on_screen():
    """A legend naming six doses over a figure showing two is a claim about
    what is in front of the reader, and the reader counts the legend."""
    variable = _line(pairs=(("HeLa", None), ("U2OS", None), ("HEK", None)))
    ctx = _ctx(variable=variable, colour_by=variable.id,
               assignments={"A01": "C0", "A02": "C0",
                            "B01": "C1", "B02": "C1"})
    entries = bar_legend_entries(ctx, _items(ctx))

    assert [label for label, _c in entries] == ["HeLa", "U2OS"]


def test_the_legend_keeps_the_users_value_order():
    variable = _line(pairs=(("U2OS", None), ("HeLa", None)))
    ctx = _ctx(variable=variable, colour_by=variable.id,
               assignments={"A01": "C0", "A02": "C0",
                            "B01": "C1", "B02": "C1"})
    assert [lab for lab, _c in bar_legend_entries(ctx, _items(ctx))] == [
        "U2OS", "HeLa"]


def test_plate_colour_mode_has_no_legend():
    """The plate *is* the legend, which is the argument for it being default."""
    assert bar_legend_entries(_ctx(), []) == []


# ── split ───────────────────────────────────────────────────────────────────

def test_split_puts_each_band_in_one_contiguous_run():
    """``cluster_centers`` centres a header over the positions it saw, so a
    band split across the axis draws its header in the gap between its halves,
    over bars belonging to something else."""
    variable = _line()
    ctx = _ctx(
        selections=[
            {"name": "a", "wells": ["A01"], "hidden": False},
            {"name": "b", "wells": ["B01"], "hidden": False},
            {"name": "c", "wells": ["A02"], "hidden": False},
            {"name": "d", "wells": ["B02"], "hidden": False},
        ],
        variable=variable, split_by=variable.id,
        assignments={"A01": "C0", "A02": "C0", "B01": "C1", "B02": "C1"})
    items = _items(ctx)

    bands = [i.group_key for i in items]
    assert bands == sorted(bands, key=["HeLa", "U2OS"].index), bands
    centres = dict(cluster_centers(items, clustered_x_positions(items)))
    assert set(centres) == {"HeLa", "U2OS"}


def test_the_bands_come_out_in_the_users_value_order():
    variable = _line(pairs=(("U2OS", None), ("HeLa", None)))
    ctx = _ctx(variable=variable, split_by=variable.id,
               assignments={"A01": "C1", "A02": "C1",
                            "B01": "C0", "B02": "C0"})
    items = _items(ctx)
    assert [i.group_key for i in items][0] == "U2OS"


def test_the_non_values_sort_after_every_real_one():
    """A `no value` band wedged between 1 µM and 3 µM would read as a dose."""
    variable = _line()
    ctx = _ctx(variable=variable, split_by=variable.id,
               assignments={"A01": "C0", "A02": "C0"})
    items = _items(ctx)
    assert items[-1].group_key == vs.UNASSIGNED_LABEL
    assert items[0].group_key == "HeLa"


def test_a_band_is_named_rather_than_left_blank():
    """An empty cluster header reads as a header that failed to draw."""
    variable = _line()
    ctx = _ctx(variable=variable, split_by=variable.id, assignments={})
    assert all(i.group_key == vs.UNASSIGNED_LABEL for i in _items(ctx))


def test_drag_order_survives_inside_a_band():
    """Sorting on the band alone would be a second ordering rule silently
    overriding the user's."""
    variable = _line()
    ctx = _ctx(
        selections=[
            {"name": "a", "wells": ["A01"], "hidden": False},
            {"name": "b", "wells": ["A02"], "hidden": False},
        ],
        variable=variable, split_by=variable.id,
        assignments={"A01": "C0", "A02": "C0"})
    assert [i.key for i in _items(ctx)] == ["a", "b"]


# ── what the split had to give up ───────────────────────────────────────────

def test_replacing_an_existing_band_is_reported():
    """The bar tab already uses bands for timepoints and for wells. Taking
    those headers off a figure the user arranged that way, with nothing saying
    where they went, is the substitution §7 forbids."""
    variable = _line()
    ctx = _ctx(variable=variable, split_by=variable.id,
               assignments={"A01": "C0", "A02": "C0",
                            "B01": "C1", "B02": "C1"})
    items = _items(ctx)
    for item in items:
        item.group_key = "t=24"

    _out, notice = decorate_bar_items(ctx, items)

    assert "replaced" in notice and "t=24" in notice


def test_a_figure_with_no_bands_to_lose_says_nothing():
    """Saying it anyway would train the reader to skip the line."""
    variable = _line()
    ctx = _ctx(variable=variable, split_by=variable.id,
               assignments={"A01": "C0", "A02": "C0",
                            "B01": "C1", "B02": "C1"})
    items = _items(ctx)
    for item in items:
        item.group_key = ""

    _out, notice = decorate_bar_items(ctx, items)
    assert notice == ""


def test_the_notice_is_cleared_on_a_pass_that_has_nothing_to_say():
    """A notice left from the previous layout is the stale-sentence failure,
    moved off the status bar and onto the figure where it is harder to spot."""
    variable = _line()
    ctx = _ctx(variable=variable, split_by=variable.id,
               assignments={"A01": "C0"})
    slice_ = ctx.state.feature_slice("bar")
    slice_.split_notice = "left over from last time"

    ctx.state.plot.split_by_variable = ""
    ctx.state.plot.colour_by_variable = ""
    decorate_bar_items(ctx, _items(ctx))

    assert slice_.split_notice == ""


def _two_tp_ctx(*, group_mode, pool_reps=True):
    """Two timepoints, CTRL -> HeLa and TREAT -> U2OS, split by cell line."""
    variable = _line()
    app = MockApp(
        per_well_pts={w: [make_agg(0.0, 100.0 + i), make_agg(24.0, 200.0 + i)]
                      for i, w in enumerate(_WELLS)},
        selections=_TWO_SETS,
    )
    ctx = app._ctx
    ctx.state.plot.bar_pool_replicates = pool_reps
    ctx.state.plot.bar_group_mode = group_mode
    ctx.state.plot.split_by_variable = variable.id
    ctx.state.selection.variables = sv.VariableSet(
        variables=(variable,),
        assignments={variable.id: {"A01": "C0", "A02": "C0",
                                   "B01": "C1", "B02": "C1"}})
    return ctx


@pytest.mark.parametrize("group_mode, pool_reps, expected", [
    ("well", True, [("HeLa", "CTRL t=0"), ("HeLa", "CTRL t=24"),
                    ("U2OS", "TREAT t=0"), ("U2OS", "TREAT t=24")]),
    ("timepoint", True, [("HeLa", "CTRL t=0"), ("HeLa", "CTRL t=24"),
                         ("U2OS", "TREAT t=0"), ("U2OS", "TREAT t=24")]),
    ("well", False, [("HeLa", "A01 [CTRL] t=0"), ("HeLa", "A01 [CTRL] t=24"),
                     ("HeLa", "A02 [CTRL] t=0"), ("HeLa", "A02 [CTRL] t=24"),
                     ("U2OS", "B01 [TREAT] t=0"), ("U2OS", "B01 [TREAT] t=24"),
                     ("U2OS", "B02 [TREAT] t=0"), ("U2OS", "B02 [TREAT] t=24")]),
])
def test_a_split_multi_timepoint_figure_keeps_every_bar_named(
        group_mode, pool_reps, expected):
    """The reported bug: split a multi-timepoint figure and every bar read only
    its timepoint.

    In "Group by well" mode the collector puts the sample in the band header and
    only ``t=…`` on the tick; *Split by* overwrote the header with the value, so
    the bars read ``t=0, t=24, t=0, t=24`` with nothing naming whose. "Group by
    timepoint" lost the other half — two bars both called ``CTRL``. Asserted on
    the exact ``(band, tick)`` pairs, which the pre-fix code fails in both
    modes: a property like "the ticks differ" is satisfied by ``t=0``/``t=24``.
    """
    _use_groups, items, _band = collect_bar_items(
        _two_tp_ctx(group_mode=group_mode, pool_reps=pool_reps), [0.0, 24.0])

    assert [(i.group_key, i.display) for i in items] == expected


def test_a_split_batch_figure_keeps_every_bar_named():
    """Batch Export builds the same two-row labels, so it has the same hole."""
    from review.features.barplot.collect import (
        FC_STATE_OFF, collect_bar_items_for_group,
    )

    ctx = _two_tp_ctx(group_mode="well")
    group = _group(members=[("CTRL", ["A01", "A02"]), ("TREAT", ["B01", "B02"])])
    items = collect_bar_items_for_group(
        ctx, group, [0.0, 24.0], val_col="gfp_mean_intensity", threshold=0.0,
        use_sem=True, per_fov_spread=False, fc_state=FC_STATE_OFF,
        pool_replicates=True, group_mode="well")

    assert [(i.group_key, i.display) for i in items] == [
        ("HeLa", "CTRL t=0"), ("HeLa", "CTRL t=24"),
        ("U2OS", "TREAT t=0"), ("U2OS", "TREAT t=24")]


def test_a_single_timepoint_split_leaves_the_ticks_alone():
    """With one timepoint the header only repeated the tick; nothing to carry."""
    variable = _line()
    ctx = _ctx(variable=variable, split_by=variable.id,
               assignments={"A01": "C0", "A02": "C0",
                            "B01": "C1", "B02": "C1"})

    assert [i.display for i in _items(ctx)] == ["CTRL", "TREAT"]


# ── a second split level ────────────────────────────────────────────────────
#
# The fixture is built so the right and wrong answers differ (§7.8 rule 2):
# the dose values are declared low-first, but the wells are assigned
# high-dose-first in member order. A decorator that skipped the inner sort
# keeps member order (A01 before B01) and is caught; one that sorted the inner
# level by label or by member order is caught too.

def _nested_ctx(*, split=True, then=True, timepoints=(0.0,)):
    line = _line()
    dose = sv.Variable(id="dose", name="Dose", kind=sv.NUMBER, unit="µM",
                       values=(sv.VariableValue("d0", "0", 0.0),
                               sv.VariableValue("d1", "10", 10.0)))
    app = MockApp(
        per_well_pts={w: [make_agg(t, 100.0 + 10 * i + t) for t in timepoints]
                      for i, w in enumerate(_WELLS)},
        selections=[{"name": w, "wells": [w], "hidden": False}
                    for w in _WELLS],
    )
    ctx = app._ctx
    ctx.state.plot.bar_pool_replicates = True
    ctx.state.plot.split_by_variable = line.id if split else ""
    ctx.state.plot.sub_split_by_variable = dose.id if then else ""
    ctx.state.selection.variables = sv.VariableSet(
        variables=(line, dose),
        assignments={
            line.id: {"A01": "C0", "B01": "C0", "A02": "C1", "B02": "C1"},
            dose.id: {"A01": "d1", "A02": "d1", "B01": "d0", "B02": "d0"},
        })
    return ctx


def test_then_by_nests_bands_inside_each_split_band():
    """Every bar in its line band, then its dose sub-band, in the variables'
    own orders — HeLa before U2OS, 0 µM before 10 µM inside each."""
    items = _items(_nested_ctx())

    assert [(i.group_key, i.subgroup_key, i.key) for i in items] == [
        ("HeLa", "0 µM", "B01"), ("HeLa", "10 µM", "A01"),
        ("U2OS", "0 µM", "B02"), ("U2OS", "10 µM", "A02"),
    ]


def test_a_variable_with_a_unit_is_banded_in_its_own_order():
    """``band_order`` listed bare labels (``0``) while the bands are drawn with
    the unit (``0 µM``), so every lookup missed and a Dose split came out in
    member order — 10 µM before 0 µM on this fixture."""
    ctx = _nested_ctx(then=False)
    ctx.state.plot.split_by_variable = "dose"

    assert [(i.group_key, i.key) for i in _items(ctx)] == [
        ("0 µM", "B01"), ("0 µM", "B02"), ("10 µM", "A01"), ("10 µM", "A02")]


def test_a_variable_with_a_unit_gets_a_legend():
    """The same mismatch emptied the Colour-by legend for any variable with a
    unit: bars inked by dose, and no key to the inks."""
    ctx = _nested_ctx(split=False, then=False)
    ctx.state.plot.colour_by_variable = "dose"
    palette = vs.colors_for(ctx.state.selection.variables, "dose")

    assert bar_legend_entries(ctx, _items(ctx)) == [
        ("0 µM", palette["d0"]), ("10 µM", palette["d1"])]


def test_then_by_changes_no_value():
    """Decoration, not grouping: the same bars with the same numbers."""
    plain = _items(_nested_ctx(split=False, then=False))
    nested = _items(_nested_ctx())

    assert ({i.key: i.mean for i in plain}
            == {i.key: i.mean for i in nested})


def test_then_by_without_split_by_is_inert():
    """Bands inside nothing are just bands; promoting the second level to the
    first would draw a layout nobody chose."""
    plain = _items(_nested_ctx(split=False, then=False))
    items = _items(_nested_ctx(split=False))

    assert [i.subgroup_key for i in items] == [""] * 4
    assert ([(i.key, i.group_key, i.display) for i in items]
            == [(i.key, i.group_key, i.display) for i in plain])


def test_sub_bands_get_a_narrower_gap_than_bands():
    """Half a unit between sub-bands inside a band, and the band gap widened by
    the same half unit so it stays the larger: 1.5 against 0.5."""
    from review.features.barplot.collect import SUBGROUP_GAP

    xs = clustered_x_positions(_items(_nested_ctx()))

    assert SUBGROUP_GAP == 0.5
    assert xs == [0.0, 1.5, 4.0, 5.5]


def test_a_sub_band_label_repeated_across_bands_gets_a_header_in_each():
    """``0 µM`` inside HeLa and ``0 µM`` inside U2OS are two sub-bands with one
    name. Pooling by label — what ``cluster_centers`` does for bands — would
    centre one header between them, over U2OS's 10 µM bar."""
    from review.features.barplot.collect import subcluster_centers

    items = _items(_nested_ctx())
    xs = clustered_x_positions(items)

    assert list(subcluster_centers(items, xs)) == [
        ("0 µM", 0.0), ("10 µM", 1.5), ("0 µM", 4.0), ("10 µM", 5.5)]
    assert list(cluster_centers(items, xs)) == [("HeLa", 0.75), ("U2OS", 4.75)]


def test_a_sub_band_label_straddling_two_bands_is_two_headers():
    """The case where keying a sub-band on its label alone goes wrong: HeLa
    ends with ``0 µM`` and U2OS begins with it, so the two runs sit side by
    side. Keyed without the band, they merge into one header centred in the
    gap between the bands."""
    from review.features.barplot.collect import (
        BarItem, clustered_x_positions, subcluster_centers,
    )

    def bar(key, band, sub):
        return BarItem(key=key, display=key, color="#888888", mean=1.0,
                       spread=0.0, has_mean=True, frac=0.5, frac_spread=0.0,
                       has_frac=True, n_above=1.0, n_above_spread=0.0,
                       group_key=band, subgroup_key=sub)

    items = [bar("B01", "HeLa", "0 µM"),
             bar("B02", "U2OS", "0 µM"), bar("A02", "U2OS", "10 µM")]
    xs = clustered_x_positions(items)

    assert xs == [0.0, 2.5, 4.0]
    assert list(subcluster_centers(items, xs)) == [
        ("0 µM", 0.0), ("0 µM", 2.5), ("10 µM", 4.0)]


def test_then_by_naming_the_split_variable_draws_no_second_row():
    """Only a saved file can carry it — the combo never offers it — and it
    would repeat the band row word for word."""
    ctx = _nested_ctx(then=False)
    ctx.state.plot.sub_split_by_variable = ctx.state.plot.split_by_variable

    assert [i.subgroup_key for i in _items(ctx)] == [""] * 4


@pytest.mark.parametrize("then", [False, True])
def test_every_panel_carries_the_band_headers_clear_of_its_title(then):
    """Reported: with Split by on, only the first panel named its groups — the
    Fraction and Events panels below it showed the bands as gaps between bars
    and nothing else. ``render_bar_items`` (shared by the tab and Batch Export)
    draws the headers over every panel, and each panel's title clears them.
    Measured on the rendered extents of the real Batch Export figure."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    fig = _batch_figure(_nested_ctx(then=then, timepoints=(0.0, 24.0)))
    FigureCanvasAgg(fig).draw()
    renderer = fig.canvas.get_renderer()
    headers = {"HeLa", "U2OS", "0 µM", "10 µM"}
    panels = [ax for ax in fig.axes if ax.get_title()]
    assert len(panels) == 3, [ax.get_title() for ax in fig.axes]
    for ax in panels:
        drawn = [t for t in ax.texts if t.get_text() in headers]
        assert sorted(t.get_text() for t in drawn) == sorted(
            ["HeLa", "U2OS"] + (["0 µM", "10 µM"] * 2 if then else [])), ax.get_title()
        top = max(t.get_window_extent(renderer).y1 for t in drawn)
        assert top < ax.title.get_window_extent(renderer).y0, ax.get_title()


def test_the_band_row_is_drawn_above_the_sub_band_row():
    """Both rows reach the axes, band labels lifted a line above sub-band
    labels — asserted on the artists, not on the centres behind them."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from review.features.barplot._annotations import annotate_cluster_headers

    items = _items(_nested_ctx())
    xs = clustered_x_positions(items)
    fig, ax = plt.subplots()
    try:
        annotate_cluster_headers(items, xs, ax)
        # An Annotation's ``get_position`` is its text offset, not its anchor,
        # so the anchor is read from ``xy`` where there is one.
        drawn = {(t.get_text(),
                  round(float(getattr(t, "xy", t.get_position())[0]), 2),
                  tuple(getattr(t, "xyann", (0, 0))))
                 for t in ax.texts}
    finally:
        plt.close(fig)

    assert drawn == {
        ("HeLa", 0.75, (0, 11)), ("U2OS", 4.75, (0, 11)),
        ("0 µM", 0.0, (0, 0)), ("10 µM", 1.5, (0, 0)),
        ("0 µM", 4.0, (0, 0)), ("10 µM", 5.5, (0, 0)),
    }


def test_one_split_level_draws_one_header_row():
    """No sub-bands, no lift: the single-level figure is unchanged."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from review.features.barplot._annotations import annotate_cluster_headers

    items = _items(_nested_ctx(then=False))
    xs = clustered_x_positions(items)
    fig, ax = plt.subplots()
    try:
        annotate_cluster_headers(items, xs, ax)
        drawn = sorted((t.get_text(), tuple(getattr(t, "xyann", (0, 0))))
                       for t in ax.texts)
    finally:
        plt.close(fig)

    assert drawn == [("HeLa", (0, 0)), ("U2OS", (0, 0))]


def _draw_tab(ctx):
    """The tab's own grouped-bar renderer on Agg axes; returns what it drew.

    ``draw_grouped_bar_mode`` is the entry point the Bar tab calls, so this
    covers the figure-level half the collector tests cannot: the title's
    clearance over the header rows and the per-panel cluster footers. The two
    stubs are the only Qt it reaches — a widget registry with nothing in it and
    a canvas whose repaint is a no-op.
    """
    from types import SimpleNamespace

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from review.features.barplot.renderer import draw_grouped_bar_mode

    ctx.widgets = SimpleNamespace(get_or_none=lambda _name: None)
    ctx.state.plot.bar_group_mode = "well"
    fig, (ax_mean, ax_frac, ax_n) = plt.subplots(3)
    ctx.runtime.barplot.fig = fig
    ctx.runtime.barplot.canvas = SimpleNamespace(draw_idle=lambda: None)
    try:
        draw_grouped_bar_mode(
            ctx, ax_mean=ax_mean, ax_frac=ax_frac, ax_n=ax_n, active_rsets=[],
            target_ts=[0.0, 24.0], tp_str="0", threshold=0.5, band_lbl="SEM",
            use_sem=True)
        footers = [t.get_text() for ax in (ax_mean, ax_frac, ax_n)
                   for t in ax.texts
                   if tuple(getattr(t, "xyann", (0, 0))) == (0, -32)]
        pad_pt = ax_mean.titleOffsetTrans.get_matrix()[1][2] * 72 / fig.dpi
        headers = [t.get_text() for t in ax_mean.texts
                   if getattr(t, "xy", t.get_position())[1] == 1.02]
    finally:
        plt.close(fig)
    return SimpleNamespace(footers=footers, pad_pt=round(pad_pt, 3),
                           headers=headers)


def test_a_nested_split_figure_clears_both_header_rows_and_drops_footers():
    """Two header rows need the title lifted past both (30 pt, not 18). And
    the well footers under each panel go: the split displaced those clusters
    and moved each sample onto its own tick, so a footer would only repeat the
    band header once per panel."""
    drawn = _draw_tab(_nested_ctx(timepoints=(0.0, 24.0)))

    assert drawn.pad_pt == 30.0
    assert drawn.footers == []
    assert drawn.headers == ["HeLa", "U2OS",
                             "0 µM", "10 µM", "0 µM", "10 µM"]


def test_a_split_that_produced_no_bands_keeps_the_footers():
    """The footers go only when the split actually displaced the well clusters.
    A *Split by* naming a variable the plate no longer has (a saved file, a
    deleted variable not yet refreshed away) produces no bands, so the wells
    are still the clusters and still want their footers. Keying the gate on
    the setting rather than on what the decorator did would drop them."""
    ctx = _nested_ctx(split=False, then=False, timepoints=(0.0, 24.0))
    ctx.state.plot.split_by_variable = "a-variable-this-plate-does-not-have"

    drawn = _draw_tab(ctx)

    assert drawn.footers == ["A01", "A02", "B01", "B02"] * 3


def test_an_unsplit_multi_timepoint_figure_keeps_its_footers():
    """The other side of the gate: with no split the well clusters are the
    user's own layout and each keeps its footer on every panel."""
    drawn = _draw_tab(_nested_ctx(split=False, then=False,
                                  timepoints=(0.0, 24.0)))

    assert drawn.pad_pt == 18.0
    assert drawn.footers == ["A01", "A02", "B01", "B02"] * 3


def test_restoring_a_view_saved_before_then_by_clears_it():
    """A snapshot written before *Then by* existed has ``split_by_variable``
    and no ``sub_split_by_variable``. Restore assigns only the keys present, so
    the live sub-split survived and a saved one-level figure reopened with two
    levels. Saved Plots replays views through this same call."""
    from review.persistence import view_state

    ctx = _nested_ctx()
    view_state.restore(ctx, {"group_by_variable": "", "colour_by_variable": "",
                             "split_by_variable": "cell_line"})

    assert ctx.state.plot.sub_split_by_variable == ""
    assert [i.subgroup_key for i in _items(ctx)] == [""] * 4


def test_a_restore_that_names_no_split_leaves_then_by_to_its_own_key():
    """The other side: a snapshot that is silent on both keys says nothing
    about the layout, and must not clear a setting it never described."""
    from review.persistence import view_state

    ctx = _nested_ctx()
    view_state.restore(ctx, {"group_by_variable": ""})

    assert ctx.state.plot.sub_split_by_variable == "dose"


def _batch_figure(ctx):
    """Batch Export's real ``_render_bar_group_figure`` for the nested
    fixture, on a panel built without its Qt form — the method reads only the
    ctx, the run's axes spec and three form accessors, stubbed here."""
    from types import SimpleNamespace

    from review.features.batch_export.bar_panel import BarBatchExportPanel

    ctx.widgets = SimpleNamespace(get_or_none=lambda _name: None)
    panel = BarBatchExportPanel.__new__(BarBatchExportPanel)
    panel._ctx = ctx
    panel._run_axes = None
    panel._selected_export_channel = lambda: "gfp"
    panel._selected_export_metric_key = lambda *_a: "mean_intensity"
    panel._export_val_col_for = lambda *_a: "gfp_mean_intensity"
    group = _group(name="Plate 1 group",
                   members=[(w, [w]) for w in _WELLS])
    return panel._render_bar_group_figure(
        group, [0.0, 24.0], ["0", "24"], 0.5, True, "SEM",
        pool_replicates=True, group_mode="well")


@pytest.mark.parametrize("then", [False, True])
def test_batch_header_rows_clear_the_panel_title_and_the_suptitle(then):
    """Batch Export styles its panel title with ``apply_ax_style``'s 6 pt pad
    and never reached the tab renderer's, so the header rows were printed over
    the title — and *Then by*'s lifted band row went up into the suptitle.
    Measured on the rendered extents of the real figure."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    fig = _batch_figure(_nested_ctx(then=then, timepoints=(0.0, 24.0)))
    FigureCanvasAgg(fig).draw()
    renderer = fig.canvas.get_renderer()
    ax = fig.axes[0]
    headers = {"HeLa", "U2OS", "0 µM", "10 µM"}
    header_boxes = [t.get_window_extent(renderer) for t in ax.texts
                    if t.get_text() in headers]
    title = ax.title.get_window_extent(renderer)
    suptitle = fig._suptitle.get_window_extent(renderer)

    assert len(header_boxes) == (6 if then else 2)
    top_of_headers = max(b.y1 for b in header_boxes)
    assert top_of_headers < title.y0, (top_of_headers, title.y0)
    assert top_of_headers < suptitle.y0


# ── every branch of the collector is decorated ──────────────────────────────

@pytest.mark.parametrize("pool_reps", [True, False])
def test_both_pooling_modes_are_decorated(pool_reps):
    """The reason the pass is applied once over the finished list: the
    collector builds items in four branches, and a figure where some bars
    honour the control and some do not is worse than one where none does."""
    variable = _line()
    ctx = _ctx(variable=variable, colour_by=variable.id, pool_reps=pool_reps,
               assignments={"A01": "C0", "A02": "C0",
                            "B01": "C1", "B02": "C1"})
    items = _items(ctx)
    assert items
    inks = vs.member_inks(ctx, "bar", variable.id)
    for item in items:
        assert item.color == inks[item.key], item.key


def test_a_plate_with_no_groups_is_decorated_too():
    variable = _line()
    ctx = _ctx(selections=[], variable=variable, colour_by=variable.id,
               assignments={w: "C0" for w in _WELLS})
    ctx.state.selection.selected_wells = set(_WELLS)
    items = _items(ctx)
    if items:
        assert len({i.color for i in items}) == 1


# ── the controls, pressed ───────────────────────────────────────────────────
#
# §7.6: asserting a control is offered is not asserting it works. Both of these
# are combos whose ``currentIndexChanged`` is the whole mechanism, and this
# repository has shipped a control connected to nothing.

def _wire_band(qtbot, ctx):
    """Real decoration combos on *ctx*, wired the way ``centre_view`` wires
    them; returns ``(combos by field, emitted redraw requests)``."""
    from types import SimpleNamespace

    from widgets.labeled_combo import LabeledComboBox

    from review.core.group_by_controller import (
        DECORATION_CONTROLS, refresh_decoration_combos, set_decoration_variable,
    )

    emitted = []
    ctx.bus = SimpleNamespace(
        plot_options_changed=SimpleNamespace(emit=emitted.append))

    # ``MockApp``'s ctx carries no widget registry; the controller reaches its
    # combos through one, so the test supplies the same two calls it uses.
    registry = {}
    ctx.widgets = SimpleNamespace(
        register=lambda name, widget: registry.setdefault(name, widget),
        get_or_none=registry.get)

    combos = {}
    for field, name, _label in DECORATION_CONTROLS:
        combo = LabeledComboBox(name)
        qtbot.addWidget(combo)
        ctx.widgets.register(name, combo)
        combo.currentIndexChanged.connect(
            lambda *_a, f=field, c=combo: set_decoration_variable(
                ctx, f, c.currentData() or ""))
        combos[field] = combo
    refresh_decoration_combos(ctx)
    return combos, emitted


@pytest.fixture
def band(qtbot):
    """The real combos over a one-variable plate."""
    from types import SimpleNamespace

    variable = _line()
    ctx = _ctx(variable=variable)
    combos, emitted = _wire_band(qtbot, ctx)
    return SimpleNamespace(ctx=ctx, combos=combos, variable=variable,
                           emitted=emitted)


@pytest.fixture
def two_variable_band(qtbot):
    """The real combos over a plate with a cell line and a dose, nothing set."""
    from types import SimpleNamespace

    ctx = _nested_ctx(split=False, then=False)
    combos, emitted = _wire_band(qtbot, ctx)
    return SimpleNamespace(ctx=ctx, combos=combos, emitted=emitted)


def _offered(combo):
    return [combo.itemData(i) for i in range(combo.count())]


def test_both_combos_list_the_plates_variables(band):
    for combo in band.combos.values():
        data = [combo.itemData(i) for i in range(combo.count())]
        assert data == ["", band.variable.id], data


def test_choosing_a_variable_writes_the_state_and_asks_for_a_redraw(band):
    combo = band.combos["colour_by_variable"]
    combo.setCurrentIndex(combo.findData(band.variable.id))

    assert band.ctx.state.plot.colour_by_variable == band.variable.id
    assert band.emitted, "no redraw was requested, so only this tab would change"


def test_the_split_combo_is_wired_to_its_own_field(band):
    """Two controls differing only in a string is how one of them ends up
    writing the other's state."""
    combo = band.combos["split_by_variable"]
    combo.setCurrentIndex(combo.findData(band.variable.id))

    assert band.ctx.state.plot.split_by_variable == band.variable.id
    assert band.ctx.state.plot.colour_by_variable == ""


def test_then_by_is_disabled_and_cleared_without_split_by(two_variable_band):
    """The second level only exists inside the first. Clearing *Split by*
    clears *Then by* and greys it out; choosing *Then by* with no *Split by*
    does not stick. Driven through the combos' own signals."""
    ctx, combos = two_variable_band.ctx, two_variable_band.combos
    split, then = combos["split_by_variable"], combos["sub_split_by_variable"]
    assert not then.isEnabled()

    split.setCurrentIndex(split.findData("cell_line"))
    assert then.isEnabled()
    then.setCurrentIndex(then.findData("dose"))
    assert ctx.state.plot.sub_split_by_variable == "dose"

    split.setCurrentIndex(0)
    assert ctx.state.plot.sub_split_by_variable == ""
    assert then.currentIndex() == 0
    assert not then.isEnabled()

    then.setEnabled(True)  # reach it anyway, as a stale signal would
    then.setCurrentIndex(then.findData("dose"))
    assert ctx.state.plot.sub_split_by_variable == ""


def test_then_by_never_offers_the_split_by_variable(two_variable_band):
    """Sub-bands by the band's own variable are one sub-band per band with the
    band's own name — a second header row repeating the first. The list follows
    *Split by* as it changes, and a *Then by* that ends up naming the same
    variable is cleared rather than drawn."""
    ctx, combos = two_variable_band.ctx, two_variable_band.combos
    split, then = combos["split_by_variable"], combos["sub_split_by_variable"]

    split.setCurrentIndex(split.findData("cell_line"))
    assert _offered(then) == ["", "dose"]
    then.setCurrentIndex(then.findData("dose"))

    split.setCurrentIndex(split.findData("dose"))
    assert _offered(then) == ["", "cell_line"]
    assert ctx.state.plot.sub_split_by_variable == ""
    assert then.currentIndex() == 0


def test_a_deleted_variable_is_cleared_from_both_controls(band):
    """Left set, the next redraw decorates by a variable nothing offers."""
    from review.core.group_by_controller import refresh_decoration_combos

    band.ctx.state.plot.colour_by_variable = band.variable.id
    band.ctx.state.plot.split_by_variable = band.variable.id
    band.ctx.state.selection.variables = sv.VariableSet()

    refresh_decoration_combos(band.ctx)

    assert band.ctx.state.plot.colour_by_variable == ""
    assert band.ctx.state.plot.split_by_variable == ""


def test_an_unknown_field_is_refused_rather_than_set(band):
    from review.core.group_by_controller import set_decoration_variable

    set_decoration_variable(band.ctx, "group_by_variable", band.variable.id)
    assert band.ctx.state.plot.group_by_variable == "", (
        "the decoration setter wrote a field outside its roster")


def test_resetting_the_variables_clears_both_decorations():
    """Left set, a reset leaves the plot decorated by a variable that no longer
    exists — the same combo-and-plot disagreement the Group by clear exists to
    prevent, in a place nobody would think to look.

    Driven through the reset scope rather than by calling the setter, because
    the defect would be a *missing* line there and only the real scope has it.
    """
    from review.persistence import reset_scopes

    variable = _line()
    ctx = _ctx(variable=variable, colour_by=variable.id, split_by=variable.id)
    ctx.state.plot.group_by_variable = variable.id

    reset_scopes._clear_sample_variables(ctx)

    assert ctx.state.plot.colour_by_variable == ""
    assert ctx.state.plot.split_by_variable == ""
    assert ctx.state.plot.group_by_variable == ""


def test_the_reset_reads_the_controllers_roster_rather_than_naming_fields():
    """A fourth control added to the roster and not to the reset would survive
    it. The roster is the single list, so it cannot drift."""
    import inspect

    from review.persistence import reset_scopes

    source = inspect.getsource(reset_scopes._clear_sample_variables)
    assert "DECORATION_CONTROLS" in source
    assert "colour_by_variable" not in source, (
        "the reset names the fields itself instead of reading the roster")


# ── the surfaces that render without a ctx ──────────────────────────────────

def test_a_render_with_no_ctx_is_undecorated_rather_than_a_crash():
    """``render_bar_items`` takes ``ctx=None`` on purpose — the Batch Export
    panel renders these panels without one, which its own docstring says.

    Twenty-six tests went red on this, all of them ``AttributeError`` inside
    the decoration pass. A caller with no ctx has no plot state and therefore
    no setting, and the figure it draws is the one the app drew before these
    controls existed: whole and coherent, not a decorated one with pieces
    missing.
    """
    from review.features.barplot._annotations import annotate_decorations
    from review.features.barplot._decorate import (
        bar_legend_entries, decorate_bar_items,
    )

    items = _items(_ctx())
    out, notice = decorate_bar_items(None, items)

    assert out == items and notice == ""
    assert bar_legend_entries(None, items) == []
    annotate_decorations(None, items, [], None, headers=False)


def test_a_ctx_without_a_bar_slice_still_decorates():
    """The notice has nowhere to go; the ink and the bands still apply.

    Losing the whole decoration because one place to park a sentence is absent
    would be the tail wagging the figure.
    """
    variable = _line()
    ctx = _ctx(variable=variable, colour_by=variable.id,
               assignments={"A01": "C0", "A02": "C0",
                            "B01": "C1", "B02": "C1"})
    ctx.state.feature_slice = lambda _name: None

    items = _items(ctx)
    assert len({i.color for i in items}) == 2


def _batch_items(ctx, group, *, pool_replicates=True):
    """One batch group's bars, through the real batch collector."""
    from review.features.barplot.collect import (
        FC_STATE_OFF, collect_bar_items_for_group,
    )
    return collect_bar_items_for_group(
        ctx, group, 0.0, val_col="gfp_mean_intensity", threshold=0.0,
        use_sem=True, per_fov_spread=False, fc_state=FC_STATE_OFF,
        pool_replicates=pool_replicates)


def _group(name="G", members=(), solo=()):
    from review.domain.batch_models import BarGroup, ReplicateSet

    return BarGroup(name,
                    members=[ReplicateSet(n, list(w)) for n, w in members],
                    solo_wells=list(solo))


def test_batch_export_bar_figures_are_decorated_too():
    """A batch figure that ignored the control would disagree with the tab it
    mirrors, on a plate the user set up once.

    Asserted against the **variable's own palette**, not against "the colours
    differ". The batch collector already gives each member a different ink from
    its own palette, so a distinctness check passes with the decoration deleted
    — which is exactly what the first version of this test did, and a mutation
    removing the whole call left all of it green.
    """
    variable = _line()
    # The values are assigned **against** the member order on purpose. The batch
    # collector inks its members from the same ``WELL_COLORS`` in member order,
    # so with CTRL holding the first value the decorated and undecorated inks
    # are byte-identical and nothing can tell them apart — which is how three
    # mutations, including deleting the decoration outright, left the first
    # version of these tests green.
    ctx = _ctx(variable=variable, colour_by=variable.id,
               assignments={"A01": "C1", "A02": "C1",
                            "B01": "C0", "B02": "C0"})
    group = _group(members=[("CTRL", ["A01", "A02"]), ("TREAT", ["B01", "B02"])])
    palette = vs.value_colors(variable)

    by_key = {i.key: i.color for i in _batch_items(ctx, group)}

    assert by_key == {"CTRL": palette["C1"], "TREAT": palette["C0"]}, by_key


def test_the_batch_keys_are_the_groups_own_not_the_tabs():
    """The reason the key space is passed rather than looked up.

    This group's members are named `CTRL` and `TREAT`; the tab's members here
    are the *wells*, because no selections are defined. Matching the batch
    items against the tab's key space would colour nothing at all — silently,
    since a key that resolves to no member simply keeps the colour it had.
    """
    from review.domain.variable_series import member_inks

    variable = _line()
    ctx = _ctx(selections=[], variable=variable, colour_by=variable.id,
               assignments={"A01": "C1", "A02": "C1",
                            "B01": "C0", "B02": "C0"})
    group = _group(members=[("CTRL", ["A01", "A02"]), ("TREAT", ["B01", "B02"])])

    from review.domain.variable_series import member_inks

    tab_keys = set(member_inks(ctx, "bar", variable.id))
    assert "CTRL" not in tab_keys, (
        "the fixture no longer distinguishes the two key spaces")

    palette = vs.value_colors(variable)
    by_key = {i.key: i.color for i in _batch_items(ctx, group)}

    assert by_key == {"CTRL": palette["C1"], "TREAT": palette["C0"]}, (
        "the batch bars were matched against the tab's keys, so they kept "
        f"whatever ink the batch palette gave them: {by_key}")


def test_the_batch_and_the_tab_agree_on_what_a_value_looks_like():
    """One definition of "what colour is HeLa", not two."""

    variable = _line()
    ctx = _ctx(variable=variable, colour_by=variable.id,
               assignments={"A01": "C1", "A02": "C1",
                            "B01": "C0", "B02": "C0"})
    group = _group(members=[("CTRL", ["A01", "A02"]), ("TREAT", ["B01", "B02"])])

    palette = vs.value_colors(variable)
    tab = {i.key: i.color for i in _items(ctx)}
    batch = {i.key: i.color for i in _batch_items(ctx, group)}

    assert tab == batch, (tab, batch)
    # Against the palette too, so the pair cannot agree by both being wrong.
    assert set(batch.values()) == {palette["C0"], palette["C1"]}


def test_the_batch_expanded_mode_is_keyed_on_wells_and_still_decorated():
    """With pooling off the batch keys become well tokens. The map is built
    from the same ``members`` list either way, so it follows the flip."""
    variable = _line()
    ctx = _ctx(variable=variable, colour_by=variable.id,
               assignments={"A01": "C1", "A02": "C1",
                            "B01": "C0", "B02": "C0"})
    group = _group(members=[("CTRL", ["A01", "A02"]), ("TREAT", ["B01", "B02"])])

    palette = vs.value_colors(variable)
    by_key = {i.key: i.color for i in
              _batch_items(ctx, group, pool_replicates=False)}

    assert set(by_key) == {"A01", "A02", "B01", "B02"}
    assert by_key == {"A01": palette["C1"], "A02": palette["C1"],
                      "B01": palette["C0"], "B02": palette["C0"]}, by_key


def test_a_batch_solo_well_is_decorated_by_its_own_value():
    variable = _line()
    ctx = _ctx(variable=variable, colour_by=variable.id,
               assignments={"A01": "C1", "B01": "C0"})
    group = _group(solo=["A01", "B01"])
    palette = vs.value_colors(variable)

    by_key = {i.key: i.color for i in _batch_items(ctx, group)}
    assert by_key == {"A01": palette["C1"], "B01": palette["C0"]}, by_key


def test_grey_bars_get_no_colour_by_legend(monkeypatch):
    """A legend over bars that are not carrying the variable's inks is a key to
    a colouring the figure does not have.

    ``redraw_bars`` passes ``mono_bar_color`` for every bar in grey mode, which
    discards the ``item.color`` ``decorate_bar_items`` just set — so the figure
    is uniformly grey and the legend was still drawn, offering a reader swatches
    to map onto bars that carry none. Worse than no legend, because it is
    confidently wrong.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from review.features.barplot import _annotations as ann

    drawn: list = []
    monkeypatch.setattr(ann, "bar_legend_entries",
                        lambda ctx, items, **kw: drawn.append(items) or [("HeLa", "#112233")])

    fig, ax = plt.subplots()
    try:
        for mode, expect_legend in (("plate", True), ("grey", False)):
            ax.clear()
            drawn.clear()
            ctx = _ctx_with_mode(mode)
            ann.annotate_colour_legend(ctx, ax)
            assert bool(ax.get_legend()) is expect_legend, mode
    finally:
        plt.close(fig)


def _ctx_with_mode(mode):
    from types import SimpleNamespace

    from review.core.app_state import AppState

    state = AppState()
    state.plot.bar_color_mode = mode
    state.plot.colour_by_variable = "dose"
    return SimpleNamespace(state=state)


def test_an_exported_figure_gets_a_legend_in_the_groups_own_key_space():
    """Batch Export builds its own figure, so it draws its own annotations —
    and it did not.

    ``collect_bar_items_for_group`` decorates the items, so exported bars came
    out re-inked by the Colour-by variable and re-banded by Split-by, while the
    legend naming those inks and the notice saying the split replaced this
    group's own bands were drawn only by the on-tab renderer. A figure carrying
    a colouring with no key is the half-decorated figure ``_decorate``'s own
    comment calls worse than none.

    Driven through the annotator with the group's own member map, and asserted
    on the **labels and colours** that reach the axes — a legend built against
    the tab's key space would name the members whose names happen to coincide
    and omit the rest, so "a legend exists" is not the property under test.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from review.features.barplot._annotations import annotate_decorations
    from review.features.barplot.collect import batch_wells_by_key

    variable = _line()
    ctx = _ctx(variable=variable, colour_by=variable.id,
               assignments={"A01": "C1", "A02": "C1",
                            "B01": "C0", "B02": "C0"})
    group = _group(members=[("CTRL", ["A01", "A02"]), ("TREAT", ["B01", "B02"])])
    items = _batch_items(ctx, group)
    wells_by_key = batch_wells_by_key(
        [("CTRL", "CTRL", ["A01", "A02"], "CTRL", "#000000"),
         ("TREAT", "TREAT", ["B01", "B02"], "TREAT", "#000000")])
    palette = vs.value_colors(variable)

    fig, ax = plt.subplots()
    try:
        annotate_decorations(ctx, items, [], ax, headers=False,
                             wells_by_key=wells_by_key)
        legend = ax.get_legend()
        assert legend is not None, "an exported figure carries no key to its inks"
        drawn = {t.get_text(): h.get_facecolor()
                 for t, h in zip(legend.get_texts(), legend.legend_handles)}
    finally:
        plt.close(fig)

    from matplotlib.colors import to_rgba
    assert set(drawn) == {"HeLa", "U2OS"}, drawn
    assert drawn["HeLa"] == to_rgba(palette["C0"])
    assert drawn["U2OS"] == to_rgba(palette["C1"])


def test_the_export_panel_reaches_the_annotator():
    """A source-level check, and labelled as one.

    The test above proves the annotator does the right thing when the export
    panel calls it; only a full-app Batch Export run proves the panel *does*
    call it, and that fixture costs a real window. This reads the call site
    instead — weaker than driving it, stronger than nothing, and honest about
    which it is. §7.6's lesson is that the gap between "offered" and "works"
    is where the bugs live, so the seam is pinned above and the wiring here.
    """
    from pathlib import Path as _P

    import review.features.batch_export.bar_panel as bp

    text = _P(bp.__file__).read_text()
    assert "annotate_decorations(" in text
    assert "batch_wells_by_key(" in text, (
        "the legend would be labelled in the tab's key space, not the group's"
    )


def test_a_value_reads_the_same_on_a_legend_as_on_an_axis():
    """One spelling per value, unit included.

    ``variable_replicate_sets`` names its members with ``display_label`` (unit
    included) while the legend used the bare ``value.label`` — so a figure
    grouped by Dose had x labels reading ``1 µM`` and a figure coloured by the
    same Dose had a legend of bare numbers. On a saved PNG of the second there
    was nothing anywhere saying µM rather than nM.

    Asserted against ``display_label`` rather than against the literal, so the
    two cannot drift apart again by editing either one.
    """
    import review.domain.sample_variables as sv

    variable = sv.from_block({
        "schema_version": 1,
        "variables": [{
            "id": "d", "name": "Dose", "kind": "number", "unit": "µM",
            "values": [{"id": "v1", "label": "1"}, {"id": "v2", "label": "10"}],
        }],
        "assignments": {},
    }).by_id("d")

    for value in variable.values:
        assert vs.value_label(variable, value.id) == variable.display_label(value.id)
    assert "µM" in vs.value_label(variable, variable.values[0].id)


def test_the_per_cell_figures_name_their_clusters_over_every_panel():
    """The violin and beeswarm figures draw their own cluster headers; they
    were on the top panel only, under a title padded as if there were none."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from review.features.barplot.renderer import _annotate_group_key_headers

    fig, (ax_mean, ax_frac) = plt.subplots(2, 1)
    try:
        pad = _annotate_group_key_headers((ax_mean, ax_frac), [0.0, 1.0, 3.0],
                                          ["t=0", "t=0", "t=24"])
        drawn = [sorted((t.get_text(), round(t.get_position()[0], 2)) for t in ax.texts)
                 for ax in (ax_mean, ax_frac)]
    finally:
        plt.close(fig)
    assert drawn == [[("t=0", 0.5), ("t=24", 3.0)]] * 2
    assert pad == 18
