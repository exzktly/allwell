"""Significance-bracket helpers on the Bar plot (N7)."""

from __future__ import annotations

from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from review.domain.significance import significance_stars
from review.features.barplot.renderer import draw_significance_brackets


def test_significance_marker_thresholds():
    """The asterisk ladder the brackets draw.

    It lives in ``domain/significance.py`` now, shared with the Statistics tab's
    text report — the renderer had its own copy of these four thresholds and the
    report had another, so the panel and the text beside it could have disagreed
    about whether a result was significant.
    """
    assert significance_stars(0.0005) == "***"
    assert significance_stars(0.005) == "**"
    assert significance_stars(0.03) == "*"
    assert significance_stars(0.2) == "ns"
    # Boundary values fall to the less-significant bucket (strict <).
    assert significance_stars(0.05) == "ns"
    assert significance_stars(0.01) == "*"


def _bars():
    return [SimpleNamespace(key="CTRL", has_mean=True, mean=5.0, spread=0.5,
                            has_frac=True, frac=0.4, frac_spread=0.05),
            SimpleNamespace(key="DRUG", has_mean=True, mean=6.0, spread=0.5,
                            has_frac=True, frac=0.6, frac_spread=0.05)]


def _app(pairwise, *, rsets=(), active_val_col="gfp_mean_intensity"):
    _sels = [{"name": r.name, "wells": list(getattr(r, "wells", []) or [])}
             for r in rsets]
    _wp = {w: None for r in rsets for w in (getattr(r, "wells", []) or [])}
    # A real ``StatsState``, not a namespace holding a list. The store is keyed
    # by run context and the renderer looks runs up through it, so a stand-in
    # exposing only ``pairwise`` sends the renderer down a path production never
    # takes — and would have made every test here measure a shape the app does
    # not have.
    from review.features.stats.state import StatsState
    _stats = StatsState()
    _stats.pairwise = list(pairwise or [])
    state = SimpleNamespace(
        plot=SimpleNamespace(show_significance=True),
        stats=_stats,
        channel=SimpleNamespace(active_val_col=active_val_col),
        selection=SimpleNamespace(selections=_sels, selected_wells=set()),
        dataset=SimpleNamespace(well_paths=_wp),
    )
    # The real ``AppState`` answers ``feature_slice(name)`` with the slice or
    # ``None``, and core code reaches a removable feature's state that way —
    # attribute access *raises* when the feature is absent. A stand-in without
    # the method sends the renderer down a path production never takes, which
    # is the fixture-diverges-from-production trap CLAUDE.md §7.1 #4 names.
    state.feature_slice = lambda name: getattr(state, name, None)
    app = SimpleNamespace(
        _app_state=state,
        _bar_xs_keys=["CTRL", "DRUG"],
        _bar_xs=[0.0, 1.0],
        _rep_sets_active=lambda: list(rsets),
        _set_status=lambda *_a, **_k: None,
    )
    from review.core.app_runtime import AppRuntime
    app._ctx = SimpleNamespace(app=app, state=app._app_state, widgets=_W(app),
                               runtime=AppRuntime(app),
                               notify=SimpleNamespace(
                                   set_status=lambda m, *a, **k: app._set_status(m)))
    return app


class _W:
    """Minimal ctx.widgets shim over the fake app's bare _bar_* attrs."""
    def __init__(self, app):
        self._app = app
    def get_or_none(self, name):
        return getattr(self._app, name, None)


def _artists(ax):
    """Count the *bracket* artists on *ax* — the lines and their asterisks.

    Not every artist. The renderer also writes a muted one-line note saying why
    it declined to draw (``SIGNIFICANCE_NOTE_TAG``), and that note is the
    opposite of a bracket: counting it would make "drew nothing" tests pass on a
    panel that drew a bracket and fail on one that correctly explained itself.
    ``tests/test_bar_significance_says_why.py`` is where the note is asserted.
    """
    from review.features.barplot.renderer import SIGNIFICANCE_NOTE_TAG
    texts = [t for t in ax.texts if not getattr(t, SIGNIFICANCE_NOTE_TAG, False)]
    return len(ax.lines) + len(texts)


