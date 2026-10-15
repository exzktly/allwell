"""Subset, Merge Plates and Concatenate refuse nanowell results by name; Validate checks the tables (tracker P10)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from datasets.concatenate.concatenate_compute import ConcatError, build_merge_plan
from datasets.core.dataset_paths import DatasetPaths
from datasets.core.nanowell_tables import has_nanowell_tables, nanowell_findings, refuse_nanowell_tables
from datasets.subset.subset_compute import SubsetError, SubsetFilter, build_subset_plan

SCHEMA = ["experiment", "channel", "well", "fov", "timepoint"]


def _dataset(root: Path, *, nanowells=False, timepoints=("0h",), grid_timepoints=None, sites_timepoints=None, scheme=1):
    (root / "in").mkdir(parents=True)
    (root / "out").mkdir()
    with zipfile.ZipFile(root / "in" / "A01.zip", "w") as zf:
        for tp in timepoints:
            for fov in ("F1", "F2"):
                for channel in ("PHASE", "GFP"):
                    zf.writestr(f"exp_{channel}_A01_{fov}_{tp}.tif", b"x")
    with zipfile.ZipFile(root / "out" / "A01_out.zip", "w") as zf:
        zf.writestr("exp_PHASE_A01_F1_0h_labels.tif", b"x")
    (root / "out" / "measurements_A01.csv").write_text("well,fov,timepoint\nA01,F1,0h\n")
    (root / "out" / "pipeline_info.json").write_text(json.dumps({
        "filename_schema": ":".join(SCHEMA), "filename_sep": "_", "nuclear_token": "PHASE", "fluor_tokens": ["GFP"],
        "available_timepoints": list(timepoints), "tp_index": 4}))
    if nanowells:
        folder = root / "out" / "nanowells"
        folder.mkdir()
        grid_tps = timepoints if grid_timepoints is None else grid_timepoints
        (folder / "A01_grid.json").write_text(json.dumps({
            "scheme_version": scheme, "well": "A01", "timepoints": {tp: {"status": "ok"} for tp in grid_tps}}))
        sites_tps = grid_tps if sites_timepoints is None else sites_timepoints
        (folder / "A01.csv").write_text("timepoint,nw_q\n" + "".join(f"{tp},0\n" for tp in sites_tps))
    return root


def _paths(root):
    return DatasetPaths(root=root, in_dir=root / "in", out_dir=root / "out")


@pytest.fixture
def nanowell_ds(tmp_path):
    return _dataset(tmp_path / "nw", nanowells=True)


@pytest.fixture
def plain_ds(tmp_path):
    return _dataset(tmp_path / "plain")


def test_a_dataset_with_nanowell_tables_is_recognised(nanowell_ds, plain_ds):
    assert has_nanowell_tables(_paths(nanowell_ds)) and not has_nanowell_tables(_paths(plain_ds))


def test_the_concatenate_refusal_names_the_tool_and_the_way_out(nanowell_ds):
    with pytest.raises(ValueError, match="Concatenate the raw input and run Analyze once") as raised:
        refuse_nanowell_tables(_paths(nanowell_ds), "Dataset 1", ValueError, "Concatenate")
    assert "Concatenate cannot carry them" in str(raised.value) and "Dataset 1" in str(raised.value)


def test_subset_refuses_a_field_selection_of_a_nanowell_dataset_before_writing_anything(nanowell_ds, tmp_path):
    with pytest.raises(SubsetError, match="cannot be cut to a set of fields"):
        build_subset_plan(nanowell_ds, tmp_path / "dest", SubsetFilter(fovs=frozenset({"F1"})))
    assert not (tmp_path / "dest").exists()
    build_subset_plan(nanowell_ds, tmp_path / "dest", SubsetFilter())        # wells and timepoints are fine


def test_concatenate_refuses_either_dataset(nanowell_ds, plain_ds, tmp_path):
    for first, second, which in ((nanowell_ds, plain_ds, "Dataset 1"), (plain_ds, nanowell_ds, "Dataset 2")):
        with pytest.raises(ConcatError, match=f"{which} holds nanowell results"):
            build_merge_plan(first, second, tmp_path / "dest")


def test_a_dataset_without_the_tables_is_not_refused_by_them(plain_ds, tmp_path):
    other = _dataset(tmp_path / "other")
    with pytest.raises(Exception) as raised:                   # it may fail for other reasons, not this one
        build_subset_plan(plain_ds, tmp_path / "d1", SubsetFilter(wells=frozenset()))
    assert "nanowell" not in str(raised.value)
    del other


# ── Validate ─────────────────────────────────────────────────────────────────
def _findings(root):
    return nanowell_findings(_paths(root), {"A01": root / "in" / "A01.zip"}, SCHEMA, "_")


def test_matching_tables_are_reported_present(nanowell_ds):
    assert _findings(nanowell_ds) == [("info", "nanowell_present", "Nanowell tables match the grid record and the input archives")]


def test_a_dataset_without_tables_yields_no_finding(plain_ds):
    assert _findings(plain_ds) == []


@pytest.mark.parametrize("build, code", [
    (dict(scheme=9), "nanowell_scheme"),
    (dict(sites_timepoints=("0h", "24h")), "nanowell_table_mismatch"),         # rows for a timepoint the grid lacks
    (dict(sites_timepoints=()), "nanowell_table_mismatch"),                    # ok in the grid, no rows
    (dict(timepoints=("0h", "24h"), grid_timepoints=("0h",)), "nanowell_timepoint_missing"),
    (dict(grid_timepoints=("0h", "48h"), sites_timepoints=("0h", "48h")), "nanowell_timepoint_unknown"),
])
def test_tables_that_disagree_with_each_other_or_the_archive_are_findings(tmp_path, build, code):
    root = _dataset(tmp_path / "ds", nanowells=True, **build)
    assert code in {c for _s, c, _m in _findings(root)}


def test_a_missing_sites_table_and_an_unreadable_grid_are_errors(tmp_path):
    root = _dataset(tmp_path / "ds", nanowells=True)
    (root / "out" / "nanowells" / "A01.csv").unlink()
    assert ("error", "nanowell_table_missing") in {(s, c) for s, c, _m in _findings(root)}
    (root / "out" / "nanowells" / "A01_grid.json").write_text("{not json")
    assert ("error", "nanowell_grid_unreadable") in {(s, c) for s, c, _m in _findings(root)}


def test_validate_reports_the_nanowell_findings_in_its_report(tmp_path):
    from datasets.validate.validate_compute import build_report

    root = _dataset(tmp_path / "ds", nanowells=True, timepoints=("0h", "24h"), grid_timepoints=("0h",))
    report = build_report(root)
    codes = {f.code for f in report.findings}
    assert "nanowell_timepoint_missing" in codes
    clean = build_report(_dataset(tmp_path / "ok", nanowells=True))
    assert "nanowell_present" in {f.code for f in clean.findings}
