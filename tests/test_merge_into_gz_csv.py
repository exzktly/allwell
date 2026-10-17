"""A downloaded partial merges straight into the well's ``.csv.gz`` — fast, never plain on disk.

The report: the first well of an incremental remote run sat at 99% for thirty minutes, every later download queued
behind it and its remote copy held, while a *plain* ``__aw_tmp_measurements_C02.csv.pid…`` sat in the output folder.
The merge parsed every existing row into a dict, wrote the whole file out plain, then gzipped it again.
``merge_records_into_gz_csv`` scans the existing file for its keys only and, when no column is new, copies the
archive byte for byte and appends the new rows as one more gzip member.
"""

from __future__ import annotations

import csv
import gzip
import io
import time

import pandas as pd

from pipeline.io import merge_fetched_csvs, merge_records_into_csv, merge_records_into_gz_csv

HEADER = ["well", "fov", "timepoint", "nucleus_id", "area"]


def _gz_csv(path, rows, header=HEADER):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=header)
    writer.writeheader()
    writer.writerows(rows)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        fh.write(buf.getvalue())


def _row(fov, n, area="1", **extra):
    return {"well": "C02", "fov": fov, "timepoint": "t0", "nucleus_id": str(n), "area": area, **extra}


def _lines(path):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        return fh.read().splitlines()


