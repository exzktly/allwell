"""A remote command that failed is never read as "there is nothing there" (tracker section A, B4–B6, C5/C7).

The pattern behind most of the remote-review findings: an ssh listing that exited 255 produced exactly the output
of an empty directory, and the code then acted on "nothing there" — marked a well fetched whose CSV never
arrived and deleted its remote copy, passed verification over zero wells and ``rm -rf``'d the run, stamped a new
settings marker onto yesterday's numbers. Every test here drives the real ``Fetcher``, ``FetchedOutputVerifier``
and ``RemoteRunner.verify_and_cleanup`` against stand-in ``ssh`` / ``rsync`` binaries
(``tests/_standin_remote.py``) that run the remote side locally and fail on cue, and asserts what reached the
host and the output folder: which files survive, which marker landed, what verification decided.
"""

from __future__ import annotations

import datetime
import gzip
import json
import os
import zipfile
from pathlib import Path

import pytest

from services import remote_fetch
from services.remote_fetch import FetchedOutputVerifier, Fetcher, _snapshot_local_artifacts
from services.remote_run_state import LocalInputMode, OutputFilter, _RemoteRun
from services.remote_upload import DatasetCache
from tests._standin_remote import StandinHost, standin_env_ok

pytestmark = pytest.mark.skipif(not standin_env_ok(), reason="needs bash and rsync")

RUN = "~/.allwell-staging/runs/r1"
#: The listing scripts, before and after the fix: ``ls -1 <glob> 2>/dev/null || true`` and the bash glob loop.
LISTINGS = ("ls -1 ", '[ -e "$f" ]')


@pytest.fixture
def standin(tmp_path, monkeypatch):
    return StandinHost(tmp_path, monkeypatch)


def _aged(*paths: Path) -> None:
    old = (datetime.datetime.now() - datetime.timedelta(days=1)).timestamp()
    for p in paths:
        os.utime(p, (old, old))


