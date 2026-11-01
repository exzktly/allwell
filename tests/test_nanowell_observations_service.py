"""Nanowell observations reach the plot tools through the registry, as the page computes them
(ARCHITECTURE §6.2l).

The rows are the Nanowells page's ``site_metrics`` — one per nanowell × timepoint — handed out by the Nanowells
descriptor's ``observations.nanowell`` service, so a plot never imports the (removable) feature. The fixture's
counts make the nanowell rows and the cell rows disagree: a well's mean count per nanowell is not its mean cell
anything, and an implementation that handed back cell rows fails on the exact values.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from review.domain.observation import CELLS, NANOWELLS, footer_line, pooled_cells_line  # noqa: E402
from tests.fixtures.nanowell_dataset import COUNTS, GFP, SITES  # noqa: E402


@pytest.fixture
def nw_ctx(qtbot, tmp_path):
    from tests._minimal_shell import load_dataset, minimal_shell
    from tests.fixtures.nanowell_dataset import build_nanowell_dataset

    root = build_nanowell_dataset(tmp_path / "nw")
    ctx, _window = minimal_shell(qtbot)
    load_dataset(ctx, root / "out")
    ctx.state.dataset.data_dir = root / "out"
    return ctx


def _value(table, well, tp, site, key):
    f = table.frame
    row = f[(f["well"] == well) & (f["timepoint"] == tp) & (f["nw_q"] == site[0]) & (f["nw_r"] == site[1])]
    assert len(row) == 1, (well, tp, site)
    return float(row[key].iloc[0])


def test_the_service_returns_one_row_per_nanowell_and_timepoint_with_the_page_s_metrics(nw_ctx):
    from review.data.observations import available_observations, nanowell_observations

    assert available_observations(nw_ctx) == (CELLS, NANOWELLS)
    table = nanowell_observations(nw_ctx, ["A01", "B01"])
    assert table.observations == NANOWELLS
    assert len(table.frame) == 2 * len(SITES) * 2
    for well, by_tp in COUNTS.items():
        for tp, counts in by_tp.items():
            for site, n in zip(SITES, counts):
                assert _value(table, well, tp, site, "n") == n
    # Count fold change against the earliest timepoint: A01 nanowell (0,0) went 1 -> 3.
    assert _value(table, "A01", "24h", (0, 0), "count_fc") == 3.0
    # Brightness is the mean of the nanowell's cells' GFP; an empty nanowell has none.
    assert _value(table, "A01", "0h", (1, 0), "brightness_GFP") == GFP["A01"][1]
    assert table.values("brightness_GFP").isna().sum() == sum(c == 0 for by in COUNTS.values()
                                                                for cs in by.values() for c in cs)
    assert _value(table, "B01", "0h", (0, 1), "occupied") == 1.0
    assert _value(table, "B01", "0h", (-1, 1), "occupied") == 0.0


def test_a_missing_value_names_why(nw_ctx):
    from review.data.observations import nanowell_observations

    table = nanowell_observations(nw_ctx, ["A01"])
    # A01 holds two empty nanowells at 0h and one at 24h: no cells, so no brightness.
    excluded = table.exclusions("brightness_GFP")
    assert excluded == {"no cells": 3}
    assert footer_line(NANOWELLS, 7, 10, excluded) == "Observations: nanowells · 7 of 10 used · excluded: no cells 3"


def test_a_repeat_request_is_served_from_the_memo_and_a_baseline_change_recomputes(nw_ctx):
    from review.data.observations import nanowell_observations

    first = nanowell_observations(nw_ctx, ["A01"])
    assert nanowell_observations(nw_ctx, ["A01"]) is first
    nw_ctx.state.feature_slice("nanowell").baseline = "first_measurable"
    assert nanowell_observations(nw_ctx, ["A01"]) is not first


def test_cells_on_a_nanowell_plate_count_occupied_nanowells_not_all_of_them(nw_ctx):
    from review.data.observations import occupied_nanowells_by_well

    occupied = occupied_nanowells_by_well(nw_ctx, ["A01", "B01"])
    # A01 holds cells in four of its five nanowells over the two timepoints; B01 in three.
    assert occupied == {"A01": 4, "B01": 3}
    assert pooled_cells_line(occupied, nanowell_views=True) == (
        "Observations: cells · pooled from 3–4 occupied nanowells per well")
    assert pooled_cells_line({"A01": 4}, nanowell_views=False) == (
        "Observations: cells · pooled from 4 occupied nanowells per well · nanowell views are not in this build")


def test_without_the_nanowells_feature_there_are_no_nanowell_observations(nw_ctx, monkeypatch):
    from review.data import observations

    monkeypatch.setattr(nw_ctx.features, "service", lambda key: None)   # what a build without the feature answers
    assert observations.available_observations(nw_ctx) == (CELLS,)
    assert observations.nanowell_observations(nw_ctx, ["A01"]) is None


def test_the_rows_follow_the_rim_focus_rule_in_force_without_the_page_ever_opening(nw_ctx):
    """Cell Gating's rim-focus rule is the one the page and every other tab apply; the plots must too, though the
    page — which used to be the only thing copying the rule onto its state — was never opened."""
    from review.data.observations import nanowell_observations

    import dataclasses

    params = nw_ctx.features.query("qc.get_params", nw_ctx, default=None)
    assert params is not None
    nw_ctx.state.qc.params = dataclasses.replace(params, exclude_soft_nanowells=True)
    on = nanowell_observations(nw_ctx, ["A01"])
    nw_ctx.state.qc.params = dataclasses.replace(params, exclude_soft_nanowells=False)
    off = nanowell_observations(nw_ctx, ["A01"])
    # Fewer than ten rims a timepoint: the rule on cannot judge them and says so; off, it says nothing.
    assert on.notes and not off.notes


def test_the_metric_names_alone_match_the_table_s_and_read_no_rows(nw_ctx, monkeypatch):
    """The per-cell Scatter's "Nanowell ▸" entries need only the metric names, and they are built while the tab
    builds inside the dataset load. Computing the whole nanowell table there cost seconds per plate, so the names
    come from the site tables' header rows: the same names the table offers, with no table read."""
    from review.data.observations import nanowell_metric_specs, nanowell_observations
    from review.features.nanowell import observation_compute

    full = nanowell_observations(nw_ctx, ["A01", "B01"])
    nw_ctx.state.feature_slice("nanowell").observation_memo.clear()

    def no_rows(*_a, **_k):
        raise AssertionError("the metric names were computed by reading the nanowell tables")

    monkeypatch.setattr(observation_compute, "load_nanowell_data", no_rows)
    names = nanowell_metric_specs(nw_ctx, ["A01", "B01"])
    assert [(m.key, m.label) for m in names] == [(m.key, m.label) for m in full.metrics]
    assert any(m.key == "brightness_GFP" for m in names)


def test_the_scatter_s_nanowell_entries_are_listed_without_computing_the_table(nw_ctx, monkeypatch):
    from review.features.nanowell import observation_compute
    from review.features.scatter._observations import cell_nanowell_entries

    def no_rows(*_a, **_k):
        raise AssertionError("the Scatter's picker computed the nanowell table to list its entries")

    monkeypatch.setattr(observation_compute, "load_nanowell_data", no_rows)
    entries = cell_nanowell_entries(nw_ctx)
    assert entries["Nanowell ▸ Cell count"] == "n"
    assert entries["Nanowell ▸ GFP Cell brightness (mean per cell, own-well background subtracted)"] == "brightness_GFP"
