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
    return [SimpleNamespace(key="CTRL", has_mean=True, mean=5.0, spread=0.5,
                            has_frac=True, frac=0.4, frac_spread=0.05),
            SimpleNamespace(key="DRUG", has_mean=True, mean=6.0, spread=0.5,
                            has_frac=True, frac=0.6, frac_spread=0.05)]


def _app(pairwise, *, rsets=(), active_val_col="gfp_mean_intensity"):
    return SimpleNamespace(
        _app_state=SimpleNamespace(
            plot=SimpleNamespace(show_significance=True),
            stats=SimpleNamespace(pairwise=pairwise),
            channel=SimpleNamespace(active_val_col=active_val_col),
        ),
        _bar_xs_keys=["CTRL", "DRUG"],
        _bar_xs=[0.0, 1.0],
        _rep_sets_active=lambda: list(rsets),
        _set_status=lambda *_a, **_k: None,
    )


def _artists(ax):
    return len(ax.lines) + len(ax.texts)


def _axes():
    fig, (ax_mean, ax_frac) = plt.subplots(2, 1)
    ax_mean.set_ylim(0, 10)
    ax_frac.set_ylim(0, 1)
    return ax_mean, ax_frac


def test_brackets_draw_on_mean_panel_by_default():
    ax_mean, ax_frac = _axes()
    app = _app([{"a": "CTRL", "b": "DRUG", "p": 0.001, "p_adj": 0.001, "stat": "mean"}])
    draw_significance_brackets(app, _bars(), ax_mean=ax_mean, ax_frac=ax_frac)
    assert _artists(ax_mean) > 0
    assert _artists(ax_frac) == 0


def test_fraction_test_brackets_the_fraction_panel():
    # A "fraction" statistic must draw on the On-fraction panel, not the mean one.
    ax_mean, ax_frac = _axes()
    app = _app([{"a": "CTRL", "b": "DRUG", "p": 0.01, "p_adj": 0.01, "stat": "fraction"}])
    draw_significance_brackets(app, _bars(), ax_mean=ax_mean, ax_frac=ax_frac)
    assert _artists(ax_frac) > 0
    assert _artists(ax_mean) == 0


def test_brackets_gated_to_matching_channel():
    # A test computed for one channel must NOT draw over a different channel.
    ax_mean, ax_frac = _axes()
    pairwise = [{"a": "CTRL", "b": "DRUG", "p": 0.001, "p_adj": 0.001,
                 "stat": "mean", "val_col": "cy5_mean_intensity"}]
    app = _app(pairwise, active_val_col="gfp_mean_intensity")  # showing GFP
    draw_significance_brackets(app, _bars(), ax_mean=ax_mean, ax_frac=ax_frac)
    assert _artists(ax_mean) == 0  # cy5 test not drawn over the gfp bars
    # Switch the displayed channel to match → brackets appear.
    app._app_state.channel.active_val_col = "cy5_mean_intensity"
    draw_significance_brackets(app, _bars(), ax_mean=ax_mean, ax_frac=ax_frac)
    assert _artists(ax_mean) > 0


def test_brackets_gated_to_matching_timepoint():
    ax_mean, ax_frac = _axes()
    pairwise = [{"a": "CTRL", "b": "DRUG", "p": 0.001, "p_adj": 0.001,
                 "stat": "mean", "val_col": "gfp_mean_intensity", "target_t": 24.0}]
    app = _app(pairwise, active_val_col="gfp_mean_intensity")
    # Bars show t=0 only → the t=24 test is not overlaid.
    draw_significance_brackets(app, _bars(), ax_mean=ax_mean, ax_frac=ax_frac,
                               target_ts=[0.0])
    assert _artists(ax_mean) == 0
    # Bars now include t=24 → drawn.
    draw_significance_brackets(app, _bars(), ax_mean=ax_mean, ax_frac=ax_frac,
                               target_ts=[0.0, 24.0])
    assert _artists(ax_mean) > 0


def test_brackets_draw_on_wells_match_when_names_differ():
    ax_mean, ax_frac = _axes()
    rsets = [SimpleNamespace(name="CTRL", wells=["A01", "A02"]),
             SimpleNamespace(name="DRUG", wells=["B01", "B02"])]
    pairwise = [{"a": "Group 1", "b": "Group 2",
                 "a_wells": ["A01", "A02"], "b_wells": ["B01", "B02"],
                 "p": 0.0001, "p_adj": 0.0001, "stat": "mean"}]
    app = _app(pairwise, rsets=rsets)
    draw_significance_brackets(app, _bars(), ax_mean=ax_mean, ax_frac=ax_frac)
    assert _artists(ax_mean) > 0


def test_no_match_sets_status_and_draws_nothing():
    ax_mean, ax_frac = _axes()
    msgs = []
    pairwise = [{"a": "X", "b": "Y", "a_wells": ["Z01"], "b_wells": ["Z02"],
                 "p": 0.001, "p_adj": 0.001, "stat": "mean"}]
    app = _app(pairwise)
    app._set_status = lambda m, *_a, **_k: msgs.append(m)
    draw_significance_brackets(app, _bars(), ax_mean=ax_mean, ax_frac=ax_frac)
    assert _artists(ax_mean) == 0 and _artists(ax_frac) == 0
    assert msgs and "no comparison matches" in msgs[0]


def test_off_toggle_draws_nothing():
    ax_mean, ax_frac = _axes()
    app = _app([{"a": "CTRL", "b": "DRUG", "p": 0.001, "p_adj": 0.001, "stat": "mean"}])
    app._app_state.plot.show_significance = False
    draw_significance_brackets(app, _bars(), ax_mean=ax_mean, ax_frac=ax_frac)
    assert _artists(ax_mean) == 0 and _artists(ax_frac) == 0
