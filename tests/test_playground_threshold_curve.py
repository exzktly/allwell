"""The Playground's threshold curve: the controller, and what gets drawn.

Two things this file is careful about, both of them mistakes made earlier in
this same body of work.

The stubs here are mine, so they cannot disagree with me (CLAUDE.md §7.8 face
one). The antidote is in ``tests/test_playground.py``, where one slow-lane test
builds the tab through its **real** constructor and presses the **real**
button; everything below may stay cheap because that one is not.

And the assertions are on what reached the boundary — the figure's lines, the
button's enabled state, the counts handed to the worker — rather than on values
computed on the way there (§7.4 #1).
"""

from __future__ import annotations

import math
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

from matplotlib.figure import Figure  # noqa: E402

from review.domain.threshold_sweep import (  # noqa: E402
    curve_from_counts, sweep_grid,
)
from review.features.image_browser.playground.sweep_curve_view import (  # noqa: E402
    draw_threshold_curve,
)


def _decay(grid, total=400):
    return [int(round(total / (1 + math.exp((t - 0.5) / 0.05)))) for t in grid]


def _marker_only_lines(ax):
    """The single-point, markers-only artists — i.e. the "stopped here" dot."""
    return [ln for ln in ax.lines
            if str(ln.get_linestyle()).lower() == "none"
            and len(ln.get_xdata()) == 1]


# ── what is drawn ───────────────────────────────────────────────────────────

def test_the_figure_carries_the_curve_and_the_users_own_threshold():
    """Excludes the plot that computes the curve and draws an empty axis.

    Asserting the counts are right is not asserting the line is there — the
    defect this rule comes from was a correct colour recomputed and discarded
    before it reached the widget.
    """
    grid = sweep_grid()
    fig = Figure()
    draw_threshold_curve(fig, curve_from_counts(grid, _decay(grid), current=0.2))

    ax = fig.axes[0]
    assert len(ax.lines) == 2, "the curve, and the marker for where the user is"
    xs, ys = ax.lines[0].get_data()
    assert list(xs) == pytest.approx(list(grid))
    assert list(ys) == _decay(grid)
    assert "your threshold = 0.2" in " ".join(
        t.get_text() for t in ax.get_legend().get_texts())


def test_a_threshold_left_on_auto_draws_no_marker_rather_than_a_wrong_one():
    """``None`` means the pretrained model's own value, which this tab does not
    know. Drawing a line at 0 would claim the user had chosen 0."""
    grid = sweep_grid(steps=5)
    fig = Figure()
    draw_threshold_curve(fig, curve_from_counts(grid, _decay(grid), current=None))
    assert len(fig.axes[0].lines) == 1


def test_nothing_in_the_figure_recommends_a_threshold():
    """Excludes the plot that grows the suggestion back.

    The measurement that says not to is in ``review.domain.threshold_sweep``:
    the plateau rule returns either the zero tail or the Analyze form's own
    hardcoded constant, depending on how ``log(0)`` is guarded.
    """
    grid = sweep_grid()
    fig = Figure()
    draw_threshold_curve(fig, curve_from_counts(grid, _decay(grid), current=0.2))
    ax = fig.axes[0]
    words = " ".join(
        [ax.get_title(), ax.get_xlabel(), ax.get_ylabel()]
        + [t.get_text() for t in ax.get_legend().get_texts()]
    ).lower()
    for banned in ("suggest", "recommend", "optimal", "best threshold"):
        assert banned not in words


def test_a_declined_sweep_draws_its_reason_rather_than_empty_axes():
    """Excludes the blank window. The user pressed a button and is owed an
    answer; "no nuclei at any threshold" is one."""
    fig = Figure()
    draw_threshold_curve(fig, curve_from_counts([0.1, 0.2], [0, 0]))
    ax = fig.axes[0]
    assert len(ax.lines) == 0
    assert any("No nuclei were detected" in t.get_text() for t in ax.texts)


def test_a_figure_with_no_sweep_yet_says_so_instead_of_raising():
    from review.domain.threshold_sweep import ThresholdCurve
    fig = Figure()
    draw_threshold_curve(fig, ThresholdCurve())
    assert any("No sweep has been run yet" in t.get_text()
               for t in fig.axes[0].texts)


# ── the controller ──────────────────────────────────────────────────────────

class _Btn:
    def __init__(self):
        self.enabled = True
        self.visible = True

    def setEnabled(self, v):
        self.enabled = bool(v)

    def setVisible(self, v):
        self.visible = bool(v)


