"""The Distribution window's members, every and any, snapping and its sentences, from hand-built models.

Asked for: draw a window on the Distribution figure and get the nanowells inside it, including those a click cannot
reach — dots packed into a "+N" overflow, and nanowells drawn inside a density. So membership is read from the
model's swarms, never from the dots drawn. The models here are built by hand with the values chosen so that
*every* and *any* disagree, a panel lacks a timepoint another has, and two nanowells tie on the ranking value.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from review.features.nanowell import renderer
from review.features.nanowell import window_compute as wc
from review.features.nanowell.distribution_compute import DistributionModel, Panel, Swarm
from review.features.nanowell.window_compute import DistributionWindow


def _swarm(label, well, x, values, group=None, start=0):
    sites = [(well, start + i, 0) for i in range(len(values))]
    return Swarm(label, well, x, np.array(values, float), sites, [None] * len(values), len(values), {},
                 group or well)


def _time_model():
    a01 = Panel("A01", [_swarm("T0", "A01", 0.0, [1, 2.5, 3]), _swarm("T1", "A01", 1.0, [1, 3.1]),
                        _swarm("T2", "A01", 2.0, [1, 1.2, 3.5])], [(0.0, "0"), (1.0, "1"), (2.0, "2")])
    b01 = Panel("B01", [_swarm("T0", "B01", 0.0, [3])], [(0.0, "0")])
    return DistributionModel("count_fc", "count_fc", "Count fold change", "time", False, [a01, b01])


def _wells_model():
    swarms = []
    for j in range(1, 6):
        well = f"W{j}"
        swarm = _swarm(well, well, (j - 3) * 0.16, [3.0], group="G")
        swarms.append(swarm)
    panel = Panel("G", swarms, [(0.0, "G")])
    return DistributionModel("n", "n", "Cell count", "wells", True, [panel], timepoint="T1")


def _w(**kw):
    kw.setdefault("lo", 2)
    kw.setdefault("hi", 4)
    kw.setdefault("keys", ("T0", "T1", "T2"))
    return DistributionWindow("count_fc", "time", **kw)


def test_snap_takes_every_column_the_span_overlaps():
    m = _time_model()
    assert wc.snap(m, 0, -0.2, 2.2) == ("T0", "T1", "T2")
    assert wc.snap(m, 0, 0.6, 0.7) == ("T1",)
    assert wc.snap(m, 0, 0.4, 0.6) == ("T0", "T1")
    assert wc.snap(m, 0, 2.6, 3.0) == ()


def test_every_needs_a_value_inside_at_each_covered_timepoint():
    result = wc.members(_time_model(), _w(rule="every"))
    # (A01,1,0) is out at T2 (1.2); (A01,2,0) has no value at T1; B01's panel has only T0, so T0 is all it needs.
    assert result.sites() == [("B01", 0, 0)]
    assert (result.n_every, result.n_any) == (1, 3)


def test_any_needs_one_value_inside_and_ranks_by_the_latest_column():
    result = wc.members(_time_model(), _w(rule="any"))
    assert result.sites() == [("A01", 2, 0), ("A01", 1, 0), ("B01", 0, 0)]
    assert result.rank_key == "T2"
    assert result.rows[0].value == 3.5 and result.rows[1].value == 1.2
    assert math.isnan(result.rows[2].value)             # B01 has no value at T2, so it is ranked last


def test_ties_rank_by_well_then_address():
    result = wc.members(_time_model(), _w(keys=("T0",), panel="A01"))
    assert result.sites() == [("A01", 2, 0), ("B01", 0, 0), ("A01", 1, 0)]         # 3.0, 3.0, 2.5


def test_this_panel_only_keeps_the_panel_it_was_drawn_in():
    m = _time_model()
    result = wc.members(m, _w(keys=("T0",), panel="A01", panel_only=True))
    assert result.sites() == [("A01", 2, 0), ("A01", 1, 0)]
    gone = wc.members(m, _w(keys=("T0",), panel="Z9", panel_only=True))
    assert gone.missing_panel is True and gone.rows == []


def test_a_row_carries_its_value_at_every_timepoint():
    result = wc.members(_time_model(), _w(rule="any"))
    assert result.rows[1].values == {"T0": 2.5, "T1": 3.1, "T2": 1.2}


def test_a_window_round_trips_and_a_bad_block_is_dropped():
    w = _w(keys=("T0",), panel="A01", panel_only=True, rule="any",
           drawn_under=(("baseline", "earliest"),), n_at_drawing=4)
    assert DistributionWindow.from_config(w.to_config()) == w
    good = w.to_config()
    assert DistributionWindow.from_config({"lo": 1}) is None
    assert DistributionWindow.from_config({**good, "lo": 5}) is None
    assert DistributionWindow.from_config({**good, "axis": "x"}) is None
    assert DistributionWindow.from_config({**good, "keys": []}) is None


def test_the_sentence_states_both_counts_and_what_was_not_drawn():
    w3 = _w(rule="any")
    result = wc.members(_time_model(), w3)
    said = wc.label_text(w3, result, {"in panels not drawn": 0, "in densities": 1, "in overflow": 0}, "time")
    assert said == ("Window 2–4 · 3 inside at any covered timepoint · 1 at every · "
                    "1 with no dot on the figure (1 in densities)")


def test_the_changed_sentence_names_what_changed():
    w = replace(_w(), drawn_under=(("baseline", "earliest"), ("moving average", "0")), n_at_drawing=37)
    assert wc.changed_sentence(w, (("baseline", "T1"), ("moving average", "3")), 29) == (
        "Baseline, moving average changed since the window was drawn: 37 → 29 nanowells.")
    assert wc.changed_sentence(w, (("baseline", "earliest"), ("moving average", "0")), 37) == ""


def test_membership_reads_the_model_not_the_drawing():
    """Five wells share a column, so the renderer draws densities and no dot at all; the hit map is empty, and the
    window still finds all five nanowells."""
    model = _wells_model()
    window = DistributionWindow("n", "wells", ("G",), 2, 4)
    hits = renderer.HitMap()
    hits.panels = [(object(), 0)]
    result = wc.members(model, window)
    assert len(result.rows) == 5 and result.rank_key == "T1"
    assert wc.not_drawn(result, model, hits, max_panels=12, density_above=4) == {
        "in panels not drawn": 0, "in densities": 5, "in overflow": 0}


def test_the_density_rule_is_the_renderer_s():
    for model in (_time_model(), _wells_model()):
        for panel in model.panels:
            assert wc._members_per_column(model, panel) == renderer._members_per_tick(model, panel)


def test_the_range_includes_both_of_its_ends():
    """Both bounds are inclusive: at T0 the values 2.5, 3.0 and 3.0 sit exactly on the window's ends. (The tracker's
    other cases keep every value strictly inside or outside, so exclusive bounds passed them all.)"""
    result = wc.members(_time_model(), _w(keys=("T0",), lo=2.5, hi=3.0))
    assert result.sites() == [("A01", 2, 0), ("B01", 0, 0), ("A01", 1, 0)]


def test_the_page_keeps_its_windows_and_pop_out_choices():
    from review.features.nanowell.state import NanowellState

    state = NanowellState()
    window = _w(keys=("T0",), panel="A01", rule="any")
    state.windows[wc.window_key("count_fc", "time")] = window
    state.popout_source = "window"
    restored = NanowellState()
    restored.apply_config(state.to_config())
    assert restored.windows == {wc.window_key("count_fc", "time"): window} and restored.popout_source == "window"
    damaged = NanowellState()
    damaged.apply_config({"windows": {"k": {"lo": 1}}, "popout_source": "nonsense", "popout_layout": "nonsense"})
    assert (damaged.windows, damaged.popout_source, damaged.popout_layout) == ({}, "selected", "rows")
