"""What a remote run's fetched output must prove before the remote copy goes.

Row R1 of the remediation plan (deleted when its last row closed; ``git log --
_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md`` recovers it) moves fetched-
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

import gzip
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from services import remote_fetch
from services.remote_config import RemoteHost
from services.remote_runner import (
    LocalInputMode,
    OutputFilter,
    RemoteRunner,
)
from services.remote_upload import DatasetCache
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
    monkeypatch.setattr(remote_fetch, "compress_remote_csvs", lambda *a: None)
    try:
        fetcher._fetch_one("A01")
        # An incremental well is merged on the Placer's thread (remote_fetch_place); the app drains it before the
        # run is reported done, and so does this.
        fetcher.flush_and_join(timeout=10)
    finally:
        fetcher.cancel()

    # The merged well CSV is gzipped — and was never written plain.
    from util.csv_gz import open_csv_text
    assert not (out / "plate_A01.csv").exists()
    with open_csv_text(out / "plate_A01.csv.gz") as fh:
        rows = fh.read().splitlines()
    assert rows[0] == header
    assert sorted(rows[1:]) == ["A01,1,0,1,10", "A01,2,0,1,20"]


def _drive_fetch_one(host, out, run, monkeypatch, *, pulled_name, events):
    """Run ``_fetch_one`` with the transport stubbed; ``events`` records the
    order of remote compression, listing and pull."""
    fetcher = _fetcher(host, run)

    def fake_list(self, src):
        events.append(("list", src))
        return "test-host", [f"/r/out/{pulled_name}"], 20

    def fake_pull_listed(self, host, files, dest_dir):
        events.append(("pull", list(files)))
        (dest_dir / pulled_name).write_bytes(
            gzip.compress(b"well,fov\nA01,NEW\n") if pulled_name.endswith(".gz")
            else b"well,fov\nA01,NEW\n")
        return 1

    monkeypatch.setattr(Fetcher, "_rsync",
                        lambda self, argv: _zip(argv[-1], {"A01_f1_t0_gfp.tif": b"x"}) or True)
    monkeypatch.setattr(Fetcher, "_remote_file_size", lambda self, p: None)
    monkeypatch.setattr(Fetcher, "_already_local", lambda self, w: None)
    monkeypatch.setattr(Fetcher, "_list_remote_glob", fake_list)
    monkeypatch.setattr(Fetcher, "_pull_listed", fake_pull_listed)
    monkeypatch.setattr(Fetcher, "_delete_well_files_on_remote", lambda self, w: None)
    monkeypatch.setattr(Fetcher, "_pull_settings_marker", lambda self, w: None)
    monkeypatch.setattr(remote_fetch, "compress_remote_csvs",
                        lambda host, rout, well, log: events.append(("compress", well)))
    try:
        fetcher._fetch_one("A01")
    finally:
        fetcher.cancel()
    return fetcher


def test_a_well_not_yet_transferred_is_compressed_on_the_remote_first(host, tmp_path, monkeypatch):
    """The in-flight case: a run started before CSVs were compressed leaves a
    plain CSV on the remote, and it must be gzipped before it is listed and
    pulled — and the listing must name both spellings."""
    out = tmp_path / "out"
    out.mkdir()
    events: list = []
    _drive_fetch_one(host, out, _run(out), monkeypatch,
                     pulled_name="plate_A01.csv.gz", events=events)
    kinds = [e[0] for e in events]
    assert kinds == ["compress", "list", "pull"]
    listing = events[1][1]
    assert listing.endswith(":/r/out/*_A01.csv /r/out/*_A01.csv.gz")


def test_a_well_already_transferred_is_not_compressed_again(host, tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    run = _run(out)
    run.fetched_wells.add("A01")
    events: list = []
    _drive_fetch_one(host, out, run, monkeypatch,
                     pulled_name="plate_A01.csv.gz", events=events)
    assert "compress" not in [e[0] for e in events]


def test_a_fetched_gz_is_not_shadowed_by_the_previous_runs_plain_csv(host, tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    (out / "plate_A01.csv").write_text("well,fov\nA01,STALE\n")
    _drive_fetch_one(host, out, _run(out), monkeypatch,
                     pulled_name="plate_A01.csv.gz", events=[])
    from util.csv_gz import existing_csv, open_csv_text
    found = existing_csv(out / "plate_A01.csv")
    assert found == out / "plate_A01.csv.gz"
    with open_csv_text(found) as fh:
        assert fh.read().splitlines()[1] == "A01,NEW"