def _zip(path: Path, members: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def _gz(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


def _remote_well(standin, well="A01", *, marker="today's settings"):
    """What a finished remote well leaves: its input, its archive, its gzipped CSV and its settings marker."""
    _zip(standin.path(f".allwell-staging/runs/r1/in/{well}.zip"), {f"exp_DAPI_{well}_f1_t0.tif": b"raw"})
    _zip(standin.path(f".allwell-staging/runs/r1/out/{well}_out.zip"), {f"exp_DAPI_{well}_f1_t0.tif": b"new image"})
    _gz(standin.path(f".allwell-staging/runs/r1/out/measurements_{well}.csv.gz"),
        f"well,fov,timepoint,nucleus_id,area\n{well},f1,t0,1,250\n")
    standin.path(f".allwell-staging/runs/r1/out/.aw_settings/{well}.json").write_text(
        json.dumps({"well": well, "digest": marker}))
    standin.path(".allwell-staging/runs/r1/out/pipeline_info.json").write_text(json.dumps(
        {"schema": "exp:channel:well:fov:timepoint", "separator": "_",
         "execution_options": {"csv_prefix": "measurements"}}))


def _local_old_well(out: Path, well="A01") -> None:
    """Yesterday's output for the well: whole, non-empty, and what this run was meant to replace."""
    _zip(out / f"{well}_out.zip", {f"exp_DAPI_{well}_f1_t0.tif": b"old image"})
    (out / f"measurements_{well}.csv").write_text(f"well,fov,timepoint,nucleus_id,area\n{well},f1,t0,1,100\n")
    _aged(out / f"{well}_out.zip", out / f"measurements_{well}.csv")


def _run(out: Path, **kw) -> _RemoteRun:
    params = dict(run_id="r1", cache_key="k1", remote_input_dir=f"{RUN}/in", remote_output_dir=f"{RUN}/out",
                  remote_pidfile=f"{RUN}/run.pid", local_output_dir=out, local_input_mode=LocalInputMode.ZIP,
                  output_filter=OutputFilter.FULL, pre_run_artifacts=_snapshot_local_artifacts(out))
    params.update(kw)
    return _RemoteRun(**params)


def _fetch(standin, run, *wells, lines=None):
    """The app's own order: the done line reaches ``observe``, then the stream ends and ``flush_and_join``."""
    log = lines.append if lines is not None else (lambda _m: None)
    fetcher = Fetcher(standin.host(), run, output_filter=run.output_filter, log_fn=log, delete_after_download=True)
    for well in wells:
        fetcher.observe(f"2026-10-28 10:00:00  INFO  Well {well} — done.\n")
    fetcher.flush_and_join(timeout=30)
    return fetcher


def _verify(standin, run, lines=None, **kw):
    import threading

    log = lines.append if lines is not None else (lambda _m: None)
    verifier = FetchedOutputVerifier(standin.host(), run, DatasetCache(standin.host()), threading.Event())
    return verifier.verify(True, log=log, **kw)


def _local_marker(out: Path, well="A01"):
    p = out / ".aw_settings" / f"{well}.json"
    return json.loads(p.read_text())["digest"] if p.exists() else None


# ── A1: the listing itself ───────────────────────────────────────────────────

def test_a_listing_whose_ssh_failed_is_unknown_not_empty(standin, tmp_path):
    _remote_well(standin)
    fetcher = Fetcher.__new__(Fetcher)
    fetcher.host, fetcher._log = standin.host(), (lambda _m: None)
    glob = f"standin:{RUN}/out/*_A01.csv.gz"
    assert fetcher._list_remote_glob(glob)[1] == [str(standin.root / ".allwell-staging/runs/r1/out/measurements_A01.csv.gz")]
    assert fetcher._list_remote_glob(f"standin:{RUN}/out/*_B07.csv.gz")[1] == [], "a genuinely empty answer"
    standin.fail_ssh(*LISTINGS)
    assert fetcher._list_remote_glob(glob)[1] is None, "an ssh that exited 255 read as 'the remote has no such files'"
    assert fetcher._rsync_glob(glob, tmp_path) == (None, 0), "(0, 0) is the answer for 'nothing there'"


def test_a_completed_wells_listing_whose_ssh_failed_is_unknown(standin, tmp_path):
    import threading

    _remote_well(standin)
    verifier = FetchedOutputVerifier(standin.host(), _run(tmp_path), DatasetCache(standin.host()), threading.Event())
    assert verifier._list_remote_completed_wells() == {"A01"}
    assert verifier._list_completed_wells_in("~/.allwell-staging/runs/nowhere/out") == set()
    standin.fail_ssh(*LISTINGS)
    assert verifier._list_remote_completed_wells() is None


def test_the_retry_s_csv_listing_whose_ssh_failed_is_unknown(standin):
    """Row A10."""
    from services.remote_csv import list_remote_csvs

    _remote_well(standin)
    assert len(list_remote_csvs(standin.host(), f"{RUN}/out", "A01", lambda _m: None, compress=False)) == 1
    standin.fail_ssh(*LISTINGS)
    assert list_remote_csvs(standin.host(), f"{RUN}/out", "A01", lambda _m: None, compress=False) is None


# ── A2: a well whose CSV never arrived is not fetched ────────────────────────

@pytest.mark.parametrize("fault", ["listing", "csv-pull"])
def test_a_well_whose_csv_did_not_arrive_is_kept_on_the_remote(standin, tmp_path, fault):
    out = tmp_path / "out"
    out.mkdir()
    _local_old_well(out)
    _remote_well(standin)
    run = _run(out)
    if fault == "listing":
        standin.fail_ssh(*LISTINGS)
    else:
        standin.fail_rsync("measurements_A01.csv.gz")
    lines: list[str] = []
    _fetch(standin, run, "A01", lines=lines)

    remote_out = standin.root / ".allwell-staging/runs/r1/out"
    assert "A01" not in run.fetched_wells
    assert (remote_out / "measurements_A01.csv.gz").exists(), "the only copy of this run's rows was deleted"
    assert (remote_out / "A01_out.zip").exists() and (standin.root / ".allwell-staging/runs/r1/in/A01.zip").exists()
    assert _local_marker(out) is None, "the new settings marker was stamped onto the old CSV"
    assert any("A01: WARN not counted as fetched" in line for line in lines), lines
    assert (out / "measurements_A01.csv").read_text().endswith(",100\n"), "the old CSV is untouched"


def test_verify_fails_a_well_whose_csv_was_not_replaced_and_withholds_its_marker(standin, tmp_path):
    """The second half of A2: the archive arrived, the CSV did not; yesterday's CSV must not pass verify."""
    out = tmp_path / "out"
    out.mkdir()
    _local_old_well(out)
    _remote_well(standin)
    run = _run(out)
    standin.fail_rsync("measurements_A01.csv.gz")
    _fetch(standin, run, "A01")
    verdict = _verify(standin, run)
    assert not verdict.passed
    assert "the CSV for A01 was not replaced (same size and timestamp as before the run)" in verdict.missing
    assert _local_marker(out) is None
    # Once the CSV can come home, the retry replaces it and only then brings the marker.
    standin.fail_rsync()
    verdict = _verify(standin, run)
    assert verdict.passed, verdict.missing
    assert _local_marker(out) == "today's settings"
    with gzip.open(out / "measurements_A01.csv.gz", "rt") as fh:
        assert fh.read().endswith(",250\n")


# ── A3: a failed verify listing never permits cleanup ────────────────────────

def test_a_cache_hit_rerun_whose_verify_listing_fails_deletes_nothing(standin, tmp_path):
    """FULL CACHE HIT: the pipeline is replaced by ``true``, no well announces, and verify's listing fails."""
    from services.remote_runner import RemoteRunner

    out = tmp_path / "out"
    out.mkdir()
    _remote_well(standin)
    standin.path(".allwell-staging/datasets/k1/A01.zip").write_bytes(b"cached input")
    runner = RemoteRunner(standin.host())
    runner._run = _run(out)
    standin.fail_ssh(*LISTINGS)
    lines: list[str] = []
    assert runner.verify_and_cleanup(True, cleanup_mode="all", log_fn=lines.append) is False
    assert (standin.root / ".allwell-staging/runs/r1/out/A01_out.zip").exists(), "the run dir was removed"
    assert (standin.root / ".allwell-staging/datasets/k1/A01.zip").exists(), "the dataset cache was removed"
    assert any("could not be listed" in line for line in lines), lines


# ── A5: the retry never stamps a marker over output it did not replace ───────

def test_the_retry_of_an_announced_well_whose_zip_failed_withholds_the_marker(standin, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    _local_old_well(out)
    _remote_well(standin)
    run = _run(out)
    standin.fail_rsync("A01_out.zip")
    _fetch(standin, run, "A01")
    verdict = _verify(standin, run)
    assert not verdict.passed
    assert "A01_out.zip was not replaced (same size and timestamp as before the run)" in verdict.missing
    assert _local_marker(out) is None, "the next run would skip A01 as current under today's settings"
    # The retry did pull the CSV the run was meant to replace: the half it could get, it got.
    assert (out / "measurements_A01.csv.gz").exists()


# ── A6: a failed incremental merge keeps the well ────────────────────────────

def test_a_failed_incremental_merge_keeps_the_remote_copy_and_the_fetched_rows(standin, tmp_path, monkeypatch):
    import errno

    import pipeline.io as pio

    out = tmp_path / "out"
    out.mkdir()
    _zip(out / "A01_out.zip", {"exp_DAPI_A01_f1_t0.tif": b"fov 1"})
    _gz(out / "measurements_A01.csv.gz", "well,fov,timepoint,nucleus_id\nA01,f1,t0,1\n")
    # The remote analysed only the new field, f2.
    _zip(standin.path(".allwell-staging/runs/r1/in/A01.zip"), {"exp_DAPI_A01_f2_t0.tif": b"raw"})
    _zip(standin.path(".allwell-staging/runs/r1/out/A01_out.zip"), {"exp_DAPI_A01_f2_t0.tif": b"fov 2"})
    _gz(standin.path(".allwell-staging/runs/r1/out/measurements_A01.csv.gz"),
        "well,fov,timepoint,nucleus_id\nA01,f2,t0,1\n")
    standin.path(".allwell-staging/runs/r1/out/pipeline_info.json").write_text("{}")

    def _eio(*_a, **_k):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(pio, "merge_records_into_gz_csv", _eio)
    run = _run(out, incremental=True)
    _fetch(standin, run, "A01")

    assert "A01" not in run.fetched_wells
    assert (standin.root / ".allwell-staging/runs/r1/out/measurements_A01.csv.gz").exists()
    assert (standin.root / ".allwell-staging/runs/r1/out/A01_out.zip").exists()
    with zipfile.ZipFile(out / "A01_out.zip") as zf:
        assert zf.namelist() == ["exp_DAPI_A01_f1_t0.tif"], "f2's image was merged beside a CSV without its rows"
    kept = list(out.glob(".allwell-transit*/allwell-csv-A01-*/measurements_A01.csv.gz")) + \
        list(out.glob("**/allwell-csv-A01-*/measurements_A01.csv.gz"))
    assert kept, "the fetched rows were deleted with the transit directory"
    verdict = _verify(standin, run)
    assert not verdict.passed
    assert any(m.startswith("A01: the merge of measurements_A01.csv.gz") for m in verdict.missing), verdict.missing


# ── B6: an unreadable local archive is set aside, and the run says so ────────

def test_an_unreadable_local_zip_is_set_aside_and_fails_the_run(standin, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    whole = _zip(tmp_path / "whole.zip", {"exp_DAPI_A01_f1_t0.tif": b"x" * 4000})
    (out / "A01_out.zip").write_bytes(whole.read_bytes()[:1000])          # cut short
    _gz(out / "measurements_A01.csv.gz", "well,fov,timepoint,nucleus_id\nA01,f1,t0,1\n")
    _zip(standin.path(".allwell-staging/runs/r1/in/A01.zip"), {"exp_DAPI_A01_f2_t0.tif": b"raw"})
    _zip(standin.path(".allwell-staging/runs/r1/out/A01_out.zip"), {"exp_DAPI_A01_f2_t0.tif": b"fov 2"})
    _gz(standin.path(".allwell-staging/runs/r1/out/measurements_A01.csv.gz"),
        "well,fov,timepoint,nucleus_id\nA01,f2,t0,1\n")
    standin.path(".allwell-staging/runs/r1/out/pipeline_info.json").write_text("{}")
    run = _run(out, incremental=True)
    _fetch(standin, run, "A01")
    aside = out / ".A01_out.zip.unreadable"
    assert aside.exists(), "the unreadable archive was replaced by the new fields alone, with nothing kept or said"
    assert aside.read_bytes() == whole.read_bytes()[:1000], "never deleted"
    assert "--force" in (out / "errors" / "A01_local_zip_unreadable.txt").read_text()
    verdict = _verify(standin, run)
    assert not verdict.passed
    assert any(m.startswith("A01: the local A01_out.zip could not be read") for m in verdict.missing)


# ── A8: this run's sidecar must arrive ───────────────────────────────────────

def test_a_failed_sidecar_pull_fails_verification(standin, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    _remote_well(standin)
    run = _run(out)
    _fetch(standin, run, "A01")
    assert "A01" in run.fetched_wells, "the healthy well is fetched — the fixture's happy path"
    (out / "pipeline_info.json").write_text(json.dumps({"schema": "a:b", "note": "another run's sidecar"}))
    standin.fail_rsync("pipeline_info.json")
    verdict = _verify(standin, run)
    assert not verdict.passed
    assert any("pipeline_info.json could not be pulled" in m for m in verdict.missing), verdict.missing


# ── B4: the completeness verdict counts ──────────────────────────────────────

def test_a_partial_well_fails_verification_and_says_so_at_warning_level(standin, tmp_path):
    from services.pipeline_runner import classify_log_line

    inp, out = tmp_path / "in", tmp_path / "out"
    out.mkdir()
    _zip(inp / "A01.zip", {"exp_DAPI_A01_f1_t0.tif": b"1", "exp_DAPI_A01_f2_t0.tif": b"2"})
    _remote_well(standin)                       # its output covers f1 only
    run = _run(out, local_input_dir=inp)
    _fetch(standin, run, "A01")
    lines: list[str] = []
    verdict = _verify(standin, run, lines)
    assert not verdict.passed
    assert "A01 covers fewer FOV-groups than its input holds (completeness check: partial)" in verdict.missing
    verdicts = [line for line in lines if "INCOMPLETE" in line]
    assert verdicts and {classify_log_line(line) for line in verdicts} == {"WARNING"}, verdicts
    # A Suspend's wind-down is partial on purpose: the record says so, and the run is not failed for it.
    assert _verify(standin, run, suspended=True).passed


# ── B5: no "downloaded" line for a well the retry could not complete ─────────

def test_the_retry_prints_no_downloaded_lines_for_a_well_it_did_not_complete(standin, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    _local_old_well(out)
    _remote_well(standin)
    run = _run(out)
    standin.fail_rsync("A01_out.zip")
    _fetch(standin, run, "A01")
    lines: list[str] = []
    _verify(standin, run, lines)
    assert not [line for line in lines if "(end-of-run retry)" in line], lines


# ── C5 / C7: every pull is bounded by rsync's idle timeout and by Stop ───────

def test_every_fetch_pull_carries_an_idle_timeout_and_no_wall_clock(monkeypatch, tmp_path, standin):
    calls: list[tuple] = []

    def _record(argv, **kw):
        import subprocess
        calls.append((list(argv), kw.get("timeout"), kw.get("stop_event")))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(remote_fetch, "_rsync_with_retry", _record)
    run = _run(tmp_path)
    import threading
    stop = threading.Event()
    verifier = FetchedOutputVerifier(standin.host(), run, None, stop)
    verifier._rsync_optional("standin:/x/A01_out.zip", tmp_path / "A01_out.zip", log_fn=lambda _m: None)
    fetcher = Fetcher.__new__(Fetcher)
    fetcher.host, fetcher._log, fetcher._stop = standin.host(), (lambda _m: None), threading.Event()
    fetcher._pull_listed("standin", ["/x/m_A01.csv.gz"], tmp_path)
    assert len(calls) == 2
    for argv, timeout, stop_event in calls:
        assert "--timeout=120" in argv, argv
        assert timeout is None, "a wall clock kills a large re-anchored archive mid-transfer (C5)"
        assert stop_event is not None, "Stop cannot interrupt the pull"
