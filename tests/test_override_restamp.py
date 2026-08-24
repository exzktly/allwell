"""A carried-over cell override must not keep the source dataset's fingerprint.

Every tool that copies ``cell_overrides.json`` to a destination re-stamps each
entry's ``seg_fp`` to the destination ``<WELL>_out.zip``'s mtime, because a
repack changes the file's mtime while the segmentation behind it is untouched.
The stamp was assigned *inside* the ``swallow`` guarding the ``stat``:

    with swallow(f"stat {zip_path}"):
        entry["seg_fp"] = f"{zip_path.stat().st_mtime_ns}"

so when there is no ``<WELL>_out.zip`` in the destination — a run made without
QC images, or a well whose archive was tidied away — the entry silently kept the
**source** dataset's fingerprint. Validate then compares that against the
destination's archive, finds it different, and prunes the entry: the user's
manual cell exclusions vanish with every step reporting success.

A fingerprint that could not be computed is not a fingerprint. The key is
removed instead, which is the legacy unfingerprinted shape both readers already
keep, and the shortfall is stated in the line the dashboard shows.

Qt-free, fast lane.
"""

from __future__ import annotations

import json
import zipfile

from datasets._common.dataset_paths import DatasetPaths
from datasets.concatenate.apply import execute_plan as concat_execute
from datasets.concatenate.concatenate_compute import build_merge_plan
from datasets.merge_plates.apply import execute_plan as merge_execute
from datasets.merge_plates.merge_plates_compute import build_plate_merge_plan
from datasets.subset.apply import execute_plan as subset_execute
from datasets.subset.subset_compute import SubsetFilter, build_subset_plan
from datasets.validate.apply import _prune_stale_overrides
from tests.fixtures.dataset_builder import build_dataset

SOURCE_FINGERPRINT = "111111111111"


def _dataset_without_qc_archives(root, wells, timepoints, override_well):
    build_dataset(root, wells=wells, timepoints=timepoints, fovs=("F001",))
    for well in wells:
        (root / "out" / f"{well}_out.zip").unlink()
    (root / "out" / "cell_overrides.json").write_text(json.dumps({
        "version": 2,
        "overrides": [{
            "well": override_well, "fov": "F001", "tp": "24", "nucleus_id": "1",
            "included": "0", "seg_fp": SOURCE_FINGERPRINT,
        }],
    }))
    return root


def _carried(dest):
    return json.loads((dest / "out" / "cell_overrides.json").read_text())["overrides"]


def test_subset_drops_a_fingerprint_it_could_not_compute(tmp_path):
    src = _dataset_without_qc_archives(
        tmp_path / "src", ["A01"], ["0h", "24h"], "A01")
    dest = tmp_path / "dest"
    lines: list[str] = []
    subset_execute(
        build_subset_plan(src, dest, SubsetFilter(timepoint_labels=frozenset({"24h"}))),
        log=lines.append,
    )

    entry = _carried(dest)[0]
    assert entry.get("seg_fp", "") != SOURCE_FINGERPRINT
    assert "seg_fp" not in entry
    # The shortfall is stated where the dashboard shows it, not only implied.
    assert any("could not be fingerprinted" in line for line in lines)


def test_merge_plates_does_the_same(tmp_path):
    ds1 = _dataset_without_qc_archives(tmp_path / "ds1", ["A01"], ["0h", "24h"], "A01")
    ds2 = _dataset_without_qc_archives(tmp_path / "ds2", ["D04"], ["0h", "24h"], "D04")
    dest = tmp_path / "dest"
    merge_execute(build_plate_merge_plan(ds1, ds2, dest))
    assert all("seg_fp" not in entry for entry in _carried(dest))


def test_concatenate_does_the_same(tmp_path):
    ds1 = _dataset_without_qc_archives(tmp_path / "ds1", ["A01"], ["0h", "24h"], "A01")
    ds2 = _dataset_without_qc_archives(tmp_path / "ds2", ["A01"], ["48h", "72h"], "A01")
    dest = tmp_path / "dest"
    concat_execute(build_merge_plan(ds1, ds2, dest))
    assert all("seg_fp" not in entry for entry in _carried(dest))


def test_validate_keeps_the_carried_override_instead_of_pruning_it(tmp_path):
    """The end of the chain, and the reason any of this matters: with the source
    fingerprint left on it, the very next Validate run threw the override away
    as stale."""
    src = _dataset_without_qc_archives(
        tmp_path / "src", ["A01"], ["0h", "24h"], "A01")
    dest = tmp_path / "dest"
    subset_execute(
        build_subset_plan(src, dest, SubsetFilter(timepoint_labels=frozenset({"24h"}))))

    # A later QC re-run puts an out-zip there, which is what gives Validate
    # something to compare a stored fingerprint against.
    with zipfile.ZipFile(dest / "out" / "A01_out.zip", "w") as zf:
        zf.writestr("exp_GFP_A01_F001_24h_labels.tif", b"mask")

    paths = DatasetPaths(root=dest, in_dir=dest / "in", out_dir=dest / "out")
    assert _prune_stale_overrides(paths) == 0
    assert len(_carried(dest)) == 1


def test_a_fingerprint_that_can_be_computed_is_still_stamped(tmp_path):
    """The control: with the destination archive present, the entry is
    re-stamped to it — the behaviour the restamp exists for."""
    src, dest = tmp_path / "src", tmp_path / "dest"
    build_dataset(src, wells=["A01"], timepoints=["0h", "24h"], fovs=("F001",))
    (src / "out" / "cell_overrides.json").write_text(json.dumps({
        "version": 2,
        "overrides": [{"well": "A01", "fov": "F001", "tp": "24", "nucleus_id": "1",
                       "included": "0", "seg_fp": SOURCE_FINGERPRINT}],
    }))
    subset_execute(
        build_subset_plan(src, dest, SubsetFilter(timepoint_labels=frozenset({"24h"}))))

    entry = _carried(dest)[0]
    assert entry["seg_fp"] == f"{(dest / 'out' / 'A01_out.zip').stat().st_mtime_ns}"
