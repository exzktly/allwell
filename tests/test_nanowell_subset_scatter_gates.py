"""The Nanowells page's *Subset* can name a gate drawn on the Scatter Plot's nanowell scatter.

Asked for: "surface the gates drawn in scatter → nanowell as reachable by nanowell's subset tool". A nanowell at a
timepoint is one point on the nanowell scatter; the condition keeps the nanowells whose point at the chosen
timepoint lies *inside* (or *outside*) the named gate. The box is read from the gates each time the subset is
evaluated (``subset_compute.bind_gates``), so the condition is the gate's name, and moving the gate moves it.

The fixture's counts are ``test_nanowell_page_starting_cells``'s. The gate is drawn on the count (X) against
``tp_order`` (Y, 0 at t_c), so reading the axes swapped, or at another timepoint, keeps other nanowells
(CLAUDE.md §7.8 #2).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from review.features.nanowell import subset_compute as ss  # noqa: E402
from tests.test_nanowell_page_starting_cells import _dots, _texts  # noqa: E402
from tests.test_nanowell_subset import _kept, metrics, page  # noqa: E402,F401

C = ss.Condition
#: Counts 2–4 at t_c (tp_order 0): A01 q1 (2), q3 (3), q7 (2); no B01 nanowell (1, 1).
GATE = {"x0": 4.0, "x1": 2.0, "y0": 0.5, "y1": -0.5, "x_col": "nw::n", "y_col": "nw::tp_order", "name": "Mid"}
#: A gate over a cell metric: it places cells, not nanowells, and is never offered.
CELL_GATE = {"x0": 0.0, "x1": 1.0, "y0": 0.0, "y1": 1.0, "x_col": "GFP_mean_intensity", "y_col": "nw::n",
             "name": "Cells"}


def _bound(*conditions, gates=(GATE,), match="all"):
    return ss.bind_gates(ss.Subset(tuple(conditions), match).validated(), list(gates))


def _sites(metrics, subset):
    kept, done = ss.apply(metrics, subset, "earliest")
    return sorted(set(zip(kept["well"], kept["nw_q"].astype(int)))), done


def test_inside_keeps_the_nanowells_whose_point_lies_in_the_box_at_the_timepoint(metrics):
    kept, _ = _sites(metrics, _bound(C("gate", "in", values=("Mid",), at="any")))
    assert kept == [("A01", 1), ("A01", 3), ("A01", 7)]
    # At the latest timepoint (tp_order 2) no point lies in the box's Y range: nothing is kept.
    latest, done = _sites(metrics, _bound(C("gate", "in", values=("Mid",), at="latest")))
    assert latest == [] and done.kept == 0


def test_outside_keeps_the_rest_and_a_point_with_no_value_is_neither(metrics):
    kept, done = _sites(metrics, _bound(C("gate", "not_in", values=("Mid",), at="earliest")))
    # A01 q6 has no count at t_c: undecided, not outside; q1, q3, q7 are inside.
    assert kept == [("A01", 0), ("A01", 2), ("A01", 4), ("A01", 5), ("B01", 0), ("B01", 1)]
    assert (done.kept, done.outside, done.unknown) == (6, 3, 1)


def test_a_gate_that_is_gone_gives_no_value_and_the_condition_says_so(metrics):
    subset = _bound(C("gate", "in", values=("Mid",), at="any"), gates=())
    kept, done = _sites(metrics, subset)
    assert kept == [] and done.unknown == done.total == 10
    assert subset.label() == "Subset: Scatter gate at any timepoint: inside “Mid” (no nanowell gate by that name)"


def test_only_gates_over_two_nanowell_metrics_are_offered():
    assert [name for name, _box in ss.nanowell_gates([GATE, CELL_GATE])] == ["Mid"]
    assert ss.nanowell_gates([GATE])[0][1] == ("n", "tp_order", 2.0, -0.5, 4.0, 0.5)   # corners put in order


def test_the_condition_persists_by_name_and_not_by_box():
    subset = _bound(C("gate", "in", values=("Mid",), at="tp:t_a"))
    block = subset.to_config()
    assert block == {"match": "all", "conditions": [{"field": "gate", "op": "in", "values": ["Mid"], "at": "tp:t_a"}]}
    restored = ss.Subset.from_config(block)
    assert restored == subset and restored.conditions[0].box is None    # read from the scatter, never from the file


# ── the page: the real editor ────────────────────────────────────────────────
def test_the_editor_names_a_scatter_gate_and_the_figure_draws_its_nanowells(page):
    from review.features.nanowell import controller as nwc

    page.state.cell_gating.scatter_gates = [dict(GATE), dict(CELL_GATE)]
    page.widgets.get("_nw_subset_edit_btn").click()
    editor = page.widgets.get("_nw_subset_editor")
    editor.add_btn.click()
    row = editor.rows[0]
    row.field_cb.setCurrentIndex(row.field_cb.findData("gate"))
    assert row.gate_cb.isVisibleTo(page.window) and not row.a_spin.isVisibleTo(page.window)
    assert [row.gate_cb.itemData(i) for i in range(row.gate_cb.count())] == ["Mid"]
    row.at_cb.setCurrentIndex(row.at_cb.findData("any"))
    st = page.state.feature_slice("nanowell")
    assert st.subset == ss.Subset((C("gate", "in", values=("Mid",), at="any"),))
    assert _dots(page) == [("A01", 1), ("A01", 3), ("A01", 7)]
    assert any("Scatter gate at any timepoint: inside “Mid”" in t for t in _texts(page))

    # The gate moved on the scatter: the page's next draw reads the new box.
    page.state.cell_gating.scatter_gates[0].update({"x0": 0.5, "x1": 1.5})          # counts of 1 at t_c
    nwc.redraw(page)
    assert _dots(page) == [("A01", 0), ("A01", 2), ("A01", 4), ("B01", 0), ("B01", 1)]
