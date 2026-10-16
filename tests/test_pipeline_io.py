"""Unit tests for pipeline.io.py (output writers extracted from the pipeline in P4)."""

import csv
from collections import namedtuple
from unittest.mock import patch

import numpy as np
import pytest

from pipeline import io as mio
from util.csv_gz import open_csv_text

_Usage = namedtuple("_Usage", "total used free")


def _ample_disk(monkeypatch):
    """Force ``shutil.disk_usage`` to report plenty of free space so a write
    failure is triaged as transient (retry path), not disk-full."""
    monkeypatch.setattr(mio.shutil, "disk_usage",
                        lambda p: _Usage(1 << 40, 0, 1 << 40))


def test_safe_imwrite_tif_roundtrip(tmp_path):
    from tifffile import imread
    img = (np.arange(64, dtype=np.float32).reshape(8, 8))
    out = tmp_path / "img.tif"
    mio._safe_imwrite(out, img)
    assert out.exists()
    np.testing.assert_array_equal(imread(str(out)), img)
    # No scratch temp file left behind.
    assert not list(tmp_path.glob("__aw_tmp_*"))


def test_safe_imwrite_retries_transient_oserror_then_succeeds(tmp_path, monkeypatch):
    """A transient ``OSError: <N> requested and 0 written`` (short write on a NAS
    output volume) must be absorbed by the retry loop, not fail the FOV."""
    from tifffile import imread
    img = np.arange(64, dtype=np.float32).reshape(8, 8)
    out = tmp_path / "img.tif"

    monkeypatch.setattr(mio, "_RETRY_BASE_DELAY_S", 0.0)
    _ample_disk(monkeypatch)
    real_imwrite = mio.imwrite
    attempts = {"n": 0}

    def flaky_imwrite(path, data, *a, **k):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise OSError("15334400 requested and 0 written")
        return real_imwrite(path, data, *a, **k)

    with patch.object(mio, "imwrite", side_effect=flaky_imwrite):
        mio._safe_imwrite(out, img)

    assert attempts["n"] == 2                       # one failure, then success
    np.testing.assert_array_equal(imread(str(out)), img)
    assert not list(tmp_path.glob("__aw_tmp_*"))    # partial tmp cleaned up


def test_safe_imwrite_exhausts_retries_with_honest_diagnostic(tmp_path, monkeypatch):
    """The real-world bug: a ``<N> requested and 0 written`` short write on a
    LOCAL disk that HAS free space. Every attempt is retried (it's usually
    transient); when they're all exhausted the FOV still fails, but with an
    enriched diagnostic — the original message, the free space, and the resume
    hint — not a false "disk full" claim."""
    img = np.zeros((4, 4), dtype=np.float32)
    out = tmp_path / "img.tif"

    monkeypatch.setattr(mio, "_RETRY_BASE_DELAY_S", 0.0)
    _ample_disk(monkeypatch)                          # 1 TiB free — not disk-full
    calls = {"n": 0}

    def always_fails(path, data, *a, **k):
        calls["n"] += 1
        raise OSError("15334400 requested and 0 written")

    with patch.object(mio, "imwrite", side_effect=always_fails):
        with pytest.raises(OSError) as ei:
            mio._safe_imwrite(out, img)

    msg = str(ei.value)
    assert "0 written" in msg                         # original error preserved
    assert "No space left on device" not in msg       # NOT misreported as full
    assert "--incremental" in msg                     # actionable resume hint
    assert calls["n"] == mio._WRITE_MAX_ATTEMPTS       # every attempt tried
    assert not out.exists()
    assert not list(tmp_path.glob("__aw_tmp_*"))


def test_safe_imwrite_reports_disk_full_without_retrying(tmp_path, monkeypatch):
    """When the volume really is out of room (free < image size), retrying is
    futile: the write is attempted once and re-raised as a clear "No space left
    on device" message."""
    img = np.zeros((64, 64), dtype=np.float32)        # 16 KiB
    out = tmp_path / "img.tif"

    monkeypatch.setattr(mio, "_RETRY_BASE_DELAY_S", 0.0)
    # Only 1 KiB free — below the 16 KiB image → genuine disk-full.
    monkeypatch.setattr(mio.shutil, "disk_usage", lambda p: _Usage(1 << 30, 1 << 30, 1024))
    calls = {"n": 0}

    def always_short(path, data, *a, **k):
        calls["n"] += 1
        raise OSError("15334400 requested and 0 written")

    with patch.object(mio, "imwrite", side_effect=always_short):
        with pytest.raises(OSError, match="No space left on device"):
            mio._safe_imwrite(out, img)

    assert calls["n"] == 1                            # no futile retries
    assert not out.exists()
    assert not list(tmp_path.glob("__aw_tmp_*"))


