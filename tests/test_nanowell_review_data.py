"""Review's nanowell data and calculations (tracker Section D), on tables written in the pipeline's own format.

The synthetic datasets are written with ``pipeline.nanowell_task.write_sites_csv`` and a grid record shaped
like the pipeline's, so a column the pipeline renames breaks these tests rather than passing them. Their
timepoint labels are deliberately out of order against the recorded times, and the times are unevenly
spaced, so ordering by label, or treating the timepoint index as time, gives a different answer from the
right one.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from pipeline.nanowell_task import SCHEME_VERSION, write_sites_csv
from review.features.nanowell import controller as nwc
from review.features.nanowell.collect import (
    NOT_LOCATED, SUPPORTED_SCHEMES, UNADDRESSED, NanowellSchemeError, load_nanowell_data, recorded_hours,
)
from review.features.nanowell.series_compute import (
    exclusion_reason, site_metrics, site_series,
)
from review.features.nanowell.state import NanowellState
from review.features.nanowell.summary_compute import group_summary, well_summary

#: Labels in an order that is not their time order; recorded starts 0 h, 5 h, 10 h, 30 h after the first.
STARTS = {"t_c": "2026-03-01T09:00:00", "t_a": "2026-03-01T14:00:00", "t_d": "2026-03-01T19:00:00",
          "t_b": "2026-03-02T15:00:00"}
ORDER = ["t_c", "t_a", "t_d", "t_b"]
HOURS = [0.0, 5.0, 10.0, 30.0]


def _row(tp, q, r, state="measured", n=None, c=None, gfp=None, fused=False):
    measured = state in ("measured", "empty")
    return {"timepoint": tp, "nw_q": q, "nw_r": r, "nw_ring": max(abs(q), abs(r)) if q is not None else None,
            "well_x_um": 100.0, "well_y_um": 100.0, "owner": "F0", "radius_um": 50.0, "geometry": "fitted",
            "detected": True, "fused": fused, "imaged_fraction": 1.0, "visible_fraction": 1.0, "state": state,
            "partially_visible": False, "focus": 1.0, "n_cells": n if measured else None,
            "confluence": c if measured else None, "area_est_cells": n, "count_disagreement": False,
            "consistency": None, "GFP_integrated": gfp, "GFP_mean_bg": None if gfp is None else gfp / 100}


def _dataset(tmp_path, wells: dict, *, starts=STARTS, grids=None, scheme=SCHEME_VERSION, cells=None) -> Path:
    """``out/`` with ``nanowells/<well>.csv`` + ``_grid.json`` for each well, the sidecar, and ``in/acquisition.json``."""
    from util.acquisition_record import new_record, new_timepoint, write_record_file

    root = tmp_path / "ds"
    out = root / "out"
    (out / "nanowells").mkdir(parents=True)
    for well, rows in wells.items():
        write_sites_csv(out / "nanowells" / f"{well}.csv", rows, ["GFP"])
        blocks = (grids or {}).get(well) or {tp: {"status": "ok"} for tp in {r["timepoint"] for r in rows}}
        (out / "nanowells" / f"{well}_grid.json").write_text(json.dumps(
            {"scheme_version": scheme, "well": well, "timepoints": blocks}))
    (out / "pipeline_info.json").write_text(json.dumps({"nanowell": {"scheme_version": SCHEME_VERSION}}))
    if cells is not None:
        pd.DataFrame(cells).to_csv(out / "measurements_A01.csv", index=False)
    if starts is not None:
        record = new_record()
        for tp, start in starts.items():
            record["timepoints"][tp] = new_timepoint(started_at=start)
            record["tiles"].append({"well": "A01", "field": "F0", "timepoint": tp, "mosaic_xy_px": None,
                                    "stage_xy_um": None})
        (root / "in").mkdir()
        write_record_file(root / "in" / "acquisition.json", record)
    return out


def _series_well():
    """Site (0,0): 2 → occluded → 4 → 8 cells. Site (1,0): empty at the start, 3 cells later (seeded later)."""
    return [_row("t_c", 0, 0, n=2, c=0.2, gfp=100.0), _row("t_a", 0, 0, state="occluded"),
            _row("t_d", 0, 0, n=4, c=0.4, gfp=300.0), _row("t_b", 0, 0, n=8, c=0.8, gfp=500.0),
            _row("t_c", 1, 0, state="empty", n=0, c=0.0, gfp=-5.0), _row("t_a", 1, 0, n=3, c=0.3, gfp=50.0),
            _row("t_d", 1, 0, n=0, state="empty", c=0.0, gfp=-1.0), _row("t_b", 1, 0, n=3, c=0.3, gfp=40.0)]


# ── D1: loading ──────────────────────────────────────────────────────────────
def test_the_tables_load_with_times_from_the_record_not_from_the_labels(tmp_path):
    data = load_nanowell_data(_dataset(tmp_path, {"A01": _series_well()}))
    assert data.time_source == "recorded" and data.time_note == ""
    assert data.timepoints("A01") == ORDER
    assert [data.times_h[tp] for tp in ORDER] == HOURS
    assert data.channels == ["GFP"] and data.problems == []


def test_mixed_clocks_fall_back_to_the_labels_and_say_why(tmp_path):
    starts = {"0h": "2026-03-01T09:00:00+00:00", "24h": "2026-03-02T15:00:00"}
    rows = [_row("0h", 0, 0, n=1, c=0.1), _row("24h", 0, 0, n=2, c=0.2)]
    data = load_nanowell_data(_dataset(tmp_path, {"A01": rows}, starts=starts))
    assert data.time_source == "label" and data.times_h == {"0h": 0.0, "24h": 24.0}
    assert "timezone" in data.time_note
    assert recorded_hours({"timepoints": {tp: {"started_at": t} for tp, t in starts.items()}}, list(starts)) == ({}, "the record's start times mix clocks "
                                                                     "with and without a timezone")


def test_without_a_record_or_readable_labels_time_is_unknown_and_growth_undefined(tmp_path):
    rows = [_row(tp, 0, 0, n=n, c=0.1) for tp, n in (("p", 1), ("q", 2), ("s", 4))]
    data = load_nanowell_data(_dataset(tmp_path, {"A01": rows}, starts=None))
    assert data.time_source is None and "unknown" in data.time_note
    assert site_metrics(data)["growth_rate"].isna().all()


def test_a_scheme_this_version_does_not_read_is_refused_by_name(tmp_path):
    out = _dataset(tmp_path, {"A01": _series_well()})
    (out / "pipeline_info.json").write_text(json.dumps({"nanowell": {"scheme_version": 99}}))
    with pytest.raises(NanowellSchemeError, match="scheme version 99"):
        load_nanowell_data(out)


def test_a_well_whose_grid_states_another_scheme_is_left_out_and_named(tmp_path):
    out = _dataset(tmp_path, {"A01": _series_well(), "B02": _series_well()})
    grid = json.loads((out / "nanowells" / "B02_grid.json").read_text())
    (out / "nanowells" / "B02_grid.json").write_text(json.dumps({**grid, "scheme_version": 2}))
    data = load_nanowell_data(out)
    assert data.wells == ["A01"] and len(data.problems) == 1 and data.problems[0].startswith("B02: scheme version 2")


def test_the_pipeline_s_scheme_is_one_review_reads():
    assert SCHEME_VERSION in SUPPORTED_SCHEMES


def test_a_dataset_without_nanowell_tables_has_none(tmp_path):
    (tmp_path / "out").mkdir()
    assert load_nanowell_data(tmp_path / "out") is None


def test_brightness_is_the_mean_per_cell_intensity_inside_each_nanowell(tmp_path):
    cells = {"well": ["A01"] * 3, "timepoint": ["t_c"] * 3, "nw_q": [0, 0, 1], "nw_r": [0, 0, 0],
             "GFP_mean_intensity": [10.0, 30.0, 99.0], "area_px": [5, 5, 5]}
    rows = [_row("t_c", 0, 0, n=2, c=0.2, gfp=1.0), _row("t_c", 1, 0, n=1, c=0.1, gfp=1.0)]
    data = load_nanowell_data(_dataset(tmp_path, {"A01": rows}, starts={"t_c": STARTS["t_c"]}, cells=cells))
    got = data.sites.set_index(["nw_q", "nw_r"])["brightness_GFP"]
    assert got[(0, 0)] == 20.0 and got[(1, 0)] == 99.0


# ── D2 / D2a: each nanowell over time ────────────────────────────────────────
def test_a_nanowell_s_series_is_every_timepoint_in_recorded_order_with_its_state(tmp_path):
    rows = _series_well()
    rows += [_row("t_e", None, None, n=1, c=0.1)]                      # a timepoint with no origin
    grids = {"A01": {**{tp: {"status": "ok"} for tp in ORDER},
                     "t_e": {"status": "origin_unresolved", "reason": "the fiducial is hidden"},
                     "t_f": {"status": "no_lattice", "reason": "no array was found"}}}
    starts = {**STARTS, "t_e": "2026-03-03T09:00:00", "t_f": "2026-03-04T09:00:00"}
    data = load_nanowell_data(_dataset(tmp_path, {"A01": rows}, starts=starts, grids=grids))
    series = site_series(site_metrics(data), "A01", 0, 0)
    assert list(series["timepoint"]) == ORDER + ["t_e", "t_f"]
    assert list(series["state"]) == ["measured", "occluded", "measured", "measured", UNADDRESSED, NOT_LOCATED]
    assert list(series["reason"])[-2:] == ["the fiducial is hidden", "no array was found"]
    assert series["n"].isna().tolist() == [False, True, False, False, True, True]
    assert site_series(site_metrics(data), "A01", 7, 7).empty


# ── D3: the metrics ──────────────────────────────────────────────────────────
@pytest.fixture
def metrics(tmp_path):
    return site_metrics(load_nanowell_data(_dataset(tmp_path, {"A01": _series_well()})))


def _site(metrics, q, r):
    return site_series(metrics, "A01", q, r).set_index("timepoint").loc[ORDER]


def test_count_fold_change_and_its_log_against_the_earliest_timepoint(metrics):
    s = _site(metrics, 0, 0)
    assert s["count_fc"].tolist()[0] == 1.0 and math.isnan(s["count_fc"].tolist()[1])
    assert s["count_fc"].tolist()[2:] == [2.0, 4.0]
    assert s["log2_count_fc"].tolist()[2] == pytest.approx(math.log2(5 / 3))
    assert s["confluence_fc"].tolist()[3] == pytest.approx(4.0)
    assert s["signal_fc_GFP"].tolist()[3] == pytest.approx(5.0)


def test_a_nanowell_empty_at_baseline_has_no_count_fold_change_and_is_seeded_later_when_cells_arrive(metrics):
    s = _site(metrics, 1, 0)
    assert s["count_fc"].isna().all()
    assert s["seeded_later"].tolist() == [False, True, False, True]
    assert s["log2_count_fc"].tolist()[1] == pytest.approx(math.log2(4 / 1))      # defined with the +1
    mine = (metrics["nw_q"] == 1) & (metrics["nw_r"] == 0)
    by_tp = dict(zip(metrics.loc[mine, "timepoint"], exclusion_reason(metrics, "count_fc", ["GFP"])[mine]))
    assert by_tp == {"t_c": "empty at baseline", "t_a": "seeded later", "t_d": "empty at baseline",
                     "t_b": "seeded later"}


def test_site_signal_over_a_baseline_at_or_below_background_is_undefined_and_says_so(metrics):
    mine = (metrics["nw_q"] == 1) & (metrics["nw_r"] == 0)
    assert metrics.loc[mine, "signal_fc_GFP"].isna().all()
    assert set(exclusion_reason(metrics, "signal_fc_GFP", ["GFP"])[mine]) == {
        "no signal above background at baseline"}


def test_growth_rate_is_the_slope_of_ln_n_per_recorded_hour(metrics):
    expected = np.polyfit([0.0, 10.0, 30.0], np.log([2.0, 4.0, 8.0]), 1)[0]
    assert _site(metrics, 0, 0)["growth_rate"].iloc[0] == pytest.approx(expected)
    assert _site(metrics, 1, 0)["growth_rate"].isna().all()                  # only two timepoints with cells


def test_occupancy_class_is_the_count_at_baseline_with_three_and_more_together(tmp_path):
    rows = [_row("t_c", q, 0, n=n, c=0.1) for q, n in enumerate((0, 1, 2, 3, 5))]
    rows += [_row("t_c", 9, 0, state="clipped")]
    metrics = site_metrics(load_nanowell_data(_dataset(tmp_path, {"A01": rows}, starts={"t_c": STARTS["t_c"]})))
    got = dict(zip(metrics["nw_q"], metrics["occupancy_class"]))
    assert [got[q] for q in range(5)] == ["0", "1", "2", "3+", "3+"] and got[9] is pd.NA


def test_a_fused_nanowell_has_confluence_but_no_count_and_says_why(tmp_path):
    rows = [_row("t_c", 0, 0, n=None, c=0.5, fused=True), _row("t_a", 0, 0, n=None, c=0.7, fused=True)]
    starts = {k: STARTS[k] for k in ("t_c", "t_a")}
    metrics = site_metrics(load_nanowell_data(_dataset(tmp_path, {"A01": rows}, starts=starts)))
    assert metrics["count_fc"].isna().all() and metrics["confluence_fc"].tolist()[1] == pytest.approx(1.4)
    assert set(exclusion_reason(metrics, "count_fc", ["GFP"])) == {"fused (no count)"}


def test_the_state_decides_whether_a_number_is_used_not_whether_one_was_written(tmp_path):
    """A row whose state carries no contents is not measured, even if the table holds a count for it."""
    occluded = {**_row("t_a", 0, 0, state="occluded"), "n_cells": 9, "confluence": 0.9, "GFP_integrated": 900.0}
    rows = [_row("t_c", 0, 0, n=3, c=0.3, gfp=100.0), occluded]
    metrics = site_metrics(load_nanowell_data(_dataset(tmp_path, {"A01": rows},
                                                       starts={k: STARTS[k] for k in ("t_c", "t_a")})))
    late = metrics[metrics["timepoint"] == "t_a"].iloc[0]
    assert math.isnan(late["count_fc"]) and math.isnan(late["confluence_fc"]) and math.isnan(late["signal_fc_GFP"])


# ── D4: the baseline ─────────────────────────────────────────────────────────
def test_a_nanowell_occluded_at_the_earliest_timepoint_has_a_fold_change_only_from_its_first_measurable(tmp_path):
    rows = [_row("t_c", 0, 0, state="occluded"), _row("t_a", 0, 0, n=2, c=0.2), _row("t_d", 0, 0, n=6, c=0.3)]
    data = load_nanowell_data(_dataset(tmp_path, {"A01": rows}, starts={k: STARTS[k] for k in ORDER[:3]}))
    earliest = site_metrics(data, "earliest")
    assert earliest["count_fc"].isna().all()
    assert set(exclusion_reason(earliest, "count_fc", ["GFP"])) == {"occluded", "baseline occluded"}
    first = site_metrics(data, "first_measurable")
    assert first["count_fc"].tolist()[1:] == [1.0, 3.0]
    assert math.isnan(first["count_fc"].tolist()[0]) and set(first["baseline_timepoint"]) == {"t_a"}


# ── D5: per well ─────────────────────────────────────────────────────────────
def test_a_well_summary_counts_every_nanowell_as_used_or_excluded_by_reason(metrics):
    summary = well_summary(metrics, "count_fc", ["GFP"]).set_index("timepoint")
    late = summary.loc["t_b"]
    assert (late["n_sites"], late["n_used"], late["excluded"]) == (2, 1, {"seeded later": 1})
    occluded = summary.loc["t_a"]
    assert occluded["n_used"] == 0 and occluded["excluded"] == {"occluded": 1, "seeded later": 1}
    assert math.isnan(occluded["median"])
    assert late["fraction_responding"] == 1.0 and late["fraction_occupied"] == 1.0
    assert summary.loc["t_d", "fraction_occupied"] == 0.5
    assert summary.loc["t_a", "fraction_occupied"] == 1.0          # the occluded nanowell is not "unoccupied"


# ── D6 / D7: groups of wells ─────────────────────────────────────────────────
def _plate(tmp_path, per_well: dict):
    """Each well's nanowells at t_c (1 cell each) and t_a (the given counts): count_fc = the given counts."""
    wells = {}
    for well, counts in per_well.items():
        rows = []
        for i, n in enumerate(counts):
            rows += [_row("t_c", i, 0, n=1, c=0.1), _row("t_a", i, 0, n=n, c=0.1)]
        wells[well] = rows
    starts = {k: STARTS[k] for k in ("t_c", "t_a")}
    return site_metrics(load_nanowell_data(_dataset(tmp_path, wells, starts=starts)))