def test_the_buttons_swap_while_a_sweep_runs_and_swap_back(monkeypatch):
    """Excludes the Stop button that is built and never shown.

    This is not handled by the tab's change fan-out — nothing in it has ever
    looked at the segmentation's running flag either — so the controller does
    it, on every exit path including failure.
    """
    from review.features.image_browser.playground import controller as pc

    win = SimpleNamespace(_playground_sweep_btn=_Btn(),
                          _playground_sweep_stop_btn=_Btn())
    pc._set_sweep_buttons(win, running=True)
    assert win._playground_sweep_btn.enabled is False
    assert win._playground_sweep_stop_btn.visible is True

    pc._set_sweep_buttons(win, running=False)
    assert win._playground_sweep_btn.enabled is True
    assert win._playground_sweep_stop_btn.visible is False


def test_setting_the_buttons_survives_a_window_that_has_neither():
    """The Playground builds lazily, so the controller can be reached before
    the section exists."""
    from review.features.image_browser.playground import controller as pc
    pc._set_sweep_buttons(SimpleNamespace(), running=True)


def test_a_stopped_sweep_keeps_the_points_it_measured():
    """Excludes the worker that discards a partial result.

    The counts are what the user waited for; a curve over the first eight of
    nineteen thresholds is evidence, and the readout says how far it got.
    """
    from review.features.image_browser.playground import worker as pw

    grid = sweep_grid()
    stop = {"n": 0}

    def _fake_sweep(arr, thresholds, **kw):
        out = []
        for t in thresholds:
            if kw["should_stop"]():
                break
            out.append(len(out) + 1)
        return out

    def _should_stop():
        stop["n"] += 1
        return stop["n"] > 8

    import services.frame_preview as fp
    orig = fp.sweep_frame
    try:
        fp.sweep_frame = _fake_sweep
        counts = pw.threshold_sweep_for_frame(np.zeros((4, 4)), grid,
                                              should_stop=_should_stop)
    finally:
        fp.sweep_frame = orig

    assert len(counts) == 8
    curve = curve_from_counts(grid[:len(counts)], counts)
    assert len(curve.points) == 8


# ── the comparison the feature exists for ───────────────────────────────────

def test_the_model_default_is_drawn_beside_the_users_own_threshold():
    """Excludes the plot that marks where the user is against nothing.

    The wrong implementation that would still pass "there is a dashed line
    somewhere" is the one this branch started as: the user's own threshold
    marked, and no reference to read it against. Both verticals are asserted
    by *position*, so a figure carrying one of them twice fails too.
    """
    grid = sweep_grid()
    fig = Figure()
    draw_threshold_curve(fig, curve_from_counts(grid, _decay(grid), current=0.2,
                                                model_default=0.479071))
    ax = fig.axes[0]
    verticals = [ln for ln in ax.lines
                 if len(set(ln.get_xdata())) == 1 and len(ln.get_xdata()) == 2]
    xs = sorted(float(ln.get_xdata()[0]) for ln in verticals)
    assert xs == pytest.approx([0.2, 0.479071])
    labels = " ".join(t.get_text() for t in ax.get_legend().get_texts())
    assert "model default = 0.479" in labels
    assert "your threshold = 0.2" in labels


def test_a_sweep_with_no_model_default_draws_one_vertical_not_two():
    """``model_prob_threshold()`` is ``None`` until a model has been loaded,
    and a guessed reference line is worse than none."""
    grid = sweep_grid()
    fig = Figure()
    draw_threshold_curve(fig, curve_from_counts(grid, _decay(grid), current=0.2))
    ax = fig.axes[0]
    verticals = [ln for ln in ax.lines
                 if len(ln.get_xdata()) == 2 and len(set(ln.get_xdata())) == 1]
    assert len(verticals) == 1


# ── a stopped sweep, as a picture ───────────────────────────────────────────

