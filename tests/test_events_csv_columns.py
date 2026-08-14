"""The Events value reaches every surface without being rounded down.

A replicate set's Events value is the mean of its per-well counts, so it is
fractional. Three separate ``int()`` calls truncated it on the way out — the
batch bar collector, ``LinePoint.as_aggpoint`` feeding the batch line CSV, and
the CSV formatter — each rounding *down*, so a set of two wells holding 7 and 8
events was reported as 7 on surfaces that disagreed with the plot showing 7.5.

The CSV's ``n_above_threshold`` column keeps meaning what it always meant: the
integer count across the set's wells, 15 here. The drawn mean gets its own
column beside it. They are different numbers and neither is a rounding of the
other, which is exactly why deriving one from the other went wrong.

Fixtures give the two wells **different** counts on purpose. With equal counts
the mean is a whole number and every truncation is lossless — the shape of
fixture that let this ship (CLAUDE.md §7.5 #8).
"""
from __future__ import annotations

import pytest

from well_viewer.domain.batch_models import BarGroup, ReplicateSet
from well_viewer.features.barplot.collect import (
    collect_bar_items,
    collect_bar_items_for_group,
)
from well_viewer.features.export.service import bar_metric_row, line_metric_row
from well_viewer.features.lineplot.controller import collect_line_series

from tests.conftest import MockApp, make_agg


N_A, N_B = 7, 8
DRAWN_MEAN = 7.5           # mean of the per-well counts — what every panel draws
DRAWN_SPREAD = 0.5         # its cross-well spread
CROSS_WELL_TOTAL = 15      # what the CSV's integer column has always carried

FC_OFF = (False, "", False)


@pytest.fixture
def uneven_set():
    """One replicate set whose two wells carry *different* Events counts."""
    app = MockApp(
        per_well_pts={
            "A01": [make_agg(0.0, 100.0, n_above=N_A)],
            "A02": [make_agg(0.0, 100.0, n_above=N_B)],
        },
        selections=[{"name": "S", "wells": ["A01", "A02"], "hidden": False}],
    )
    ctx = getattr(app, "_ctx", app)
    ctx.state.plot.bar_pool_replicates = True
    return ctx


def _group():
    return BarGroup("G", [ReplicateSet("S", ["A01", "A02"])], [], False)


# ── the drawn value, on every collector ──────────────────────────────────────

def test_the_on_tab_bar_draws_the_mean_with_its_spread(uneven_set):
    _ug, items, _b = collect_bar_items(uneven_set, 0.0, fc_state=FC_OFF)
    assert (items[0].n_above, items[0].n_above_spread) == (DRAWN_MEAN, DRAWN_SPREAD)


def test_the_batch_bar_draws_the_same(uneven_set):
    """Was 7.0 with no spread — truncated, and the spread thrown away."""
    items = collect_bar_items_for_group(
        uneven_set, _group(), 0.0, val_col="mean_gfp_intensity",
        threshold=0.0, use_sem=True, per_fov_spread=False, fc_state=FC_OFF)
    assert (items[0].n_above, items[0].n_above_spread) == (DRAWN_MEAN, DRAWN_SPREAD)


def test_the_line_plot_draws_the_same(uneven_set):
    series, _msg = collect_line_series(
        uneven_set, threshold=0.0, use_sem=True, fc_state=FC_OFF)
    point = series[0].points[0]
    assert (point.n_above, point.n_above_spread) == (DRAWN_MEAN, DRAWN_SPREAD)


def test_every_collector_agrees(uneven_set):
    """The property that matters; each surface alone can look plausible."""
    _ug, tab, _b = collect_bar_items(uneven_set, 0.0, fc_state=FC_OFF)
    batch = collect_bar_items_for_group(
        uneven_set, _group(), 0.0, val_col="mean_gfp_intensity",
        threshold=0.0, use_sem=True, per_fov_spread=False, fc_state=FC_OFF)
    series, _msg = collect_line_series(
        uneven_set, threshold=0.0, use_sem=True, fc_state=FC_OFF)
    assert tab[0].n_above == batch[0].n_above == series[0].points[0].n_above


