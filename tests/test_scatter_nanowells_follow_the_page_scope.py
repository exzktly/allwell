"""The Scatter Plot's nanowell scatter draws the nanowells the Nanowells page draws (ARCHITECTURE §6.2l).

The report: a Subset built on the Nanowells page (and so honoured by its Distribution view) was ignored by the
Scatter Plot under Observations: Nanowells, which drew every nanowell of the selected wells. The scatter now asks
for the page's scope (``nanowell_observations(page_scope=True)``): the page's excluded wells and Subset are applied
by the Nanowells feature, its Fixed cohort by the scatter over the two metrics it draws, and the footer says each.

These drive the real Scatter builder and orchestrated redraw (CLAUDE.md §7.8 #3) and read the drawn points
(§7.4 #1). The fixture's counts make each wrong implementation draw a different set: the Subset keeps three of ten
nanowells, none of them at the same positions as the unscoped set's extremes, and the cohort drops exactly the one
nanowell whose count is defined at both timepoints but whose brightness is not — a cohort judged on one axis only
drops nothing.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from review.domain.observation import COHORT_REASON, NANOWELLS  # noqa: E402
from tests.fixtures.nanowell_dataset import COUNTS, GFP  # noqa: E402
from tests.test_scatter_draws_nanowell_observations import _footer, _points, scatter_ctx  # noqa: E402,F401


def _page(ctx):
    return ctx.state.feature_slice("nanowell")


def _drawn(ctx):
    from review.features.scatter.controller import redraw_scatter_orchestrate

    ctx.state.plot.observations = NANOWELLS
    redraw_scatter_orchestrate(ctx)
    ax = ctx.widgets.get("_ax_scatter")
    return sorted(map(tuple, _points(ax))), _footer(ctx.widgets.get("_scatter_fig"))


def _every_point(wells=("A01", "B01"), skip=()):
    """(count, brightness) of every nanowell × timepoint with a cell — an empty nanowell has no brightness."""
    return sorted((float(n), GFP[w][i]) for w in wells for tp, counts in COUNTS[w].items()
                  for i, n in enumerate(counts) if n and (w, tp, i) not in skip)


def _starting_at_least_two():
    from review.features.nanowell import subset_compute as ss

    return ss.Subset((ss.Condition("n0", "ge", 2),)).validated()


def test_the_page_s_subset_decides_which_nanowells_the_scatter_draws(scatter_ctx):
    ctx = scatter_ctx
    assert _drawn(ctx)[0] == _every_point()            # no Subset: every nanowell, as before
    _page(ctx).subset = _starting_at_least_two()
    got, footer = _drawn(ctx)
    # Starting cells ≥ 2 at the earliest timepoint: A01's (1,0) and (-1,1), B01's (0,0) — at both timepoints.
    want = sorted([(2.0, 20.0), (2.0, 20.0), (4.0, 40.0), (4.0, 40.0), (2.0, 5.0), (2.0, 5.0)])
    assert got == want
    assert "3 of 10 nanowells kept" in footer, footer


def test_a_well_excluded_on_the_page_is_not_drawn_and_the_footer_names_it(scatter_ctx):
    ctx = scatter_ctx
    _page(ctx).excluded_wells = ["B01"]
    got, footer = _drawn(ctx)
    assert got == _every_point(wells=("A01",))
    assert "Left out with Exclude well on the Nanowells page: B01." in footer, footer


def test_the_fixed_cohort_judges_both_axes(scatter_ctx):
    ctx = scatter_ctx
    _page(ctx).cohort = True
    got, footer = _drawn(ctx)
    # A01's (-1,0) holds no cell at 0h (so no brightness) and 5 at 24h: its count is defined at both timepoints,
    # its brightness at one, so the cohort over the two plotted metrics leaves its 24h point out.
    assert got == _every_point(skip={("A01", "24h", 4)})
    assert f"{COHORT_REASON} 1" in footer, footer


def test_a_fold_change_axis_says_which_baseline_it_is_over(scatter_ctx):
    ctx = scatter_ctx
    _drawn(ctx)
    combo = ctx.widgets.get("_scatter_cells_nw_y_cb")
    combo.setCurrentIndex(combo.findData("count_fc"))
    _page(ctx).baseline = "tp:24h"
    _got, footer = _drawn(ctx)
    assert "Fold changes are over the Nanowells page's Baseline: 24h." in footer, footer


def test_gate_percentages_say_they_are_of_the_subset(scatter_ctx):
    from tests.test_scatter_gates_on_nanowell_observations import _draw_gate

    ctx = scatter_ctx
    _page(ctx).subset = _starting_at_least_two()
    _drawn(ctx)
    _draw_gate(ctx, (0.0, 0.0, 3.0, 30.0))
    footer = _footer(ctx.widgets.get("_scatter_fig"))
    assert f"Gate percentages are of the nanowells drawn: those in the Subset ({_starting_at_least_two().label()})." \
        in footer, footer


def test_the_unscoped_table_still_holds_every_nanowell(scatter_ctx):
    """Cell Gating evaluates a nanowell-metric gate against the unscoped rows; a Subset naming that gate must not
    decide its own inputs, so only a caller asking for the page's scope gets it."""
    from review.data.observations import nanowell_observations

    ctx = scatter_ctx
    _page(ctx).subset = _starting_at_least_two()
    _page(ctx).excluded_wells = ["B01"]
    full = nanowell_observations(ctx, ["A01", "B01"])
    scoped = nanowell_observations(ctx, ["A01", "B01"], page_scope=True)
    assert len(full.frame) == 20 and set(full.frame["well"]) == {"A01", "B01"}
    assert set(scoped.frame["well"]) == {"A01"} and len(scoped.frame) == 4
