"""No Datasets tool carries one run's report of its source into a dataset holding other wells or timepoints.

``run_report`` (which wells, FOV-groups and timepoints a run failed, against what it expected) and ``run_check``
(the completeness record Home and Review read) describe what one run made of one dataset's whole input, and neither
can be narrowed or combined from its own contents (``pipeline.sidecar.PER_RUN_BLOCKS``). Subset drops them
(``test_subset_compute.py::test_a_subset_does_not_carry_the_source_run_s_failure_record``); Concatenate and Merge
plates build their sidecar from dataset 1's, and carried them — so a concatenation went on reporting dataset 1's
missing ``24h`` as missing from a dataset that also holds dataset 2's timepoints, and a merged plate reported plate
1's completeness, keyed on its labels before the remap, as the whole plate's.

Wrong implementations these reject: starting the sidecar as ``dict(info1)``; dropping only ``run_report``; dropping
every analysis block (the user's ``notes`` must survive).
"""

from __future__ import annotations

from datasets.concatenate import concatenate_compute as cc
from datasets.merge_plates import merge_plates_compute as mpc
from tests.fixtures.dataset_builder import build_dataset

_PLATE_1_RUN = {
    "run_report": {"wells": {"A01": {"well": "A01", "groups_total": 2, "groups_covered": 1, "failed": ["24h"],
                                     "not_attempted": []}},
                   "incomplete_wells": ["A01"], "groups_total": 2, "groups_covered": 1},
    "run_check": {"version": 1, "wells": {"A01": {"health": "partial", "missing": [["F001", "24h"]]}},
                  "totals": {"partial": 1}, "findings": []},
    "notes": "plate 1, kept",
}


def _carried(info: dict) -> dict:
    return {k: info.get(k) for k in ("run_report", "run_check", "notes")}


def test_a_concatenation_carries_neither_per_run_block_of_dataset_1(tmp_path):
    build_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0h", "24h"], info_extra=_PLATE_1_RUN)
    build_dataset(tmp_path / "d2", wells=["A01"], timepoints=["0h", "24h"])
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest")
    assert len(plan.merged_info["available_timepoints"]) == 4, plan.merged_info["available_timepoints"]
    assert _carried(plan.merged_info) == {"run_report": None, "run_check": None, "notes": "plate 1, kept"}


def test_a_merged_plate_carries_neither_per_run_block_of_plate_1(tmp_path):
    build_dataset(tmp_path / "p1", wells=["A01", "B02"], timepoints=["0h", "24h"], info_extra=_PLATE_1_RUN)
    build_dataset(tmp_path / "p2", wells=["C03", "D04"], timepoints=["0h", "24h"])
    plan = mpc.build_plate_merge_plan(tmp_path / "p1", tmp_path / "p2", tmp_path / "dest")
    assert _carried(plan.merged_info) == {"run_report": None, "run_check": None, "notes": "plate 1, kept"}