def test_a_group_s_centre_and_interval_come_from_one_median_per_well(tmp_path):
    # B01 has many nanowells; pooling them would pull the centre towards 2. Each well counts once.
    metrics = _plate(tmp_path, {"A01": [4, 4, 4], "B01": [2] * 30, "C01": [1, 1], "D01": [1, 3]})
    wells = well_summary(metrics, "count_fc")
    groups = {"treated": ["A01", "B01"], "control": ["C01", "D01"]}
    out = group_summary(wells, groups, metric="count_fc", control="control").set_index(["group", "timepoint"])
    treated = out.loc[("treated", "t_a")]
    assert treated["centre"] == 3.0 and treated["n_wells"] == 2 and treated["n_sites_used"] == 33
    from scipy import stats
    half = stats.t.ppf(0.975, 1) * np.std([4.0, 2.0], ddof=1) / math.sqrt(2)
    assert (treated["ci_low"], treated["ci_high"]) == pytest.approx((3.0 - half, 3.0 + half))
    assert out.loc[("control", "t_a"), "centre"] == 1.5
    assert treated["vs_control"] == pytest.approx(2.0)                  # mean of medians over mean of medians
    assert list(out.index.get_level_values(0).unique()) == ["treated", "control"]


def test_more_nanowells_in_a_well_does_not_narrow_the_interval(tmp_path):
    few = _plate(tmp_path / "a", {"A01": [4, 4], "B01": [2, 2]})
    many = _plate(tmp_path / "b", {"A01": [4] * 40, "B01": [2] * 40})
    groups = {"g": ["A01", "B01"]}
    a = group_summary(well_summary(few, "count_fc"), groups).set_index("timepoint").loc["t_a"]
    b = group_summary(well_summary(many, "count_fc"), groups).set_index("timepoint").loc["t_a"]
    assert (a["centre"], a["ci_low"], a["ci_high"], a["n_wells"]) == (b["centre"], b["ci_low"], b["ci_high"], 2)


