"""Scatter draws any nanowell metric against any other (NANOWELL_OBSERVATIONS_TRACKER #9).

Through the real Scatter builder and orchestrated redraw in a minimal shell (CLAUDE.md §7.8 #3), on the synthetic
nanowell dataset, reading the points the figure holds (§7.4 #1). The fixture is built so the right and wrong
answers differ (§7.8 #2): cell rows would give one point per *cell* with GFP on both axes, nanowell rows give one
point per *nanowell* with its count on X and its cells' mean GFP on Y; a per-well median is neither.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from review.domain.observation import NANOWELLS  # noqa: E402
from tests.fixtures.nanowell_dataset import COUNTS, GFP, SITES  # noqa: E402


@pytest.fixture
def scatter_ctx(qtbot, tmp_path):
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    from review.features.scatter.controller import update_scatter_menus
    from review.features.scatter.tab_view import build_scatter_tab
    from tests._minimal_shell import load_dataset, minimal_shell
    from tests.fixtures.nanowell_dataset import build_nanowell_dataset

    root = build_nanowell_dataset(tmp_path / "nw")
    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, root / "out")
    ctx.state.dataset.data_dir = root / "out"
    frame = QWidget()
    QHBoxLayout(frame)
    window.setCentralWidget(frame)
    # The Plotting page's shared band, as centre_view registers it: a leaf's controls live in its slots, and a
    # free-standing fallback band nothing holds would be collected with every control in it.
    from review.core.ui_helpers import PLOTTING_BAND_KEY
    from widgets.control_band import ControlBand
    ctx.widgets.register(PLOTTING_BAND_KEY, ControlBand(frame))
    build_scatter_tab(ctx, frame)
    update_scatter_menus(ctx)
    ctx.state.selection.selected_wells = {"A01", "B01"}
    return ctx


def _points(ax) -> np.ndarray:
    offsets = [c.get_offsets() for c in ax.collections if len(c.get_offsets())]
    return np.vstack(offsets) if offsets else np.empty((0, 2))


def _footer(fig) -> str:
    return next(t.get_text() for t in fig.texts if t.get_gid() == "observations_footer")


def test_each_nanowell_is_a_point_with_its_count_against_its_brightness(scatter_ctx):
    from review.features.scatter.controller import redraw_scatter_orchestrate

    scatter_ctx.state.plot.observations = NANOWELLS
    redraw_scatter_orchestrate(scatter_ctx)
    ax = scatter_ctx.widgets.get("_ax_scatter")
    got = sorted(map(tuple, _points(ax)))
    want = sorted((float(n), GFP[w][i]) for w, by_tp in COUNTS.items() for counts in by_tp.values()
                  for i, n in enumerate(counts) if n)            # an empty nanowell has no brightness
    assert got == want
    assert ax.get_xlabel() == "Cell count" and ax.get_ylabel() == "GFP Brightness (mean per-cell intensity)"
    empty = sum(c == 0 for by in COUNTS.values() for cs in by.values() for c in cs)
    total = len(COUNTS) * len(SITES) * 2
    assert _footer(scatter_ctx.widgets.get("_scatter_fig")).startswith(
        f"Observations: nanowells · {total - empty} of {total} used · excluded: no cells {empty}")
    # The per-cell axis pickers give way to the nanowell pair while nanowells are drawn.
    assert scatter_ctx.widgets.get("_scatter_ch_x_cb").parentWidget().isHidden()
    assert not scatter_ctx.widgets.get("_scatter_cells_nw_x_cb_grp").isHidden()


def test_any_two_metrics_can_be_set_against_each_other(scatter_ctx):
    from review.features.scatter.controller import redraw_scatter_orchestrate

    scatter_ctx.state.plot.observations = NANOWELLS
    redraw_scatter_orchestrate(scatter_ctx)
    y = scatter_ctx.widgets.get("_scatter_cells_nw_y_cb")
    y.setCurrentIndex(y.findData("c"))                     # confluence: the fixture's 0.1 × count
    got = sorted(map(tuple, _points(scatter_ctx.widgets.get("_ax_scatter"))))
    want = sorted((float(n), round(0.1 * n, 3)) for by_tp in COUNTS.values() for counts in by_tp.values()
                  for n in counts)
    assert np.allclose(got, want)


def test_per_well_points_are_each_well_s_median_over_its_nanowells(scatter_ctx):
    from review.features.scatter.controller import redraw_scatter_agg_orchestrate

    scatter_ctx.state.plot.observations = NANOWELLS
    scatter_ctx.state.scatter.mode = "aggregate"
    redraw_scatter_agg_orchestrate(scatter_ctx)
    got = sorted(map(tuple, _points(scatter_ctx.widgets.get("_ax_scatter_agg"))))
    want = []
    for w, by_tp in COUNTS.items():
        for counts in by_tp.values():
            occupied = [(n, GFP[w][i]) for i, n in enumerate(counts) if n]
            want.append((float(np.median([n for n, _ in occupied])), float(np.median([g for _, g in occupied]))))
    assert got == sorted(want)


def test_under_cells_the_scatter_draws_cells_as_before(scatter_ctx):
    from review.features.scatter.controller import redraw_scatter_orchestrate

    redraw_scatter_orchestrate(scatter_ctx)
    fig = scatter_ctx.widgets.get("_scatter_fig")
    # Cells, and the footer says they are a nanowell plate's cells, pooled from its occupied nanowells.
    assert _footer(fig) == "Observations: cells · pooled from 3–4 occupied nanowells per well"
    assert not scatter_ctx.widgets.get("_scatter_ch_x_cb").parentWidget().isHidden()
