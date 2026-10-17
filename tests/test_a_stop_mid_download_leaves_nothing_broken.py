"""A Stop part-way through a download leaves a truncated zip; the next attempt replaces it, never trusts it.

The question asked of this: stop a restarted run while it is downloading, pull the update, resume — does the plate
complete? The end-of-run retry pulls straight into the output folder with rsync's ``--partial``, so a Stop
part-way through a 4 GB ``<well>_out.zip`` leaves the partial file under its real name. Three places then went
wrong with it:

* the retry skipped any zip that was present and non-empty, pulled only the CSV, and counted the well complete
  around a truncated archive;
* the Fetcher's incremental merge treated it as an existing output to merge into;
* ``merge_images_into_zip`` read it again inside its write and raised, so the well was never placed.
"""

from __future__ import annotations

import zipfile
from unittest.mock import patch

from pipeline.io import merge_images_into_zip
from services import remote_fetch
from services.remote_config import RemoteHost
from services.remote_fetch import Fetcher
from tests.test_remote_fetch_freshness import _retry_pulls, _run


def _truncated(path, *, members=3):
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(members):
            zf.writestr(f"img{i}.tif", b"x" * 50_000)
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])          # what rsync --partial leaves after a Stop
    assert not zipfile.is_zipfile(path)


def test_the_retry_pulls_again_a_zip_a_stop_left_half_downloaded(monkeypatch, tmp_path):
    _truncated(tmp_path / "C08_out.zip")
    (tmp_path / "measurements_C08.csv").write_text("well,cell\nC08,1\n")
    run = _run(tmp_path, pre_run_artifacts=remote_fetch._snapshot_local_artifacts(tmp_path))
    assert _retry_pulls(monkeypatch, tmp_path, run) == ["test-host:/r/out/C08_out.zip"]


def test_a_fetched_well_replaces_a_truncated_local_zip(tmp_path):
    host = RemoteHost(name="t", ssh_alias="h", remote_project_dir="~/a", remote_staging_dir="~/.s",
                      remote_python="/p", cpu_only=False, extra_args=(), cache_budget_gb=1,
                      measured_bandwidth_mbps=1.0)
    _truncated(tmp_path / "C08_out.zip")
    run = _run(tmp_path, incremental=True)
    with patch.object(Fetcher, "_loop", lambda self: None):
        fetcher = Fetcher(host, run, output_filter=run.output_filter, log_fn=lambda _m: None)
    fetched = tmp_path / "fetched.zip"
    with zipfile.ZipFile(fetched, "w") as zf:
        zf.writestr("C08_f1_t0.tif", b"whole")
    downloaded = fetched.read_bytes()
    try:
        assert fetcher._materialize_incremental("C08", fetched, tmp_path) is True
    finally:
        fetcher.cancel()
    assert (tmp_path / "C08_out.zip").read_bytes() == downloaded


def test_merging_into_a_truncated_archive_writes_a_whole_one(tmp_path):
    out_zip = tmp_path / "C08_out.zip"
    _truncated(out_zip)
    images = tmp_path / "imgs"
    images.mkdir()
    (images / "C08_f2_t0_gfp.tif").write_bytes(b"\x49\x49\x2a\x00" + b"\0" * 64)

    assert merge_images_into_zip(out_zip, images) == 1
    with zipfile.ZipFile(out_zip) as zf:
        assert zf.namelist() == ["C08_f2_t0_gfp.tif"]


def _gz_rows(path, rows):
    import gzip
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        fh.write("well,fov,timepoint,nucleus_id\r\n" + "".join(f"C08,{f},t0,{n}\r\n" for f, n in rows))


def _cut(path):
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])


def test_a_truncated_local_csv_is_set_aside_and_replaced_by_the_download(tmp_path):
    """Not merged into (it raises), and not left in place while the well is marked fetched and its remote copy
    deleted — which would leave the cut-off file as the well's only CSV."""
    from pipeline.io import merge_fetched_csvs
    from util.csv_gz import csv_is_whole

    out, transit = tmp_path / "out", tmp_path / "transit"
    out.mkdir(), transit.mkdir()
    rows = [(f"f{i}", n) for i in range(200) for n in range(20)]
    _gz_rows(out / "measurements_C08.csv.gz", rows)
    _cut(out / "measurements_C08.csv.gz")
    assert not csv_is_whole(out / "measurements_C08.csv.gz")
    _gz_rows(transit / "measurements_C08.csv.gz", rows)
    import gzip

    with gzip.open(transit / "measurements_C08.csv.gz", "rt") as fh:
        whole = sorted(fh.read().splitlines())

    merge_fetched_csvs(transit, out, log_fn=lambda _m: None)

    with gzip.open(out / "measurements_C08.csv.gz", "rt") as fh:
        assert sorted(fh.read().splitlines()) == whole, "the well's CSV is not the downloaded rows, exactly"
    assert not (out / "errors").exists(), "the download restored the cut group, so nothing was lost to report"
    assert (out / ".measurements_C08.csv.gz.truncated").exists(), "the cut-off copy is kept, never deleted"