def test_log_fold_change_is_compared_to_control_by_difference(tmp_path):
    metrics = _plate(tmp_path, {"A01": [7], "C01": [3]})
    out = group_summary(well_summary(metrics, "log2_count_fc"), {"a": ["A01"], "c": ["C01"]},
                        metric="log2_count_fc", control="c").set_index(["group", "timepoint"])
    assert out.loc[("a", "t_a"), "vs_control"] == pytest.approx(math.log2(8 / 2) - math.log2(4 / 2))


def test_replicate_groups_come_from_the_plot_tabs_sets_and_loose_wells_stand_alone(monkeypatch):
    from review.domain import plot_members
    from review.domain.batch_models import ReplicateSet

    monkeypatch.setattr(plot_members, "plot_replicate_sets",
                        lambda ctx: [ReplicateSet("dose 1", ["A01", "A02", "Z99"]), ReplicateSet("empty", ["Z98"])])
    assert nwc.replicate_groups(object(), ["A01", "A02", "B05"]) == {"dose 1": ["A01", "A02"], "B05": ["B05"]}


# ── D8: export ───────────────────────────────────────────────────────────────
def test_the_export_is_one_row_per_nanowell_and_timepoint_with_every_metric(tmp_path):
    out = _dataset(tmp_path, {"A01": _series_well()})
    state = NanowellState()
    nwc.load_into(state, out)
    path = tmp_path / "export.csv"
    assert nwc.export_long_csv(state, path) == 8
    table = pd.read_csv(path)
    assert not list(tmp_path.glob("*.part"))
    assert {"well", "nw_q", "nw_r", "timepoint", "time_h", "time_source", "state", "count_fc", "log2_count_fc",
            "confluence_fc", "brightness_fc_GFP", "signal_fc_GFP", "growth_rate", "occupancy_class",
            "baseline", "baseline_timepoint"} <= set(table.columns)
    row = table[(table["nw_q"] == 0) & (table["timepoint"] == "t_b")].iloc[0]
    assert (row["time_h"], row["time_source"], row["count_fc"], row["baseline"]) == (30.0, "recorded", 4.0, "earliest")
    occluded = table[(table["nw_q"] == 0) & (table["timepoint"] == "t_a")].iloc[0]
    assert occluded["state"] == "occluded" and math.isnan(occluded["n_cells"])


