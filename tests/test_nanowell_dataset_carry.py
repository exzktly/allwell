"""Subset and Merge Plates carry a dataset's nanowell tables (tracker P10b, P10c), driven through each tool's
own plan and execution, on datasets whose images, measurements and tables agree."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from datasets.merge_plates.apply import execute_plan as merge_execute
from datasets.merge_plates.merge_plates_compute import MergePlatesError, build_plate_merge_plan
from datasets.subset.apply import execute_plan as subset_execute
from datasets.subset.subset_compute import SubsetFilter, build_subset_plan
from tests.fixtures.dataset_builder import build_dataset

SETTINGS = {"plate_format": "100um", "cell_diameter_um": 15.0}


def _dataset(root: Path, wells=("A01",), timepoints=("0h", "24h"), *, nanowells=True, settings=SETTINGS,
             spelling=None):
    """The repo's realistic dataset (two fields, OS debris and a stray file in every archive), plus, with
    *nanowells*, each well's tables naming every timepoint and the sidecar's nanowell block. *spelling* maps a
    well to the spelling its nanowell files use on disk (``A1`` for ``A01``)."""
    info_extra = None
    if nanowells:
        info_extra = {"nanowell": {"scheme_version": 1, "settings": dict(settings)},
                      "acquisition": {tp: {"pixel_size_um": {"x": 4.3, "y": 4.3}} for tp in timepoints}}
    build_dataset(root, wells=list(wells), timepoints=list(timepoints), fovs=("F001", "F002"),
                  info_extra=info_extra, junk=True, stray=True)
    if nanowells:
        folder = root / "out" / "nanowells"
        folder.mkdir()
        for well in wells:
            name = (spelling or {}).get(well, well)
            (folder / f"{name}_grid.json").write_text(json.dumps({
                "scheme_version": 1, "well": name, "timepoints": {tp: {"status": "ok"} for tp in timepoints}}))
            body = "".join(f"{tp},{q},0,measured,{q + 1}\n" for tp in timepoints for q in range(3))
            (folder / f"{name}.csv").write_text("timepoint,nw_q,nw_r,state,n_cells\n" + body)
    return root


def _tables(root: Path, well: str):
    folder = root / "out" / "nanowells"
    grid = json.loads((folder / f"{well}_grid.json").read_text())
    with (folder / f"{well}.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    return grid, rows


# ── P10b: Subset ─────────────────────────────────────────────────────────────
def test_subset_carries_the_tables_of_the_wells_and_timepoints_it_keeps(tmp_path):
    src = _dataset(tmp_path / "src", wells=("A01", "B02"))
    plan = build_subset_plan(src, tmp_path / "dest", SubsetFilter(wells=frozenset({"B02"}),
                                                                   timepoint_labels=frozenset({"24h"})))
    summary = subset_execute(plan)
    assert summary.startswith("Subset written")
    grid, rows = _tables(tmp_path / "dest", "B02")
    assert list(grid["timepoints"]) == ["24h"] and grid["well"] == "B02"
    assert [(r["timepoint"], r["nw_q"], r["n_cells"]) for r in rows] == [("24h", "0", "1"), ("24h", "1", "2"),
                                                                         ("24h", "2", "3")]
    assert sorted(p.name for p in (tmp_path / "dest" / "out" / "nanowells").iterdir()) == ["B02.csv", "B02_grid.json"]
    info = json.loads((tmp_path / "dest" / "out" / "pipeline_info.json").read_text())
    assert info["nanowell"]["settings"] == SETTINGS                     # Review reads the scheme from here


def test_subset_finds_tables_written_under_an_unpadded_well(tmp_path):
    src = _dataset(tmp_path / "src", wells=("A01",), spelling={"A01": "A1"})
    subset_execute(build_subset_plan(src, tmp_path / "dest", SubsetFilter()))
    grid, rows = _tables(tmp_path / "dest", "A01")
    assert grid["well"] == "A01" and len(rows) == 6


def test_a_timepoint_filter_that_keeps_none_of_a_well_s_table_writes_no_table(tmp_path):
    src = _dataset(tmp_path / "src", wells=("A01",), timepoints=("0h", "24h"))
    folder = src / "out" / "nanowells"
    grid = json.loads((folder / "A01_grid.json").read_text())
    grid["timepoints"].pop("24h")                                      # the analysis covered 0h only
    (folder / "A01_grid.json").write_text(json.dumps(grid))
    (folder / "A01.csv").write_text("timepoint,nw_q,nw_r,state,n_cells\n0h,0,0,measured,1\n")
    subset_execute(build_subset_plan(src, tmp_path / "dest", SubsetFilter(timepoint_labels=frozenset({"24h"}))))
    assert not (tmp_path / "dest" / "out" / "nanowells").exists()


# ── P10c: Merge Plates ───────────────────────────────────────────────────────
def test_merge_plates_carries_both_plates_tables_with_dataset_2_s_wells_remapped(tmp_path):
    one = _dataset(tmp_path / "one", wells=("A01",))
    two = _dataset(tmp_path / "two", wells=("A01",))
    plan = build_plate_merge_plan(one, two, tmp_path / "dest", well_map={"A01": "C03"})
    merge_execute(plan)
    first, first_rows = _tables(tmp_path / "dest", "A01")
    moved, moved_rows = _tables(tmp_path / "dest", "C03")
    assert (first["well"], moved["well"]) == ("A01", "C03")
    assert len(first_rows) == len(moved_rows) == 6
    assert plan.blocks_warning is None


def test_merge_plates_refuses_two_analyses_under_different_settings(tmp_path):
    one = _dataset(tmp_path / "one", wells=("A01",))
    two = _dataset(tmp_path / "two", wells=("B01",), settings=dict(SETTINGS, plate_format="50um"))
    with pytest.raises(MergePlatesError, match="computed under different nanowell settings"):
        build_plate_merge_plan(one, two, tmp_path / "dest")


def test_merge_plates_says_when_only_one_plate_has_nanowell_results(tmp_path):
    one = _dataset(tmp_path / "one", wells=("A01",), nanowells=False)
    two = _dataset(tmp_path / "two", wells=("B01",))
    plan = build_plate_merge_plan(one, two, tmp_path / "dest")
    assert plan.blocks_warning == ("Only two has nanowell results; one's wells will have none, and the Nanowells "
                                   "page names them as wells without a table.")
    assert plan.merged_info["nanowell"]["settings"] == SETTINGS     # dataset 2's block, though dataset 1 led
    merge_execute(plan)
    assert _tables(tmp_path / "dest", "B01")[0]["well"] == "B01"
