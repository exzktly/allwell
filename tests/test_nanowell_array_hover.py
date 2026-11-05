"""Hovering a nanowell on the Array view shows what its row of the nanowell table holds.

Asked for: "If I mouse over a nanowell in array view, I'd like to see its status, statistics, focus status, address,
counts, etc". The view could only be clicked; the readout chip said ``x = … · y = …`` and nothing about the nanowell
under the pointer.

What is pinned: the pointer finds the nanowell whose disc it is in (and none off every disc); the canvas's readout chip
leads with that nanowell's address, state and shown value; and the card the controller shows as a tooltip — driven
through ``controller.on_motion``, the handler the page's canvas connects — names *that* nanowell's address, ring,
timepoint, state, counts, fluorescence, fold changes, rim focus verdict and place. Every nanowell in the fixture has
its own count, so a card read off the wrong row (or the wrong timepoint) says a different number.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from tests.test_nanowell_review_data import STARTS, _dataset, _row  # noqa: E402


def _count(q, r, tp):
    """A count no other nanowell or timepoint shares."""
    return 10 * (q + 3) + (r + 3) + (100 if tp == "t_a" else 0)


@pytest.fixture
def array(tmp_path):
    """A 5 × 5 well at two timepoints, its nanowells 120 µm apart; (1, 1) blurred at t_a, (2, 3) occluded there."""
    from matplotlib.figure import Figure

    from review.domain.nanowell_rim_focus import RimFocusRule
    from review.features.nanowell import controller
    from review.features.nanowell.collect import load_nanowell_data
    from review.features.nanowell.series_compute import site_metrics
    from review.features.nanowell.state import ArrayLayers

    rows = []
    for q in range(-2, 3):
        for r in range(-2, 3):
            for tp in ("t_c", "t_a"):
                n = _count(q, r, tp)
                row = _row(tp, q, r, n=n, c=n / 1000, gfp=5.0 * n)
                row["well_x_um"], row["well_y_um"] = 100.0 + 120 * q, 100.0 + 120 * r
                if (q, r) == (1, 1) and tp == "t_a":
                    row["focus"] = 0.3
                rows.append(row)
    rows.append({**_row("t_c", 2, 3, n=2, c=0.1), "well_x_um": 340.0, "well_y_um": 460.0})
    rows.append({**_row("t_a", 2, 3, state="occluded"), "well_x_um": 340.0, "well_y_um": 460.0})
    data = load_nanowell_data(_dataset(tmp_path, {"A01": rows}, starts={k: STARTS[k] for k in ("t_c", "t_a")}))
    rule = RimFocusRule()
    metrics = site_metrics(data, focus=rule)
    fig = Figure(figsize=(8, 6))
    layers = replace(ArrayLayers.preset("map"), positions="measured", image="off")
    drawn = controller.draw_array(fig, metrics, data, well="A01", timepoint="t_a", metric="n", channel=None,
                                  layers=layers)
    return SimpleNamespace(drawn=drawn, metrics=metrics, data=data, rule=rule)


def _at(drawn, q, r):
    k = drawn.sites.sites.index(("A01", q, r))
    return float(drawn.sites.x[k]), float(drawn.sites.y[k]), float(drawn.sites.radius[k])


def test_the_pointer_finds_the_nanowell_whose_disc_it_is_in_and_none_between_them(array):
    from review.features.nanowell.array_hover_compute import site_at

    sites = array.drawn.sites
    x, y, radius = _at(array.drawn, 1, -1)
    assert sites.sites[site_at(sites, x + 0.9 * radius, y)] == ("A01", 1, -1)
    assert site_at(sites, x + 60.0, y + 60.0) is None              # between four nanowells, 50 µm discs at 120 µm
    assert site_at(sites, x + 1.1 * radius, y) is None


def test_the_readout_chip_leads_with_the_nanowell_s_address_state_and_value(array):
    ax = array.drawn.ax
    x, y, _r = _at(array.drawn, -2, 1)
    assert ax._aw_hover_label(x, y) == f"nanowell -2,1 · measured · {_count(-2, 1, 't_a')}"
    x, y, _r = _at(array.drawn, 1, 1)
    assert ax._aw_hover_label(x, y) == "nanowell 1,1 · rim out of focus · no value"
    assert ax._aw_hover_label(x + 60.0, y + 60.0) is None


def _card(array, q, r, monkeypatch, *, press=None):
    """What ``controller.on_motion`` shows as the tooltip with the pointer on (*q*, *r*); ``None`` when hidden."""
    from PySide6 import QtWidgets

    from review.features.nanowell import controller
    from review.features.nanowell.state import NanowellState

    shown = {"text": "unset"}
    monkeypatch.setattr(QtWidgets.QToolTip, "showText", staticmethod(lambda _pos, text, *_a: shown.update(text=text)))
    monkeypatch.setattr(QtWidgets.QToolTip, "hideText", staticmethod(lambda: shown.update(text=None)))
    st = NanowellState()
    st.view, st.drawn, st.metrics, st.data, st.focus_rule, st.press = (
        "array", array.drawn, array.metrics, array.data, array.rule, press)
    ctx = SimpleNamespace(state=SimpleNamespace(feature_slice=lambda _name: st),
                          widgets=SimpleNamespace(get_or_none=lambda _name: None))
    x, y, _r = _at(array.drawn, q, r)
    controller.on_motion(ctx, SimpleNamespace(inaxes=array.drawn.ax, xdata=x, ydata=y, x=5.0, y=5.0))
    return shown["text"]


def test_the_card_names_the_hovered_nanowell_s_address_counts_fluorescence_fold_change_focus_and_place(
        array, monkeypatch):
    card = _card(array, -1, 2, monkeypatch).split("\n")
    n, n0 = _count(-1, 2, "t_a"), _count(-1, 2, "t_c")
    assert card[0] == "A01 · nanowell -1,2 (ring 2) · t_a (5 h)"
    assert card[1] == "State: measured"
    assert card[2] == f"Shown — Cell count: {n}"
    assert card[3] == f"Cells: {n} · confluence {n / 1000:.3g} · area estimate {n} cells · occupancy at baseline 3+ ({n0} cells, t_c)"
    assert card[4] == f"GFP: cell fluorescence no value · nanowell fluorescence {5.0 * n:.3g}"
    assert card[5].startswith(f"From baseline t_c: count fold change {n / n0:.3g} · ")
    assert card[6] == ("Rim focus: 1 = 100% of the well's sharp rims (1, the 90th percentile) — in focus "
                       "(needs 75%)")
    assert card[-1] == "Where: at -20, 340 µm · radius 50 µm · 100% visible · tile F0"


def test_a_nanowell_whose_rim_was_out_of_focus_says_so_with_its_numbers_and_what_the_run_counted(array, monkeypatch):
    card = _card(array, 1, 1, monkeypatch)
    assert "State: rim out of focus (the run recorded it measured)" in card
    assert "Why: rim focus 0.3 under 75% of its well's sharp rims (1) at this timepoint" in card
    assert f"Cells: no value (the run counted {_count(1, 1, 't_a')})" in card
    assert ("Rim focus: 0.3 = 30% of the well's sharp rims (1, the 90th percentile) — OUT OF FOCUS (under 75%), "
            "so it has no value here") in card


def test_an_occluded_nanowell_is_not_judged_on_focus_and_has_no_value(array, monkeypatch):
    card = _card(array, 2, 3, monkeypatch)
    assert "State: occluded" in card and "Shown — Cell count: no value" in card
    assert "— not judged (occluded)" in card


def test_a_drag_hides_the_card_rather_than_trailing_the_pan(array, monkeypatch):
    from matplotlib.transforms import IdentityTransform

    ax = array.drawn.ax
    press = (0.0, 0.0, ax.get_xlim(), ax.get_ylim(), IdentityTransform())
    assert _card(array, 0, 0, monkeypatch, press=press) is None


def test_a_card_for_a_nanowell_with_no_row_says_so_rather_than_inventing_one():
    from review.features.nanowell.array_hover_compute import hover_text

    card = hover_text(None, ("A01", 0, 0), timepoint="t_a", state="absent")
    assert card.split("\n") == ["A01 · nanowell 0,0 · t_a", "State: absent",
                                "The nanowell table has no row for it at this timepoint."]
