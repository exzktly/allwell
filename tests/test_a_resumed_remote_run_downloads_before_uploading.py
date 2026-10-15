"""Resuming a remote run downloads what the server finished; it never re-uploads it.

The report: every well had finished processing on the remote and only needed
downloading. Resume was pressed, and the run began uploading the wells again.

Two defects, each enough on its own:

* A resumed run is incremental (``analyze.suspend`` forces the flag), and
  incremental staging decides what to upload from the **local** folder alone.
  A well finished on the server and never downloaded has nothing local, so it
  was uploaded whole and recomputed. An incremental run also never re-attaches
  to an earlier run's remote directory, so nothing ever looked there.
* The address a run records for the next one is keyed on
  ``pipeline_argv_digest``, which hashed ``--incremental`` and
  ``--incremental_prior_dir`` — flags the remote never even sees. So the resume
  was a "differently configured" run than the one it resumed, and the recorded
  address could not match it.

These drive the app's own sequence — ``PipelineRunner`` opens the journal, run
one spawns and is suspended before its download, run two is the resume through
the real ``_run_pipeline_remote`` and the real staging — against a host that
runs its commands for real (``tests/_fake_remote.py``).
"""

from __future__ import annotations

import csv
import gzip
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services import run_journal as J
from tests._fake_remote import FakeRemote

SCHEMA = "experiment:channel:well:fov:timepoint"
WELLS = ("A01", "B02")


@pytest.fixture
def host():
    from services.remote_config import RemoteHost
    return RemoteHost(
        name="test", ssh_alias="test-host", remote_project_dir="~/allwell",
        remote_staging_dir="~/.allwell-staging", remote_python="/opt/python",
        cpu_only=False, extra_args=(), cache_budget_gb=10,
        measured_bandwidth_mbps=100.0,
    )


@pytest.fixture
def plate(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "index.json")
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    out.mkdir()
    for well in WELLS:
        with zipfile.ZipFile(inp / f"{well}.zip", "w") as zf:
            for fov in (1, 2):
                for ch in ("DAPI", "GFP"):
                    zf.writestr(f"e_{ch}_{well}_{fov}_T01.tif", b"x" * 64)
    return inp, out


@pytest.fixture
def remote(tmp_path, host, monkeypatch):
    from services.remote_runner import RemoteRunner
    from services.remote_upload import DatasetCache

    fake = FakeRemote(tmp_path / "remote-host")

    def _launched(_argv):
        proc = MagicMock()
        proc.stdout = iter([])
        proc.wait.return_value = 0
        proc.returncode = 0
        proc.pid = 4242
        return proc

    fake.install(monkeypatch, host, on_launch=_launched)
    fake.uploads = []

    def _upload(self, zipped, **_k):
        fake.uploads.append(sorted(Path(w).stem for w in zipped.well_zips))
        return self.cache_key(zipped), 0, 0.01

    monkeypatch.setattr(DatasetCache, "ensure_uploaded", _upload)
    monkeypatch.setattr(RemoteRunner, "sync_required_files", lambda self, **k: None)
    return fake


def _opts(**extra):
    return {"filename_schema": SCHEMA, "filename_sep": "_",
            "fluor_tokens": ["GFP"], "nuclear_token": "DAPI",
            "csv_prefix": "measurements", **extra}


def _pipeline_runner(host, stop_fetchers):
    import services.pipeline_runner as pr
    from services.remote_runner import RemoteRunner

    r = pr.PipelineRunner()
    r._remote = host
    r._remote_runner = RemoteRunner(host)
    stop_fetchers(r._remote_runner)
    r._journal_spec = {"opts": {}}
    return r


def _run_one_finished_on_the_server(plate, host, remote, stop_fetchers):
    """Run one: spawned, computed every well on the host, suspended before a
    single well was downloaded. Returns the remote directory it wrote to."""
    from services.pipeline_service import build_pipeline_args
    from services.remote_runner import OutputFilter, stage_local_zips

    inp, out = plate
    r = _pipeline_runner(host, stop_fetchers)
    args = build_pipeline_args(Path("process_microscopy.py"), inp, out, _opts())
    r._open_journal(inp, out, args, _opts())
    zipped = stage_local_zips(inp, filename_schema=SCHEMA, filename_sep="_",
                              prior_output_dir=out)
    r._remote_runner.spawn(zipped, out, args, output_filter=OutputFilter.FULL)
    remote_out = r._remote_runner._run.remote_output_dir
    seeded = remote.path(remote_out)
    seeded.mkdir(parents=True, exist_ok=True)
    for well in WELLS:
        with zipfile.ZipFile(seeded / f"{well}_out.zip", "w") as zf:
            for fov in (1, 2):
                zf.writestr(f"e_DAPI_{well}_{fov}_T01_overlay.png", b"\0" * 64)
        rows = "".join(f"{well},{fov},T01,1\n" for fov in (1, 2))
        (seeded / f"measurements_{well}.csv").write_text(
            f"well,fov,timepoint,nucleus_id\n{rows}")
    r._suspend_requested.set()
    r._close_journal()
    assert not list(out.glob("*_out.zip")) + list(out.glob("measurements_*")), (
        "the fixture is meant to leave nothing downloaded")
    return remote_out


