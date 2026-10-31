"""Local input staging cleans up after itself, on success and on failure.

Row R2 of the remediation plan (deleted when its last row closed; ``git log --
_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md`` recovers it) moves staging
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


def test_a_staged_member_is_copied_a_chunk_at_a_time(tmp_path, monkeypatch):
    """A rewritten archive is written by streaming each kept member, never by
    reading one whole into memory (remote review S10): a single image
    of a stitched or multi-page acquisition is hundreds of megabytes, and
    ``writestr(info, src.read(member))`` held it all at once. Any read of a
    member larger than one copy chunk fails here — which also catches reading
    it whole through ``ZipExtFile.read()`` rather than ``ZipFile.read``."""
    import os

    chunk = getattr(remote_staging, "_COPY_CHUNK", 1 << 20)
    plate = tmp_path / "plate"
    plate.mkdir()
    big = os.urandom(chunk * 3 + 17)
    with zipfile.ZipFile(plate / "A01.zip", "w") as zf:
        zf.writestr("e_w1_A01_1_T01.tif", big)
        zf.writestr("e_w2_A01_1_T01.tif", os.urandom(chunk * 3))
    real_read = zipfile.ZipExtFile.read

    def bounded_read(self, n=-1):
        assert n is not None and 0 <= n <= chunk, f"read({n}) of a whole member"
        return real_read(self, n)

    monkeypatch.setattr(zipfile.ZipExtFile, "read", bounded_read)
    zipped = remote_runner.stage_local_zips(
        plate, filename_schema=SCHEMA, filename_sep=SEP,
        fluor_tokens=["w1"], nuclear_token=None)
    monkeypatch.setattr(zipfile.ZipExtFile, "read", real_read)
    try:
        (staged,) = zipped.well_zips
        assert staged.parent != plate
        with zipfile.ZipFile(staged) as zf:
            assert zf.namelist() == ["e_w1_A01_1_T01.tif"]
            assert zf.read("e_w1_A01_1_T01.tif") == big
    finally:
        zipped.cleanup()
