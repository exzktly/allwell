"""Two runs that share one scratch directory leave each other's work alone.

The scratch base is one directory per machine or remote host
(``$ALLWELL_SCRATCH_DIR``, or ``~/.allwell/scratch``), so two app instances, or
two remote jobs whose *Remote scratch dir* is not set per job, work side by side
in it. Two things broke that:

* **The sweep at the start of every batch deleted every unfinished folder in
  it**, whichever run owned it — every ``_tmp_extract_*``, and every
  ``_tmp_images_*`` without a finished field. Run B starting a batch removed run
  A's unpacked images and in-flight staging, mid-well.
* **The unpack folder was named for the well alone** (``_tmp_extract_A01``), so
  two plates working on the same well at once shared one folder, and each run's
  start and end removed the other's images.

Now a folder named for this plate is swept at once, any other only once nothing
in it has changed for a day, and the unpack folder carries the plate's stamp.
"""

from __future__ import annotations

import io
import os
import time
import zipfile
from pathlib import Path

import numpy as np
import pytest

import pipeline.io as pio
from pipeline.fov_checkpoint import _staging_dir_for, extract_dir_for, sweep_run_scratch

tifffile = pytest.importorskip("tifffile")


def _plate(tmp_path: Path, name: str) -> Path:
    out = tmp_path / name
    out.mkdir()
    return out


def _live_folders(out: Path, well: str = "A01") -> tuple[Path, Path]:
    """The two folders a run has open while a well is part way through, before
    its first field has finished (no checkpoint marker yet)."""
    extract = extract_dir_for(out, well)
    extract.mkdir(parents=True)
    (extract / f"exp_DAPI_{well}_F001_0h.tif").write_bytes(b"pixels")
    staging = _staging_dir_for(out, well, "settings-a")
    staging.mkdir(parents=True)
    (staging / "partial.tif").write_bytes(b"pixels")
    return extract, staging


def _age(folder: Path, seconds: float) -> None:
    """Make *folder* and everything in it look last changed *seconds* ago."""
    then = time.time() - seconds
    for path in [folder, *folder.rglob("*")]:
        os.utime(path, (then, then))


def test_a_batch_start_leaves_another_plates_live_folders_alone(tmp_path):
    plate_a, plate_b = _plate(tmp_path, "plate_a"), _plate(tmp_path, "plate_b")
    a_extract, a_staging = _live_folders(plate_a)
    b_extract, b_staging = _live_folders(plate_b, "B02")       # plate B's own left-overs

    sweep_run_scratch(plate_b, "settings-b", extra_extract=True)

    assert sorted(p.name for p in a_extract.iterdir()) == ["exp_DAPI_A01_F001_0h.tif"]
    assert sorted(p.name for p in a_staging.iterdir()) == ["partial.tif"]
    assert not b_extract.exists() and not b_staging.exists(), (
        "this plate's own unfinished folders are left-overs and go at once")


def test_another_plates_folder_untouched_for_a_day_is_still_cleaned_up(tmp_path):
    plate_a, plate_b = _plate(tmp_path, "plate_a"), _plate(tmp_path, "plate_b")
    a_extract, a_staging = _live_folders(plate_a)
    legacy = pio.scratch_base_for(plate_a) / "_tmp_extract_C03"     # a name from before the stamp
    legacy.mkdir()
    (legacy / "x.tif").write_bytes(b"pixels")
    for folder in (a_extract, a_staging, legacy):
        _age(folder, pio.FOREIGN_SCRATCH_IDLE_S + 60)

    sweep_run_scratch(plate_b, "settings-b", extra_extract=True)

    assert not a_extract.exists() and not a_staging.exists() and not legacy.exists()


def test_a_day_old_folder_with_one_fresh_file_is_in_use(tmp_path):
    """Idle is the newest change anywhere inside, not the folder's own mtime —
    a live well writes files into a folder whose own entry may be old."""
    plate_a, plate_b = _plate(tmp_path, "plate_a"), _plate(tmp_path, "plate_b")
    a_extract, a_staging = _live_folders(plate_a)
    for folder in (a_extract, a_staging):
        _age(folder, pio.FOREIGN_SCRATCH_IDLE_S + 60)
    (a_staging / "partial.tif").write_bytes(b"just written")
    os.utime(a_staging, (time.time() - pio.FOREIGN_SCRATCH_IDLE_S - 60,) * 2)

    sweep_run_scratch(plate_b, "settings-b", extra_extract=True)

    assert a_staging.exists(), "a folder with a file written just now was swept as abandoned"
    assert not a_extract.exists()


def test_another_plates_checkpoints_are_kept_however_old(tmp_path):
    plate_a, plate_b = _plate(tmp_path, "plate_a"), _plate(tmp_path, "plate_b")
    _a_extract, a_staging = _live_folders(plate_a)
    (a_staging / ".fov_exp_DAPI_A01_F001_0h.done").touch()
    _age(a_staging, pio.FOREIGN_SCRATCH_IDLE_S * 30)

    sweep_run_scratch(plate_b, "settings-b", extra_extract=True)

    assert a_staging.exists()


def _well_zip(tmp_path: Path) -> Path:
    zip_path = tmp_path / "A01.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for channel in ("DAPI", "GFP"):
            buf = io.BytesIO()
            tifffile.imwrite(buf, np.zeros((8, 8), dtype=np.uint16))
            zf.writestr(f"exp_{channel}_A01_F001_0h.tif", buf.getvalue())
    return zip_path


def test_two_plates_running_the_same_well_do_not_share_an_unpack_folder(tmp_path, monkeypatch):
    """Drives plate B's whole well task while plate A is part way through its
    own A01: B's start-of-well cleanup and end-of-well removal must not reach
    A's unpacked images."""
    import process_microscopy as pm

    plate_a, plate_b = _plate(tmp_path, "plate_a"), _plate(tmp_path, "plate_b")
    a_extract, _a_staging = _live_folders(plate_a)
    b_seen: list[Path] = []

    def _group(nuclear_path, fluor_paths, **_kwargs):
        b_seen.append(nuclear_path.parent)
        return [{"nucleus_id": 1}], None

    monkeypatch.setattr(pm, "process_image_group", _group)
    records, failed = pm.process_well_zip_task(
        "A01", _well_zip(tmp_path), plate_b, {}, "DAPI", ["GFP"], "measurements")

    assert failed == [] and len(records) == 1
    assert b_seen and b_seen[0] != a_extract
    assert sorted(p.name for p in a_extract.iterdir()) == ["exp_DAPI_A01_F001_0h.tif"], (
        "plate B's well task removed plate A's unpacked images for the same well")
