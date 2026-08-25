"""With "Significance: On", a plot without asterisks has to say why.

The bracket renderer declines to draw for six different reasons, and five of
them used to be a bare ``return``. The gates themselves are right — the
Statistics tab carries its own Channel, Property and Timepoint selectors,
independent of the Plotting band's, so a comparison run on one column can easily
be looked at over another's bars, and drawing it there would be worse than
drawing nothing. What was wrong is that declining was invisible.

That is the failure CLAUDE.md §7 names: degraded output indistinguishable from
good output, with the software knowing and not saying. From the outside the
feature read as *unreliable* — asterisks that come and go — rather than as
conditional, which is what it is. In Split View, where the Statistics pane and
the Bar plot are on screen together and either side's channel or timepoint is
one click away, it is the normal experience rather than an edge case.

So each gate now states its reason twice: in the status line, and — because a
status line is transient, sits at the other end of the window, and is not part
of anything you export — in muted type on the panel itself.
"""

from __future__ import annotations

from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pytest  # noqa: E402

from tests.test_bar_significance import _app, _axes, _bars  # noqa: E402
from well_viewer.features.barplot.renderer import (  # noqa: E402
    SIGNIFICANCE_NOTE_TAG,
    draw_significance_brackets,
)

#: A run whose context matches the bars the fixture draws.
MATCHING_RUN = [{"a": "CTRL", "b": "DRUG", "p": 0.001, "p_adj": 0.001,
                 "stat": "mean", "val_col": "gfp_mean_intensity",
                 "target_t": 24.0}]


def _draw(pairwise, *, active_val_col="gfp_mean_intensity", target_ts=(24.0,),
          xs_keys=None, xs=None, show_significance=True):
    """Run the real renderer and report what reached the user.

    Returns ``(stars, notes, status)`` — the asterisk texts actually drawn, the
    on-panel explanations, and whatever went to the status line.
    """
    ax_mean, ax_frac = _axes()
    app = _app(list(pairwise), active_val_col=active_val_col)
    app._app_state.plot.show_significance = show_significance
    if xs_keys is not None:
        app._bar_xs_keys = xs_keys
    if xs is not None:
        app._bar_xs = xs
    status: list[str] = []
    app._ctx.notify = SimpleNamespace(set_status=lambda m, *a, **k: status.append(m))
    draw_significance_brackets(app._ctx, _bars(), ax_mean=ax_mean, ax_frac=ax_frac,
                               target_ts=list(target_ts) if target_ts else None)
    stars = [t.get_text() for a in (ax_mean, ax_frac) for t in a.texts
             if t.get_text() in ("*", "**", "***", "ns")]
    notes = [t.get_text() for a in (ax_mean, ax_frac) for t in a.texts
             if getattr(t, SIGNIFICANCE_NOTE_TAG, False)]
    plt.close("all")
    return stars, notes, status


# ── the case that works is unchanged ────────────────────────────────────────

def test_a_matching_run_still_draws_its_asterisks_and_says_nothing():
    """The note is for the absence. A drawn bracket explains itself."""
    stars, notes, status = _draw(MATCHING_RUN)
    # ``**``, not ``***``: the marker thresholds are strict ``<``, so p = 0.001
    # falls to the less-significant bucket. Pinned here because this test's job
    # is that the working path is untouched, which includes its wording.
    assert stars == ["**"]
    assert notes == []
    assert status == []


def test_the_toggle_being_off_stays_silent():
    """The one absence the user asked for. Explaining it would be noise."""
    stars, notes, status = _draw(MATCHING_RUN, show_significance=False)
    assert (stars, notes, status) == ([], [], [])


# ── every other way of drawing nothing ──────────────────────────────────────

def _cases():
    return {
        "no test has been run": dict(pairwise=[]),
        "tested another channel": dict(
            pairwise=[{**MATCHING_RUN[0], "val_col": "cy5_mean_intensity"}]),
        "tested another timepoint": dict(
            pairwise=MATCHING_RUN, target_ts=(48.0,)),
        "bar positions missing": dict(pairwise=MATCHING_RUN, xs_keys=[]),
        "bar positions mismatched": dict(
            pairwise=MATCHING_RUN, xs_keys=["CTRL"], xs=[0.0, 1.0]),
        "groups match no bar": dict(
            pairwise=[{**MATCHING_RUN[0], "a": "GROUP_X", "b": "GROUP_Y"}]),
    }


