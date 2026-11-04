"""A dataset whose folder cannot be written keeps its row cache on this machine, and opens fast the second time.

Reported: a 60-well nanowell plate on a USB hard disk took ten minutes to load, stuck on reading wells. A volume the
app cannot write — a read-only share, or a Windows-formatted (NTFS) drive on a Mac, which macOS mounts read-only —
cannot hold the ``.aw_parquet`` folder, so before this every open parsed every CSV again. ``parquet_cache.cache_dir``
now sends those writes to ``row-cache/`` in the app's folder (``util.state_dir.local_row_cache_dir``).

The test session runs as root in CI, where ``chmod`` does not stop a write, so a read-only folder is simulated at the
two places the app learns about one: ``os.access`` saying no, and a write raising ``OSError`` anyway.
"""

from __future__ import annotations

import csv
import errno
import os

import pytest

pytest.importorskip("pyarrow")

from pandas.testing import assert_frame_equal  # noqa: E402

import review.data.data_loading as dl  # noqa: E402
from review.data import parquet_cache as pc  # noqa: E402
from review.data import well_summary as ws  # noqa: E402
from util.state_dir import local_row_cache_dir  # noqa: E402


def _write_plate(path, rows=200):
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["well", "fov", "timepoint", "timepoint_hours", "nucleus_id", "gfp_mean_intensity"])
        for i in range(rows):
            writer.writerow(["A01", str(i % 4 + 1), f"{(i % 3) * 24}h", float((i % 3) * 24), i + 1, 100.0 + i])
    return path


@pytest.fixture
def plate(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "_local_dirs", {})
    folder = tmp_path / "usb drive" / "out"
    folder.mkdir(parents=True)
    return _write_plate(folder / "measurements_A01.csv")


def _read_only(monkeypatch, folder):
    """``os.access`` says no for *folder* and anything under it, as on a read-only mount."""
    real = os.access
    folder = str(folder)

    def access(path, mode, *a, **k):
        if mode & os.W_OK and str(path).startswith(folder):
            return False
        return real(path, mode, *a, **k)

    monkeypatch.setattr(pc.os, "access", access)


def _counting_parses(monkeypatch):
    parses = {"n": 0}
    real = dl.load_well_csv

    def counting(path):
        parses["n"] += 1
        return real(path)

    monkeypatch.setattr(dl, "load_well_csv", counting)
    return parses


def test_a_read_only_dataset_parses_once_and_then_reads_its_local_copy(plate, monkeypatch):
    _read_only(monkeypatch, plate.parent)
    parses = _counting_parses(monkeypatch)
    first = dl.read_well_frame(plate)
    second = dl.read_well_frame(plate)
    assert parses["n"] == 1, f"a read-only dataset parsed its CSV {parses['n']} times in two reads"
    assert_frame_equal(first, second)
    local = local_row_cache_dir(plate.parent)
    assert (local / "measurements_A01.parquet").is_file()
    assert not (plate.parent / pc.CACHE_DIRNAME).exists(), "it wrote into the read-only folder"


def test_a_volume_that_refuses_a_write_its_permissions_allow_falls_back_too(plate, monkeypatch):
    """A read-only mount can report the folder writable and refuse the write: the refusal moves the cache, once."""
    real_write = pc._write_through_staging
    own = plate.parent / pc.CACHE_DIRNAME

    def refuse_on_the_volume(pq, table, path):
        if own in path.parents or path.parent == own:
            raise OSError(errno.EROFS, "Read-only file system", str(path))
        return real_write(pq, table, path)

    monkeypatch.setattr(pc, "_write_through_staging", refuse_on_the_volume)
    parses = _counting_parses(monkeypatch)
    dl.read_well_frame(plate)
    dl.read_well_frame(plate)
    assert parses["n"] == 1
    assert pc.cache_dir(plate.parent) == local_row_cache_dir(plate.parent)


def test_a_read_only_share_that_was_already_cached_is_read_from_its_own_copy(plate, monkeypatch):
    """Someone who could write the share cached it; a reader who cannot is served that copy, not a new local one."""
    dl.read_well_frame(plate)                                   # cached beside the CSV while writable
    assert pc.cache_path(plate).parent == plate.parent / pc.CACHE_DIRNAME
    monkeypatch.setattr(pc, "_local_dirs", {})
    _read_only(monkeypatch, plate.parent)
    parses = _counting_parses(monkeypatch)
    dl.read_well_frame(plate)
    assert parses["n"] == 0
    assert not local_row_cache_dir(plate.parent).exists()


def test_a_rewritten_csv_still_invalidates_the_local_copy(plate, monkeypatch):
    _read_only(monkeypatch, plate.parent)
    dl.read_well_frame(plate)
    _write_plate(plate, rows=50)
    os.utime(plate, ns=(plate.stat().st_atime_ns, plate.stat().st_mtime_ns + 10**9))
    assert len(dl.read_well_frame(plate)) == 50


def test_the_well_summary_follows_the_cache(plate, monkeypatch):
    _read_only(monkeypatch, plate.parent)
    summary = ws.well_summary(plate)
    assert (local_row_cache_dir(plate.parent) / "measurements_A01.summary.json").is_file()
    monkeypatch.setattr(ws, "summarize_frame", lambda *_a, **_k: pytest.fail("the stored summary was not read"))
    assert ws.well_summary(plate) == summary


def test_pruning_reaches_the_local_copy(plate, monkeypatch):
    _read_only(monkeypatch, plate.parent)
    dl.read_well_frame(plate)
    plate.unlink()
    assert pc.prune(plate.parent) == 1
    assert not (local_row_cache_dir(plate.parent) / "measurements_A01.parquet").exists()


def test_the_storage_button_offers_the_local_copy(plate, monkeypatch):
    from core import local_storage as ls

    _read_only(monkeypatch, plate.parent)
    dl.read_well_frame(plate)
    local = local_row_cache_dir(plate.parent)
    areas = {a.key: set(a.paths) for a in ls.cache_areas(plate.parent.parent)}
    assert local in areas["rows"], "the open dataset's row cache leaves out the copy kept on this machine"
    assert local in areas["local-rows"]