def test_a_stopped_sweep_leaves_the_unmeasured_range_visibly_empty():
    """Excludes the figure that rescales to the fragment it measured.

    The wrong implementation that passes "the curve is drawn" is matplotlib's
    own autoscale: eight points from 0.05 to 0.2 fill the panel, the line runs
    corner to corner, and the picture asserts a finding about the whole range
    that the sweep never reached. The x limits must be what was *asked for*.
    """
    grid = sweep_grid()
    measured = _decay(grid)[:8]
    fig = Figure()
    draw_threshold_curve(fig, curve_from_counts(
        grid[:8], measured, current=0.2, requested=len(grid),
        requested_span=(grid[0], grid[-1])))
    ax = fig.axes[0]
    assert ax.get_xlim() == pytest.approx((grid[0], grid[-1]))
    # And the axis is genuinely wider than the data, or the assertion above
    # would be satisfied by autoscale on a complete sweep.
    assert max(grid[:8]) < grid[-1] - 0.3


def test_a_stopped_sweep_marks_where_the_measurement_stopped():
    """Excludes the figure whose line simply ends mid-panel.

    With the axis spanning the whole requested range, a line that stops has to
    say whether it stopped because the sweep did or because the counts did.
    """
    grid = sweep_grid()
    fig = Figure()
    curve = curve_from_counts(grid[:8], _decay(grid)[:8], requested=len(grid),
                              requested_span=(grid[0], grid[-1]))
    draw_threshold_curve(fig, curve)
    ax = fig.axes[0]
    # ``get_linestyle`` answers "None", capitalised, for a line drawn with
    # markers only. Spelling it "none" here matched nothing, which also made
    # the complete-sweep test below pass against a figure that drew the marker
    # unconditionally -- CLAUDE.md §7.8, caught by writing the positive case
    # first and watching it fail.
    ends = _marker_only_lines(ax)
    assert len(ends) == 1
    assert float(ends[0].get_xdata()[0]) == pytest.approx(grid[7])
    assert ends[0].get_markerfacecolor() == "none", "hollow, not a data point"
    assert "stopped here" in " ".join(t.get_text()
                                      for t in ax.get_legend().get_texts())


def test_a_complete_sweep_carries_no_stopped_marker():
    grid = sweep_grid()
    fig = Figure()
    draw_threshold_curve(fig, curve_from_counts(grid, _decay(grid)))
    ax = fig.axes[0]
    assert _marker_only_lines(ax) == []
    assert ax.get_xlim() == pytest.approx((grid[0], grid[-1]))


# ── what the figure says about itself ───────────────────────────────────────

def test_the_figure_names_the_frame_it_measured():
    """Excludes three consecutive sweeps repainting into three
    indistinguishable pictures, saved to a notebook with no attribution."""
    grid = sweep_grid()
    fig = Figure()
    draw_threshold_curve(fig, curve_from_counts(grid, _decay(grid),
                                                subject="GFP · B03 · FOV 1"))
    title = fig.axes[0].get_title()
    assert title.startswith("GFP · B03 · FOV 1\n")
    assert "19 thresholds" in title


def test_the_figure_warns_that_a_plateau_is_not_a_validation():
    """Excludes the plot whose flat shoulder reads as confirmation.

    The wrong implementation that passes "some text was drawn" is the title,
    so this asserts the sentence.
    """
    grid = sweep_grid()
    fig = Figure()
    draw_threshold_curve(fig, curve_from_counts(grid, _decay(grid)))
    said = " ".join(t.get_text() for t in fig.axes[0].texts)
    assert "insensitive to the threshold" in said
    assert "not that the detections are right" in said


# ── a truncated flat run is not the "count never moves" finding ─────────────

def test_a_flat_start_to_a_stopped_sweep_is_scoped_to_what_was_measured():
    """Excludes the finding derived from three points before the knee.

    A *complete* sweep whose count never moves says something true about the
    frame: the threshold does not matter here. Derived from the fragment a
    user got before pressing Stop, the same sentence is an artifact of
    stopping — the complete sweep of this frame goes to zero. So the refusal
    is still made (the user pressed a button and is owed an answer) but it is
    scoped: it names how many thresholds it saw and says the rest was not
    measured.

    The wrong implementation that would pass "it declines" is the unscoped
    one, which is why both halves below are asserted against each other.
    """
    grid = sweep_grid()
    short = curve_from_counts(grid[:3], [400, 400, 400], requested=len(grid),
                              requested_span=(grid[0], grid[-1]))
    assert short.truncated is True
    assert "before you stopped" in short.declined
    assert "was not measured" in short.declined

    # The identical counts, swept to the end, are a finding about the frame
    # and must NOT carry either hedge.
    whole = curve_from_counts([0.1, 0.2, 0.3], [400, 400, 400])
    assert whole.truncated is False
    assert whole.declined
    assert "before you stopped" not in whole.declined
    assert "was not measured" not in whole.declined