def test_changing_the_baseline_recomputes_the_metrics(tmp_path):
    rows = [_row("t_c", 0, 0, state="occluded"), _row("t_a", 0, 0, n=2, c=0.2), _row("t_d", 0, 0, n=6, c=0.3)]
    state = NanowellState()
    nwc.load_into(state, _dataset(tmp_path, {"A01": rows}, starts={k: STARTS[k] for k in ORDER[:3]}))
    assert state.metrics["count_fc"].isna().all()
    nwc.set_baseline(state, "first_measurable")
    assert state.metrics["count_fc"].tolist()[2] == 3.0


def test_a_scheme_refusal_reaches_the_state_as_a_reason(tmp_path):
    out = _dataset(tmp_path, {"A01": _series_well()})
    (out / "pipeline_info.json").write_text(json.dumps({"nanowell": {"scheme_version": 7}}))
    state = NanowellState()
    nwc.load_into(state, out)
    assert state.data is None and "scheme version 7" in state.load_error


# ── D9: persistence ──────────────────────────────────────────────────────────
def _ctx(data_dir):
    dataset = SimpleNamespace(data_dir=str(data_dir), persistence_doc=None, persistence_doc_degraded=False)
    return SimpleNamespace(state=SimpleNamespace(dataset=dataset))


def test_the_page_s_choices_round_trip_and_a_dataset_without_them_starts_fresh(tmp_path):
    from review.persistence import nanowell as persist

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    st = NanowellState(view="trajectories", metric="count_fc", baseline="first_measurable", axis="wells",
                       well="B02", timepoint="t_a", selected_sites=[["B02", 1, -2]])
    st.data = "the tables"
    persist.save_to_data_dir(_ctx(tmp_path / "a"), st)
    fresh = NanowellState()
    persist.load_from_data_dir(_ctx(tmp_path / "a"), fresh)
    assert fresh.to_config() == st.to_config()
    persist.load_from_data_dir(_ctx(tmp_path / "b"), st)                   # another dataset, nothing saved
    assert st.to_config() == NanowellState().to_config() and st.data == "the tables"


