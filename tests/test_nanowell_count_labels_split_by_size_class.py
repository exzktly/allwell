"""A nanowell's cell count, wherever a label writes it, is split by the size classes set on Cell Gating.

Asked for: "there are options to label images or hover tooltips with a property or metric like cell count. [...]
there is the ability to specify subpopulations based on cell size, but the labels only specify the total number of
cells. It should include the breakdown now of those subsets if they are specified."

Three labels write a nanowell's count: the caption under each of its crops (the detail's strip, the pop-out and the
Window view's tiles, all through ``controller.crop_metrics``), the hover card (the Array view's through
``controller.on_motion``, the Window composite's tiles' through ``_window_controller.tile_card``, both
``array_hover_compute.hover_text``), and the Window composite's **Corner** with *Cell count* chosen
(``_window_controller.composite_labels``). Each is driven here through the controller function the page calls, with
the gate tally on the page's state, so a call site that forgets to pass the rule is caught as well as a formatter
that drops the split.

The fixture is ``test_nanowell_size_classes``'s: nanowell (0, 0) keeps 4 cells — 2 T cells, 1 DC and 1 in the gap —
so each class, and the cells in neither, has a different number, and a split written in the wrong order or read off
the wrong column says something else. (1, 0) keeps 1 T cell and nothing else, so "in neither" is left out there.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from review.domain.size_classes import SizeClassRule
from review.features.nanowell import cell_gate_compute as cg
from review.features.nanowell import size_class_compute as scc
from review.features.nanowell.series_compute import crop_metric_lines
from tests.test_nanowell_size_classes import CELLS, KEPT, RULE


def _metrics(rule=RULE, unread=()):
    """The page's metrics for A01 at t1 as ``series_compute.site_metrics`` leaves them, gated and classed; and the
    tally they were counted in."""
    frame = pd.DataFrame({"well": ["A01"] * 4, "timepoint": ["t1"] * 4, "nw_q": [0.0, 1.0, 2.0, 3.0],
                          "nw_r": [0.0] * 4, "n": [5.0, 1.0, 0.0, np.nan], "c": [0.2, 0.1, 0.0, np.nan],
                          "state": ["measured", "measured", "empty", "occluded"], "tp_order": [0] * 4})
    gate = cg.tally([("A01", CELLS, KEPT)] if not unread else [], unread=unread, sizes=rule)
    cg.apply(frame, gate, [])
    scc.apply(frame, gate)
    return frame, gate


# ── the crop captions ───────────────────────────────────────────────────────────────────────────────────────────
def test_a_crop_caption_follows_its_count_with_each_class_and_the_cells_in_neither():
    frame, _gate = _metrics()
    row = frame.iloc[0].to_dict()
    assert crop_metric_lines(row, None, RULE) == ["4 cells", "2 T cells", "1 DCs", "1 in neither", "20% conf."]
    one = frame.iloc[1].to_dict()
    assert crop_metric_lines(one, None, RULE) == ["1 cell", "1 T cells", "0 DCs", "10% conf."]   # none in neither
    assert crop_metric_lines(frame.iloc[2].to_dict(), None, RULE) == ["0 cells", "0 T cells", "0 DCs", "0% conf."]


def test_without_classes_or_with_classes_that_were_not_counted_the_caption_is_the_bare_count():
    frame, _gate = _metrics()
    row = frame.iloc[0].to_dict()
    assert crop_metric_lines(row, None, None) == ["4 cells", "20% conf."]
    assert crop_metric_lines(row, None, SizeClassRule()) == ["4 cells", "20% conf."]
    unread, _gate = _metrics(unread=["A01"])          # the cells could not be read: no split, never a split of zeros
    assert crop_metric_lines(unread.iloc[0].to_dict(), None, RULE) == ["5 cells", "20% conf."]


def test_the_page_s_crop_captions_carry_the_split_of_the_rule_the_counts_were_made_with():
    from review.features.nanowell import controller

    frame, gate = _metrics()
    st = SimpleNamespace(metrics=frame, gate=gate)
    lines = controller.crop_metrics(st, [("A01", 0, 0)], None)
    assert lines == {("A01", 0, 0): {"t1": ["4 cells", "2 T cells", "1 DCs", "1 in neither", "20% conf."]}}


# ── the Array view's hover card ─────────────────────────────────────────────────────────────────────────────────
def test_the_card_writes_the_split_on_its_own_line_or_why_a_counted_nanowell_has_none():
    from review.features.nanowell.array_hover_compute import hover_text

    frame, _gate = _metrics()
    card = hover_text(frame.iloc[0].to_dict(), ("A01", 0, 0), timepoint="t1", sizes=RULE).split("\n")
    assert "Size classes: 2 T cells · 1 DCs · 1 in neither" in card
    assert card.index("Size classes: 2 T cells · 1 DCs · 1 in neither") == next(
        i for i, line in enumerate(card) if line.startswith("Cells: ")) + 1
    bare = hover_text(frame.iloc[0].to_dict(), ("A01", 0, 0), timepoint="t1", sizes=None)
    assert "Size classes" not in bare
    unread, _gate = _metrics(unread=["A01"])
    said = hover_text(unread.iloc[0].to_dict(), ("A01", 0, 0), timepoint="t1", sizes=RULE)
    assert f"Size classes: no value — {scc.NOT_READ}" in said.split("\n")
    occluded = hover_text(frame.iloc[3].to_dict(), ("A01", 3, 0), timepoint="t1", sizes=RULE)
    assert "Size classes" not in occluded                           # no count, so nothing to split


def test_the_page_s_hover_passes_the_gate_s_rule_to_the_card(monkeypatch):
    """Through ``controller.on_motion``, the canvas's handler: the card shown names the classes."""
    from review.features.nanowell import array_hover_compute as ahc
    from review.features.nanowell import controller

    frame, gate = _metrics()
    row = frame.iloc[0].to_dict()
    sites = SimpleNamespace(sites=[("A01", 0, 0)], x=[0.0], y=[0.0], radius=[1.0], values=[4.0], state=["measured"])
    scene = SimpleNamespace(sites=sites, well="A01", timepoint="t1", why={}, name="A01")
    ax = object()
    drawn = SimpleNamespace(ax=ax, scene=scene, rows=None, label="Cell count")
    shown = {}
    tip = SimpleNamespace(point=lambda _key, text: shown.update(text=text), clear=lambda: shown.update(text=None))
    st = SimpleNamespace(view="array", drawn=drawn, metrics=frame, data=SimpleNamespace(channels=[]), gate=gate,
                         press=None, rim_focus_rule=lambda: None)
    monkeypatch.setattr(ahc, "site_at", lambda *_a: 0)                  # the pointer on the one nanowell
    monkeypatch.setattr(ahc, "rows_by_address", lambda *_a: {})
    monkeypatch.setattr(ahc, "row_for", lambda *_a: row)
    ctx = SimpleNamespace(state=SimpleNamespace(feature_slice=lambda _name: st),
                          widgets=SimpleNamespace(get_or_none=lambda name: tip if name == "_nw_hover_tip" else None))
    controller.on_motion(ctx, SimpleNamespace(inaxes=ax, xdata=0.0, ydata=0.0, x=5.0, y=5.0))
    assert "Size classes: 2 T cells · 1 DCs · 1 in neither" in shown["text"].split("\n")


