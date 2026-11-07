"""A Distribution window drawn around one well's swarm takes that well, not the wells drawn beside it.

Reported: "when I draw a box on the distribution plot, it incorrectly includes points on neighbouring distributions
that I didn't draw around." A replicate group draws its wells side by side inside each column (a timepoint along time,
the group along wells), each well its own swarm; the window snapped to whole columns, so a box around one well's dots
took every well in that column -- and a box that strayed past a column's edge took the next column whole.

The models are hand-built so the wrong answers differ from the right one: two wells side by side in each column, every
nanowell inside the value range, so only the x snapping decides who is a member.
"""

from __future__ import annotations

import numpy as np

from review.features.nanowell import window_compute as wc
from review.features.nanowell.distribution_compute import DistributionModel, Panel, Swarm
from review.features.nanowell.window_compute import DistributionWindow


def _swarm(label, well, x, n, group="G"):
    sites = [(well, i, 0) for i in range(n)]
    return Swarm(label, well, x, np.full(n, 3.0), sites, [None] * n, n, {}, group)


def _group_model(axis="time"):
    """Panel G: wells W1 and W2 side by side (slots of width 0.5) at T0 (column 0) and T1 (column 1); a second panel H
    with well W3 alone at both."""
    g = Panel("G (2 wells)", [_swarm("T0", "W1", -0.25, 2), _swarm("T0", "W2", 0.25, 3),
                              _swarm("T1", "W1", 0.75, 2), _swarm("T1", "W2", 1.25, 3)], [(0.0, "0"), (1.0, "1")])
    h = Panel("H", [_swarm("T0", "W3", 0.0, 4, "H"), _swarm("T1", "W3", 1.0, 4, "H")], [(0.0, "0"), (1.0, "1")])
    return DistributionModel("n", "n", "Cell count", axis, True, [g, h])


def _window(model, x0, x1, panel=0):
    slots = wc.snap_slots(model, panel, x0, x1)
    keys = tuple(dict.fromkeys(k for k, _w in slots))
    return DistributionWindow(model.column, model.axis, keys, 0.0, 9.0, panel=model.panels[panel].title,
                              slots=wc.partial_slots(model, panel, slots))


def _wells(result):
    return sorted({site[0] for site in result.sites()})


def test_a_box_around_one_well_s_swarm_takes_that_well_alone():
    m = _group_model()
    window = _window(m, -0.42, -0.08)                      # around W1's dots at T0, short of W2's
    assert window.keys == ("T0",) and window.slots == (("T0", "W1"),)
    result = wc.members(m, window)
    assert sorted(result.sites()) == [("W1", 0, 0), ("W1", 1, 0)]


def test_a_box_around_both_wells_of_a_column_covers_the_column_in_every_panel():
    m = _group_model()
    window = _window(m, -0.45, 0.45)
    assert window.keys == ("T0",) and window.slots == ()   # whole: left to the key, as before
    assert _wells(wc.members(m, window)) == ["W1", "W2", "W3"]


def test_a_box_straying_past_a_column_s_edge_does_not_take_the_next_column():
    m = _group_model()
    window = _window(m, -0.45, 0.6)                        # both T0 wells, and a sliver of column 1 short of W1's middle
    assert window.keys == ("T0",)


def test_every_asks_about_the_columns_covered_for_the_nanowell_s_own_well():
    """A box over W2 at T0 and W1 at T1 covers one column for each well. Asking every nanowell to be inside at both
    columns -- its own and the other well's -- kept none of them."""
    m = _group_model()
    window = _window(m, 0.1, 0.9)
    assert set(window.slots) == {("T0", "W2"), ("T1", "W1")}
    result = wc.members(m, window)
    assert window.rule == "every" and _wells(result) == ["W1", "W2"] and result.n_every == 5


def test_the_slots_survive_a_save_and_a_slot_outside_the_keys_is_refused():
    m = _group_model()
    window = _window(m, -0.42, -0.08)
    assert DistributionWindow.from_config(window.to_config()) == window
    bad = {**window.to_config(), "slots": [["T9", "W1"]]}
    assert DistributionWindow.from_config(bad) is None


def test_a_window_saved_before_slots_were_kept_still_covers_its_whole_columns():
    m = _group_model()
    block = {k: v for k, v in _window(m, -0.45, 0.45).to_config().items() if k != "slots"}
    old = DistributionWindow.from_config(block)
    assert old is not None and old.slots == () and _wells(wc.members(m, old)) == ["W1", "W2", "W3"]


def test_the_band_is_drawn_over_the_covered_swarms_only():
    m = _group_model()
    result = wc.members(m, _window(m, -0.42, -0.08))
    assert wc.merged_spans(result.spans[0]) == [(-0.5, 0.0)]           # W1's slot at T0, not the whole column
    assert result.spans.get(1, []) == []                               # and nothing in panel H at that column