@pytest.mark.parametrize("case", sorted(_cases()))
def test_every_declined_draw_reaches_the_status_line(case):
    stars, _notes, status = _draw(**_cases()[case])
    assert stars == []
    assert status and status[0].startswith("Significance: "), (
        f"'{case}' drew nothing and said nothing")


@pytest.mark.parametrize("case", sorted(_cases()))
def test_every_declined_draw_is_written_on_the_panel(case):
    """The status line is transient and is not part of an exported figure. A
    reader who opens a copied PNG with no asterisks on it should still be able
    to tell that a comparison was withheld, and why."""
    stars, notes, _status = _draw(**_cases()[case])
    assert stars == []
    assert len(notes) == 1, f"'{case}' left no explanation on the panel"
    assert notes[0].startswith("Significance: ")


def test_the_note_names_both_channels_so_the_mismatch_is_actionable():
    """"Doesn't match" is not enough to act on: the fix is to move one of the
    two selectors, and the note has to say which two values are in play. Read as
    the channel menus write them, not as raw column keys."""
    _stars, notes, _status = _draw(
        [{**MATCHING_RUN[0], "val_col": "cy5_mean_intensity"}],
        active_val_col="gfp_mean_intensity")
    assert "CY5 Mean Intensity" in notes[0]
    assert "GFP Mean Intensity" in notes[0]
    assert "cy5_mean_intensity" not in notes[0]


def test_the_note_names_both_timepoints():
    _stars, notes, _status = _draw(MATCHING_RUN, target_ts=(48.0,))
    assert "24" in notes[0] and "48" in notes[0]


def test_the_timepoint_note_reads_in_the_units_of_the_axis_beside_it():
    """The Properties → Time correction relabels every timepoint on the figure.
    A note naming raw filename hours next to ticks reading corrected ones would
    send the user to check a timepoint that appears nowhere on screen."""
    from well_viewer.domain.time_rescale import TimeRescale

    ax_mean, ax_frac = _axes()
    app = _app(list(MATCHING_RUN), active_val_col="gfp_mean_intensity")
    app._app_state.plot.show_significance = True
    app._app_state.plot.time_scale = 4.0
    app._app_state.plot.time_offset = -2.0
    status: list[str] = []
    app._ctx.notify = SimpleNamespace(set_status=lambda m, *a, **k: status.append(m))
    draw_significance_brackets(app._ctx, _bars(), ax_mean=ax_mean,
                               ax_frac=ax_frac, target_ts=[48.0])
    note = next(t.get_text() for t in ax_mean.texts
                if getattr(t, SIGNIFICANCE_NOTE_TAG, False))
    plt.close("all")

    rs = TimeRescale(factor=4.0, offset=-2.0)
    from well_viewer.domain.time_rescale import format_scaled
    # 24 × 4 − 2 = 94 and 48 × 4 − 2 = 190 — the numbers the ticks carry.
    assert f"t={format_scaled(24.0, rs)}" in note
    assert f"t={format_scaled(48.0, rs)}" in note
    # And not the uncorrected hours, which appear nowhere on the figure.
    assert "t=24" not in note and "t=48" not in note


def test_the_note_is_cleared_by_the_next_redraw():
    """It is an ordinary artist on the panel, so ``cla()`` at the top of
    ``redraw_bars`` takes it with everything else — a stale explanation beside
    fresh brackets would be its own version of this bug."""
    ax_mean, ax_frac = _axes()
    app = _app([], active_val_col="gfp_mean_intensity")
    app._app_state.plot.show_significance = True
    app._ctx.notify = SimpleNamespace(set_status=lambda *_a, **_k: None)
    draw_significance_brackets(app._ctx, _bars(), ax_mean=ax_mean,
                               ax_frac=ax_frac, target_ts=[24.0])
    assert any(getattr(t, SIGNIFICANCE_NOTE_TAG, False) for t in ax_mean.texts)
    ax_mean.cla()
    assert not any(getattr(t, SIGNIFICANCE_NOTE_TAG, False) for t in ax_mean.texts)
    plt.close("all")