# ── the total the CSV keeps ──────────────────────────────────────────────────

def test_the_collectors_carry_the_cross_well_total(uneven_set):
    _ug, tab, _b = collect_bar_items(uneven_set, 0.0, fc_state=FC_OFF)
    batch = collect_bar_items_for_group(
        uneven_set, _group(), 0.0, val_col="mean_gfp_intensity",
        threshold=0.0, use_sem=True, per_fov_spread=False, fc_state=FC_OFF)
    series, _msg = collect_line_series(
        uneven_set, threshold=0.0, use_sem=True, fc_state=FC_OFF)
    assert tab[0].n_above_total == CROSS_WELL_TOTAL
    assert batch[0].n_above_total == CROSS_WELL_TOTAL
    assert series[0].points[0].n_above_total == CROSS_WELL_TOTAL


def test_the_line_point_round_trips_without_truncating(uneven_set):
    """``as_aggpoint`` is what the batch line CSV is built from."""
    series, _msg = collect_line_series(
        uneven_set, threshold=0.0, use_sem=True, fc_state=FC_OFF)
    agg = series[0].points[0].as_aggpoint()
    assert agg[4] == DRAWN_MEAN, "the drawn value was rounded on the way out"
    assert agg[9] == DRAWN_SPREAD
    assert agg[10] == CROSS_WELL_TOTAL


# ── the written row, which is where the truncations actually landed ──────────

def test_the_bar_csv_row_reports_both_numbers(uneven_set):
    _ug, items, _b = collect_bar_items(uneven_set, 0.0, fc_state=FC_OFF)
    it = items[0]
    row = bar_metric_row(
        mean=it.mean, spread=it.spread, frac=it.frac, frac_spread=it.frac_spread,
        has=it.has_mean, n_above=float(it.n_above),
        n_above_spread=float(it.n_above_spread),
        n_above_total=it.n_above_total, replicate_unit="well",
        ch="gfp", metric="mean_intensity", tp_str="0",
        threshold=0.0, band_lbl="SEM")
    assert row["n_above_threshold"] == str(CROSS_WELL_TOTAL)
    assert row["n_above_replicate_mean"] == f"{DRAWN_MEAN:.6f}"
    assert row["err_n_above_replicate_SEM"] == f"{DRAWN_SPREAD:.6f}"
    assert row["n_above_replicate_unit"] == "well"


def test_the_line_csv_row_reports_both_numbers(uneven_set):
    series, _msg = collect_line_series(
        uneven_set, threshold=0.0, use_sem=True, fc_state=FC_OFF)
    row = line_metric_row(
        series[0].points[0].as_aggpoint(),
        ch="gfp", metric="mean_intensity", threshold=0.0, band_lbl="SEM")
    assert row["n_above_threshold"] == CROSS_WELL_TOTAL
    assert row["n_above_replicate_mean"] == f"{DRAWN_MEAN:.6f}"


def test_a_single_well_row_is_unchanged(uneven_set):
    """A well's drawn value IS its count, so nothing about it may move."""
    row = line_metric_row(
        (0.0, 100.0, 0.0, 0.5, 9, 20, 0.0, 0.0, 0.0),
        ch="gfp", metric="mean_intensity", threshold=0.0, band_lbl="SEM")
    assert row["n_above_threshold"] == 9
    assert row["n_above_replicate_mean"] == "9.000000"


def test_an_undefined_spread_stays_an_empty_cell(uneven_set):
    """Never 0.000000 — that reads as 'the replicates agreed exactly'."""
    row = bar_metric_row(
        mean=1.0, spread=0.0, frac=0.5, frac_spread=0.0, has=True,
        n_above=9.0, n_above_spread=0.0, n_above_total=9,
        ch="gfp", metric="mean_intensity", tp_str="0",
        threshold=0.0, band_lbl="SEM")
    assert row["err_n_above_replicate_SEM"] == ""
