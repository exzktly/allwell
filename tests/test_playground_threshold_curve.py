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