def _axes():
    fig, (ax_mean, ax_frac) = plt.subplots(2, 1)
    ax_mean.set_ylim(0, 10)
    ax_frac.set_ylim(0, 1)
    return ax_mean, ax_frac


def test_brackets_draw_on_mean_panel_by_default():
    ax_mean, ax_frac = _axes()
    app = _app([{"a": "CTRL", "b": "DRUG", "p": 0.001, "p_adj": 0.001, "stat": "mean"}])
    draw_significance_brackets(app._ctx, _bars(), ax_mean=ax_mean, ax_frac=ax_frac)
    assert _artists(ax_mean) > 0
    assert _artists(ax_frac) == 0


def test_fraction_test_brackets_the_fraction_panel():
    # A "fraction" statistic must draw on the On-fraction panel, not the mean one.
    ax_mean, ax_frac = _axes()
    app = _app([{"a": "CTRL", "b": "DRUG", "p": 0.01, "p_adj": 0.01, "stat": "fraction"}])
    draw_significance_brackets(app._ctx, _bars(), ax_mean=ax_mean, ax_frac=ax_frac)
    assert _artists(ax_frac) > 0
    assert _artists(ax_mean) == 0


def test_brackets_gated_to_matching_channel():
    # A test computed for one channel must NOT draw over a different channel.
    ax_mean, ax_frac = _axes()
    pairwise = [{"a": "CTRL", "b": "DRUG", "p": 0.001, "p_adj": 0.001,
                 "stat": "mean", "val_col": "cy5_mean_intensity"}]
    app = _app(pairwise, active_val_col="gfp_mean_intensity")  # showing GFP
    draw_significance_brackets(app._ctx, _bars(), ax_mean=ax_mean, ax_frac=ax_frac)
    assert _artists(ax_mean) == 0  # cy5 test not drawn over the gfp bars
    # Switch the displayed channel to match → brackets appear.
    app._app_state.channel.active_val_col = "cy5_mean_intensity"
    draw_significance_brackets(app._ctx, _bars(), ax_mean=ax_mean, ax_frac=ax_frac)
    assert _artists(ax_mean) > 0


