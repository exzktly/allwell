"""A dataset written by Subset, Merge Plates or Concatenate names its sources
in its own provenance log (``datasets/core/provenance.py``).

Each test runs the tool's real planner and executor on datasets built on disk.
"""

from __future__ import annotations

import pytest

from datasets.concatenate import apply as concat_apply
from datasets.concatenate.concatenate_compute import build_merge_plan
from datasets.merge_plates.apply import execute_plan as merge_execute
from datasets.merge_plates.merge_plates_compute import build_plate_merge_plan
from datasets.subset import apply as subset_apply
from datasets.subset.subset_compute import SubsetFilter, build_subset_plan
from tests.fixtures.dataset_builder import build_dataset
from util import provenance_log as P


def _derived(dest):
    events = P.read_events(dest / "out").events
    assert [e["event"] for e in events] == ["derived_from"]
    assert events[0]["source"] == "datasets"
    return events[0]["detail"]


def test_a_subset_names_its_source_and_the_wells_it_kept(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    build_dataset(src, wells=["A01", "B02", "C03"], timepoints=["0h", "24h"])
    plan = build_subset_plan(src, dest, SubsetFilter(wells=frozenset({"C03", "A01"})))
    subset_apply.execute_plan(plan)
    detail = _derived(dest)
    assert detail["tool"] == "subset"
    assert detail["sources"] == [str(plan.src.root)]
    assert detail["wells"] == ["A01", "C03"]


def test_a_plate_merge_names_both_sources_and_the_remap(tmp_path):
    ds1, ds2, dest = tmp_path / "ds1", tmp_path / "ds2", tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h", "24h"])
    build_dataset(ds2, wells=["B02"], timepoints=["0h", "24h"])
    plan = build_plate_merge_plan(ds1, ds2, dest)
    merge_execute(plan)
    detail = _derived(dest)
    assert detail["tool"] == "merge_plates"
    assert detail["sources"] == [str(plan.ds1.root), str(plan.ds2.root)]
    assert detail["well_map"] == dict(plan.well_map)


def test_a_concatenation_names_both_sources_and_the_offset(tmp_path):
    ds1, ds2, dest = tmp_path / "ds1", tmp_path / "ds2", tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h", "24h", "48h"])
    build_dataset(ds2, wells=["A01"], timepoints=["0h", "24h"])
    plan = build_merge_plan(ds1, ds2, dest)
    concat_apply.execute_plan(plan)
    detail = _derived(dest)
    assert detail["tool"] == "concatenate"
    assert detail["sources"] == [str(plan.ds1.root), str(plan.ds2.root)]
    assert detail["offset_hours"] == plan.offset_hours == 72.0


def test_a_derivation_that_failed_verification_records_nothing(tmp_path, monkeypatch):
    src, dest = tmp_path / "src", tmp_path / "dest"
    build_dataset(src, wells=["A01"], timepoints=["0h"])
    plan = build_subset_plan(src, dest, SubsetFilter(wells=frozenset({"A01"})))

    def _fail(*a, **k):
        raise subset_apply.SubsetError("verification failed")

    monkeypatch.setattr(subset_apply, "_verify", _fail)
    with pytest.raises(subset_apply.SubsetError):
        subset_apply.execute_plan(plan)
    assert P.read_events(dest / "out").missing


def test_a_repair_that_changed_something_is_recorded_once(tmp_path):
    import json

    from datasets.validate.apply import apply_repairs
    from datasets.validate.validate_compute import build_report

    build_dataset(tmp_path, wells=["A01"], timepoints=["0h", "24h"])
    info_path = tmp_path / "out" / "pipeline_info.json"
    info = json.loads(info_path.read_text())
    info["available_timepoints"] = ["0h", "24h", "72h"]      # a stale entry
    info_path.write_text(json.dumps(info))

    result = apply_repairs(tmp_path, build_report(tmp_path))
    assert result.rebuilt_availability
    (event,) = P.read_events(tmp_path / "out").events
    assert event["event"] == "repaired" and event["source"] == "datasets"
    assert event["detail"]["actions"] == [
        "rebuilt available_timepoints/available_fovs from disk"]

    # The idempotent re-run found nothing to do, and the history says nothing.
    assert apply_repairs(tmp_path, build_report(tmp_path)).summary == "nothing to repair"
    assert len(P.read_events(tmp_path / "out").events) == 1
