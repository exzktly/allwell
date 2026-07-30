"""End-to-end test for Subset / Split: real dataset on disk, real planner,
real executor, then reopened through the same readers Review uses.

Qt-free and dependency-free, runs in the fast lane (see
tests/test_validate_e2e.py's module docstring for why).
"""

from __future__ import annotations

import csv
import json

from datasets.subset.apply import execute_plan
from datasets.subset.subset_compute import SubsetFilter, build_subset_plan
from tests.fixtures.dataset_builder import build_dataset
from well_viewer.data.data_loading import load_well_csv
from well_viewer.domain.viewer_state import read_pipeline_info
from well_viewer.persistence.cell_overrides import load_overrides


def test_subset_end_to_end_then_reopen_in_review(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    build_dataset(
        src, wells=["A01", "B02", "C03", "D04", "E05", "F06"],
        timepoints=["0h", "24h", "48h", "72h", "96h", "120h", "144h", "168h"],
        fovs=("F001", "F002", "F003"),
    )
    overrides = {
        "version": 2,
        # tp values are already normalized ("48", not "48h") -- the shape
        # save_to_data_dir actually writes (CellKey.as_tuple(), not the raw
        # CSV timepoint label). A fixture using the raw label here would mask
        # the exact bug this dataset covers: kept_keys used to be built from
        # unnormalized CSV values, so it only ever agreed with an (unrealistic)
        # unnormalized override fixture.
        "overrides": [
            {"well": "A01", "fov": "F001", "tp": "48", "nucleus_id": "1",
             "included": "0", "seg_fp": "stale"},
            {"well": "A01", "fov": "F001", "tp": "999", "nucleus_id": "2",
             "included": "0", "seg_fp": "stale"},  # filtered out by timepoint
        ],
    }
    (src / "out" / "cell_overrides.json").write_text(json.dumps(overrides))

    filt = SubsetFilter(
        wells=frozenset({"A01", "C03"}),
        timepoint_labels=frozenset({"24h", "48h", "72h", "96h"}),
        fovs=frozenset({"F001"}),
    )
    plan = build_subset_plan(src, dest, filt)
    summary = execute_plan(plan)
    assert "2 well(s)" in summary

    # Review-readability closing assertion.
    extractor, fluor_tokens, smfish_tokens, pinfo = read_pipeline_info(dest / "out")
    assert extractor is not None
    assert "gfp" in fluor_tokens
    assert pinfo["available_timepoints"] == ["24h", "48h", "72h", "96h"]
    assert pinfo["available_fovs"] == ["F001"]

    df = load_well_csv(dest / "out" / "measurements_A01.csv")
    assert len(df) == 4
    assert set(df["timepoint"]) == {"24h", "48h", "72h", "96h"}
    assert set(df["fov"]) == {"F001"}

    with (src / "out" / "measurements_A01.csv").open() as f:
        src_rows = {(r["fov"], r["timepoint"]): r for r in csv.DictReader(f)}
    dest_row_48h = df[df["timepoint"] == "48h"].iloc[0]
    assert str(dest_row_48h["filename"]) == src_rows[("F001", "48h")]["filename"]

    # Overrides: kept for the surviving row, dropped for the filtered-out one,
    # and re-stamped to the destination zip's own mtime — read back through
    # the real production reader, not a stub.
    dest_overrides = load_overrides(dest / "out")
    assert len(dest_overrides) == 1
    ((well, fov, tp, nid), included) = next(iter(dest_overrides.items()))
    assert well == "A01"
    assert tp == "48"
    assert included == "0"

    # Source untouched.
    assert not (src / "out" / "measurements_B02.csv").parent.joinpath(".aw_subset_in_progress").exists()
    with (src / "out" / "measurements_B02.csv").open() as f:
        assert len(list(csv.DictReader(f))) == 8 * 3  # all timepoints x all fovs
