"""The p-value option on the Bar plot's significance brackets.

A bracket used to be able to say one thing: which asterisk bucket the
comparison fell in. ``***`` covers everything below 0.001, so a p of 9e-4 and a
p of 3e-40 draw identically, and a reader who wants the number has to leave the
figure and go and read the Statistics tab's text report — which is a different
surface, showing whatever test was run most recently there.

``PlotState.significance_label_mode`` adds the other two readings: the p-value
itself, and the ladder with the number beside it. The tests below are grouped by
the thing each one protects — the formatting rules, which p gets quoted and
under what name, and the fact that the mode reaches the drawn artist at all.
"""

from __future__ import annotations

import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pytest  # noqa: E402

from tests.test_bar_significance import _app, _axes, _bars  # noqa: E402
from well_viewer.domain.significance import (  # noqa: E402
    SIGNIFICANCE_LABEL_MODE_NAMES,
    SIGNIFICANCE_LABEL_MODES,
    format_p_value,
    significance_label,
    significance_stars,
)
from well_viewer.features.barplot.renderer import (  # noqa: E402
    SIGNIFICANCE_NOTE_TAG,
    draw_significance_brackets,
)


# ── formatting the number ────────────────────────────────────────────────────

def test_a_p_value_is_printed_to_three_significant_figures():
    assert format_p_value(0.0312456) == "0.0312"
    assert format_p_value(0.2) == "0.2"
    # Past the range digits read well in, %g switches to an exponent by itself.
    assert format_p_value(3.2415e-8) == "3.24e-08"


def test_an_underflowed_p_is_a_bound_and_never_a_zero():
    """A p-value of exactly 0.0 is a double that ran out of exponent, not a
    probability of zero — the one number in this app where printing what the
    float says would always be a lie (CLAUDE.md §7: computed-as-zero is not the
    same fact as too-small-to-represent)."""
    assert format_p_value(0.0) == "< 5e-324"
    assert "0" != format_p_value(0.0)
    assert significance_label(0.0, "p") == "p < 5e-324"
    # And it is still the most significant bucket, as the value says.
    assert significance_stars(0.0) == "***"


def test_only_an_exact_zero_takes_the_bound():
    """The bound is for a number the double could not hold, and for nothing
    else. An earlier threshold of 1e-300 was picked on the belief that ``%.3g``
    stopped resolving below it — measured, it renders the whole subnormal range,
    so that threshold replaced real p-values with a weaker statement. Demoting a
    measurement to a bound is the same conflation as promoting one, facing the
    other way."""
    assert format_p_value(1e-301) == "1e-301"
    assert format_p_value(1e-310) == "1e-310"
    assert format_p_value(5e-324) == "4.94e-324"
    # The smallest positive double still prints as itself; only zero does not.
    assert format_p_value(0.0).startswith("<")


def test_an_unprintable_p_yields_no_number_but_still_yields_stars():
    """A test that could not be computed must not put the word 'nan' on a
    figure. The label falls back to the ladder, which says 'ns' — the honest
    reading of a comparison with no p-value."""
    for bad in (None, float("nan"), float("inf")):
        assert format_p_value(bad) == ""
        assert significance_label(bad, "p") == "ns"
        assert significance_label(bad, "both") == "ns"


def test_the_three_modes_read_as_they_are_named():
    assert significance_label(0.0312, "stars") == "*"
    assert significance_label(0.0312, "p") == "p = 0.0312"
    assert significance_label(0.0312, "both") == "* (p = 0.0312)"
    # A non-significant comparison still carries its number in the two numeric
    # modes: "ns" alone hides the difference between p=0.051 and p=0.97.
    assert significance_label(0.51, "both") == "ns (p = 0.51)"


def test_an_unknown_mode_falls_back_to_the_stars():
    """A saved view state can be hand-edited or written by a later build. An
    unrecognised mode must draw what every build draws, not an empty bracket."""
    assert significance_label(0.0312, "hieroglyphs") == "*"
    assert significance_label(0.0312, "") == "*"


def test_an_adjusted_p_is_quoted_under_its_own_name():
    """``p`` and ``p_BH`` are different claims about the same pair, and with
    three or more groups the raw p is the one that overstates the result. The
    renderer draws the adjusted value whenever the run has one, so printing it
    under a bare ``p =`` would attribute the looser test's name to the stricter
    test's number. The Statistics tab's report already writes ``p_BH``."""
    assert significance_label(0.04, "p", adjusted=True) == "p_BH = 0.04"
    assert significance_label(0.04, "both", adjusted=True) == "* (p_BH = 0.04)"
    # The stars mode names nothing, so the flag changes nothing there.
    assert significance_label(0.04, "stars", adjusted=True) == "*"


