"""Significance-bracket helpers on the Bar plot (N7)."""

from __future__ import annotations

from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from well_viewer.features.barplot.renderer import (  # noqa: E402
    _significance_marker,
    draw_significance_brackets,
)


def test_significance_marker_thresholds():
    assert _significance_marker(0.0005) == "***"
    assert _significance_marker(0.005) == "**"
    assert _significance_marker(0.03) == "*"
    assert _significance_marker(0.2) == "ns"
    # Boundary values fall to the less-significant bucket (strict <).
    assert _significance_marker(0.05) == "ns"
    assert _significance_marker(0.01) == "*"


def _bars():
    return [SimpleNamespace(key="CTRL", has_mean=True, mean=5.0, spread=0.5),
            SimpleNamespace(key="DRUG", has_mean=True, mean=6.0, spread=0.5)]


def _app(pairwise, *, rsets=()):
    return SimpleNamespace(
        _app_state=SimpleNamespace(
            plot=SimpleNamespace(show_significance=True),
            stats=SimpleNamespace(pairwise=pairwise),
        ),
        _bar_xs_keys=["CTRL", "DRUG"],
        _bar_xs=[0.0, 1.0],
        _rep_sets_active=lambda: list(rsets),
        _set_status=lambda *_a, **_k: None,
    )


def _artists(ax):
    return len(ax.lines) + len(ax.texts)


def test_brackets_draw_on_name_match():
    fig, ax = plt.subplots(); ax.set_ylim(0, 10)
    app = _app([{"a": "CTRL", "b": "DRUG", "p": 0.001, "p_adj": 0.001}])
    before = _artists(ax)
    draw_significance_brackets(app, ax, _bars())
    assert _artists(ax) > before


def test_brackets_draw_on_wells_match_when_names_differ():
    # Statistics groups named differently from the bar replicate sets, but the
    # WELLS line up → the comparison still maps onto the right bars (the fix).
    fig, ax = plt.subplots(); ax.set_ylim(0, 10)
    rsets = [SimpleNamespace(name="CTRL", wells=["A01", "A02"]),
             SimpleNamespace(name="DRUG", wells=["B01", "B02"])]
    pairwise = [{"a": "Group 1", "b": "Group 2",
                 "a_wells": ["A01", "A02"], "b_wells": ["B01", "B02"],
                 "p": 0.0001, "p_adj": 0.0001}]
    app = _app(pairwise, rsets=rsets)
    before = _artists(ax)
    draw_significance_brackets(app, ax, _bars())
    assert _artists(ax) > before


def test_no_match_sets_status_and_draws_nothing():
    fig, ax = plt.subplots(); ax.set_ylim(0, 10)
    msgs = []
    pairwise = [{"a": "X", "b": "Y", "a_wells": ["Z01"], "b_wells": ["Z02"],
                 "p": 0.001, "p_adj": 0.001}]
    app = _app(pairwise)
    app._set_status = lambda m, *_a, **_k: msgs.append(m)
    before = _artists(ax)
    draw_significance_brackets(app, ax, _bars())
    assert _artists(ax) == before
    assert msgs and "no comparison matches" in msgs[0]


def test_off_toggle_draws_nothing():
    fig, ax = plt.subplots(); ax.set_ylim(0, 10)
    app = _app([{"a": "CTRL", "b": "DRUG", "p": 0.001, "p_adj": 0.001}])
    app._app_state.plot.show_significance = False
    before = _artists(ax)
    draw_significance_brackets(app, ax, _bars())
    assert _artists(ax) == before