def test_a_saved_block_with_values_the_page_cannot_show_is_not_taken():
    st = NanowellState()
    st.apply_config({"view": "pie", "baseline": "latest", "axis": "diagonal", "metric": 3,
                     "selected_sites": [["A01", 1, 2], ["A01", "x", 2], ["A01", True, 0], "junk"]})
    assert st.to_config() == {**NanowellState().to_config(), "selected_sites": [["A01", 1, 2]]}


# ── what the review of this section found ────────────────────────────────────
def test_when_only_some_labels_are_times_no_timepoint_has_a_time_and_growth_is_undefined(tmp_path):
    rows = [_row(tp, 0, 0, n=n, c=0.1) for tp, n in (("0h", 1), ("12h", 2), ("24h", 4), ("final", 8))]
    data = load_nanowell_data(_dataset(tmp_path, {"A01": rows}, starts=None))
    assert data.time_source is None and data.times_h == {}
    metrics = site_metrics(data)
    assert metrics["time_h"].isna().all() and metrics["growth_rate"].isna().all()


def test_a_member_well_with_no_addressed_nanowell_is_named_not_dropped(tmp_path):
    wells = {"A01": [_row("t_c", 0, 0, n=1, c=0.1), _row("t_a", 0, 0, n=2, c=0.1)],
             "B01": [_row("t_c", None, None, n=1, c=0.1), _row("t_a", None, None, n=1, c=0.1)],
             "C01": [_row("t_c", 0, 0, n=1, c=0.1), _row("t_a", 0, 0, n=4, c=0.1)]}
    metrics = site_metrics(load_nanowell_data(_dataset(tmp_path, wells, starts={k: STARTS[k] for k in ("t_c", "t_a")})))
    out = group_summary(well_summary(metrics, "count_fc"), {"g": ["A01", "B01", "C01"]}).set_index("timepoint")
    assert out.loc["t_a", "wells"] == ["A01", "C01"] and out.loc["t_a", "wells_without_value"] == ["B01"]
    assert out.loc["t_a", "n_wells"] == 2


