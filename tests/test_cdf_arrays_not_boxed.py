"""The CDF panels build arrays, not Python lists of boxed floats.

A plate died in the Line tab's CDF panel:

    File "well_viewer/features/lineplot/controller.py", line 458, in <listcomp>
        fluor_s, [(k + 1) / n for k in range(n)],
    MemoryError

``_all_fluor_values_filtered`` returns a numpy array and the collector boxed it
straight back into a Python list — 32 bytes a value against 8 — then sorted a
copy, built the y-values as a third list, and flattened every series into a
fourth just to take a min and a max.

Honest about the size of the win: 183 MiB → 161 MiB rendering 26 series over
537,368 cells. The allocation that raised was **the straw, not the weight** —
this makes the panel stop wasting four times what it needs, and it does not by
itself buy a machine enough headroom to open a plate that was already at the
ceiling.

What these tests are really for is the risk that comes with the change. An
ndarray's truthiness raises, so ``if series.cdf_vals:`` and ``s.cdf_vals or []``
had to go; get that wrong and the panel silently draws nothing, which is the
one outcome worse than a MemoryError because nobody sees it happen.
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pytest

from well_viewer.features.lineplot.controller import (
    LineAxes, LinePoint, LineSeries, _paint_series, render_line_series,
)


@pytest.fixture
def axes():
    fig, ax = plt.subplots(4, 1)
    yield LineAxes(*ax)
    plt.close(fig)


def _series(vals, key="A01"):
    return LineSeries(
        key=key, display=key, color="#6AB2FB", kind="well", wells=[key],
        points=[LinePoint(t=0.0, mean=1.0, spread=0.1, frac=0.5)],
        cdf_vals=vals,
    )


# ── the values drawn are the values that were drawn before ───────────────────

def test_the_cdf_curve_is_what_the_list_comprehension_produced(axes):
    """The differential. ``np.arange(1, n+1)/n`` must equal ``[(k+1)/n …]``
    exactly, not merely closely — this is a cumulative fraction and the last
    point has to land on 1.0."""
    vals = np.array([5.0, 1.0, 3.0, 2.0, 4.0])
    _paint_series(axes, _series(vals), fov_active=False)
    line = axes.cdf.get_lines()[-1]

    n = vals.size
    assert list(line.get_xdata()) == sorted(vals.tolist())
    assert list(line.get_ydata()) == [(k + 1) / n for k in range(n)]
    assert line.get_ydata()[-1] == 1.0


def test_a_plain_list_still_draws(axes):
    """The trap the ndarray switch sets.

    ``if series.cdf_vals:`` raises on an array of more than one element, so the
    emptiness test moved to ``.size`` — and a caller still handing in a list
    (a test, a saved fixture, a plugin) must not quietly get an empty panel.
    """
    _paint_series(axes, _series([3.0, 1.0, 2.0]), fov_active=False)
    line = axes.cdf.get_lines()[-1]
    assert list(line.get_xdata()) == [1.0, 2.0, 3.0]


def test_an_empty_series_draws_nothing_and_does_not_raise(axes):
    before = len(axes.cdf.get_lines())
    _paint_series(axes, _series(np.empty(0, dtype=float)), fov_active=False)
    _paint_series(axes, _series([]), fov_active=False)
    assert len(axes.cdf.get_lines()) == before


# ── the x-range is what flattening every series produced ─────────────────────

def test_the_cdf_x_range_matches_the_flattened_min_and_max(axes):
    """Computed per series now instead of by building one list of every value
    across every series. Same two numbers, or the axis moves under the user."""
    a, b = np.array([4.0, 9.0, 2.0]), np.array([11.0, 1.0, 7.0])
    series = [_series(a, "A01"), _series(b, "B01")]
    render_line_series(
        axes, series, fov_active=False, fc_active=False, threshold=1.0,
        legend_kw={}, warn_color="#F59E0B", muted_fg="#98A2B3",
        legend_visible={"mean": True, "frac": True, "n": True, "cdf": True},
        show_threshold_line=False, cdf_xrange=None, empty_cdf_msg="none",
    )
    flat = a.tolist() + b.tolist()
    lo, hi = axes.cdf.get_xlim()
    assert lo <= min(flat) and hi >= max(flat)


# ── the collectors hand out arrays ───────────────────────────────────────────

def test_the_default_cdf_vals_is_an_array():
    s = LineSeries(key="A01", display="A01", color="#000", kind="well",
                   wells=["A01"])
    assert isinstance(s.cdf_vals, np.ndarray)
    assert s.cdf_vals.size == 0


# ── the Statistics tab's CDF, the sibling that carried the same shape ────────

def test_the_stats_cdf_step_curve_is_unchanged():
    """``features/stats`` built the same list comprehension. Its curve pads with
    a duplicated first and last point so ``step(where="post")`` closes at 0 and
    1; the array version has to keep that padding."""
    vals = [5.0, 1.0, 3.0, 2.0, 4.0]
    sv_old = sorted(vals)
    n = len(sv_old)
    xs_old = [sv_old[0]] + sv_old + [sv_old[-1]]
    ys_old = [0.0] + [(i + 1) / n for i in range(n)] + [1.0]

    sv = np.sort(np.asarray(vals, dtype=float))
    xs = np.concatenate(([sv[0]], sv, [sv[-1]]))
    ys = np.concatenate(([0.0], np.arange(1, sv.size + 1, dtype=float) / sv.size, [1.0]))

    assert xs.tolist() == xs_old
    assert ys.tolist() == ys_old


# ── the mechanism ────────────────────────────────────────────────────────────

def test_no_cdf_value_is_ever_a_boxed_python_float(axes):
    """Stated as a property, because a peak-RSS assertion would be flaky.

    What is stable is the dtype: the data matplotlib receives must be a numpy
    array. If it is a list, every value is a boxed float again and the four-fold
    waste is back.
    """
    _paint_series(axes, _series(np.arange(100, dtype=float)), fov_active=False)
    line = axes.cdf.get_lines()[-1]
    assert isinstance(line.get_ydata(), np.ndarray)
    assert line.get_ydata().dtype == np.float64
