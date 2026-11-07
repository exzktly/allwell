"""Cell Gating's panel titles stay inside a phone-width figure.

The page-render test, mini dataset on a phone upright, in CI: "Home/Cell Gating: [figure] FigureCanvas: 'MCHERRY
Distribution' spans 213..386 of 374". The page draws a 2 x 2 grid of CDFs on a figure as wide as the phone's page
(3.74 in at 100 dpi), each panel about 116 px wide under a 10 pt bold title of about 150-170 px. The page never goes
through ``plot_style``'s style pass, which is where every other plot's titles are fitted to their axes
(``plot_style._fit_title``), so the right-hand column's titles hung off the figure's edge.

Driven through the real renderer on a real ``AppState`` and ``AppRuntime``, as
``test_cell_gating_cdf_is_drawn_through_its_points.py`` does.
"""

from __future__ import annotations

from types import SimpleNamespace

import matplotlib
import numpy as np
import pytest

matplotlib.use("Agg")

from matplotlib.figure import Figure  # noqa: E402

from review.core import plot_style  # noqa: E402
from review.features.home.cell_gating import renderer  # noqa: E402


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
    runtime.cell_gating.figure = Figure(figsize=(3.74, 7.6), dpi=100)    # the phone page's figure, measured
    runtime.cell_gating.canvas = SimpleNamespace(draw_idle=lambda: None, setMinimumHeight=lambda _h: None)
    runtime.cell_gating.axes_stack = []
    runtime.cell_gating.ratio_data = {}
    return c


@pytest.mark.parametrize("family", ["Inter", "DejaVu Sans"])
def test_every_panel_title_is_inside_a_phone_width_figure(ctx, family):
    """Under both faces matplotlib may draw in: the bundled Inter, and DejaVu Sans, matplotlib's own and wider
    one, which is what the CI runner drew the titles in (173 px for 'MCHERRY Distribution' against Inter's 153)."""
    with matplotlib.rc_context({"font.family": [family]}):
        _draw_and_check(ctx)


def _draw_and_check(ctx):
    rng = np.random.default_rng(0)
    ctx.runtime.cell_gating.cell_areas = rng.lognormal(5, 0.5, 500).tolist()
    ctx.runtime.cell_gating.fluor_data = {name: rng.lognormal(3, 1, 500).tolist()
                                         for name in ("dapi", "gfp", "mcherry")}
    renderer._cell_gating_plot_cdf(ctx)
    fig = ctx.runtime.cell_gating.figure
    measure = plot_style._renderer_of(fig)
    titles = [ax.title for ax in fig.axes if ax.title.get_text()]
    assert len(titles) == 4, "premise: the area panel and three channels each have a title"
    outside = [(t.get_text(), round(e.x0), round(e.x1)) for t in titles
               for e in [t.get_window_extent(measure)] if e.x0 < -1.5 or e.x1 > fig.bbox.width + 1.5]
    assert not outside, f"titles past the {fig.bbox.width:.0f}px figure's edges: {outside}"
    assert "MCHERRY" in " ".join(t.get_text() for t in titles), "a fitted title still names its channel"