# ── the seventh absence: the per-cell modes ─────────────────────────────────
#
# Violin, Beeswarm and Properties → Statistics → "Show: All points" all route
# through ``draw_per_cell_bar_mode``, which returns before
# ``draw_significance_brackets`` is ever called. None of them draws a bar, so
# there is no bar top for a bracket and no bar for a compared group to resolve
# to — withholding the comparison is right. Saying nothing about it was not, and
# it is the one absence the renderer's own gates cannot report, because the
# renderer is not reached.
#
# "Show: All points" is the one that reads as a bug: it is a sidebar setting
# rather than a visible mode switch, so the asterisks vanish from what still
# looks like the same plot.

def _per_cell_redraw(qtbot, monkeypatch, **bar_flags):
    """Run the real ``redraw_bars`` far enough to take the per-cell branch."""
    from types import SimpleNamespace as NS

    from well_viewer.features.barplot import renderer as r

    from well_viewer.core.plot_state import PlotState

    fig, (ax_mean, ax_frac, ax_n) = plt.subplots(3, 1)
    app = _app(list(MATCHING_RUN), active_val_col="gfp_mean_intensity")
    # The real ``PlotState``: ``redraw_bars`` runs the fold-change title
    # machinery on its way to the branch, and a namespace holding only the
    # fields this test cares about sends it down a path the app never takes.
    plot = PlotState()
    app._app_state.plot = plot
    plot.show_significance = True
    app._app_state.bar = NS(violin=bar_flags.get("violin", False),
                            swarm=bar_flags.get("swarm", False))
    plot.bar_show_mode = bar_flags.get("show_mode", "mean_spread")
    status: list[str] = []
    app._ctx.notify = NS(set_status=lambda m, *a, **k: status.append(m))

    # Everything ``redraw_bars`` needs before the branch, stubbed to the
    # shortest path that still reaches it. The branch itself is real.
    monkeypatch.setattr(r, "draw_per_cell_bar_mode", lambda *_a, **_k: True)
    monkeypatch.setattr(r, "apply_export_style_to_current",
                        lambda *_a, **_k: None)
    monkeypatch.setattr(r, "resolve_bar_timepoints", lambda _c: [(24.0, "24")])
    monkeypatch.setattr(r, "refresh_bar_layout_btn_state", lambda _c: None)
    monkeypatch.setattr(r, "on_cutoff_for_column",
                        lambda *_a, **_k: 0.0)
    monkeypatch.setattr(r, "active_replicate_sets", lambda *_a, **_k: [])
    # Deferred import inside ``redraw_bars``, so it is patched at its home.
    import well_viewer.core.selection_controller as _sel
    monkeypatch.setattr(_sel, "has_plottable_selection", lambda _c: True)
    plot.use_sem = True
    plot.use_fov_replicates = False
    app._app_state.channel.active_channel = "gfp"
    app._app_state.channel.active_metric = "mean_intensity"
    app._app_state.channel.threshold = 0.0
    app._app_state.dataset.well_paths = {"A01": None}
    app._ax_bar_mean, app._ax_bar_frac, app._ax_bar_n = ax_mean, ax_frac, ax_n
    app._ctx.widgets = _HasW(app)
    app._ctx.runtime.barplot.fig = fig

    r.redraw_bars(app._ctx)

    notes = [t.get_text() for a in (ax_mean, ax_frac, ax_n) for t in a.texts
             if getattr(t, SIGNIFICANCE_NOTE_TAG, False)]
    plt.close("all")
    return notes, status


class _HasW:
    """``ctx.widgets`` with the ``has``/``get_or_none`` pair ``redraw_bars``
    uses, over the fake app's bare attributes."""

    def __init__(self, app):
        self._app = app

    def has(self, name):
        return getattr(self._app, name, None) is not None

    def get_or_none(self, name):
        return getattr(self._app, name, None)