def test_safe_imwrite_does_not_retry_keyboardinterrupt(tmp_path, monkeypatch):
    """Non-OSError exceptions (e.g. Ctrl-C) are never retried — they propagate
    immediately after tmp cleanup."""
    img = np.zeros((4, 4), dtype=np.float32)
    out = tmp_path / "img.tif"
    calls = {"n": 0}

    def interrupt(path, data, *a, **k):
        calls["n"] += 1
        raise KeyboardInterrupt

    with patch.object(mio, "imwrite", side_effect=interrupt):
        with pytest.raises(KeyboardInterrupt):
            mio._safe_imwrite(out, img)

    assert calls["n"] == 1                            # no retry
    assert not list(tmp_path.glob("__aw_tmp_*"))


def test_safe_imwrite_png(tmp_path):
    rgb = np.zeros((4, 4, 3), dtype=np.uint8)
    rgb[1, 1] = (255, 0, 0)
    out = tmp_path / "o.png"
    mio._safe_imwrite(out, rgb)
    assert out.exists() and out.stat().st_size > 0


def test_safe_atomic_text_write_renames_on_success(tmp_path):
    out = tmp_path / "a.txt"
    with mio._safe_atomic_text_write(out) as fh:
        fh.write("hello")
    assert out.read_text() == "hello"
    assert not list(tmp_path.glob("__aw_tmp_*"))


def test_safe_atomic_text_write_discards_on_error(tmp_path):
    out = tmp_path / "a.txt"
    with pytest.raises(ValueError):
        with mio._safe_atomic_text_write(out) as fh:
            fh.write("partial")
            raise ValueError("boom")
    # On error the target is never created and no temp file lingers.
    assert not out.exists()
    assert not list(tmp_path.glob("__aw_tmp_*"))


def test_safe_well_sanitises_separators():
    assert mio._safe_well("A/01") == "A_01"
    assert mio._safe_well("B\\02") == "B_02"
    assert mio._safe_well("C03") == "C03"


def test_write_csvs_per_well_partitions_by_well(tmp_path):
    records = [
        {"well": "A01", "nucleus_id": 1, "area_px": 10},
        {"well": "A01", "nucleus_id": 2, "area_px": 20},
        {"well": "B02", "nucleus_id": 1, "area_px": 30},
    ]
    mio.write_csvs_per_well(records, tmp_path, "exp")
    a = tmp_path / "exp_A01.csv.gz"                 # a finished well's CSV is gzipped
    b = tmp_path / "exp_B02.csv.gz"
    assert a.exists() and b.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == [a.name, b.name]   # no plain twin left
    with open_csv_text(a) as fh:
        rows_a = list(csv.DictReader(fh))
    assert [r["nucleus_id"] for r in rows_a] == ["1", "2"]
    with open_csv_text(b) as fh:
        assert list(csv.DictReader(fh))[0]["area_px"] == "30"


def test_write_csvs_per_well_empty_is_noop(tmp_path):
    mio.write_csvs_per_well([], tmp_path, "exp")
    assert not list(tmp_path.iterdir())


def test_verify_files_complete_filters_missing_and_empty(tmp_path):
    good = tmp_path / "good.tif"
    good.write_bytes(b"\x00\x01\x02")
    empty = tmp_path / "empty.tif"
    empty.write_bytes(b"")
    missing = tmp_path / "missing.tif"
    ok = mio._verify_files_complete([good, empty, missing])
    assert ok == [good]


def test_save_overlay_draws_red_boundaries(tmp_path):
    from tifffile import imread  # noqa: F401  (kept parallel to imread usage style)
    import imageio.v3 as iio
    nir = np.zeros((16, 16), dtype=np.float32)
    nir[4:12, 4:12] = 100.0
    labels = np.zeros((16, 16), dtype=np.int32)
    labels[4:12, 4:12] = 1
    out = tmp_path / "ov.png"
    mio.save_overlay(nir, labels, out)
    assert out.exists()
    rgb = np.asarray(iio.imread(str(out)))
    assert rgb.shape[-1] in (3, 4)
    # At least one pure-red boundary pixel was drawn.
    red = (rgb[..., 0] == 255) & (rgb[..., 1] == 0) & (rgb[..., 2] == 0)
    assert red.any()