def test_first_measurable_takes_each_measurement_s_own_first_timepoint(tmp_path):
    """The pipeline records ``measured`` with no count where no template could be built."""
    rows = [{**_row("t_c", 0, 0, n=None, c=0.2), "n_cells": None}, _row("t_a", 0, 0, n=2, c=0.4),
            _row("t_d", 0, 0, n=4, c=0.8)]
    data = load_nanowell_data(_dataset(tmp_path, {"A01": rows}, starts={k: STARTS[k] for k in ORDER[:3]}))
    s = site_metrics(data, "first_measurable").set_index("timepoint")
    assert s.loc["t_a", "count_fc"] == 1.0 and s.loc["t_d", "count_fc"] == 2.0
    assert s.loc["t_d", "confluence_fc"] == pytest.approx(4.0)                # confluence's baseline is t_c
    assert set(s["baseline_timepoint"]) == {"t_a"} and set(s["occupancy_class"]) == {"2"}


def test_the_consistency_flag_is_read_as_the_text_the_pipeline_writes(tmp_path):
    rows = [{**_row("t_c", 0, 0, n=1, c=0.1), "consistency": "roster_mismatch"}]
    data = load_nanowell_data(_dataset(tmp_path, {"A01": rows}, starts={"t_c": STARTS["t_c"]}))
    assert data.sites["consistency"].tolist() == ["roster_mismatch"]


