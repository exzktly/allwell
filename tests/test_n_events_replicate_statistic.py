"""The Events panel reports a replicate set's per-well mean, on every surface.

Mean and Fraction have always aggregated a replicate set as a mean of its
per-well values with a cross-well spread. The Events (N-above) panel did that
on the Bar tab and summed instead on the Line tab, so the same replicate set
read 5 on a bar and 10 on a curve — the two tabs could not be compared, and
nothing said which one meant what.

Fold change made it worse than a labelling problem: the denominator was the
sum while the Bar numerator was the mean, so a replicate set normalised against
itself landed on 1/n. A two-well set drew 0.5 on a panel whose whole point is
that a value divided by itself is 1.0.

The number these tests pin is user-visible and it moved: a Line Graph that
showed a two-well set's Events at 10 now shows 5.
"""
from __future__ import annotations

import pytest

from well_viewer.domain import fold_change as _fc
from well_viewer.features.barplot.collect import collect_bar_items
from well_viewer.features.lineplot.controller import collect_line_series

from tests.conftest import MockApp, make_agg


#: ``make_agg`` gives every well n_above=5, so a two-well set is 5 per well
#: and 10 in total — the two numbers this is about.
PER_WELL_N = 5.0

#: Spelled as the legacy 3-tuple rather than the typed request object, so
#: this file also runs against a base branch that predates that type — a
#: guard that dies on an AttributeError before measuring anything is not a
#: proof (CLAUDE.md §7.4).
FC_OFF = (False, "", False)
FC_VS_CTRL = (True, "CTRL", False)


@pytest.fixture
def two_well_set():
    """One replicate set, two wells, identical values."""
    app = MockApp(
        per_well_pts={
            "A01": [make_agg(0.0, 100.0), make_agg(1.0, 100.0)],
            "A02": [make_agg(0.0, 100.0), make_agg(1.0, 100.0)],
        },
        selections=[{"name": "CTRL", "wells": ["A01", "A02"], "hidden": False}],
    )
    ctx = getattr(app, "_ctx", app)
    ctx.state.plot.bar_pool_replicates = True
    return ctx


def _bar_n(ctx, t=0.0, fc=None):
    _ug, items, _b = collect_bar_items(ctx, t, fc_state=fc or FC_OFF)
    return items[0].n_above


def _line_n(ctx, t=0.0, fc=None):
    series, _msg = collect_line_series(
        ctx, threshold=0.0, use_sem=True, fc_state=fc or FC_OFF)
    point = next(p for p in series[0].points if abs(p.t - t) < 1e-9)
    return point.n_above


# ── raw values ───────────────────────────────────────────────────────────────

def test_bar_reports_the_per_well_mean(two_well_set):
    assert _bar_n(two_well_set) == PER_WELL_N


def test_line_reports_the_per_well_mean(two_well_set):
    """The regression. This was 10.0 — the cross-well total."""
    assert _line_n(two_well_set) == PER_WELL_N


def test_the_two_tabs_agree(two_well_set):
    """The property that actually matters; either tab alone can look fine."""
    assert _bar_n(two_well_set) == _line_n(two_well_set)


# ── the fold-change consequence ──────────────────────────────────────────────

def test_a_set_normalised_against_itself_is_one_on_the_bar(two_well_set):
    """Was 0.5 — a per-well mean of 5 over a cross-well total of 10."""
    fc = FC_VS_CTRL
    assert _bar_n(two_well_set, fc=fc) == pytest.approx(1.0)


def test_a_set_normalised_against_itself_is_one_on_the_line(two_well_set):
    fc = FC_VS_CTRL
    assert _line_n(two_well_set, fc=fc) == pytest.approx(1.0)


def test_the_mean_and_fraction_panels_were_already_one(two_well_set):
    """Pins that only the Events panel was wrong, so a future change that
    'fixes' the others has to explain itself."""
    fc = FC_VS_CTRL
    _ug, items, _b = collect_bar_items(two_well_set, 0.0, fc_state=fc)
    assert items[0].mean == pytest.approx(1.0)
    assert items[0].frac == pytest.approx(1.0)


# ── the denominator helpers themselves ───────────────────────────────────────

def test_member_n_series_returns_the_per_well_mean(two_well_set):
    series = _fc.member_n_series(
        two_well_set, "CTRL", threshold=0.0, val_col="mean_gfp_intensity",
        use_sem=True, per_fov_spread=False, fluor_gates=None)
    assert series[0.0][0] == PER_WELL_N


def test_member_n_stats_series_returns_the_per_well_mean(two_well_set):
    """The line-side denominator, which had its own copy of the sum."""
    series = _fc.member_n_stats_series(
        two_well_set, "CTRL", threshold=0.0, val_col="mean_gfp_intensity",
        per_fov_spread=False, fluor_gates=None)
    assert series[0.0][0] == PER_WELL_N


def test_a_solo_well_is_unaffected(two_well_set):
    """A one-well member has no sum-versus-mean distinction; it must not move."""
    series = _fc.member_n_series(
        two_well_set, "A01", threshold=0.0, val_col="mean_gfp_intensity",
        use_sem=True, per_fov_spread=False, fluor_gates=None)
    assert series[0.0][0] == PER_WELL_N