def test_brackets_gated_to_matching_timepoint():
    ax_mean, ax_frac = _axes()
    pairwise = [{"a": "CTRL", "b": "DRUG", "p": 0.001, "p_adj": 0.001,
                 "stat": "mean", "val_col": "gfp_mean_intensity", "target_t": 24.0}]
    app = _app(pairwise, active_val_col="gfp_mean_intensity")
    # Bars show t=0 only → the t=24 test is not overlaid.
    draw_significance_brackets(app._ctx, _bars(), ax_mean=ax_mean, ax_frac=ax_frac,
                               target_ts=[0.0])
    assert _artists(ax_mean) == 0
    # Bars now include t=24 → drawn.
    draw_significance_brackets(app._ctx, _bars(), ax_mean=ax_mean, ax_frac=ax_frac,
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
    draw_significance_brackets(app._ctx, _bars(), ax_mean=ax_mean, ax_frac=ax_frac)
    assert _artists(ax_mean) > 0


def _per_well_bars(keys=("C08", "D02")):
    """Bars as drawn in per-well mode: the bar's key IS the well token."""
    return [SimpleNamespace(key=k, has_mean=True, mean=5.0 + i, spread=0.5,
                            has_frac=True, frac=0.4, frac_spread=0.05)
            for i, k in enumerate(keys)]


def test_solo_well_groups_bracket_their_own_per_well_bars():
    """Two one-well groups, per-well bars, no replicate sets defined.

    Regression: the wells-to-bar map was built only from the active replicate
    sets, so a Statistics group holding a single well had nothing to match
    through — its own bar is keyed by the well token, not by any set — and the
    comparison was silently dropped with a "pool replicates" hint. A one-well
    group must bracket the bar for that well.
    """
    ax_mean, ax_frac = _axes()
    pairwise = [{"a": "Ctrl", "b": "Treated",
                 "a_wells": ["C08"], "b_wells": ["D02"],
                 "p": 0.001, "p_adj": 0.001, "stat": "mean"}]
    app = _app(pairwise)
    app._app_state.dataset.well_paths = {"C08": None, "D02": None}
    app._bar_xs_keys = ["C08", "D02"]
    draw_significance_brackets(app._ctx, _per_well_bars(),
                               ax_mean=ax_mean, ax_frac=ax_frac)
    assert _artists(ax_mean) > 0


def test_solo_well_group_does_not_bracket_a_pooled_bar_of_the_same_name():
    """A replicate set may be named after one of the wells it pools.

    That bar stands for the whole set, so a comparison computed on the single
    well of the same name must not be drawn over it — the well-token fallback
    skips any key that is a replicate set's name.
    """
    ax_mean, ax_frac = _axes()
    rsets = [SimpleNamespace(name="C08", wells=["C08", "C09"]),
             SimpleNamespace(name="D02", wells=["D02", "D03"])]
    pairwise = [{"a": "Ctrl", "b": "Treated",
                 "a_wells": ["C08"], "b_wells": ["D02"],
                 "p": 0.001, "p_adj": 0.001, "stat": "mean"}]
    app = _app(pairwise, rsets=rsets)
    app._bar_xs_keys = ["C08", "D02"]
    draw_significance_brackets(app._ctx, _per_well_bars(),
                               ax_mean=ax_mean, ax_frac=ax_frac)
    assert _artists(ax_mean) == 0


def test_no_match_says_why_on_the_panel_and_draws_nothing():
    """A comparison that lines up with no bar is explained, not silently dropped.

    This used to assert the explanation reached ``ctx.notify.set_status``. It no
    longer goes there, and the change is deliberate rather than a regression:
    that is the *persistent* status bar, so the message outlived the condition
    it described — run a test and the panel note went with the redraw while the
    bar kept insisting the comparison could not be placed. The reason now lives
    on the figure only, where the redraw that resolves it is the same redraw
    that clears it.
    """
    from review.features.barplot.renderer import SIGNIFICANCE_NOTE_TAG

    ax_mean, ax_frac = _axes()
    msgs = []
    pairwise = [{"a": "X", "b": "Y", "a_wells": ["Z01"], "b_wells": ["Z02"],
                 "p": 0.001, "p_adj": 0.001, "stat": "mean"}]
    app = _app(pairwise)
    app._set_status = lambda m, *_a, **_k: msgs.append(m)
    draw_significance_brackets(app._ctx, _bars(), ax_mean=ax_mean, ax_frac=ax_frac)
    assert _artists(ax_mean) == 0 and _artists(ax_frac) == 0
    notes = [t.get_text() for t in ax_mean.texts
             if getattr(t, SIGNIFICANCE_NOTE_TAG, False)]
    assert len(notes) == 1 and "no comparison matches" in notes[0]
    assert msgs == [], "the reason must not reach the persistent status bar"


def test_off_toggle_draws_nothing():
    ax_mean, ax_frac = _axes()
    app = _app([{"a": "CTRL", "b": "DRUG", "p": 0.001, "p_adj": 0.001, "stat": "mean"}])
    app._app_state.plot.show_significance = False
    draw_significance_brackets(app._ctx, _bars(), ax_mean=ax_mean, ax_frac=ax_frac)
    assert _artists(ax_mean) == 0 and _artists(ax_frac) == 0


def test_export_style_skips_tagged_bracket_lines():
    # Regression: the export-style pass used to stamp the per-plot data marker
    # onto *every* Line2D in ``ax.lines`` — including the significance bracket —
    # turning the flat bracket into a row of circles. Tagged lines must opt out.
    from review.core.plot_style import apply_export_style_prefs

    fig, ax = plt.subplots()
    (data_line,) = ax.plot([0, 1], [0, 1], marker="None", linestyle="-")
    (bracket,) = ax.plot([0, 0, 1, 1], [2, 2.1, 2.1, 2],
                         marker="None", linestyle="-")
    bracket._aw_no_style_override = True

    apply_export_style_prefs(fig, {"marker_style": "o", "line_style": "-"})

    # The ordinary data line picks up the chosen data marker...
    assert data_line.get_marker() == "o"
    # ...but the tagged bracket keeps its flat, marker-free shape.
    assert bracket.get_marker() == "None"
    plt.close(fig)
