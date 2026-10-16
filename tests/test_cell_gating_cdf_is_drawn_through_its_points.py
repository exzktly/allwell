"""Cell Gating's CDFs are drawn through their own points above a cap (tracker F3e).

The CDF inputs are already one image per well (the first field's first
timepoint), but in nanowell mode that image is the whole mosaic, and a
million-vertex line with a fill under it is seconds of drawing on every redraw.
Above ``CDF_MAX_POINTS`` the curve is drawn through evenly spaced vertices **of
the exact empirical CDF** — not a sample of cells — always including the first and
last, so every point drawn lies on the full curve and the x range is every cell's.

Driven through the real renderer on a real ``AppState`` and ``AppRuntime``, and
read back from the lines it drew.
"""

from __future__ import annotations

from types import SimpleNamespace

import matplotlib
import numpy as np
import pytest

matplotlib.use("Agg")

from matplotlib.figure import Figure  # noqa: E402

from review.features.home.cell_gating import renderer  # noqa: E402
from review.features.home.cell_gating.renderer import CDF_MAX_POINTS, cdf_points  # noqa: E402


def _full(values):
    xs = np.sort(np.asarray(values, dtype=float))
    return xs, np.arange(1, xs.size + 1) / xs.size


def test_under_the_cap_the_curve_is_every_vertex():
    vals = np.random.default_rng(0).lognormal(3, 1, CDF_MAX_POINTS)
    xs, ys, n = cdf_points(vals)
    full_x, full_y = _full(vals)
    assert n == vals.size and xs.tolist() == full_x.tolist() and ys.tolist() == full_y.tolist()


def test_above_the_cap_every_vertex_drawn_is_on_the_full_curve():
    vals = np.random.default_rng(1).lognormal(3, 1, 250_001)
    xs, ys, n = cdf_points(vals)
    full_x, full_y = _full(vals)
    assert n == vals.size and len(xs) <= CDF_MAX_POINTS
    ranks = np.round(ys * n).astype(int) - 1
    assert np.array_equal(full_x[ranks], xs) and np.array_equal(full_y[ranks], ys)
    assert (xs[0], xs[-1], ys[-1]) == (full_x[0], full_x[-1], 1.0), "the ends of the curve were dropped"
    assert np.all(np.diff(ranks) > 0)


@pytest.fixture
def ctx():
    from review.core.app_runtime import AppRuntime
    from review.core.app_state import AppState
    from review.features.registry import attach_feature_state, build_feature_registry

    build_feature_registry()
    state = AppState()
    attach_feature_state(state)
    runtime = AppRuntime(None, store={})
    c = SimpleNamespace(state=state, runtime=runtime)
    runtime.cell_gating.figure = Figure()
    runtime.cell_gating.canvas = SimpleNamespace(draw_idle=lambda: None, setMinimumHeight=lambda _h: None)
    runtime.cell_gating.axes_stack = []
    runtime.cell_gating.ratio_data = {}
    return c


def _note(fig):
    return [t.get_text() for t in fig.texts if getattr(t, renderer.THINNED_NOTE_TAG, False)]


def test_the_figure_draws_the_thinned_curve_and_says_so(ctx):
    rng = np.random.default_rng(2)
    areas = rng.lognormal(5, 0.5, 30_000).tolist()
    ctx.runtime.cell_gating.cell_areas = areas
    ctx.runtime.cell_gating.fluor_data = {"gfp": rng.lognormal(3, 1, 800).tolist()}
    renderer._cell_gating_plot_cdf(ctx)
    fig = ctx.runtime.cell_gating.figure
    area_line = fig.axes[0].lines[0]
    full_x, _full_y = _full(areas)
    assert len(area_line.get_xdata()) <= CDF_MAX_POINTS
    assert area_line.get_xdata()[0] == full_x[0] and area_line.get_xdata()[-1] == full_x[-1]
    gfp_line = fig.axes[1].lines[0]
    assert len(gfp_line.get_xdata()) == 800, "a curve under the cap was thinned"
    assert _note(fig) == [f"CDFs over {CDF_MAX_POINTS:,} cells are drawn through {CDF_MAX_POINTS:,} of their "
                          "points (largest: 30,000); every point drawn is on the full curve"]


def test_a_figure_under_the_cap_carries_no_note(ctx):
    ctx.runtime.cell_gating.cell_areas = np.random.default_rng(3).lognormal(5, 0.5, 500).tolist()
    ctx.runtime.cell_gating.fluor_data = {}
    renderer._cell_gating_plot_cdf(ctx)
    assert _note(ctx.runtime.cell_gating.figure) == []