def test_new_rows_are_appended_to_the_archive_without_rewriting_it(tmp_path):
    target = tmp_path / "measurements_C02.csv.gz"
    _gz_csv(target, [_row("f1", 0), _row("f1", 1)])
    original = target.read_bytes()

    added = merge_records_into_gz_csv(tmp_path / "measurements_C02.csv", [_row("f1", 1, "9"), _row("f2", 0, "5")])

    assert added == 1
    assert target.read_bytes().startswith(original), "the existing archive was rewritten, not appended to"
    assert _lines(target) == ["well,fov,timepoint,nucleus_id,area", "C02,f1,t0,0,1", "C02,f1,t0,1,1",
                              "C02,f2,t0,0,5"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["measurements_C02.csv.gz"], (
        "a plain CSV or a temp file was left in the output folder")
    frame = pd.read_csv(target)                       # every reader here must see both gzip members
    assert len(frame) == 3 and list(frame["fov"]) == ["f1", "f1", "f2"]


def test_a_new_column_rewrites_into_gzip_with_the_same_rows_as_the_plain_merge(tmp_path):
    rows = [_row("f1", 0), _row("f1", 1)]
    new = [_row("f2", 0, "7", gfp="3.5"), _row("f1", 0, "8")]
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    _gz_csv(a / "measurements_C02.csv.gz", rows)
    _gz_csv(b / "measurements_C02.csv.gz", rows)

    assert merge_records_into_gz_csv(a / "measurements_C02.csv", new) == 1
    assert merge_records_into_csv(b / "measurements_C02.csv", new) == 1

    assert _lines(a / "measurements_C02.csv.gz") == (b / "measurements_C02.csv").read_text().splitlines()
    assert not (a / "measurements_C02.csv").exists()


def test_a_plain_existing_csv_ends_up_gzipped(tmp_path):
    plain = tmp_path / "measurements_C02.csv"
    plain.write_text("well,fov,timepoint,nucleus_id,area\r\nC02,f1,t0,0,1\r\n")
    assert merge_records_into_gz_csv(plain, [_row("f2", 0)]) == 1
    assert not plain.exists()
    assert _lines(tmp_path / "measurements_C02.csv.gz")[-1] == "C02,f2,t0,0,1"


def test_the_download_merge_leaves_only_the_archive(tmp_path):
    """Through ``merge_fetched_csvs``, the Fetcher's own entry point."""
    target_dir, fetched = tmp_path / "out", tmp_path / "transit"
    target_dir.mkdir(), fetched.mkdir()
    _gz_csv(target_dir / "measurements_C02.csv.gz", [_row("f1", 0)])
    _gz_csv(fetched / "measurements_C02.csv.gz", [_row("f2", 0)])

    assert merge_fetched_csvs(fetched, target_dir) == 1
    assert [p.name for p in target_dir.iterdir()] == ["measurements_C02.csv.gz"]
    assert len(_lines(target_dir / "measurements_C02.csv.gz")) == 3


def test_a_large_well_merges_far_faster_than_the_whole_file_rewrite(tmp_path):
    """200k rows. The old path parsed and rewrote every row; this one reads four columns and copies bytes."""
    header = HEADER + [f"m{i}" for i in range(12)]
    rows = [dict(zip(header, ["C02", f"f{i % 40}", "t0", str(i), "1"] + [f"{i * 1.0001:.6f}"] * 12))
            for i in range(200_000)]
    for name in ("fast", "slow"):
        (tmp_path / name).mkdir()
        _gz_csv(tmp_path / name / "measurements_C02.csv.gz", rows, header)
    new = [dict(zip(header, ["C02", "f999", "t1", "0", "1"] + ["2"] * 12))]

    t0 = time.perf_counter()
    merge_records_into_gz_csv(tmp_path / "fast" / "measurements_C02.csv", new)
    fast = time.perf_counter() - t0
    t0 = time.perf_counter()
    merge_records_into_csv(tmp_path / "slow" / "measurements_C02.csv", new)
    slow = time.perf_counter() - t0

    assert fast < slow / 2, f"the merge took {fast:.2f}s against the old {slow:.2f}s"


def test_a_downloaded_csv_with_nothing_local_to_merge_with_is_placed_as_it_arrived(tmp_path, monkeypatch):
    """A well new to this run: no parse, no rewrite — the downloaded archive is the well's CSV."""
    import pipeline.io as pio

    target_dir, fetched = tmp_path / "out", tmp_path / "transit"
    target_dir.mkdir(), fetched.mkdir()
    _gz_csv(fetched / "measurements_C03.csv.gz", [_row("f1", 0), _row("f2", 0)])
    downloaded = (fetched / "measurements_C03.csv.gz").read_bytes()

    def no_merge(*a, **k):
        raise AssertionError("a CSV with no local counterpart was merged")

    monkeypatch.setattr(pio, "merge_records_into_gz_csv", no_merge)
    assert merge_fetched_csvs(fetched, target_dir) == 2
    assert (target_dir / "measurements_C03.csv.gz").read_bytes() == downloaded
    assert list(fetched.iterdir()) == []


def test_a_downloaded_zip_with_nothing_local_to_merge_with_is_placed_as_it_arrived(tmp_path, monkeypatch):
    import zipfile
    from unittest.mock import patch

    import pipeline.io as pio
    from services.remote_config import RemoteHost
    from services.remote_fetch import Fetcher
    from services.remote_run_state import _RemoteRun
    from services.remote_runner import LocalInputMode, OutputFilter

    host = RemoteHost(name="t", ssh_alias="h", remote_project_dir="~/a", remote_staging_dir="~/.s",
                      remote_python="/p", cpu_only=False, extra_args=(), cache_budget_gb=1,
                      measured_bandwidth_mbps=1.0)
    out = tmp_path / "out"
    out.mkdir()
    run = _RemoteRun(run_id="r", cache_key="k", remote_input_dir="/r/in", remote_output_dir="/r/out",
                     remote_pidfile="/r/pid", local_output_dir=out, local_input_mode=LocalInputMode.ZIP,
                     incremental=True)
    with patch.object(Fetcher, "_loop", lambda self: None):
        fetcher = Fetcher(host, run, output_filter=OutputFilter.FULL, log_fn=lambda _m: None)
    fetched = tmp_path / "fetched.zip"
    with zipfile.ZipFile(fetched, "w") as zf:
        zf.writestr("C03_f1_t0_gfp.tif", b"pixels")
    downloaded = fetched.read_bytes()
    monkeypatch.setattr(pio, "merge_images_into_zip",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("merged with nothing")))

    assert fetcher._materialize_incremental("C03", fetched, out) is True
    assert (out / "C03_out.zip").read_bytes() == downloaded
    fetcher.cancel()