def test_the_retry_pulls_again_a_csv_a_stop_left_half_downloaded(monkeypatch, tmp_path):
    with zipfile.ZipFile(tmp_path / "C08_out.zip", "w") as zf:
        zf.writestr("img.tif", b"x")
    _gz_rows(tmp_path / "measurements_C08.csv.gz", [(f"f{i}", 1) for i in range(2000)])
    _cut(tmp_path / "measurements_C08.csv.gz")
    listed = []
    monkeypatch.setattr(remote_fetch, "list_remote_csvs",
                        lambda *a, **k: listed.append(a) or ["/r/out/measurements_C08.csv.gz"])
    run = _run(tmp_path, pre_run_artifacts=remote_fetch._snapshot_local_artifacts(tmp_path))
    pulled = _retry_pulls(monkeypatch, tmp_path, run)
    assert listed and "test-host:/r/out/measurements_C08.csv.gz" in pulled, pulled


def test_the_retry_never_leaves_a_partial_under_the_real_name(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(remote_fetch, "_rsync_with_retry", lambda argv, **kw: seen.append(argv))
    monkeypatch.setattr(remote_fetch, "pull_small_file", lambda *a, **k: False)
    monkeypatch.setattr(remote_fetch, "list_remote_csvs", lambda *a, **k: ["/r/out/measurements_C08.csv.gz"])
    run = _run(tmp_path)
    host = RemoteHost(name="t", ssh_alias="test-host", remote_project_dir="~/a", remote_staging_dir="~/.s",
                      remote_python="/p", cpu_only=False, extra_args=(), cache_budget_gb=1,
                      measured_bandwidth_mbps=1.0)
    remote_fetch.FetchedOutputVerifier(host, run, None, __import__("threading").Event())._retry_missing_wells(
        ["C08_out.zip missing"], log=lambda _m: None)
    assert seen and all("--partial" not in a and any(x.startswith("--partial-dir=") for x in a) for a in seen)


def test_an_incremental_download_keeps_the_readable_rows_of_a_truncated_local_csv(tmp_path):
    """The incremental case the set-aside rule did not consider: the download holds only the *new* field groups,
    so replacing the cut-off local CSV with it threw away every earlier group's rows — readable rows — and the
    well's done-set (which also reads its out.zip) never brought them back. The readable whole groups are kept, the
    group the cut fell in is dropped rather than kept short, and the loss is written where the run's own failure
    records go, not only to the log."""
    import gzip

    from pipeline.completeness import error_file_type
    from pipeline.io import merge_fetched_csvs
    from pipeline.incremental_keys import read_done_fov_keys

    out, transit = tmp_path / "out", tmp_path / "transit"
    out.mkdir(), transit.mkdir()
    _gz_rows(out / "measurements_C08.csv.gz", [(f"f{i}", n) for i in (1, 2, 3) for n in range(2000)])
    data = (out / "measurements_C08.csv.gz").read_bytes()
    (out / "measurements_C08.csv.gz").write_bytes(data[: len(data) - 40])      # cut inside f3's rows
    _gz_rows(transit / "measurements_C08.csv.gz", [("f4", 1)])
    log: list[str] = []

    merge_fetched_csvs(transit, out, log_fn=log.append)

    with gzip.open(out / "measurements_C08.csv.gz", "rt") as fh:
        rows = fh.read().splitlines()[1:]
    by_fov = {f: sum(1 for r in rows if r.split(",")[1] == f) for f in ("f1", "f2", "f3", "f4")}
    assert by_fov == {"f1": 2000, "f2": 2000, "f3": 0, "f4": 1}, by_fov
    assert read_done_fov_keys(out / "measurements_C08.csv.gz") == {("f1", "t0"), ("f2", "t0"), ("f4", "t0")}
    assert (out / ".measurements_C08.csv.gz.truncated").exists(), "the cut-off copy is kept, never deleted"
    (note,) = (out / "errors").glob("C08_*.txt")
    assert error_file_type(note) == "EOFError"
    assert "f3" in note.read_text() and "--force" in note.read_text()
    assert not any("no local CSV to merge with" in line for line in log), log