def test_every_declared_mode_has_a_name_for_its_picker():
    assert set(SIGNIFICANCE_LABEL_MODE_NAMES) == set(SIGNIFICANCE_LABEL_MODES)
    assert all(SIGNIFICANCE_LABEL_MODE_NAMES[m] for m in SIGNIFICANCE_LABEL_MODES)


# ── the mode reaching the figure ─────────────────────────────────────────────

def _bracket_texts(pairwise, mode, *, target_ts=None):
    """The text of every bracket the real renderer draws, in *mode*.

    Drives ``draw_significance_brackets`` rather than the label function it
    calls: the formatting is settled above, and what is in question here is
    whether the setting reaches the artist. A test that read the label helper
    would pass just as happily with the renderer still hard-coding its stars —
    CLAUDE.md §7.4 #1, assert at the boundary, not on the value behind it.
    """
    ax_mean, ax_frac = _axes()
    app = _app(list(pairwise))
    app._app_state.plot.significance_label_mode = mode
    draw_significance_brackets(app._ctx, _bars(), ax_mean=ax_mean,
                               ax_frac=ax_frac, target_ts=target_ts)
    texts = [t.get_text() for a in (ax_mean, ax_frac) for t in a.texts
             if not getattr(t, SIGNIFICANCE_NOTE_TAG, False)]
    plt.close("all")
    return texts


_ONE_RUN = [{"a": "CTRL", "b": "DRUG", "p": 0.0312, "p_adj": 0.0312,
             "stat": "mean"}]


def test_the_bracket_draws_stars_by_default():
    from well_viewer.core.plot_state import PlotState
    assert PlotState().significance_label_mode == "stars"
    assert _bracket_texts(_ONE_RUN, "stars") == ["*"]


def test_the_bracket_draws_the_p_value_when_the_mode_asks_for_it():
    assert _bracket_texts(_ONE_RUN, "p") == ["p = 0.0312"]


def test_the_bracket_draws_both_when_the_mode_asks_for_both():
    assert _bracket_texts(_ONE_RUN, "both") == ["* (p = 0.0312)"]


def test_a_single_comparison_is_not_labelled_as_bh_adjusted():
    """``_bh_adjust`` adjusts nothing across one comparison, and the record
    stores the raw p under ``p_adj`` anyway — so the values alone cannot tell
    the two cases apart. A one-pair run must read ``p``, never ``p_BH``."""
    assert _bracket_texts(_ONE_RUN, "p") == ["p = 0.0312"]
    assert "p_BH" not in _bracket_texts(_ONE_RUN, "both")[0]


def test_a_run_that_was_bh_adjusted_says_so_on_every_bracket():
    run = [
        {"a": "CTRL", "b": "DRUG", "p": 0.0104, "p_adj": 0.0312,
         "p_adj_applied": True, "stat": "mean"},
    ]
    assert _bracket_texts(run, "p") == ["p_BH = 0.0312"]


def test_an_older_record_without_the_flag_is_read_from_the_run_s_size():
    """Records written before ``p_adj_applied`` existed (a saved plot, a
    hand-built fixture) still have to be labelled correctly: BH runs across at
    least two comparisons, and a run's records are exactly the comparisons that
    produced a p-value, so the count is the answer."""
    two = [{"a": "CTRL", "b": "DRUG", "p": 0.0104, "p_adj": 0.0312,
            "stat": "mean"},
           {"a": "CTRL", "b": "MISSING", "p": 0.5, "p_adj": 0.5,
            "stat": "mean"}]
    # Only the first pair has bars at both ends; the second is dropped.
    assert _bracket_texts(two, "p") == ["p_BH = 0.0312"]


def test_the_p_value_the_bracket_prints_is_the_one_the_run_stored():
    """The number on the figure has to be the number the test produced —
    a label showing a rounded, rescaled or otherwise re-derived value would be
    the figure describing a comparison other than the one that was run."""
    run = [{"a": "CTRL", "b": "DRUG", "p": 1.234e-9, "p_adj": 1.234e-9,
            "stat": "mean"}]
    assert _bracket_texts(run, "p") == ["p = 1.23e-09"]


# ── the numbers have to fit ─────────────────────────────────────────────────

