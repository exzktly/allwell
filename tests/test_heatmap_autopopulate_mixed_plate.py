"""Auto-populate lays out a mixed plate whole — replicate sets *and* solo wells.

Replicate mode does not mean every well is in a group. The plate expresses its
active set as the visible groups' wells **plus** the wells toggled on
individually (``selection.active_solo_wells``), which is what
``selection_controller.on_sidebar_plate_well_activated`` writes when a click
lands on a well no group claims, and what the sidebar paints as selected.

The Heat Map's *Auto-populate from selection* read the replicate sets alone.
So on a plate of two replicate pairs and three solo wells it answered with the
grouped half and dropped the other — four cells out of seven, with nothing
said and a full-looking grid to show for it. The plan now comes from
``data_loading.plot_group_well_lists``, the one function that decides which
wells a plot is about, whose own docstring warns that a second implementation
of that rule "produces a plausible result with the wrong wells in it: right
schema, right-looking counts, no error". This was that second implementation.

**Every fixture here is mixed on purpose.** A plate of only groups, or only
solo wells, cannot tell the fixed code from the broken code — the two answer
identically — so a test built on one would have passed against the defect
(CLAUDE.md §7.8 rule 2). And ``selected_wells`` carries a stale token
throughout, because in replicate mode the plate does not write it: an
implementation reading that store instead would produce a visibly different
plan rather than the same one.
"""

from __future__ import annotations

import types

import pytest

from review.features.heatmap import sidebar_view as SV

_LOADED = ["A01", "A02", "B01", "B02", "C01", "C02", "D05"]

#: Two replicate pairs. Together they claim four of the seven loaded wells.
_GROUPS = [
    {"name": "Ctrl", "wells": ["A01", "A02"]},
    {"name": "Drug", "wells": ["B01", "B02"]},
]

#: The three wells no group claims, toggled onto the plot one click at a time.
_SOLO = ["C01", "C02", "D05"]


def _ctx(*, selections, solo, repset_avg, selected=("H12",), loaded=_LOADED):
    """A ctx in the state a mixed plate is actually in.

    ``selected`` defaults to a well that is **not loaded and in no group** —
    the stale residue ``selected_wells`` holds in replicate mode. Nothing in
    the plan may come from it.
    """
    return types.SimpleNamespace(
        window=None,
        state=types.SimpleNamespace(
            dataset=types.SimpleNamespace(well_paths=dict.fromkeys(loaded, "p")),
            selection=types.SimpleNamespace(
                selections=[dict(s) for s in selections],
                selected_wells=set(selected),
                active_solo_wells=set(solo),
            ),
            plot=types.SimpleNamespace(group_by_variable=""),
            heatmap=types.SimpleNamespace(repset_avg=repset_avg),
        ),
    )


def test_rep_set_avg_lays_out_every_set_and_every_solo_well():
    """One cell per set, then one per solo well — the mixture the bar draws."""
    plan = SV._autopopulate_cells(
        _ctx(selections=_GROUPS, solo=_SOLO, repset_avg=True))

    assert plan == [["A01", "A02"], ["B01", "B02"], ["C01"], ["C02"], ["D05"]]


def test_per_well_mode_lays_out_every_well_of_both_halves():
    plan = SV._autopopulate_cells(
        _ctx(selections=_GROUPS, solo=_SOLO, repset_avg=False))

    assert plan == [["A01"], ["A02"], ["B01"], ["B02"],
                    ["C01"], ["C02"], ["D05"]]


@pytest.mark.parametrize("repset_avg", [True, False])
def test_no_well_the_plate_shows_as_active_is_missing_from_the_grid(repset_avg):
    """The count is the point: seven wells were active, seven reached the grid.

    Stated as a set over the plan rather than as a length, so a plan that
    happened to hold the right *number* of the wrong wells still fails.
    """
    plan = SV._autopopulate_cells(
        _ctx(selections=_GROUPS, solo=_SOLO, repset_avg=repset_avg))

    assert {w for cell in plan for w in cell} == set(_LOADED)


def test_a_hidden_group_stays_out_while_the_solo_wells_stay_in():
    """Visibility governs the groups; it has nothing to say about a solo well."""
    groups = [_GROUPS[0], {**_GROUPS[1], "hidden": True}]
    plan = SV._autopopulate_cells(
        _ctx(selections=groups, solo=_SOLO, repset_avg=True))

    assert plan == [["A01", "A02"], ["C01"], ["C02"], ["D05"]]


def test_a_solo_well_that_is_not_loaded_is_not_laid_out():
    """The plan holds wells this dataset has, not every token ever soloed."""
    plan = SV._autopopulate_cells(
        _ctx(selections=_GROUPS, solo=[*_SOLO, "G11"], repset_avg=True))

    assert plan == [["A01", "A02"], ["B01", "B02"], ["C01"], ["C02"], ["D05"]]


def test_a_plate_with_groups_and_no_solo_wells_is_unchanged():
    plan = SV._autopopulate_cells(
        _ctx(selections=_GROUPS, solo=[], repset_avg=True))

    assert plan == [["A01", "A02"], ["B01", "B02"]]


def test_a_plate_with_no_groups_still_lays_out_the_plain_selection():
    """Plain mode is the other store, and it is read exactly as before."""
    plan = SV._autopopulate_cells(
        _ctx(selections=[], solo=[], repset_avg=False,
             selected=["C02", "A01", "B01"]))

    assert plan == [["A01"], ["B01"], ["C02"]]


def test_nothing_selected_and_no_groups_plans_nothing():
    """Not the whole plate — an empty plan leaves the grid alone."""
    assert SV._autopopulate_cells(
        _ctx(selections=[], solo=[], repset_avg=False, selected=[])) == []
