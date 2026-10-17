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


def _run_one_finished_on_the_server(plate, host, remote, stop_fetchers, *,
                                    batch_size: int = 0, opts=None):
    """Run one: spawned, computed every well on the host, suspended before a
    single well was downloaded. Returns the remote directory it wrote to.

    ``batch_size`` > 0 spawns the batched driver instead; its stream ends at
    once, which is a run that lost its driver — what matters here is only the
    address it journalled and the output directory it owns.
    """
    from services.pipeline_service import build_pipeline_args
    from services.remote_runner import OutputFilter, stage_local_zips

    inp, out = plate
    opts = opts or _opts()
    r = _pipeline_runner(host, stop_fetchers)
    args = build_pipeline_args(Path("process_microscopy.py"), inp, out, opts)
    r._open_journal(inp, out, args, opts)
    zipped = stage_local_zips(inp, filename_schema=SCHEMA, filename_sep="_",
                              prior_output_dir=out)
    proc = r._remote_runner.spawn(zipped, out, args, output_filter=OutputFilter.FULL,
                                  batch_size=batch_size)
    if batch_size:
        proc.wait(timeout=30)
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
        marker = seeded / ".aw_settings" / f"{well}.json"
        marker.parent.mkdir(exist_ok=True)
        marker.write_text(f'{{"well": "{well}", "digest": "d-{well}"}}')
        seal = seeded / ".aw_complete" / f"{well}.json"      # each covered its whole input
        seal.parent.mkdir(exist_ok=True)
        seal.write_text(f'{{"well": "{well}"}}')
    r._suspend_requested.set()
    r._close_journal()
    assert not list(out.glob("*_out.zip")) + list(out.glob("measurements_*")), (
        "the fixture is meant to leave nothing downloaded")
    return remote_out


def _resume(plate, host, stop_fetchers, *, incremental=True, **opts_extra):
    """Run two: the recorded spec, as Resume starts it (incremental forced on)
    or as a plain Run with incremental unticked."""
    from services.pipeline_service import build_pipeline_args

    inp, out = plate
    opts = _opts(**({"incremental": True} if incremental else {}), **opts_extra)
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


@pytest.mark.parametrize("incremental", [True, False],
                         ids=["resume-incremental", "run-incremental-unticked"])
@pytest.mark.parametrize("batch_size", [0, 2], ids=["single-shot", "batched"])
def test_resume_downloads_the_finished_wells_and_uploads_nothing(
        plate, host, remote, stop_fetchers, incremental, batch_size):
    _run_one_finished_on_the_server(plate, host, remote, stop_fetchers,
                                    batch_size=batch_size)
    launches = len(remote.launched)
    uploads_before = list(remote.uploads)

    _r, events = _resume(plate, host, stop_fetchers, incremental=incremental,
                         remote_batch_size=batch_size)
    text = _text(events)

    assert remote.uploads == uploads_before, (
        f"the resume uploaded wells the server had already finished: "
        f"{remote.uploads[len(uploads_before):]}\n{text}")
    assert len(remote.launched) == launches, "the pipeline was launched again"
    _inp, out = plate
    for well in WELLS:
        assert (out / f"{well}_out.zip").is_file(), text
        # The record of which settings produced it comes home too, or a later
        # run with different settings would reuse these numbers unchecked.
        assert (out / ".aw_settings" / f"{well}.json").is_file(), text
        body = gzip.decompress((out / f"measurements_{well}.csv.gz").read_bytes())
        rows = list(csv.DictReader(body.decode().splitlines()))
        assert [(r["fov"], r["timepoint"]) for r in rows] == [("1", "T01"), ("2", "T01")]
    kinds = [e[0] for e in events]
    assert "error" not in kinds, text
    if batch_size:
        # A batched run downloads them as part of the run (remote_batch_resume), alongside any batches it still
        # has to compute, rather than before staging; with nothing to compute it ends as a completed run.
        assert "downloading the 2 finished there, nothing to run" in text, text
        assert ("done", "Remote pipeline completed successfully.\n") in events, text
    else:
        assert ("done", "Nothing to upload: every well is already complete "
                        "locally.\n") in events, text