def _draw_n_groups(n_groups, mode):
    """Bracket every pair of *n_groups* bars on a real bar-panel figure.

    Sized as the app sizes it — ``_FIG_W, _FIG_H, _DPI`` from
    ``barplot/tab_view.py``, three stacked panels — because the whole question
    here is how much room one panel actually has, and a default-sized test
    figure would answer it for a panel the app never draws.
    """
    from types import SimpleNamespace
    from well_viewer.features.barplot.tab_view import _DPI, _FIG_H, _FIG_W

    fig, axes = plt.subplots(3, 1, figsize=(_FIG_W, _FIG_H), dpi=_DPI)
    ax_mean, ax_frac = axes[0], axes[1]
    ax_mean.set_ylim(0, 10)
    ax_frac.set_ylim(0, 1)
    fig.canvas.draw()

    keys = [f"G{i}" for i in range(n_groups)]
    bars = [SimpleNamespace(key=k, has_mean=True, mean=5.0, spread=0.5,
                            has_frac=True, frac=0.4, frac_spread=0.05)
            for k in keys]
    run = [{"a": a, "b": b, "p": 1.234e-9, "p_adj": 1.234e-9,
            "p_adj_applied": True, "stat": "mean"}
           for i, a in enumerate(keys) for b in keys[i + 1:]]

    app = _app(run)
    app._app_state.plot.significance_label_mode = mode
    app._bar_xs_keys = list(keys)
    app._bar_xs = [float(i) for i in range(n_groups)]
    draw_significance_brackets(app._ctx, bars, ax_mean=ax_mean, ax_frac=ax_frac)

    labels = [t for t in ax_mean.texts
              if not getattr(t, SIGNIFICANCE_NOTE_TAG, False)]
    notes = [t.get_text() for t in ax_mean.texts
             if getattr(t, SIGNIFICANCE_NOTE_TAG, False)]
    renderer = fig.canvas.get_renderer()
    boxes = [t.get_window_extent(renderer) for t in labels]
    overlaps = sum(
        1
        for i in range(len(boxes)) for j in range(i + 1, len(boxes))
        if min(boxes[i].x1, boxes[j].x1) - max(boxes[i].x0, boxes[j].x0) > 1
        and min(boxes[i].y1, boxes[j].y1) - max(boxes[i].y0, boxes[j].y0) > 0.5
    )
    texts = [t.get_text() for t in labels]
    plt.close("all")
    return texts, notes, overlaps


def test_six_groups_of_p_values_still_print_the_numbers():
    """The fallback must not be eager. Six groups is fifteen brackets and the
    measured spacing is 10.6 px against a 7 pt line — tight, and it fits."""
    texts, notes, overlaps = _draw_n_groups(6, "p")
    assert len(texts) == 15
    assert all(t.startswith("p_BH = ") for t in texts), texts
    assert notes == []
    assert overlaps == 0


def test_too_many_comparisons_fall_back_to_stars_and_say_so():
    """Seven groups is 21 brackets in a 249 px panel — 8.4 px a level against a
    7 pt line, and measured, 17 of the 21 labels printed through the one above.

    Every bracket and every verdict is kept; what is dropped is the precision
    that would have been illegible. And the panel says it dropped it, because a
    figure quietly showing a different label than the one that was picked is the
    silent substitution CLAUDE.md §7 forbids — the user would otherwise read
    asterisks off a plot they had set to p-values.
    """
    texts, notes, overlaps = _draw_n_groups(7, "both")
    assert len(texts) == 21
    assert all(t == "***" for t in texts), texts
    assert overlaps == 0
    assert len(notes) == 1
    assert "too little room to print the p-values" in notes[0]
    assert "stars" in notes[0]


def test_the_stars_are_never_withheld_for_want_of_room():
    """The fallback is about the numbers only — an asterisk fits where a whole
    expression does not, so the mode the user did not change must be unaffected
    at any comparison count."""
    texts, notes, _overlaps = _draw_n_groups(9, "stars")
    assert len(texts) == 36
    assert all(t == "***" for t in texts)
    assert notes == []


def test_a_p_value_label_is_drawn_a_point_smaller_than_the_stars():
    """One label per bracket is a lot of text on a busy panel; a whole
    expression takes the smaller of the two sizes and a glyph does not."""
    from well_viewer.features.barplot.renderer import (
        _BRACKET_P_VALUE_PT, _BRACKET_STARS_PT, _bracket_font_pt,
    )
    assert _BRACKET_P_VALUE_PT < _BRACKET_STARS_PT
    assert _bracket_font_pt("stars") == _BRACKET_STARS_PT
    assert _bracket_font_pt("p") == _BRACKET_P_VALUE_PT
    assert _bracket_font_pt("both") == _BRACKET_P_VALUE_PT

    ax_mean, ax_frac = _axes()
    app = _app(list(_ONE_RUN))
    app._app_state.plot.significance_label_mode = "p"
    draw_significance_brackets(app._ctx, _bars(), ax_mean=ax_mean, ax_frac=ax_frac)
    sizes = [t.get_fontsize() for t in ax_mean.texts
             if not getattr(t, SIGNIFICANCE_NOTE_TAG, False)]
    plt.close("all")
    assert sizes == [_BRACKET_P_VALUE_PT]