@pytest.mark.parametrize("flags,expected", [
    ({"violin": True}, "Violin"),
    ({"swarm": True}, "Beeswarm"),
    ({"show_mode": "all_pts"}, "Show: All points"),
])
def test_a_per_cell_mode_says_the_brackets_cannot_be_drawn(qtbot, monkeypatch,
                                                           flags, expected):
    notes, status = _per_cell_redraw(qtbot, monkeypatch, **flags)
    assert len(notes) == 1, "the per-cell branch left no explanation"
    assert expected in notes[0], (
        f"the note has to name the control that is on, not 'per-cell mode' — "
        f"three separate switches reach it. Got: {notes[0]!r}")
    assert status and status[0].startswith("Significance: ")


def test_a_per_cell_mode_says_nothing_when_the_toggle_is_off(qtbot, monkeypatch):
    from types import SimpleNamespace as NS
    from well_viewer.features.barplot import renderer as r

    from well_viewer.core.plot_state import PlotState

    fig, (ax_mean, ax_frac, ax_n) = plt.subplots(3, 1)
    app = _app(list(MATCHING_RUN), active_val_col="gfp_mean_intensity")
    app._app_state.plot = PlotState()
    app._app_state.plot.show_significance = False
    app._app_state.bar = NS(violin=True, swarm=False)
    app._app_state.channel.active_channel = "gfp"
    app._app_state.channel.active_metric = "mean_intensity"
    app._app_state.channel.threshold = 0.0
    app._app_state.dataset.well_paths = {"A01": None}
    status: list[str] = []
    app._ctx.notify = NS(set_status=lambda m, *a, **k: status.append(m))
    monkeypatch.setattr(r, "draw_per_cell_bar_mode", lambda *_a, **_k: True)
    monkeypatch.setattr(r, "apply_export_style_to_current", lambda *_a, **_k: None)
    monkeypatch.setattr(r, "resolve_bar_timepoints", lambda _c: [(24.0, "24")])
    monkeypatch.setattr(r, "refresh_bar_layout_btn_state", lambda _c: None)
    monkeypatch.setattr(r, "on_cutoff_for_column", lambda *_a, **_k: 0.0)
    monkeypatch.setattr(r, "active_replicate_sets", lambda *_a, **_k: [])
    import well_viewer.core.selection_controller as _sel
    monkeypatch.setattr(_sel, "has_plottable_selection", lambda _c: True)
    app._ax_bar_mean, app._ax_bar_frac, app._ax_bar_n = ax_mean, ax_frac, ax_n
    app._ctx.widgets = _HasW(app)
    app._ctx.runtime.barplot.fig = fig

    r.redraw_bars(app._ctx)

    notes = [t for a in (ax_mean, ax_frac, ax_n) for t in a.texts
             if getattr(t, SIGNIFICANCE_NOTE_TAG, False)]
    plt.close("all")
    assert notes == [] and status == []


# ── brackets drawn, then clipped out of sight ───────────────────────────────
#
# The eighth way the asterisks go missing, and the only one that is not a
# decline: every gate passes, the artists are created, and they are then put
# where nobody can see them.
#
# ``draw_significance_brackets`` grew the panel's y-limit to fit the brackets it
# had just stacked above the bars. ``apply_export_style_to_current`` runs at the
# end of ``redraw_bars`` and re-applies the user's own axis limits **last**, so a
# y-limit set on that panel clipped the growth straight back off — and because
# the brackets are drawn with ``clip_on=False`` they were not clipped so much as
# drawn outside the panel altogether. No gate had fired, so no note could
# describe it; the asterisks were simply not there.
#
# The fix reads the ceiling before placing them and stacks to fit under it. The
# user's limit wins: it is an explicit statement about the panel, and quietly
# overriding it to make room would trade one silent substitution for another.

