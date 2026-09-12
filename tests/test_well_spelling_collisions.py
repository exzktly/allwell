"""Two spellings of one plate position must not silently become one.

``in_containers`` / ``out_zips`` / ``out_csvs`` / ``out_folders`` each map a
canonical well to exactly one path. A dataset holding both ``in/A1.zip`` and
``in/A01.zip`` — what a plate exported by a scope that does not zero-pad and
later topped up by a run that does is left in — therefore collapses to whichever
the glob reached last, in filesystem order. Every Datasets copy tool walks that
map rather than the folder, so a whole well's images left no trace in the
destination, in the well count, or in the summary.

The maps still cannot represent the state (one key, one value), so what changed
is that the *folder* is asked directly before anything is written, and that the
survivor is at least deterministic rather than filesystem-order dependent.

Qt-free, fast lane.
"""

from __future__ import annotations

import zipfile

import pytest

from datasets.core.dataset_paths import DatasetPaths, in_containers
from datasets.concatenate.concatenate_compute import ConcatError, build_merge_plan
from datasets.merge_plates.merge_plates_compute import (
    MergePlatesError,
    build_plate_merge_plan,
)
from datasets.subset.subset_compute import SubsetError, SubsetFilter, build_subset_plan
from tests.fixtures.dataset_builder import build_dataset


def _add_unpadded_twin(root, padded="A01", unpadded="A1"):
    """Put the same plate position on disk twice, spelled two ways — inputs and
    QC archives both, the way a real half-converted plate carries it."""
    with zipfile.ZipFile(root / "in" / f"{unpadded}.zip", "w") as zf:
        zf.writestr(f"exp_GFP_{unpadded}_F001_0h.tif", b"unpadded-0h")
        zf.writestr(f"exp_GFP_{unpadded}_F001_24h.tif", b"unpadded-24h")
    with zipfile.ZipFile(root / "out" / f"{unpadded}_out.zip", "w") as zf:
        zf.writestr(f"exp_GFP_{unpadded}_F001_0h_labels.tif", b"mask")
    assert (root / "in" / f"{padded}.zip").exists()


def test_subset_refuses_a_dataset_that_spells_one_well_two_ways(tmp_path):
    src = tmp_path / "src"
    build_dataset(src, wells=["A01", "B02"], timepoints=["0h", "24h"])
    _add_unpadded_twin(src)

    with pytest.raises(SubsetError) as excinfo:
        build_subset_plan(src, tmp_path / "dest", SubsetFilter(wells=frozenset({"A01"})))
    message = str(excinfo.value)
    assert "A01.zip" in message and "A1.zip" in message
    assert "A01_out.zip" in message and "A1_out.zip" in message


def test_merge_plates_refuses_it_on_either_side(tmp_path):
    ds1, ds2 = tmp_path / "ds1", tmp_path / "ds2"
    build_dataset(ds1, wells=["A01"], timepoints=["0h", "24h"])
    build_dataset(ds2, wells=["D04"], timepoints=["0h", "24h"])
    _add_unpadded_twin(ds2, padded="D04", unpadded="D4")

    with pytest.raises(MergePlatesError) as excinfo:
        build_plate_merge_plan(ds1, ds2, tmp_path / "dest")
    assert "Dataset 2" in str(excinfo.value)
    assert "D4.zip" in str(excinfo.value)


def test_concatenate_refuses_it_too(tmp_path):
    ds1, ds2 = tmp_path / "ds1", tmp_path / "ds2"
    build_dataset(ds1, wells=["A01"], timepoints=["0h", "24h"])
    build_dataset(ds2, wells=["A01"], timepoints=["48h", "72h"])
    _add_unpadded_twin(ds1)

    with pytest.raises(ConcatError) as excinfo:
        build_merge_plan(ds1, ds2, tmp_path / "dest")
    assert "Dataset 1" in str(excinfo.value)


def test_a_measurements_csv_duplicated_under_one_prefix_is_refused(tmp_path):
    src = tmp_path / "src"
    build_dataset(src, wells=["A01"], timepoints=["0h", "24h"])
    (src / "out" / "measurements_A1.csv").write_text(
        (src / "out" / "measurements_A01.csv").read_text())

    with pytest.raises(SubsetError, match="measurements_A1.csv"):
        build_subset_plan(src, tmp_path / "dest", SubsetFilter())


def test_two_csv_prefixes_for_one_well_are_not_a_spelling_collision(tmp_path):
    """A mixed-``--csv_prefix`` ``out/`` folder is a different question, and one
    Validate already reports with a cause of its own. Refusing it here would
    make every copy tool reject a dataset Validate merely comments on."""
    src = tmp_path / "src"
    build_dataset(src, wells=["A01"], timepoints=["0h", "24h"])
    (src / "out" / "nuclei_A01.csv").write_text(
        (src / "out" / "measurements_A01.csv").read_text())

    plan = build_subset_plan(src, tmp_path / "dest", SubsetFilter())
    assert plan.wells_kept == ("A01",)


def test_a_healthy_dataset_with_real_archive_debris_is_not_refused(tmp_path):
    """The control. A well archive that has ever been opened in Finder carries
    a ``.DS_Store``, a directory entry and an AppleDouble sidecar, and a user
    may have dropped a text file in on purpose; none of that is a second
    spelling of a plate position, and a guard that refused it would refuse
    every real plate."""
    src = tmp_path / "src"
    build_dataset(src, wells=["A01", "B02"], timepoints=["0h"], junk=True, stray=True)
    paths = DatasetPaths(root=src, in_dir=src / "in", out_dir=src / "out")
    assert sorted(in_containers(paths)) == ["A01", "B02"]

    plan = build_subset_plan(src, tmp_path / "dest", SubsetFilter())
    assert plan.wells_kept == ("A01", "B02")


def test_the_surviving_container_is_deterministic_not_glob_order(tmp_path):
    """While one key can hold only one path, which path it holds must not depend
    on the order the filesystem happened to return: the canonical spelling wins,
    every time, so two machines reading one folder agree."""
    src = tmp_path / "src"
    build_dataset(src, wells=["A01"], timepoints=["0h"])
    _add_unpadded_twin(src)
    paths = DatasetPaths(root=src, in_dir=src / "in", out_dir=src / "out")
    assert in_containers(paths)["A01"].name == "A01.zip"