@pytest.mark.parametrize("incremental", [True, False],
                         ids=["resume-incremental", "run-incremental-unticked"])
def test_resume_uploads_only_the_well_the_server_had_not_finished(
        plate, host, remote, stop_fetchers, incremental):
    """The recovery narrows the upload; it does not replace it."""
    remote_out = _run_one_finished_on_the_server(plate, host, remote, stop_fetchers)
    for name in ("B02_out.zip", "measurements_B02.csv"):
        (remote.path(remote_out) / name).unlink()
    uploads_before = list(remote.uploads)

    _r, events = _resume(plate, host, stop_fetchers, incremental=incremental)

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


def test_reloaded_settings_that_differ_only_in_execution_knobs_still_find_it(
        plate, host, remote, stop_fetchers):
    """"Load previous run" does not restore ``allow_low_disk``; worker counts
    and the run check are machine choices. None of them changes a byte of the
    output, so none of them may hide the finished wells."""
    _run_one_finished_on_the_server(
        plate, host, remote, stop_fetchers,
        opts=_opts(allow_low_disk=True, workers=8))
    uploads_before = list(remote.uploads)

    _r, events = _resume(plate, host, stop_fetchers, incremental=False,
                         workers=2, run_check=False)

    assert remote.uploads == uploads_before, _text(events)
    assert (plate[1] / "B02_out.zip").is_file(), _text(events)


def test_a_plate_recorded_under_the_old_digest_is_still_found(
        plate, host, remote, stop_fetchers):
    """A plate left finished on the server by the previous build carries a
    digest that hashed ``--incremental``; it is exactly the one stuck today."""
    from services.remote_run_id import legacy_argv_digests

    _run_one_finished_on_the_server(plate, host, remote, stop_fetchers)
    _inp, out = plate
    record = J.read_journal(out).remote
    from services.pipeline_service import build_pipeline_args
    from services.remote_runner import OutputFilter, RemoteRunner
    old_args = build_pipeline_args(Path("process_microscopy.py"), plate[0], out,
                                   _opts(incremental=True))
    old = legacy_argv_digests(
        old_args, extras=RemoteRunner(host)._host_argv_extras(OutputFilter.FULL))[0]
    assert old != record["argv_digest"]
    J.record_remote(out, **{**{k: record[k] for k in (
        "run_id", "cache_key", "host_alias", "remote_input_dir",
        "remote_output_dir", "source_key")}, "argv_digest": old})
    uploads_before = list(remote.uploads)

    _r, events = _resume(plate, host, stop_fetchers)

    assert remote.uploads == uploads_before, _text(events)
    assert (plate[1] / "A01_out.zip").is_file(), _text(events)


def test_changed_settings_never_download_the_old_output(
        plate, host, remote, stop_fetchers):
    """The other half: a setting that changes the numbers must not match."""
    _run_one_finished_on_the_server(plate, host, remote, stop_fetchers)
    uploads_before = list(remote.uploads)

    _r, events = _resume(plate, host, stop_fetchers, min_nucleus_area_px=200)

    assert "never downloaded" not in _text(events), _text(events)
    assert not (plate[1] / "A01_out.zip").exists()
    assert remote.uploads[len(uploads_before):] == [list(WELLS)]


def test_a_plain_rerun_of_a_plate_the_server_holds_whole_uploads_nothing(
        plate, host, remote, stop_fetchers):
    """Nothing missing locally, everything on the host: the pre-flight finds a
    full cache hit and the pipeline is replaced by ``true``. The upload used to
    run anyway, so a plate whose staged archives keyed differently from the
    last run's was sent in full for a run that computes nothing."""
    _run_one_finished_on_the_server(plate, host, remote, stop_fetchers)
    _resume(plate, host, stop_fetchers)          # downloads both wells
    uploads_before = list(remote.uploads)

    _r, events = _resume(plate, host, stop_fetchers, incremental=False)

    assert "FULL CACHE HIT" in _text(events), _text(events)
    assert remote.uploads == uploads_before, _text(events)


# ── a later run overwrote the address (the second report) ───────────────────