def _resume(plate, host, stop_fetchers, **opts_extra):
    """Run two, as Resume starts it: the recorded spec, incremental forced on."""
    from services.pipeline_service import build_pipeline_args

    inp, out = plate
    opts = _opts(incremental=True, **opts_extra)
    r = _pipeline_runner(host, stop_fetchers)
    args = build_pipeline_args(Path("process_microscopy.py"), inp, out, opts)
    r._open_journal(inp, out, args, opts)
    r._run_pipeline_remote(inp, out, args, opts)
    events = []
    while not r.log_q.empty():
        events.append(r.log_q.get_nowait())
    return r, events


def _text(events):
    return "".join(str(e[1]) for e in events if e[0] in ("line", "done", "error"))


def test_resume_downloads_the_finished_wells_and_uploads_nothing(
        plate, host, remote, stop_fetchers):
    _run_one_finished_on_the_server(plate, host, remote, stop_fetchers)
    launches = len(remote.launched)
    uploads_before = list(remote.uploads)

    _r, events = _resume(plate, host, stop_fetchers)
    text = _text(events)

    assert remote.uploads == uploads_before, (
        f"the resume uploaded wells the server had already finished: "
        f"{remote.uploads[len(uploads_before):]}\n{text}")
    assert len(remote.launched) == launches, "the pipeline was launched again"
    _inp, out = plate
    for well in WELLS:
        assert (out / f"{well}_out.zip").is_file(), text
        body = gzip.decompress((out / f"measurements_{well}.csv.gz").read_bytes())
        rows = list(csv.DictReader(body.decode().splitlines()))
        assert [(r["fov"], r["timepoint"]) for r in rows] == [("1", "T01"), ("2", "T01")]
    kinds = [e[0] for e in events]
    assert "error" not in kinds, text
    assert ("done", "Nothing to upload: every well is already complete "
                    "locally.\n") in events, text


def test_resume_uploads_only_the_well_the_server_had_not_finished(
        plate, host, remote, stop_fetchers):
    """The recovery narrows the upload; it does not replace it."""
    remote_out = _run_one_finished_on_the_server(plate, host, remote, stop_fetchers)
    for name in ("B02_out.zip", "measurements_B02.csv"):
        (remote.path(remote_out) / name).unlink()
    uploads_before = list(remote.uploads)

    _r, events = _resume(plate, host, stop_fetchers)

    assert remote.uploads[len(uploads_before):] == [["B02"]], _text(events)
    assert (plate[1] / "A01_out.zip").is_file()


def test_a_failed_download_ends_the_run_rather_than_re_uploading(
        plate, host, remote, stop_fetchers, monkeypatch):
    from services import remote_fetch

    _run_one_finished_on_the_server(plate, host, remote, stop_fetchers)
    uploads_before = list(remote.uploads)
    launches = len(remote.launched)

    def _pull_fails(argv, **_k):
        return MagicMock(returncode=12, stdout="", stderr="connection reset")

    monkeypatch.setattr(remote_fetch, "_rsync_with_retry", _pull_fails)
    _r, events = _resume(plate, host, stop_fetchers)

    assert remote.uploads == uploads_before, "a failed download fell through to an upload"
    assert len(remote.launched) == launches
    errors = [e[1] for e in events if e[0] == "error"]
    assert len(errors) == 1 and "previous run of this plate already finished" \
        in errors[0], _text(events)


def test_a_forced_resume_does_not_download_the_old_output(
        plate, host, remote, stop_fetchers):
    """Force means recompute: the old results are exactly what is not wanted."""
    _run_one_finished_on_the_server(plate, host, remote, stop_fetchers)
    uploads_before = list(remote.uploads)

    _r, events = _resume(plate, host, stop_fetchers, force=True)

    # Asserted on the log and the upload rather than on the local folder: the
    # fake host's pipeline writes nothing, so the forced run's own end-of-run
    # fetch brings back the seeded files from the directory it overwrote.
    text = _text(events)
    assert "[remote] resume:" not in text, text
    assert remote.uploads[len(uploads_before):] == [list(WELLS)], text


def test_the_reuse_digest_ignores_the_client_side_incremental_flags():
    from services.remote_run_id import pipeline_argv_digest

    base = ["py", "pm.py", "--input_dir", "/i", "--output_dir", "/o",
            "--nuclear_token", "DAPI"]
    resumed = base + ["--incremental", "--incremental_prior_dir", "/o"]
    assert pipeline_argv_digest(resumed) == pipeline_argv_digest(base)
    assert pipeline_argv_digest(base + ["--nuclear_token", "H"]) \
        != pipeline_argv_digest(base)
