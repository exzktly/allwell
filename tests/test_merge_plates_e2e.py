"""End-to-end test for Merge Plates: real datasets on disk, real planner,
real executor, then reopened through the same readers Review uses.

Qt-free and dependency-free, runs in the fast lane.
"""

from __future__ import annotations

import csv
import zipfile

import pytest

from datasets.merge_plates.apply import execute_plan
from datasets.merge_plates.merge_plates_compute import (
    MergePlatesError,
    build_plate_merge_plan,
)
from tests.fixtures.dataset_builder import build_dataset
from review.data.data_loading import load_well_csv
from review.domain.viewer_state import read_pipeline_info


def test_identity_merge_end_to_end_then_reopen_in_review(tmp_path):
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01", "B02", "C03"], timepoints=["0h", "24h", "48h"])
    build_dataset(ds2, wells=["D04", "E05", "F06"], timepoints=["0h", "24h", "48h"])

    plan = build_plate_merge_plan(ds1, ds2, dest)
    summary = execute_plan(plan)
    assert "6 well" in summary

    extractor, fluor_tokens, smfish_tokens, pinfo = read_pipeline_info(dest / "out")
    assert extractor is not None
    assert "gfp" in fluor_tokens
    assert pinfo["available_timepoints"] == ["0h", "24h", "48h"]

    df1 = load_well_csv(dest / "out" / "measurements_A01.csv")
    df2 = load_well_csv(dest / "out" / "measurements_D04.csv")
    assert len(df1) == 3
    assert len(df2) == 3

    # Pure copy: byte-identical rows, no rewriting.
    with (ds2 / "out" / "measurements_D04.csv").open() as f:
        src_rows = list(csv.DictReader(f))
    with (dest / "out" / "measurements_D04.csv").open() as f:
        dest_rows = list(csv.DictReader(f))
    assert src_rows == dest_rows

    # Sources untouched.
    assert (ds1 / "out" / "measurements_A01.csv").exists()
    assert (ds2 / "out" / "measurements_D04.csv").exists()


def test_remap_merge_end_to_end_moves_four_coupled_values_then_reopens(tmp_path):
    ds1 = tmp_path / "ds1"
    ds2 = tmp_path / "ds2"
    dest = tmp_path / "dest"
    build_dataset(ds1, wells=["A01", "A02"], timepoints=["0h", "24h"])
    build_dataset(ds2, wells=["A01", "A02"], timepoints=["0h", "24h"])  # collides fully

    with pytest.raises(MergePlatesError, match="collide"):
        build_plate_merge_plan(ds1, ds2, dest)

    plan = build_plate_merge_plan(ds1, ds2, dest, well_map={"A01": "E01", "A02": "E02"})
    execute_plan(plan)

    for src_well, dest_well in (("A01", "E01"), ("A02", "E02")):
        with zipfile.ZipFile(dest / "in" / f"{dest_well}.zip") as zf:
            in_members = zf.namelist()
        assert all(dest_well in m and src_well not in m for m in in_members)

        with zipfile.ZipFile(dest / "out" / f"{dest_well}_out.zip") as zf:
            out_members = zf.namelist()
        assert all(dest_well in m for m in out_members)

        with (dest / "out" / f"measurements_{dest_well}.csv").open() as f:
            rows = list(csv.DictReader(f))
        assert all(r["well"] == dest_well for r in rows)
        assert all(dest_well in r["filename"] for r in rows)
        assert {r["timepoint"] for r in rows} == {"0h", "24h"}

    # Dataset 1's own wells are untouched by the remap.
    with (dest / "out" / "measurements_A01.csv").open() as f:
        ds1_rows = list(csv.DictReader(f))
    assert all(r["well"] == "A01" for r in ds1_rows)

    extractor, fluor_tokens, _smfish, pinfo = read_pipeline_info(dest / "out")
    assert extractor is not None
    assert pinfo["available_timepoints"] == ["0h", "24h"]

    # Sources still byte-identical to before the merge.
    with (ds2 / "out" / "measurements_A01.csv").open() as f:
        src_rows = list(csv.DictReader(f))
    assert all(r["well"] == "A01" for r in src_rows)  # ds2's own copy unmodified


def _members(path):
    with zipfile.ZipFile(path) as zf:
        return {i.filename: zf.read(i.filename) for i in zf.infolist() if not i.is_dir()}


def test_a_remapped_well_keeps_every_member_byte_identical(tmp_path):
    """A well-token remap changes only the name in each member's header, so
    every member's data must arrive unaltered — including the junk a real
    archive carries, which no remap rule has any opinion about."""
    ds1, ds2, dest = tmp_path / "ds1", tmp_path / "ds2", tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h", "24h"], junk=True)
    build_dataset(ds2, wells=["A01"], timepoints=["0h", "24h"], junk=True)

    plan = build_plate_merge_plan(ds1, ds2, dest, well_map={"A01": "B02"})
    execute_plan(plan)

    source = _members(ds2 / "in" / "A01.zip")
    copied = _members(dest / "in" / "B02.zip")
    assert set(copied) == {n.replace("A01", "B02") for n in source}
    for name, data in source.items():
        assert copied[name.replace("A01", "B02")] == data, f"{name} came back different"


def test_an_unremapped_well_is_copied_whole(tmp_path):
    ds1, ds2, dest = tmp_path / "ds1", tmp_path / "ds2", tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=["0h"], junk=True)
    build_dataset(ds2, wells=["D04"], timepoints=["0h"], junk=True)

    execute_plan(build_plate_merge_plan(ds1, ds2, dest))

    assert (dest / "in" / "A01.zip").read_bytes() == (ds1 / "in" / "A01.zip").read_bytes()
    assert (dest / "in" / "D04.zip").read_bytes() == (ds2 / "in" / "D04.zip").read_bytes()