# ── one spelling of a p-value, on both surfaces ─────────────────────────────

def test_the_report_prints_a_p_value_the_way_a_bracket_does():
    """De-duplicating the ladder and leaving two spellings of the number would
    have fixed half the divergence. The report used ``:.4g`` and a bracket
    ``%.3g``, so one run read ``p = 0.03125`` in the text and ``p = 0.0312`` on
    the panel beside it."""
    from well_viewer.features.stats.controller import _format_pairwise_lines

    rows = [("A", "B", "t", 3.0, 0.0312456, None)]
    line = [ln for ln in _format_pairwise_lines(rows, {}, False) if "t =" in ln][0]
    assert "p = 0.0312" in line
    assert "0.03125" not in line


def test_the_report_never_prints_p_equals_zero_either():
    """The rule the shared module exists for, checked on the surface that is
    *not* the one it was written for. An underflowed p reported as ``p = 0``
    claims a probability of zero — and this line printed exactly that while the
    bracket beside it printed the bound."""
    from well_viewer.features.stats.controller import _format_pairwise_lines

    rows = [("A", "B", "t", 12.3, 0.0, None), ("A", "C", "t", 9.0, 0.0, None)]
    lines = [ln for ln in _format_pairwise_lines(rows, {0: 0.0, 1: 0.0}, True)
             if "t =" in ln]
    assert lines, "no result lines"
    for line in lines:
        assert "p = 0 " not in line and not line.endswith("p = 0"), line
        assert "p < 5e-324" in line
        assert "p_BH < 5e-324" in line


# ── the persisted setting ────────────────────────────────────────────────────

@pytest.mark.qt
def test_the_mode_round_trips_through_view_state(loaded_app):
    """Captured beside the toggle that draws the brackets: a saved plot whose
    brackets quoted their p-values must not come back showing bare asterisks."""
    from well_viewer.persistence import view_state as vs

    app = loaded_app
    app._app_state.plot.significance_label_mode = "both"
    snap = vs.snapshot(app._ctx)
    assert snap.get("significance_label_mode") == "both"

    app._app_state.plot.significance_label_mode = "stars"
    vs.restore(app._ctx, snap)
    assert app._app_state.plot.significance_label_mode == "both"


@pytest.mark.qt
def test_a_restore_puts_the_toggle_and_the_picker_back_in_step(loaded_app, qtbot):
    """Restoring writes the state; the widgets have to be told.

    Nothing calls ``setChecked`` / ``setEnabled`` on these two but the toggle's
    own click handler, so a restored view drew p-value brackets above a popover
    whose picker read ``Stars (*)`` and was **greyed out** — the picker being
    enable-gated on a toggle that still said "Off". That is worse than a stale
    caption: the control the user would reach for was the one they could not
    use, and README_WellViewer.md's "usable once Significance is on" was false
    on the main path that gets brackets back.
    """
    from PySide6.QtWidgets import QWidget
    from well_viewer.features.barplot import tab_view as bar_tab_view
    from well_viewer.persistence import view_state as vs

    app = loaded_app
    parent = QWidget()
    qtbot.addWidget(parent)
    bar_tab_view.build_bar_plots_tab(app._ctx, parent)

    btn = app._bar_significance_btn
    cb = app._bar_significance_label_cb
    assert btn.isChecked() is False and cb.isEnabled() is False

    vs.restore(app._ctx, {"show_significance": True,
                          "significance_label_mode": "p"})

    assert app._app_state.plot.show_significance is True
    assert btn.isChecked() is True
    assert btn.text() == "Significance: On"
    assert cb.isEnabled() is True
    assert cb.currentData() == "p"
    # The sync must not have fired the picker's own handler mid-restore.
    assert app._app_state.plot.significance_label_mode == "p"


@pytest.mark.qt
def test_a_bar_tab_built_later_opens_on_the_mode_already_chosen(viewer_app, qtbot):
    """Tab bodies build lazily, so the picker is usually constructed *after*
    the mode was set — by a restore, or on another plate. It has to open on the
    live value; seeding it from the default would show ``Stars (*)`` over a
    figure drawing p-values."""
    from PySide6.QtWidgets import QWidget
    from well_viewer.features.barplot import tab_view as bar_tab_view

    app = viewer_app
    app._app_state.plot.significance_label_mode = "both"
    app._app_state.plot.show_significance = True
    parent = QWidget()
    qtbot.addWidget(parent)
    bar_tab_view.build_bar_plots_tab(app._ctx, parent)

    cb = app._bar_significance_label_cb
    assert cb.currentData() == "both"
    assert cb.isEnabled() is True
    # The toggle beside it is seeded from the same state, caption included.
    assert app._bar_significance_btn.isChecked() is True
    assert app._bar_significance_btn.text() == "Significance: On"


