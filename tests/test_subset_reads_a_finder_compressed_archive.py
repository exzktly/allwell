"""Subset accepts an input archive whose images sit under a folder.

Finder's *Compress "A01"* writes ``A01.zip`` with a directory entry, every
image as ``A01/<name>``, and an AppleDouble twin of each under
``__MACOSX/A01/._<name>``. The pipeline reads such an archive (it takes every
member by basename) and Validate reports it clean, so it is an ordinary
dataset — and Subset refused it, because it looked each row's ``filename`` up
among the *full* member names: ``'exp_GFP_A01_F001_0h.tif', which isn't in the
filtered in/A01.zip``.
"""

from __future__ import annotations

import csv
import zipfile
from pathlib import Path

import pytest

from datasets.subset.apply import execute_plan
from datasets.subset.subset_compute import SubsetError, SubsetFilter, build_subset_plan
from datasets.validate.validate_compute import build_report
from tests.fixtures.dataset_builder import build_dataset


def _finder_compress(root: Path, *, drop_image: str | None = None) -> Path:
    """Rewrite every ``in/<well>.zip`` the way Finder's Compress lays one out."""
    for archive in sorted((root / "in").glob("*.zip")):
        with zipfile.ZipFile(archive) as zf:
            members = [(i.filename, zf.read(i.filename)) for i in zf.infolist()]
        archive.unlink()
        well = archive.stem
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(zipfile.ZipInfo(f"{well}/"), b"")
            zf.writestr(f"{well}/.DS_Store", b"\x00\x00\x00\x01Bud1")
            for name, data in members:
                if name != drop_image:
                    zf.writestr(f"{well}/{name}", data)
                zf.writestr(f"__MACOSX/{well}/._{name}", b"\x00\x05\x16\x07appledouble")
    return root


def _members(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        return sorted(zf.namelist())


def _rows(dataset: Path, well: str) -> list[dict]:
    (path,) = sorted((dataset / "out").glob(f"*_{well}.csv"))
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture
def nested(tmp_path) -> Path:
    src = _finder_compress(
        build_dataset(tmp_path / "src", wells=["A01", "B01"], timepoints=["0h", "24h"]))
    assert build_report(src).is_clean          # the premise: the app calls this dataset healthy
    return src


def test_a_well_subset_copies_the_archive_and_every_row(nested, tmp_path):
    dest = tmp_path / "dest"
    execute_plan(build_subset_plan(nested, dest, SubsetFilter(wells=frozenset({"A01"}))))
    assert _members(dest / "in" / "A01.zip") == _members(nested / "in" / "A01.zip")
    assert not (dest / "in" / "B01.zip").exists()
    kept, source = _rows(dest, "A01"), _rows(nested, "A01")
    assert len(source) > 0
    assert [r["filename"] for r in kept] == [r["filename"] for r in source]


def test_a_timepoint_subset_keeps_that_timepoints_images_and_rows(nested, tmp_path):
    dest = tmp_path / "dest"
    execute_plan(build_subset_plan(nested, dest, SubsetFilter(timepoint_labels=frozenset({"24h"}))))
    for well in ("A01", "B01"):
        images = [m for m in _members(dest / "in" / f"{well}.zip")
                  if m.endswith(".tif") and "__MACOSX" not in m]
        assert images == [f"{well}/exp_GFP_{well}_F001_24h.tif"]
        rows = _rows(dest, well)
        assert {r["filename"] for r in rows} == {f"exp_GFP_{well}_F001_24h.tif"}
        assert len(rows) == sum(
            r["filename"].endswith("_24h.tif") for r in _rows(nested, well))


def test_an_appledouble_twin_does_not_stand_in_for_a_missing_image(tmp_path):
    """The check still bites: only the ``._`` twin of the 24h image is in the archive."""
    src = _finder_compress(
        build_dataset(tmp_path / "src", wells=["A01"], timepoints=["0h", "24h"]),
        drop_image="exp_GFP_A01_F001_24h.tif")
    assert "__MACOSX/A01/._exp_GFP_A01_F001_24h.tif" in _members(src / "in" / "A01.zip")
    with pytest.raises(SubsetError, match="exp_GFP_A01_F001_24h.tif.*isn't in the filtered in/A01.zip"):
        execute_plan(build_subset_plan(
            src, tmp_path / "dest", SubsetFilter(wells=frozenset({"A01"}))))