def test_brightness_is_found_whichever_of_the_output_folder_and_its_parent_is_loaded(tmp_path):
    cells = {"well": ["A01"], "timepoint": ["t_c"], "nw_q": [0], "nw_r": [0], "GFP_mean_intensity": [20.0]}
    out = _dataset(tmp_path, {"A01": [_row("t_c", 0, 0, n=1, c=0.1, gfp=1.0)]}, starts={"t_c": STARTS["t_c"]},
                   cells=cells)
    for loaded in (out, out.parent):
        assert load_nanowell_data(loaded).sites["brightness_GFP"].tolist() == [20.0]


def test_a_well_that_repeats_an_address_at_one_timepoint_is_left_out_and_named(tmp_path):
    rows = [_row("t_c", 0, 0, n=1, c=0.1), _row("t_c", 0, 0, n=9, c=0.9)]
    data = load_nanowell_data(_dataset(tmp_path, {"A01": rows, "B01": [_row("t_c", 0, 0, n=1, c=0.1)]},
                                       starts={"t_c": STARTS["t_c"]}))
    assert data.wells == ["B01"] and data.problems[0].startswith("A01: 2 rows repeat an address")


def test_a_timepoint_the_grid_lists_without_rows_says_so(tmp_path):
    grids = {"A01": {"t_c": {"status": "ok"}, "t_a": {"status": "ok"}}}
    data = load_nanowell_data(_dataset(tmp_path, {"A01": [_row("t_c", 0, 0, n=1, c=0.1)]}, grids=grids,
                                       starts={k: STARTS[k] for k in ("t_c", "t_a")}))
    late = site_series(site_metrics(data), "A01", 0, 0).iloc[1]
    assert late["state"] == NOT_LOCATED and "no rows" in late["reason"]


def test_occupancy_class_summaries_count_three_and_more_as_three(tmp_path):
    rows = [_row("t_c", q, 0, n=n, c=0.1) for q, n in enumerate((1, 3, 7))]
    metrics = site_metrics(load_nanowell_data(_dataset(tmp_path, {"A01": rows}, starts={"t_c": STARTS["t_c"]})))
    summary = well_summary(metrics, "occupancy_class").iloc[0]
    assert (summary["n_used"], summary["median"]) == (3, 3.0)


def test_a_nanowell_seeded_later_responds_on_the_log_fold_change_and_is_excluded_by_name_on_the_ratio(metrics):
    late = well_summary(metrics, "log2_count_fc").set_index("timepoint").loc["t_a"]
    assert (late["n_used"], late["fraction_responding"]) == (1, 1.0)            # (1,0): 0 → 3 cells
    assert well_summary(metrics, "count_fc").set_index("timepoint").loc["t_a", "excluded"]["seeded later"] == 1


def test_brightness_reads_a_gzipped_cell_csv_and_prefers_a_plain_twin(tmp_path):
    """A finished well's per-cell CSV may be ``.csv.gz``; when both spellings exist the plain one is the newer."""
    out = _dataset(tmp_path, {"A01": [_row("t_c", 0, 0, n=1, c=0.1, gfp=1.0)]}, starts={"t_c": STARTS["t_c"]})
    cells = {"well": ["A01"], "timepoint": ["t_c"], "nw_q": [0], "nw_r": [0], "GFP_mean_intensity": [20.0]}
    pd.DataFrame(cells).to_csv(out / "measurements_A01.csv.gz", index=False)
    assert load_nanowell_data(out).sites["brightness_GFP"].tolist() == [20.0]
    pd.DataFrame({**cells, "GFP_mean_intensity": [30.0]}).to_csv(out / "measurements_A01.csv", index=False)
    assert load_nanowell_data(out).sites["brightness_GFP"].tolist() == [30.0]