@pytest.mark.qt
def test_an_unrecognised_saved_mode_restores_to_the_stars(loaded_app):
    """A view-state file can be hand-edited, and a later build may write a mode
    this one cannot draw. Restoring it verbatim would leave every bracket
    blank — the silent-substitution shape CLAUDE.md §7 names — so the restore
    validates against the known set and falls back to what every build draws."""
    from well_viewer.persistence import view_state as vs

    app = loaded_app
    app._app_state.plot.significance_label_mode = "p"
    vs.restore(app._ctx, {"significance_label_mode": "hieroglyphs"})
    assert app._app_state.plot.significance_label_mode == "stars"


def test_a_new_dataset_keeps_the_chosen_mode():
    """A presentation preference, not a statement about this plate's wells."""
    from well_viewer.core.plot_state import PlotState
    plot = PlotState()
    plot.significance_label_mode = "both"
    plot.reset_for_new_dataset()
    assert plot.significance_label_mode == "both"


# ── the control the user actually touches ───────────────────────────────────

@pytest.mark.qt
def test_the_display_popover_s_picker_moves_the_state(viewer_app, qtbot,
                                                      monkeypatch):
    """Drives the real controls rather than the handlers behind them.

    CLAUDE.md §7.6: a control that is *offered* is not a control that *works*,
    and the per-sample editor shipped unopenable behind two tests that built a
    combo, populated it, and never selected anything in it. So this clicks the
    button and selects in the combo.

    ``redraw_bars`` is stood in for, and only it. Building a second Bar tab onto
    a bare parent leaves the app holding the canvas of the first, so a real
    redraw here dies on a deleted C++ object — a limitation of driving the tab
    builder directly, not of the controls. Recording the calls instead also
    pins the part of the wiring that is this feature's own: the picker asks for
    a redraw only when there are brackets on the figure for it to change.
    """
    from PySide6.QtWidgets import QWidget
    from well_viewer.features.barplot import tab_view as bar_tab_view

    redraws: list[int] = []
    monkeypatch.setattr(bar_tab_view, "redraw_bars",
                        lambda _ctx: redraws.append(1))

    app = viewer_app
    parent = QWidget()
    qtbot.addWidget(parent)
    bar_tab_view.build_bar_plots_tab(app._ctx, parent)

    cb = app._bar_significance_label_cb
    assert [cb.itemData(i) for i in range(cb.count())] == list(
        SIGNIFICANCE_LABEL_MODES)
    # Brackets are off by default, so the picker changes nothing on screen and
    # says so by being unusable — the same gating the Smooth slider has under
    # Violin.
    assert cb.isEnabled() is False

    cb.setCurrentIndex(cb.findData("p"))
    assert app._app_state.plot.significance_label_mode == "p"
    assert redraws == [], "no bracket is drawn, so there is nothing to redraw"

    # ``click()`` on a checkable button toggles it; setting the check first
    # would leave it back where it started.
    app._bar_significance_btn.click()
    assert app._app_state.plot.show_significance is True
    assert cb.isEnabled() is True

    cb.setCurrentIndex(cb.findData("both"))
    assert app._app_state.plot.significance_label_mode == "both"
    # The toggle's redraw and the picker's.
    assert len(redraws) == 2


# ── the ladder is shared, not copied ────────────────────────────────────────

def test_the_report_and_the_brackets_share_one_ladder():
    """The Statistics tab's text report and the Bar plot's brackets state the
    same comparison. They had a private copy of these thresholds each; a p of
    0.05 reading 'ns' in one place and '*' in the other is the divergence one
    shared function exists to prevent."""
    from well_viewer.features.stats.controller import _sig_marker
    for p in (0.0, 5e-4, 0.001, 0.005, 0.01, 0.03, 0.05, 0.2, 1.0):
        assert _sig_marker(p) == significance_stars(p), p


def test_the_stars_never_raise_on_an_uncomputable_p():
    """Called while drawing: one unusable comparison must not take the figure
    down with it."""
    assert significance_stars(float("nan")) == "ns"
    assert significance_stars(None) == "ns"
    assert math.isnan(float("nan"))
