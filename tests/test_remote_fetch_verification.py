"""What a remote run's fetched output must prove before the remote copy goes.

Row R1 of ``_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md`` moves fetched-
output verification and materialisation out of ``services/remote_runner.py``.
Before the move, each behaviour the plan names was attacked by mutation against
every test file that reaches the runner; four survived, and these tests are the
ones that now catch them:

* an empty local ``<well>_out.zip`` passed verification (only a missing one
  failed it), so a zero-byte artefact would have let the remote copy be deleted;
* the end-of-run retry of missing wells could be removed from
  ``verify_and_cleanup`` without any test noticing — the existing tests call the
  retry directly, never through the verification that is supposed to use it;
* on an incremental run, a fetched well zip or CSV holds only the new fields of
  view. Both could be written over the existing local artefact instead of merged
  into it, discarding every earlier field, with the suite green.
"""

from __future__ import annotations

import zipfile
from unittest.mock import MagicMock, patch

import pytest

from services.remote_config import RemoteHost
from services.remote_runner import (
    DatasetCache,
    LocalInputMode,
    OutputFilter,
    RemoteRunner,
)
from services.remote_fetch import Fetcher
from services.remote_run_state import _RemoteRun
from services.remote_fetch import FetchedOutputVerifier


@pytest.fixture
def host() -> RemoteHost:
    return RemoteHost(
        name="test", ssh_alias="test-host", remote_project_dir="~/allwell",
        remote_staging_dir="~/.allwell-staging", remote_python="/opt/python",
        cpu_only=False, extra_args=(), cache_budget_gb=10,
        measured_bandwidth_mbps=100.0,
    )


def _run(tmp_path, **kw) -> _RemoteRun:
    return _RemoteRun(
        run_id="r", cache_key="k", remote_input_dir="/r/in",
        remote_output_dir="/r/out", remote_pidfile="/r/pid",
        local_output_dir=tmp_path, local_input_mode=LocalInputMode.ZIP, **kw)


def _zip(path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def _names(path) -> set[str]:
    with zipfile.ZipFile(path) as zf:
        return set(zf.namelist())


def test_an_empty_local_out_zip_fails_verification(host, tmp_path):
    runner = RemoteRunner(host)
    run = _run(tmp_path)
    run.expected_wells = {"A01"}
    runner._run = run
    (tmp_path / "plate_A01.csv").write_text("a,b\n1,2\n")
    (tmp_path / "A01_out.zip").write_bytes(b"")

    with patch.object(FetchedOutputVerifier, "_list_remote_completed_wells", lambda self: []):
        missing = runner._output_verifier()._verify_local_manifest(log=lambda _m: None)

    assert missing == ["missing A01_out.zip locally"]


def test_verification_retries_a_missing_well_and_passes_once_it_lands(
        host, tmp_path, monkeypatch):
    """The retry is part of verification: a well missing at first and pulled
    by the end-of-run retry must let verification pass and cleanup run."""
    runner = RemoteRunner(host)
    run = _run(tmp_path)
    run.expected_wells = {"A01"}
    runner._run = run
    rm_calls: list[list[str]] = []
    monkeypatch.setattr(
        DatasetCache, "_ssh_run",
        lambda self, argv, *, check: (rm_calls.append(list(argv)),
                                      MagicMock(stdout="", returncode=0))[1])
    monkeypatch.setattr(FetchedOutputVerifier, "_rsync_optional",
                        lambda self, src, dest, log_fn, **kw: None)
    monkeypatch.setattr(FetchedOutputVerifier, "_list_remote_completed_wells", lambda self: [])

    retried: list[list[str]] = []

    def fake_retry(self, missing, *, log):
        retried.append(list(missing))
        (tmp_path / "plate_A01.csv").write_text("a,b\n1,2\n")
        _zip(tmp_path / "A01_out.zip", {"A01_f1.tif": b"x"})
        return {"A01"}

    monkeypatch.setattr(FetchedOutputVerifier, "_retry_missing_wells", fake_retry)

    ok = runner.verify_and_cleanup(ok=True, cleanup_mode="run", log_fn=lambda _m: None)

    assert retried == [["missing CSV for A01"]]
    assert ok is True
    assert [c for c in rm_calls if c and c[0] == "rm"], "cleanup did not run"


def _fetcher(host, run):
    with patch.object(Fetcher, "_loop", lambda self: None):
        return Fetcher(host, run, output_filter=OutputFilter.FULL,
                       log_fn=lambda _m: None)


def test_an_incremental_well_zip_is_merged_into_the_local_one(host, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    _zip(out / "A01_out.zip", {"A01_f1_t0_gfp.tif": b"old"})
    fetched = tmp_path / "fetched.zip"
    _zip(fetched, {"A01_f2_t0_gfp.tif": b"new"})
    fetcher = _fetcher(host, _run(out, incremental=True))
    try:
        assert fetcher._materialize("A01", fetched) is True
    finally:
        fetcher.cancel()
    assert _names(out / "A01_out.zip") == {"A01_f1_t0_gfp.tif", "A01_f2_t0_gfp.tif"}


def test_an_incremental_well_csv_is_merged_into_the_local_one(host, tmp_path, monkeypatch):
    """Driven through ``_fetch_one``, which is where the merge-or-replace
    decision is made; the fetched CSV holds only the new field's rows."""
    out = tmp_path / "out"
    out.mkdir()
    header = "well,fov,timepoint,nucleus_id,v"
    (out / "plate_A01.csv").write_text(f"{header}\nA01,1,0,1,10\n")
    _zip(out / "A01_out.zip", {"A01_f1_t0_gfp.tif": b"old"})
    run = _run(out, incremental=True)
    fetcher = _fetcher(host, run)

    def fake_rsync(self, argv):
        _zip(argv[-1], {"A01_f2_t0_gfp.tif": b"new"})
        return True

    def fake_pull_listed(self, host, files, dest_dir):
        (dest_dir / "plate_A01.csv").write_text(f"{header}\nA01,2,0,1,20\n")
        return 1

    monkeypatch.setattr(Fetcher, "_rsync", fake_rsync)
    monkeypatch.setattr(Fetcher, "_remote_file_size", lambda self, p: None)
    monkeypatch.setattr(Fetcher, "_already_local", lambda self, w: None)
    monkeypatch.setattr(Fetcher, "_list_remote_glob",
                        lambda self, src: ("test-host", ["/r/out/plate_A01.csv"], 20))
    monkeypatch.setattr(Fetcher, "_pull_listed", fake_pull_listed)
    monkeypatch.setattr(Fetcher, "_delete_well_files_on_remote", lambda self, w: None)
    monkeypatch.setattr(Fetcher, "_pull_settings_marker", lambda self, w: None)
    try:
        fetcher._fetch_one("A01")
    finally:
        fetcher.cancel()

    rows = (out / "plate_A01.csv").read_text().splitlines()
    assert rows[0] == header
    assert sorted(rows[1:]) == ["A01,1,0,1,10", "A01,2,0,1,20"]
