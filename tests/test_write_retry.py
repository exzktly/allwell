"""Tests for the retry shield on the two writes that must land on the output
volume: the per-well CSV and the ``<well>_out.zip``.

Both used to be unshielded, so a single transient filesystem fault failed the
whole well with a bare errno — the ``OSError: [Errno 9] Bad file descriptor``
seen on network-mounted output dirs, where macOS's SMB client invalidates open
handles across a session reconnect. The image writer has always had this
shield (``_safe_imwrite``); these two now share it.
"""

from __future__ import annotations

import errno
import zipfile

import pytest

import pipeline.io as pio
from pipeline.io import _write_single_csv, merge_records_into_csv, retry_write
import os


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    monkeypatch.setattr(pio, "_RETRY_BASE_DELAY_S", 0)


def _ebadf() -> OSError:
    return OSError(errno.EBADF, "Bad file descriptor")


# ── retry_write itself ──────────────────────────────────────────────────────

def test_retries_then_succeeds():
    calls = []

    def _fn():
        calls.append(1)
        if len(calls) < 3:
            raise _ebadf()
        return "ok"

    assert retry_write(_fn, what="x.csv") == "ok"
    assert len(calls) == 3


def test_exhausted_budget_raises_enriched_oserror():
    def _fn():
        raise _ebadf()

    with pytest.raises(OSError) as ei:
        retry_write(_fn, what="A01_out.zip")
    msg = str(ei.value)
    assert "A01_out.zip" in msg
    assert "attempts" in msg
    # The bare errno was the unactionable part; the cause is preserved but the
    # surfaced message names the likely reason.
    assert "network mount" in msg
    assert isinstance(ei.value.__cause__, OSError)
    assert ei.value.__cause__.errno == errno.EBADF


def test_cleanup_runs_between_attempts():
    cleaned = []

    def _fn():
        raise _ebadf()

    with pytest.raises(OSError):
        retry_write(_fn, what="x", cleanup=lambda: cleaned.append(1))
    assert len(cleaned) == pio._WRITE_MAX_ATTEMPTS


def test_non_oserror_is_not_retried():
    calls = []

    def _fn():
        calls.append(1)
        raise ValueError("nope")

    with pytest.raises(ValueError):
        retry_write(_fn, what="x")
    assert len(calls) == 1


# ── the CSV writer ──────────────────────────────────────────────────────────

def test_csv_write_survives_a_transient_fault(monkeypatch, tmp_path):
    out = tmp_path / "measurements_A01.csv"
    real_replace = pio.os.replace
    state = {"n": 0}

    def _flaky_replace(src, dst):
        state["n"] += 1
        if state["n"] == 1:
            raise _ebadf()
        return real_replace(src, dst)

    monkeypatch.setattr(pio.os, "replace", _flaky_replace)
    _write_single_csv([{"well": "A01", "nucleus_id": 1}], out)

    assert out.is_file()
    assert "A01" in out.read_text()
    assert state["n"] == 2  # failed once, retried once
    # The staging file must not be left behind for the zip step to pack.
    assert not list(tmp_path.glob("__aw_tmp_*"))


def test_csv_flush_failure_does_not_truncate_the_target(monkeypatch, tmp_path):
    """A failed flush must abort the commit, not rename a short file into place."""
    out = tmp_path / "measurements_A01.csv"
    out.write_text("previous,contents\n1,2\n")

    class _BadFlush:
        def __init__(self, fh):
            self._fh = fh

        def __getattr__(self, name):
            return getattr(self._fh, name)

        def flush(self):
            raise _ebadf()

    real_open = pio.open if hasattr(pio, "open") else open
    monkeypatch.setattr(
        pio, "open", lambda *a, **k: _BadFlush(real_open(*a, **k)), raising=False
    )

    with pytest.raises(OSError):
        _write_single_csv([{"well": "A01"}], out)

    # Original file untouched, no staging debris.
    assert out.read_text() == "previous,contents\n1,2\n"
    assert not list(tmp_path.glob("__aw_tmp_*"))


def test_merge_records_into_csv_is_retried(monkeypatch, tmp_path):
    out = tmp_path / "measurements_A01.csv"
    real_replace = pio.os.replace
    state = {"n": 0}

    def _flaky_replace(src, dst):
        state["n"] += 1
        if state["n"] == 1:
            raise _ebadf()
        return real_replace(src, dst)

    monkeypatch.setattr(pio.os, "replace", _flaky_replace)
    added = merge_records_into_csv(
        out, [{"well": "A01", "fov": "1", "timepoint": "0", "nucleus_id": 1}]
    )
    assert added == 1
    assert out.is_file()
    assert state["n"] == 2


# ── the output zip ──────────────────────────────────────────────────────────

def test_out_zip_write_survives_a_transient_fault(monkeypatch, tmp_path):

    img_dir = tmp_path / "stage"
    img_dir.mkdir()
    (img_dir / "a_labels.tif").write_bytes(b"not-really-a-tif-but-non-empty")

    out_zip = tmp_path / "A01_out.zip"
    real_replace = os.replace
    state = {"n": 0}

    def _flaky_replace(src, dst):
        state["n"] += 1
        if state["n"] == 1:
            raise _ebadf()
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _flaky_replace)
    n = pio.compress_images_to_zip(img_dir, out_zip)

    assert n == 1
    assert state["n"] == 2
    with zipfile.ZipFile(out_zip) as zf:
        assert zf.namelist() == ["a_labels.tif"]
    assert not list(tmp_path.glob("__aw_tmp_*"))


def test_out_zip_failure_is_enriched_not_bare_errno(monkeypatch, tmp_path):

    img_dir = tmp_path / "stage"
    img_dir.mkdir()
    (img_dir / "a_labels.tif").write_bytes(b"x")

    monkeypatch.setattr(
        os, "replace", lambda *a, **k: (_ for _ in ()).throw(_ebadf())
    )
    with pytest.raises(OSError) as ei:
        pio.compress_images_to_zip(img_dir, tmp_path / "A01_out.zip")
    assert "A01_out.zip" in str(ei.value)
    assert not list(tmp_path.glob("__aw_tmp_*"))
