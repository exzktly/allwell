"""Subset, Merge Plates and Concatenate carry Review's inclusion settings into the dataset they write.

Which cells count is decided by curation (``cell_overrides.json``, already carried) and by three sections of
``persistence.json``: the QC limits (``qc_params``), the crop (``crop_region``) and the fields excluded by hand
(``field_exclusions``). None of the tools copied that file, so a derived dataset counted the debris, the cropped-out
cells and the excluded fields again, with no warning (cell population audit P6). Each test runs the tool's real
planner and executor on a dataset on disk and reads the destination's file back.

The fixtures make the right answer differ from the wrong one: the QC limits are not the defaults, a field exclusion
names a well or timepoint the tool renames (so carrying it unrenamed is caught), and one names a well or timepoint
the tool leaves behind (so carrying everything is caught).
"""

from __future__ import annotations

import json

from datasets.concatenate import apply as concat_apply
from datasets.concatenate import concatenate_compute as cc
from datasets.merge_plates import apply as mp_apply
from datasets.merge_plates import merge_plates_compute as mpc
from datasets.subset.apply import execute_plan as subset_execute
from datasets.subset.subset_compute import SubsetFilter, build_subset_plan
from tests.fixtures.dataset_builder import build_dataset

QC = {"enabled": True, "min_area": 37.0, "max_area": 900.0}
CROP = {"region": [10, 20, 300, 400], "scope": {"well": None, "fov": None, "timepoint": None}}


def _persist(root, *, qc=QC, crop=CROP, fields=()):
    doc = {"ratios": {"keep": "me"}}
    if qc is not None:
        doc["qc_params"] = qc
    if crop is not None:
        doc["crop_region"] = crop
    doc["field_exclusions"] = [{"well": w, "fov": f, "timepoint": t, "reason": "blurred", "at": "x"}
                               for w, f, t in fields]
    (root / "out" / "persistence.json").write_text(json.dumps(doc))


def _read(dest):
    return json.loads((dest / "out" / "persistence.json").read_text())


def _fields(doc):
    return sorted((r["well"], r["fov"], r["timepoint"]) for r in doc["field_exclusions"])


def test_subset_carries_the_qc_limits_crop_and_the_kept_fields_exclusions(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    build_dataset(src, wells=["A01", "B02"], timepoints=["0h", "24h", "48h"], fovs=("F001",))
    _persist(src, fields=[("A01", "F001", "24h"),             # kept
                          ("B02", "F001", "24h"),             # a well the subset drops
                          ("A01", "F001", "48h")])            # a timepoint the subset drops
    plan = build_subset_plan(src, dest, SubsetFilter(wells=frozenset({"A01"}),
                                                     timepoint_labels=frozenset({"0h", "24h"})))
    said: list[str] = []
    subset_execute(plan, log=said.append)
    doc = _read(dest)
    assert doc["qc_params"] == QC and doc["crop_region"] == CROP
    assert _fields(doc) == [("A01", "F001", "24h")]


def test_merge_plates_renames_dataset_2_s_exclusions_and_says_when_the_qc_limits_differ(tmp_path):
    ds1, ds2, dest = tmp_path / "ds1", tmp_path / "ds2", tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    build_dataset(ds2, wells=["A01"], timepoints=["0h"])                 # collides, so it is renamed E01
    _persist(ds1, fields=[("A01", "F001", "0h")])
    _persist(ds2, qc={**QC, "min_area": 80.0}, fields=[("A01", "F002", "0h")])
    plan = mpc.build_plate_merge_plan(ds1, ds2, dest, well_map={"A01": "E01"})
    said: list[str] = []
    mp_apply.execute_plan(plan, log=said.append)
    doc = _read(dest)
    assert doc["qc_params"] == QC                                         # dataset 1's, and said so
    assert any("dataset 2 had different QC limits from dataset 1" in line for line in said)
    assert _fields(doc) == [("A01", "F001", "0h"), ("E01", "F002", "0h")]


def test_concatenate_shifts_dataset_2_s_exclusions_and_keeps_the_other_sections(tmp_path):
    d1, d2, dest = tmp_path / "d1", tmp_path / "d2", tmp_path / "dest"
    build_dataset(d1, wells=["A01"], timepoints=["0h", "24h"])
    build_dataset(d2, wells=["A01"], timepoints=["0h", "24h"])
    _persist(d1, fields=[("A01", "F001", "24h")])
    _persist(d2, qc=None, crop=None, fields=[("A01", "F001", "0h"), ("A01", "F001", "not_a_time")])
    plan = cc.build_merge_plan(d1, d2, dest)
    said: list[str] = []
    concat_apply.execute_plan(plan, log=said.append)
    doc = _read(dest)
    assert doc["qc_params"] == QC and doc["crop_region"] == CROP
    shifted = cc.remap_timepoint_label("0h", plan.offset_hours, plan.tp_format)
    assert shifted != "0h"
    assert _fields(doc) == sorted([("A01", "F001", "24h"), ("A01", "F001", shifted)])
    assert any("1 field exclusion(s)" in line and "left behind" in line for line in said)


def test_concatenate_says_when_a_dataset_2_override_cannot_be_carried(tmp_path):
    """U3: a dataset-2 override whose timepoint cannot be read was dropped without a word, so the derived plate
    counted that curated-out cell again; now the run says how many."""
    d1, d2, dest = tmp_path / "d1", tmp_path / "d2", tmp_path / "dest"
    build_dataset(d1, wells=["A01"], timepoints=["0h", "24h"])
    build_dataset(d2, wells=["A01"], timepoints=["0h", "24h"])
    (d2 / "out" / "cell_overrides.json").write_text(json.dumps({"version": 2, "overrides": [
        {"well": "A01", "fov": "F001", "tp": "0h", "nucleus_id": "1", "included": "0", "seg_fp": "s"},
        {"well": "A01", "fov": "F001", "tp": "not_a_time", "nucleus_id": "2", "included": "0", "seg_fp": "s"}]}))
    said: list[str] = []
    concat_apply.execute_plan(cc.build_merge_plan(d1, d2, dest), log=said.append)
    assert any("1 of dataset 2's cell override(s)" in line for line in said)


def test_merge_plates_says_when_an_override_file_cannot_be_read(tmp_path):
    ds1, ds2, dest = tmp_path / "ds1", tmp_path / "ds2", tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    build_dataset(ds2, wells=["B01"], timepoints=["0h"])
    (ds2 / "out" / "cell_overrides.json").write_text("{not json")
    said: list[str] = []
    mp_apply.execute_plan(mpc.build_plate_merge_plan(ds1, ds2, dest), log=said.append)
    assert any("could not be read" in line and "cell overrides" in line for line in said)