def test_the_window_s_tile_card_passes_the_gate_s_rule_too():
    """The Window composite's hex tiles show the same card (``_window_controller.tile_card``)."""
    from review.features.nanowell import _window_controller as wnc

    frame, gate = _metrics()
    st = SimpleNamespace(metrics=frame, data=SimpleNamespace(channels=[]), gate=gate, rim_focus_rule=lambda: None)
    card = wnc.tile_card(st, ("A01", 0, 0), "t1")
    assert "Size classes: 2 T cells · 1 DCs · 1 in neither" in card.split("\n")


# ── the Window composite's Corner ───────────────────────────────────────────────────────────────────────────────
def test_corner_cell_count_is_written_with_its_split_and_the_caption_names_the_classes():
    from review.features.nanowell import _window_controller as wnc

    frame, gate = _metrics()
    sites = [("A01", q, 0) for q in range(4)]
    st = SimpleNamespace(composite_label="n", metrics=frame, data=SimpleNamespace(channels=[]), channel="",
                         gate=gate)
    ctx = SimpleNamespace(state=SimpleNamespace(feature_slice=lambda _name: st,
                                                channel=SimpleNamespace(active_channel="")))
    corner, said = wnc.composite_labels(ctx, st, sites, ["t1"])
    assert corner == {"t1": {("A01", 0, 0): "4 (2/1)", ("A01", 1, 0): "1 (1/0)", ("A01", 2, 0): "0 (0/0)"}}
    assert said == ("Corner: each tile's Cell count at its panel's timepoint, then in brackets T cells / DCs "
                    "(the rest in neither).")
    st.gate = None                                                   # no classes: the bare number, as before
    corner, said = wnc.composite_labels(ctx, st, sites, ["t1"])
    assert corner == {"t1": {("A01", 0, 0): 4.0, ("A01", 1, 0): 1.0, ("A01", 2, 0): 0.0}}
    assert said == "Corner: each tile's Cell count at its panel's timepoint."


def test_a_corner_label_written_as_text_is_drawn_as_it_is():
    from review.features.nanowell._composite_render import corner_text

    assert corner_text("4 (2/1)") == "4 (2/1)"
    assert corner_text(4.0) == "4"
