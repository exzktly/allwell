"""The Parquet cache is keyed on a well CSV's size **and** its modification time.

Either alone has a way to miss a replaced file. A tool that preserves
modification times (``cp -p``, ``rsync -t``, ``shutil.copy2``, an archive
extraction) can put a different CSV in place under the old time; the byte count
is what notices that. A rewrite of the same byte count is noticed by the time.

What neither notices is a replacement with the same byte count **and** the same
time, which only a content hash would, at the cost of the read the cache exists
to avoid; ``parquet_cache.source_fingerprint`` names that blind spot and it is
deliberately not asserted here.
"""

from __future__ import annotations

import os

import pytest

from review.data import parquet_cache
from review.data.data_loading import read_well_frame

_HEADER = "filename,well,fov,timepoint,timepoint_hours,nucleus_id,area_px\n"


def _row(area: int) -> str:
    return f"a.tif,A01,1,0h,0,1,{area}\n"


@pytest.fixture
def cached_well(tmp_path):
    if parquet_cache._pyarrow() is None:
        pytest.skip("no Parquet engine: there is no cache to key")
    path = tmp_path / "results_A01.csv"
    path.write_text(_HEADER + _row(100))
    assert read_well_frame(path)["area_px"].tolist() == [100]
    assert parquet_cache.read_cached(path) is not None      # the premise: it was cached
    return path


def test_a_replacement_under_the_old_modification_time_is_read(cached_well):
    before = os.stat(cached_well)
    cached_well.write_text(_HEADER + _row(1000))             # one byte longer
    os.utime(cached_well, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = os.stat(cached_well)
    assert after.st_mtime_ns == before.st_mtime_ns and after.st_size == before.st_size + 1
    assert read_well_frame(cached_well)["area_px"].tolist() == [1000]


def test_a_rewrite_of_the_same_byte_count_is_read(cached_well):
    before = os.stat(cached_well)
    cached_well.write_text(_HEADER + _row(999))              # the same length as 100
    os.utime(cached_well, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
    assert os.stat(cached_well).st_size == before.st_size
    assert read_well_frame(cached_well)["area_px"].tolist() == [999]
