"""Loading a Plot Group has to repaint replicate visibility, not just wells.

In replicate mode the plotting tabs read the *visible* replicate sets, and
``selection.selected_wells`` is not consulted — a plate click there toggles each
group's ``hidden`` flag so the highlight and the plots stay in lock-step. The
Plot Groups drawer only wrote ``selected_wells``, so with Sample Definition
groups defined, clicking a saved group changed nothing on screen.

These cover the pure mapping; no application fixture needed.
"""

from __future__ import annotations

from review.core.sidebar_view import plot_group_visibility_plan


def test_sets_the_group_touches_are_shown_and_the_rest_hidden():
    selections = [
        {"name": "a", "wells": ["A01", "B01"]},
        {"name": "b", "wells": ["A02", "B02"]},
        {"name": "c", "wells": ["A03", "B03"]},
    ]

    flags, solo = plot_group_visibility_plan(selections, {"A01", "B03"})

    assert flags == [False, True, False]   # a shown, b hidden, c shown
    assert solo == set()


def test_one_matching_well_is_enough_to_show_a_set():
    """A group naming a single member shows that whole set — visibility is
    per-set, and a replicate set is not meaningfully half-shown."""
    selections = [{"name": "a", "wells": ["A01", "B01", "C01"]}]

    flags, solo = plot_group_visibility_plan(selections, {"B01"})

    assert flags == [False]
    assert solo == set()


def test_wells_in_no_set_are_carried_as_solo():
    selections = [{"name": "a", "wells": ["A01", "B01"]}]

    flags, solo = plot_group_visibility_plan(selections, {"A01", "H12"})

    assert flags == [False]
    assert solo == {"H12"}


def test_a_group_matching_nothing_hides_every_set():
    selections = [
        {"name": "a", "wells": ["A01"]},
        {"name": "b", "wells": ["A02"]},
    ]

    flags, solo = plot_group_visibility_plan(selections, {"H12"})

    assert flags == [True, True]
    assert solo == {"H12"}


def test_non_dict_entries_are_tolerated():
    """Persisted selections have been repaired but the list is user data."""
    flags, solo = plot_group_visibility_plan(
        [{"name": "a", "wells": ["A01"]}, "junk"], {"A01"},
    )

    assert flags == [False, True]
    assert solo == set()
