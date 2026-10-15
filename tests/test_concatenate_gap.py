"""The time between two concatenated datasets is the user's to state.

Concatenate used to place dataset 2 after dataset 1 by dataset 1's own median
interval, which is a guess for acquisitions taken whenever a plate was put back
on the microscope, and impossible for a dataset with one timepoint. These tests
drive the planner with an explicit gap, with the gap recorded in the two
datasets' acquisition records, and with neither, and each is built so the old
interval rule gives a different answer from the right one.
"""

from __future__ import annotations

import pytest

from datasets.concatenate import concatenate_compute as cc
from tests.fixtures.dataset_builder import build_dataset
from util.acquisition_record import new_record, new_timepoint, write_record


def _dataset(root, timepoints):
    build_dataset(root, wells=["A01"], timepoints=list(timepoints))
    return root


def _record(root, starts: dict):
    """An acquisition record whose timepoints start at *starts* (token -> ISO)."""
    record = new_record()
    for token, start in starts.items():
        record["timepoints"][token] = new_timepoint(started_at=start)
        record["tiles"].append({"well": "A01", "field": "F001", "timepoint": token,
                                "mosaic_xy_px": None, "stage_xy_um": None})
    write_record(root, record)


def _merged_labels(plan):
    return plan.merged_info["available_timepoints"]


def test_an_entered_gap_places_dataset_2_where_the_user_said(tmp_path):
    _dataset(tmp_path / "d1", ["0h", "24h"])
    _dataset(tmp_path / "d2", ["0h", "24h"])
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest",
                               gap_hours=6.0)
    # The interval rule would put dataset 2's first timepoint at 24 + 24 = 48h.
    assert _merged_labels(plan) == ["0h", "24h", "30h", "54h"]
    assert (plan.offset_hours, plan.gap_hours, plan.gap_source) == (30.0, 6.0, cc.GAP_ENTERED)


def test_leaving_the_prefilled_interval_reproduces_the_old_result(tmp_path):
    _dataset(tmp_path / "d1", ["0h", "24h", "48h"])
    _dataset(tmp_path / "d2", ["0h", "24h"])
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest")
    assert _merged_labels(plan) == ["0h", "24h", "48h", "72h", "96h"]
    assert (plan.gap_hours, plan.gap_source) == (24.0, cc.GAP_INTERVAL)
    old = cc.compute_offset({"available_timepoints": ["0h", "24h", "48h"]},
                            {"available_timepoints": ["0h", "24h"]})
    assert plan.offset_hours == old[0]


def test_a_single_timepoint_dataset_1_merges_once_a_gap_is_given(tmp_path):
    _dataset(tmp_path / "d1", ["0h"])
    _dataset(tmp_path / "d2", ["0h"])
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest",
                               gap_hours=24.0)
    assert _merged_labels(plan) == ["0h", "24h"]
    assert plan.ds1_interval_hours is None
    with pytest.raises(cc.ConcatError, match="enter the time gap"):
        cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest2")


def test_recorded_starts_prefill_the_gap_in_place_of_the_interval(tmp_path):
    _dataset(tmp_path / "d1", ["0h", "24h"])
    _dataset(tmp_path / "d2", ["0h"])
    _record(tmp_path / "d1", {"0h": "2026-03-01T09:00:00+00:00", "24h": "2026-03-02T09:00:00+00:00"})
    _record(tmp_path / "d2", {"0h": "2026-03-03T15:00:00+00:00"})  # 30 h after d1's last
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest")
    # The interval rule would say 24h; the records say 30h.
    assert (plan.gap_hours, plan.gap_source) == (30.0, cc.GAP_RECORDED)
    assert _merged_labels(plan) == ["0h", "24h", "54h"]


def test_an_entered_gap_beats_the_recorded_one_and_warns_when_they_differ(tmp_path):
    _dataset(tmp_path / "d1", ["0h", "24h"])
    _dataset(tmp_path / "d2", ["0h"])
    _record(tmp_path / "d1", {"0h": "2026-03-01T09:00:00+00:00", "24h": "2026-03-02T09:00:00+00:00"})
    _record(tmp_path / "d2", {"0h": "2026-03-03T15:00:00+00:00"})
    far = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest", gap_hours=36.0)
    assert (far.gap_hours, far.gap_source) == (36.0, cc.GAP_ENTERED)
    assert "36h" in far.gap_warning and "30h" in far.gap_warning
    near = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest", gap_hours=30.4)
    assert near.gap_warning is None       # inside max(2 %, 1 minute) of 30h


def test_records_on_different_clocks_are_not_compared(tmp_path):
    _dataset(tmp_path / "d1", ["0h", "24h"])
    _dataset(tmp_path / "d2", ["0h"])
    _record(tmp_path / "d1", {"0h": "2026-03-01T09:00:00", "24h": "2026-03-02T09:00:00"})  # no zone
    _record(tmp_path / "d2", {"0h": "2026-03-03T15:00:00+00:00"})
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest")
    assert (plan.gap_hours, plan.gap_source) == (24.0, cc.GAP_INTERVAL)   # not 30h


@pytest.mark.parametrize("text,hours", [("6", 6.0), ("6h", 6.0), ("30.25h", 30.25),
                                        ("1d", 24.0), ("1d2h30m", 26.5)])
def test_gap_text_is_read_as_a_duration(text, hours):
    assert cc.parse_gap_hours(text) == hours


@pytest.mark.parametrize("text,message", [("", "Enter the time"), ("   ", "Enter the time"),
                                          ("abc", "can't be read"), ("-3h", "can't be read"),
                                          ("0", "longer than zero"), ("nan", "can't be read")])
def test_a_bad_gap_is_refused_with_the_reason(text, message):
    with pytest.raises(cc.ConcatError, match=message):
        cc.parse_gap_hours(text)


def test_the_gap_text_the_field_shows_reads_back_to_the_same_gap():
    for hours in (6.0, 24.0, 30.0, 26.5, 0.5):
        assert cc.parse_gap_hours(cc.format_gap(hours)) == hours
