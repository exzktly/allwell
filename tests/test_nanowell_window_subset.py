"""A Distribution window frozen into the Subset (*Use as Subset*): the ``window_sites`` condition, from hand-built tables.

Asked for: use the window as a subset, so the Distribution itself loses the nanowells outside it. The condition holds
the window's nanowells — it cannot be bound to the window live, since the Subset filters the figure the window is
drawn on. The table here is built so the listed nanowells are not the first ones in it, one has no value for the metric
it is stacked with, and the other well has an address the list also names, so a test that read the wrong well or the
wrong order would pick other nanowells.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from review.features.nanowell import subset_compute as ss

LISTED = (("B02", 3, 0), ("A01", 2, 0), ("A01", 1, 0))


def _metrics():
    rows = []
    for well in ("A01", "B02"):
        for order, tp in enumerate(("t0", "t1", "t2")):
            for q in range(4):
                n = np.nan if (well, q, tp) == ("A01", 1, "t2") else float(q + order)
                rows.append({"well": well, "timepoint": tp, "tp_order": order, "nw_q": q, "nw_r": 0, "n": n,
                             "baseline_timepoint": "t0"})
    return pd.DataFrame(rows)


def _condition(op="in", sites=LISTED, name="Window 1"):
    return ss.Condition(ss.WINDOW, op, values=(name,), sites=sites).validated()


def _kept(subset):
    frame, result = ss.apply(_metrics(), subset, "earliest")
    return set(zip(frame["well"], frame["nw_q"])), result


def test_inside_keeps_exactly_the_listed_nanowells_and_outside_exactly_the_others():
    kept, result = _kept(ss.Subset((_condition("in"),)))
    assert kept == {("B02", 3), ("A01", 2), ("A01", 1)} and (result.kept, result.outside, result.unknown) == (3, 5, 0)
    kept, result = _kept(ss.Subset((_condition("not_in"),)))
    assert kept == {(w, q) for w in ("A01", "B02") for q in range(4)} - {("B02", 3), ("A01", 2), ("A01", 1)}
    assert (result.kept, result.outside, result.unknown) == (5, 3, 0)


def test_a_listed_nanowell_the_table_lacks_changes_nothing_for_the_others():
    kept, _result = _kept(ss.Subset((_condition("in", LISTED + (("Z99", 0, 0),)),)))
    assert kept == {("B02", 3), ("A01", 2), ("A01", 1)}


def test_stacked_with_a_metric_condition_under_all_it_is_the_intersection():
    metric = ss.Condition("n", "ge", 3, at="latest").validated()        # the latest n is q + 2 (A01 q1 has none)
    kept, _result = _kept(ss.Subset((_condition("in"), metric), "all"))
    # (A01, q1) is listed but has no latest n, so the metric leaves it undecided and it is not kept.
    assert kept == {("B02", 3), ("A01", 2)}


def test_the_condition_is_saved_and_restored_with_its_name_and_nanowells():
    subset = ss.Subset((_condition(),)).validated()
    config = subset.to_config()
    assert config["conditions"][0]["values"] == ["Window 1"]
    assert config["conditions"][0]["sites"] == [["A01", 1, 0], ["A01", 2, 0], ["B02", 3, 0]]
    assert ss.Subset.from_config(config) == subset


@pytest.mark.parametrize("bad", [("A01", 1), ("A01", "1", 0), (1, 1, 0), ("A01", True, 0), "A01,1,0", ("", 1, 0)])
def test_a_site_that_is_not_a_well_and_two_whole_numbers_is_refused(bad):
    with pytest.raises(ValueError):
        ss.Condition(ss.WINDOW, "in", values=("Window 1",), sites=(bad,)).validated()


def test_none_and_too_many_nanowells_are_refused():
    with pytest.raises(ValueError):
        ss.Condition(ss.WINDOW, "in", values=("Window 1",), sites=()).validated()
    too_many = tuple(("A01", q, 0) for q in range(ss.MAX_WINDOW_SITES + 1))
    with pytest.raises(ValueError):
        ss.Condition(ss.WINDOW, "in", values=("Window 1",), sites=too_many).validated()


def test_a_damaged_saved_site_list_refuses_the_whole_block_and_restores_no_subset():
    block = {"match": "all", "conditions": [{"field": "window_sites", "op": "in", "values": ["Window 1"],
                                             "sites": [["A01", 1, 0], ["A01", "x", 0]]}]}
    with pytest.raises(ValueError):
        ss.Subset.from_config(block)
    assert ss.restore(block) == ss.Subset()


def test_the_label_names_the_window_and_counts_its_nanowells():
    assert _condition().text() == "Distribution window: inside “Window 1” (3 nanowells)"
    assert _condition("not_in").text() == "Distribution window: outside “Window 1” (3 nanowells)"
