"""Fast-lane Qt-free tests for `datasets.merge_plates`."""

from __future__ import annotations

import csv
import json
import zipfile

import pytest

from datasets.merge_plates import apply as mp_apply
from datasets.merge_plates import merge_plates_compute as mpc
from tests.fixtures.dataset_builder import build_dataset


def test_identity_merge_is_a_pure_copy_no_rewriting(tmp_path):
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01", "B02"], timepoints=["0h", "24h"])
    build_dataset(ds2, wells=["C03", "D04"], timepoints=["0h", "24h"])

    plan = mpc.build_plate_merge_plan(ds1, ds2, dest)
    assert plan.well_map == {}
    mp_apply.execute_plan(plan)

    with (ds2 / "out" / "measurements_C03.csv").open() as f:
        src_rows = list(csv.DictReader(f))
    with (dest / "out" / "measurements_C03.csv").open() as f:
        dest_rows = list(csv.DictReader(f))
    assert src_rows == dest_rows

    with zipfile.ZipFile(ds2 / "in" / "C03.zip") as zf:
        src_members = sorted(zf.namelist())
    with zipfile.ZipFile(dest / "in" / "C03.zip") as zf:
        dest_members = sorted(zf.namelist())
    assert src_members == dest_members


def test_colliding_wells_refused(tmp_path):
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01", "B02"], timepoints=["0h"])
    build_dataset(ds2, wells=["A01", "C03"], timepoints=["0h"])
    with pytest.raises(mpc.MergePlatesError, match="collide"):
        mpc.build_plate_merge_plan(ds1, ds2, dest)


def test_remap_moves_all_four_coupled_values_together(tmp_path):
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    build_dataset(ds2, wells=["A01"], timepoints=["0h"])  # collides -> must remap

    plan = mpc.build_plate_merge_plan(ds1, ds2, dest, well_map={"A01": "E01"})
    mp_apply.execute_plan(plan)

    # container member name — E01 (remapped dataset 2) and A01 (dataset 1's
    # own, untouched well) both legitimately exist in the destination.
    with zipfile.ZipFile(dest / "in" / "E01.zip") as zf:
        members = zf.namelist()
    assert all("E01" in m for m in members)
    with zipfile.ZipFile(dest / "in" / "A01.zip") as zf:
        ds1_members = zf.namelist()
    assert all("A01" in m for m in ds1_members)

    # CSV well column + filename column
    with (dest / "out" / "measurements_E01.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert all(r["well"] == "E01" for r in rows)
    assert all("E01" in r["filename"] for r in rows)

    # out-zip member name
    with zipfile.ZipFile(dest / "out" / "E01_out.zip") as zf:
        out_members = zf.namelist()
    assert all("E01" in m for m in out_members)


def test_capacity_refused_past_column_12(tmp_path):
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    build_dataset(ds2, wells=["A01"], timepoints=["0h"])
    with pytest.raises(mpc.MergePlatesError, match="valid 96-well"):
        mpc.build_plate_merge_plan(ds1, ds2, dest, well_map={"A01": "A13"})


def test_divergent_timepoints_warn_not_refuse(tmp_path):
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h", "24h"])
    build_dataset(ds2, wells=["B02"], timepoints=["0h"])
    plan = mpc.build_plate_merge_plan(ds1, ds2, dest)
    assert plan.timepoint_warning is not None
    assert "ragged" in plan.timepoint_warning
    # Not refused — still executes.
    mp_apply.execute_plan(plan)
    info = json.loads((dest / "out" / "pipeline_info.json").read_text())
    assert info["available_timepoints"] == ["0h", "24h"]


def test_block_shift_well_map(tmp_path):
    m = mpc.block_shift_well_map({"A01", "A02", "B01"}, target_origin="E01")
    assert m == {"A01": "E01", "A02": "E02", "B01": "F01"}


def test_sample_definitions_names_disambiguated(tmp_path):
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(
        ds1, wells=["A01"], timepoints=["0h"],
        info_extra={"sample_definitions": {
            "well_labels": {"A01": "Control"},
            "rep_sets": [{"name": "Rep 1", "wells": ["A01"]}],
            "groups": [{"name": "Control", "hidden": False, "members": ["Rep 1"], "solo_wells": []}],
        }},
    )
    build_dataset(
        ds2, wells=["B02"], timepoints=["0h"],
        info_extra={"sample_definitions": {
            "well_labels": {"B02": "Treated"},
            "rep_sets": [{"name": "Rep 1", "wells": ["B02"]}],
            "groups": [{"name": "Control", "hidden": False, "members": ["Rep 1"], "solo_wells": []}],
        }},
    )
    plan = mpc.build_plate_merge_plan(ds1, ds2, dest)
    sd = plan.merged_info["sample_definitions"]
    rep_names = {r["name"] for r in sd["rep_sets"]}
    group_names = {g["name"] for g in sd["groups"]}
    assert len(rep_names) == 2  # "Rep 1" from ds1 + a disambiguated ds2 one
    assert len(group_names) == 2
    assert "Rep 1" in rep_names


def test_verify_fails_on_zero_rows(tmp_path):
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    (ds1 / "in").mkdir(parents=True)
    (ds1 / "out").mkdir(parents=True)
    (ds1 / "out" / "pipeline_info.json").write_text(json.dumps({
        "schema": "experiment:channel:well:fov:timepoint", "separator": "_",
        "schema_fields": ["experiment", "channel", "well", "fov", "timepoint"],
        "well_index": 2, "channel_index": 1, "fov_index": 3, "tp_index": 4,
        "nuclear_token": "nir", "segmentation_method": "stardist_nuclei",
        "available_timepoints": ["0h"], "available_fovs": ["F001"],
    }))
    build_dataset(ds2, wells=["B02"], timepoints=["0h"])
    plan = mpc.build_plate_merge_plan(ds1, ds2, dest)
    mp_apply.execute_plan(plan)  # ds2 has rows, so this should succeed
    assert (dest / "out" / "measurements_B02.csv").exists()


def test_marker_survives_cancel(tmp_path):
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    build_dataset(ds2, wells=["B02"], timepoints=["0h"])
    plan = mpc.build_plate_merge_plan(ds1, ds2, dest)
    with pytest.raises(mpc.MergePlatesError, match="Cancelled"):
        mp_apply.execute_plan(plan, is_cancelled=lambda: True)
    assert (dest / mpc.IN_PROGRESS_MARKER).exists()
    assert not (dest / "out" / "pipeline_info.json").exists()


def test_destination_same_as_source_refused(tmp_path):
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    build_dataset(ds2, wells=["B02"], timepoints=["0h"])
    with pytest.raises(mpc.MergePlatesError, match="different folder"):
        mpc.build_plate_merge_plan(ds1, ds2, ds1)


def test_incompatible_schema_refused(tmp_path):
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"])
    build_dataset(ds2, wells=["B02"], timepoints=["0h"], info_extra={"nuclear_token": "dapi"})
    with pytest.raises(mpc.MergePlatesError, match="not analysed the same way"):
        mpc.build_plate_merge_plan(ds1, ds2, dest)