# ── what the controller puts into the curve, and what the read-out keeps ────

class _Lbl:
    def __init__(self):
        self.t = ""

    def setText(self, v):
        self.t = str(v)

    def text(self):
        return self.t


def _ctx_with(st):
    """A context thin enough to call the read-out helpers against.

    Mine, so it cannot disagree with me (§7.8 face one) — which is why the
    sweep's own read-out is also driven through the real widget in
    ``tests/test_playground.py``. This file covers the shapes that one press
    cannot reach in a single run.
    """
    return SimpleNamespace(
        window=SimpleNamespace(_playground_status=_Lbl()),
        state=SimpleNamespace(playground=st),
        runtime=SimpleNamespace(playground=SimpleNamespace()),
    )


def test_the_curve_is_asked_for_the_models_own_default_not_a_literal():
    """Excludes the reference line hardcoded to whatever the weights say today.

    ``model_prob_threshold`` reads ``model.thresholds.prob`` off the loaded
    model, so the number on the plot is the number the sweep was measured
    against. A literal would be a fourth copy of it and would go stale the day
    anyone changes model — and the wrong implementation that passes "there is
    a default" is exactly a literal.
    """
    import services.frame_preview as fp
    import review.features.image_browser.playground.controller as pc
    src = pc.playground_sweep_now.__doc__ or ""
    assert "recommendation" in src
    # The controller imports it from the service rather than writing a number.
    import inspect
    body = inspect.getsource(pc.playground_sweep_now)
    assert "model_prob_threshold()" in body
    assert "0.47" not in body, "a literal copy of the weights' own threshold"
    # And the service reads it off the model rather than returning a constant.
    assert "thresholds" in inspect.getsource(fp.model_prob_threshold)


def test_a_curve_measured_on_another_frame_says_so_rather_than_sitting_there():
    """Excludes the sentence that describes one frame under a picture of
    another.

    The result now survives a repaint, which is what makes this reachable:
    sweep B03, then click to C04, and without this the read-out goes on
    reporting B03's counts beside C04's pixels. The dialog is still open
    showing that curve, so the honest move is to name whose frame it is — not
    to drop the sentence and leave the picture unexplained.
    """
    from review.features.image_browser.playground import controller as pc
    from review.features.image_browser.playground.state import PlaygroundState

    grid = sweep_grid()
    st = PlaygroundState(well="B03", channel="GFP", fov="1", timepoint="24h")
    curve = curve_from_counts(grid, _decay(grid), subject="GFP · B03 · FOV 1 · 24h")
    st.sweep_curve = curve
    st.sweep_status = curve.describe()
    ctx = _ctx_with(st)

    assert pc._describe_sweep(ctx) == [st.sweep_status], "same frame, no hedge"

    st.well = "C04"
    lines = pc._describe_sweep(ctx)
    assert len(lines) == 2
    assert "not the frame on screen" in lines[0]
    assert "GFP · B03 · FOV 1 · 24h" in lines[0]
    assert lines[1] == st.sweep_status, "the counts are still shown, not dropped"


def test_a_running_sweep_never_leaves_the_readout_silent():
    from review.features.image_browser.playground import controller as pc
    from review.features.image_browser.playground.state import PlaygroundState

    st = PlaygroundState()
    st.sweep_running = True
    assert pc._describe_sweep(_ctx_with(st)) == ["Threshold curve: sweeping…"]
    st.sweep_status = "Threshold curve: 4 of 19…"
    assert pc._describe_sweep(_ctx_with(st)) == ["Threshold curve: 4 of 19…"]


def test_no_sweep_yet_adds_no_line_at_all():
    from review.features.image_browser.playground import controller as pc
    from review.features.image_browser.playground.state import PlaygroundState
    assert pc._describe_sweep(_ctx_with(PlaygroundState())) == []


def test_loading_another_dataset_drops_the_previous_plates_sweep():
    """``clear_caches`` runs on a dataset swap. A sentence about the previous
    plate's frame, left in the read-out of the new one, is the same silent
    substitution one selector click away."""
    from review.features.image_browser.playground.state import PlaygroundState
    st = PlaygroundState()
    st.sweep_status = "19 thresholds from 0.05 to 0.95: 86 nuclei down to 0."
    st.sweep_curve = object()
    st.clear_caches()
    assert st.sweep_status == ""
    assert st.sweep_curve is None