def _with_user_ylimit(ymax, *, bars=None):
    """Draw the brackets with *ymax* set the way the Properties sidebar sets it.

    Axis limits are **figure**-scoped, so this writes through ``figure_prefs``
    rather than the flat global dict. A probe that wrote the global one measured
    nothing: the renderer read a limit that was not there, took the no-ceiling
    path, and the bug reproduced under a test that claimed to be exercising the
    fix.
    """
    from well_viewer.core.export_prefs_state import ExportPrefsState
    from well_viewer.core.plot_style import apply_export_style_prefs, figure_prefs

    fig, (ax_mean, ax_frac) = plt.subplots(2, 1)
    ax_mean.set_ylim(0, 10)
    ax_frac.set_ylim(0, 1)
    app = _app(list(MATCHING_RUN), active_val_col="gfp_mean_intensity")
    app._app_state.export_prefs = ExportPrefsState()
    status: list[str] = []
    app._ctx.notify = SimpleNamespace(set_status=lambda m, *a, **k: status.append(m))

    prefs = figure_prefs(app._ctx, fig)
    if ymax is not None:
        prefs["y_lim_max"] = str(ymax)

    draw_significance_brackets(app._ctx, bars or _bars(), ax_mean=ax_mean,
                               ax_frac=ax_frac, target_ts=[24.0])
    # The pass that used to undo the growth. Running it is the point.
    apply_export_style_prefs(fig, prefs)

    stars = [t for t in ax_mean.texts if t.get_text() in ("*", "**", "***", "ns")]
    notes = [t.get_text() for t in ax_mean.texts
             if getattr(t, SIGNIFICANCE_NOTE_TAG, False)]
    hi = ax_mean.get_ylim()[1]
    off = [t.get_position()[1] for t in stars if t.get_position()[1] > hi]
    plt.close("all")
    return SimpleNamespace(stars=stars, notes=notes, off_panel=off,
                           ylim_top=hi, status=status)


@pytest.mark.parametrize("ymax", [7.0, 8.0, 12.0, 20.0])
def test_a_user_y_limit_no_longer_pushes_the_brackets_off_the_panel(ymax):
    out = _with_user_ylimit(ymax)
    assert out.stars, f"no bracket drawn at y_lim_max={ymax}"
    assert not out.off_panel, (
        f"y_lim_max={ymax} left {len(out.off_panel)} asterisk(s) above the "
        f"visible top ({out.ylim_top}) — drawn, and invisible")
    assert out.notes == []


def test_the_user_s_own_ceiling_is_not_overridden_to_make_room():
    """Growing past the limit would make the brackets visible by ignoring an
    explicit instruction — one silent substitution traded for another."""
    out = _with_user_ylimit(7.0)
    assert out.ylim_top == pytest.approx(7.0)


def test_no_user_limit_still_grows_the_panel_to_fit():
    out = _with_user_ylimit(None)
    assert out.stars and not out.off_panel
    assert out.ylim_top > 6.5, "the auto-grow path must still make room"


def test_a_ceiling_with_no_room_above_the_bars_says_so():
    """The honest end of the same rule. The tallest bar plus its spread is 6.5,
    so a ceiling of 6.5 leaves nothing to stack into — and the user is told
    which control to move rather than left with a bar plot that quietly stopped
    annotating itself."""
    out = _with_user_ylimit(6.5)
    assert out.stars == []
    assert len(out.notes) == 1
    assert "y-limit" in out.notes[0]
    assert out.status and out.status[0].startswith("Significance: ")


def test_a_ceiling_below_the_bars_says_so_too():
    out = _with_user_ylimit(5.0)
    assert out.stars == []
    assert len(out.notes) == 1 and "y-limit" in out.notes[0]


# ── an empty store is not proof that no test was run ────────────────────────

def test_the_empty_store_note_does_not_deny_that_a_test_ran():
    """"No test has been run yet" was this note's first wording, and it is only
    the *common* reason the store is empty.

    ``stats.compute_pairwise`` also returns ``[]`` after a run: fewer than two
    groups with wells, any group with under two replicate values, a
    misconfigured KS test, scipy missing, or every pair coming back with an
    error. In several of those the Statistics tab has already printed a result
    explaining itself — so a note flatly denying a test happened would
    contradict the panel beside it and send the user to re-run something that
    had already run. The note states what is known and points at what knows
    why.
    """
    _stars, notes, status = _draw([])
    note = notes[0]
    assert "no comparison is stored" in note
    assert "has been run" not in note, (
        f"the note asserts something it cannot know: {note!r}")
    assert "Statistics" in note and "results panel" in note
    assert status[0] == f"Significance: {note.split('Significance: ', 1)[1]}"
