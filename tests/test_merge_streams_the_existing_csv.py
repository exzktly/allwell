"""Merging a fetched partial CSV into a large local one streams the local file instead of loading it.

The report: an incremental remote run sat at 99% for a long time on a well whose local CSV was large, with the
merge's temp file (``__aw_tmp_measurements_C02.csv.pid…``) in the output folder not growing. The merge read every
existing row into a list of dicts before writing any — many times the file's own size in memory for a CSV of
gigabytes. It now reads the existing file twice, once for its keys and once while copying it, so memory is the
keys rather than the rows. The bytes written must not change.
"""

from __future__ import annotations

import csv
import gzip
import tracemalloc

from pipeline.io import merge_records_into_csv


def _write_csv(path, rows, header):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def test_the_merged_file_is_exactly_what_the_whole_file_merge_wrote(tmp_path):
    header = ["well", "fov", "timepoint", "nucleus_id", "area"]
    existing = [{"well": "C02", "fov": "f1", "timepoint": "t0", "nucleus_id": str(i), "area": str(10 + i)}
                for i in range(3)]
    plain = tmp_path / "measurements_C02.csv"
    _write_csv(plain, existing, header)
    with open(plain, "rb") as src, gzip.open(tmp_path / "measurements_C02.csv.gz", "wb") as dst:
        dst.write(src.read())
    plain.unlink()                                   # a finished well's CSV is the gzipped one
    new = [
        {"well": "C02", "fov": "f1", "timepoint": "t0", "nucleus_id": "1", "area": "999"},   # duplicate key: dropped
        {"well": "C02", "fov": "f2", "timepoint": "t0", "nucleus_id": "0", "area": "7", "gfp": "3.5"},  # new column
        {"well": "C02", "fov": "f2", "timepoint": "t0", "nucleus_id": "0", "area": "8", "gfp": "1"},    # dup in new
    ]

    added = merge_records_into_csv(plain, new)

    assert added == 1
    assert plain.read_text(encoding="utf-8").splitlines() == [
        "well,fov,timepoint,nucleus_id,area,gfp",
        "C02,f1,t0,0,10,",
        "C02,f1,t0,1,11,",
        "C02,f1,t0,2,12,",
        "C02,f2,t0,0,7,3.5",
    ]


def test_nothing_new_rewrites_nothing(tmp_path):
    header = ["well", "fov", "timepoint", "nucleus_id"]
    target = tmp_path / "measurements_C02.csv"
    _write_csv(target, [{"well": "C02", "fov": "f1", "timepoint": "t0", "nucleus_id": "0"}], header)
    before = target.stat().st_mtime_ns
    assert merge_records_into_csv(target, [{"well": "C02", "fov": "f1", "timepoint": "t0", "nucleus_id": "0"}]) == 0
    assert target.stat().st_mtime_ns == before


def test_memory_is_the_keys_not_the_rows(tmp_path):
    """60k rows of 12 columns. Held as dicts that is well over 60 MB; streamed it is the key set."""
    header = ["well", "fov", "timepoint", "nucleus_id"] + [f"m{i}" for i in range(8)]
    target = tmp_path / "measurements_C02.csv"
    with open(target, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        for i in range(60_000):
            writer.writerow(["C02", f"f{i % 50}", "t0", str(i)] + [f"{i * 1.000123456789:.12f}"] * 8)
    size_mb = target.stat().st_size / 1e6
    new = [dict(zip(header, ["C02", "f999", "t1", "0"] + ["1"] * 8))]

    tracemalloc.start()
    try:
        merge_records_into_csv(target, new)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak / 1e6 < 3 * size_mb, (
        f"the merge peaked at {peak / 1e6:.0f} MB for a {size_mb:.0f} MB CSV — it is holding the rows")
    with open(target, encoding="utf-8") as fh:
        assert sum(1 for _ in fh) == 60_002