def _a_stopped_run_overwrites_the_address(plate, host, remote, *, keep_history):
    """What pressing Resume on the old build and stopping it during the upload
    left behind: its own, empty directory recorded as *the* address. With
    ``keep_history=False`` the journal is shaped as that build wrote it — the
    original address gone, not kept."""
    _inp, out = plate
    current = J.read_journal(out).remote
    empty = current["remote_output_dir"].replace("/run-", "/run-stopped-")
    remote.path(empty).mkdir(parents=True)
    J.record_remote(out, **{**{k: current[k] for k in (
        "run_id", "cache_key", "host_alias", "remote_input_dir", "argv_digest",
        "source_key")}, "remote_output_dir": empty})
    if not keep_history:
        import json
        path = out / ".aw_journal" / "run.json"
        payload = json.loads(path.read_text())
        payload.pop("remote_history", None)
        path.write_text(json.dumps(payload))


@pytest.mark.parametrize("incremental", [True, False],
                         ids=["start-made-incremental", "start-plain"])
@pytest.mark.parametrize("keep_history", [True, False],
                         ids=["journal-keeps-history", "address-already-lost"])
def test_a_stopped_later_run_does_not_hide_the_finished_wells(
        plate, host, remote, stop_fetchers, keep_history, incremental):
    """Loaded the old pipeline_info and pressed Start, after a Resume on the old
    build had been stopped mid-upload: the batched run's finished wells were not
    downloaded and the plate was uploaded again. Start is made incremental by
    ``resume_aware_spec`` whenever an unfinished run is on record, which is the
    case the user hit — and an incremental run never re-attaches, so only the
    recovery can find the wells."""
    _run_one_finished_on_the_server(plate, host, remote, stop_fetchers,
                                    batch_size=2)
    _a_stopped_run_overwrites_the_address(plate, host, remote,
                                          keep_history=keep_history)
    uploads_before = list(remote.uploads)

    _r, events = _resume(plate, host, stop_fetchers, incremental=incremental)

    text = _text(events)
    assert remote.uploads == uploads_before, text
    for well in WELLS:
        assert (plate[1] / f"{well}_out.zip").is_file(), text


def test_the_blank_alone_is_not_uploaded_once_every_well_is_here(
        plate, host, remote, stop_fetchers):
    """The empty-well blank is staged whole on every run. With every sample
    well already here and the blank's own output too, it was the only thing in
    the upload, and a node was asked for to recompute it."""
    opts = _opts(flatfield="empty_well", background_well="B02")
    _run_one_finished_on_the_server(plate, host, remote, stop_fetchers, opts=opts)
    launches = len(remote.launched)
    uploads_before = list(remote.uploads)

    _r, events = _resume(plate, host, stop_fetchers, flatfield="empty_well",
                         background_well="B02")

    text = _text(events)
    assert remote.uploads == uploads_before, text
    assert len(remote.launched) == launches, text
    assert ("done", "Nothing to upload: every well is already complete "
                    "locally.\n") in events, text


def test_the_journal_keeps_an_address_the_name_cannot_be_recomputed_from(
        plate, host, remote, stop_fetchers):
    """A run whose upload was *rewritten* (a channel filter that dropped enough
    bytes) addresses a directory keyed on a fresh copy, which nothing can
    recompute. Only the kept history finds it once a later run has recorded
    its own address."""
    import shutil

    remote_out = _run_one_finished_on_the_server(plate, host, remote, stop_fetchers)
    _inp, out = plate
    rewritten = remote_out.replace("/runs/run-", "/runs/run-rewritten-")
    shutil.move(str(remote.path(str(Path(remote_out).parent))),
                str(remote.path(str(Path(rewritten).parent))))
    current = J.read_journal(out).remote
    J.record_remote(out, **{**{k: current[k] for k in (
        "run_id", "cache_key", "host_alias", "remote_input_dir", "argv_digest",
        "source_key")}, "remote_output_dir": rewritten})
    _a_stopped_run_overwrites_the_address(plate, host, remote, keep_history=True)
    uploads_before = list(remote.uploads)

    _r, events = _resume(plate, host, stop_fetchers)

    assert remote.uploads == uploads_before, _text(events)
    assert (out / "A01_out.zip").is_file(), _text(events)
