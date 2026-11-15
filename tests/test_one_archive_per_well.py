"""Two archives that name one well are refused, by the run, the remote staging and the Analyze readout.

The full-app review (pull request #1343), row H1. ``A1.zip`` beside ``A01.zip`` were both
accepted and both run as well A01; each wrote the well's one CSV, so the second to finish replaced the first's
rows (fields 1-2 survived with one worker, fields 3-4 with two) and the run report counted the well complete.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from pipeline.input_layout import find_well_zips, non_zip_input, refuse_non_zip_input


def _plate(root: Path, names) -> Path:
    in_dir = root / "plate" / "in"
    in_dir.mkdir(parents=True)
    for name in names:
        with zipfile.ZipFile(in_dir / name, "w") as zf:
            zf.writestr(f"exp_GFP_{Path(name).stem}_F001_0h.tif", b"not really a tif")
        # The mess a real folder carries: none of it is a well archive.
    (in_dir / ".DS_Store").write_bytes(b"\x00")
    (in_dir / "._A01.zip").write_bytes(b"\x00")
    return in_dir


def test_two_spellings_of_one_well_are_named_and_refused(tmp_path):
    in_dir = _plate(tmp_path, ["A1.zip", "A01.zip", "B02.zip", "b3.zip", "B03.zip"])
    well_zips = find_well_zips(in_dir)
    found = non_zip_input(in_dir, well_zips)
    assert found.duplicates == [("A01", ["A01.zip", "A1.zip"]), ("B03", ["B03.zip", "b3.zip"])]
    assert found.refused
    with pytest.raises(SystemExit) as stopped:
        refuse_non_zip_input(in_dir, well_zips)
    message = str(stopped.value)
    assert "A01 (A01.zip, A1.zip)" in message and "B03 (B03.zip, b3.zip)" in message


def test_a_plate_with_one_archive_per_well_is_not_refused(tmp_path):
    in_dir = _plate(tmp_path, ["A01.zip", "A02.zip", "B02.zip"])
    well_zips = find_well_zips(in_dir)
    assert non_zip_input(in_dir, well_zips).duplicates == []
    refuse_non_zip_input(in_dir, well_zips)


def test_remote_staging_refuses_the_same_plate(tmp_path):
    from services.remote_staging import _refuse_non_zip_input

    in_dir = _plate(tmp_path, ["A1.zip", "A01.zip"])
    with pytest.raises(RuntimeError, match=r"A01 \(A01\.zip, A1\.zip\)"):
        _refuse_non_zip_input(in_dir, tuple(sorted(in_dir.glob("[!.]*.zip"))))


def test_the_analyze_readout_says_so_before_run(tmp_path):
    from services.input_resolution_service import plan_input_layout

    in_dir = _plate(tmp_path, ["A1.zip", "A01.zip", "B02.zip"])
    plan = plan_input_layout(in_dir.parent)
    assert not plan.runnable
    assert "A01" in plan.message and "A1.zip" in plan.message and "one archive per well" in plan.message
