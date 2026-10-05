"""Local input staging cleans up after itself, on success and on failure.

Row R2 of ``_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md`` moves staging
out of ``services/remote_runner.py``. Its cleanup was characterised by mutation
first, against every test that reaches the remote client, and four mutations
survived:

* the incremental split, re-planning archives a channel filter had just
  staged, dropped the filter's own cleanup — leaving the filtered copies on
  the user's volume after every filtered resume;
* the helper that combines two cleanups ran only the first (invisible through
  ``stage_local_zips``, where the two directories nest, so it is tested on
  its own);
* a failure while writing the split's archives left the half-written staging
  directory behind (only the complexity ratchet noticed, because the function
  got simpler);
* the split lost the callback that removes a folder plate's leftover sources
  after a successful run.

Each test below fails under one of those. They compare the tree before staging
with the tree after cleanup, rather than naming the directories staging uses,
so a leftover is found wherever it was put.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from services import remote_runner
from services import remote_staging

SCHEMA = "experiment:channel:well:fov:timepoint"
SEP = "_"


def _zip(path: Path, members: list[str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name in members:
            zf.writestr(name, b"data")


def _prior_csv(path: Path, fov_tp_pairs: list[tuple[str, str]]) -> None:
    rows = ["well,fov,timepoint,nucleus_id"]
    rows += [f"{path.stem.split('_')[-1]},{f},{t},1" for f, t in fov_tp_pairs]
    path.write_text("\n".join(rows) + "\n")


def _tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def _folder_plate(tmp_path: Path) -> tuple[Path, Path]:
    """A folder-layout plate whose A01 is half done, so a resume rewrites it."""
    out = tmp_path / "out"
    out.mkdir()
    _prior_csv(out / "measurements_A01.csv", [("1", "T01")])
    plate = tmp_path / "plate"
    (plate / "A01").mkdir(parents=True)
    for name in ("e_w1_A01_1_T01.tif", "e_w1_A01_2_T01.tif"):
        (plate / "A01" / name).write_bytes(b"data")
    return plate, out


def test_a_filtered_resume_of_a_mixed_plate_leaves_only_its_archives(tmp_path):
    """A channel filter stages filtered copies (one cleanup); the resume then
    splits those into a second staging directory (another). After cleanup the
    plate holds exactly what staging is meant to leave: the well archive it
    built in place for the folder well, and the empty transit parent."""
    out = tmp_path / "out"
    out.mkdir()
    _prior_csv(out / "measurements_A01.csv", [("1", "T01")])
    _prior_csv(out / "measurements_B02.csv", [("1", "T01")])
    plate = tmp_path / "plate"
    plate.mkdir()
    _zip(plate / "A01.zip", ["e_w1_A01_1_T01.tif", "e_w1_A01_2_T01.tif",
                             "e_w2_A01_2_T01.tif"])
    (plate / "B02").mkdir()
    for name in ("e_w1_B02_1_T01.tif", "e_w1_B02_2_T01.tif", "e_w2_B02_2_T01.tif"):
        (plate / "B02" / name).write_bytes(b"data")
    before = _tree(tmp_path)

    zipped = remote_runner.stage_local_zips(
        plate, filename_schema=SCHEMA, filename_sep=SEP, fluor_tokens=["w1"],
        nuclear_token="w1", incremental=True, prior_output_dir=out)
    assert zipped.cleanup is not None
    zipped.cleanup()

    assert sorted(_tree(tmp_path) - before) == ["plate/.allwell-transit", "plate/B02.zip"]


def test_a_failure_while_writing_the_split_leaves_no_staging_behind(tmp_path, monkeypatch):
    """Archives already in zip form, one of them partial, so the split has to
    rewrite it; the rewrite then fails. Nothing staging created may remain
    beyond the transit parent every run shares."""
    out = tmp_path / "out"
    out.mkdir()
    _prior_csv(out / "measurements_A01.csv", [("1", "T01")])
    plate = tmp_path / "plate"
    plate.mkdir()
    _zip(plate / "A01.zip", ["e_w1_A01_1_T01.tif", "e_w1_A01_2_T01.tif"])
    before = _tree(tmp_path)

    def broken(*a, **kw):
        raise RuntimeError("disk full while rewriting")

    monkeypatch.setattr(remote_staging, "_materialize_staging", broken)
    with pytest.raises(RuntimeError, match="disk full"):
        remote_runner.stage_local_zips(
            plate, filename_schema=SCHEMA, filename_sep=SEP, incremental=True,
            prior_output_dir=out)
    assert sorted(_tree(tmp_path) - before) == ["plate/.allwell-transit"]


def test_the_split_keeps_the_callback_that_removes_zipped_sources(tmp_path):
    """A folder plate's success callback removes its leftover sources. A
    resumed run is split after the zip-up, and the split must hand the
    callback on rather than drop it."""
    plate, out = _folder_plate(tmp_path)
    zipped = remote_runner.stage_local_zips(
        plate, filename_schema=SCHEMA, filename_sep=SEP, incremental=True,
        prior_output_dir=out)
    try:
        assert zipped.incremental
        assert zipped.on_pipeline_success is not None
    finally:
        if zipped.cleanup is not None:
            zipped.cleanup()


def test_combined_cleanups_all_run():
    """The helper that joins the zipper's cleanup to the split's runs both.
    Through ``stage_local_zips`` the two directories nest, so running only the
    outer one hides the defect; this is the helper's own contract."""
    ran: list[str] = []
    joined = remote_staging._both(lambda: ran.append("a"), None,
                                 lambda: ran.append("b"))
    assert joined is not None
    joined()
    assert ran == ["a", "b"]
    assert remote_staging._both(None, None) is None


def test_a_typoed_channel_on_a_mixed_plate_fails_before_staging(tmp_path):
    """A plate interrupted part way through its zip-up — some wells zipped,
    some still folders — goes through its own staging path, which has its own
    zero-match check. Measured: disabling that one left every test green; the
    ZIP-layout test only reaches the other."""
    plate = tmp_path / "plate"
    plate.mkdir()
    _zip(plate / "A01.zip", ["e_w1_A01_1_T01.tif"])
    (plate / "B02").mkdir()
    (plate / "B02" / "e_w1_B02_1_T01.tif").write_bytes(b"data")
    before = _tree(tmp_path)

    with pytest.raises(RuntimeError, match="matched zero images in 2 well"):
        remote_runner.stage_local_zips(
            plate, filename_schema=SCHEMA, filename_sep=SEP,
            nuclear_token="w9", fluor_tokens=["w8"])
    leftover = sorted(_tree(tmp_path) - before)
    assert not [p for p in leftover if "allwell-stage" in p], leftover
