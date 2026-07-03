"""Audit X2 / X9 / E14 — remote-fetch robustness.

* X2: Fetcher._materialize writes the local artifact atomically (temp →
  os.replace) and returns success, so an interrupted copy can't leave a
  truncated {well}_out.zip and a failed copy is reported (the caller then keeps
  the remote copy instead of deleting it).
* E14: a bad ALLWELL_SCRATCH_DIR is surfaced, not silently ignored.
"""
from __future__ import annotations

import logging
import tempfile
import types
import zipfile
from pathlib import Path

import services.remote_runner as rr


def _bare_fetcher(dest, output_filter, incremental=False):
    f = object.__new__(rr.Fetcher)
    f.output_filter = output_filter
    f.run = types.SimpleNamespace(local_output_dir=dest, incremental=incremental)
    f._log = lambda _m: None
    return f


def _make_zip(p: Path):
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("a.txt", "hello")


# ── X2 ──────────────────────────────────────────────────────────────────
def test_materialize_success_is_atomic(tmp_path):
    src = tmp_path / "src.zip"; _make_zip(src)
    dest = tmp_path / "out"; dest.mkdir()
    f = _bare_fetcher(dest, rr.OutputFilter.FULL)

    assert f._materialize("A01", src) is True
    assert (dest / "A01_out.zip").exists()
    assert not list(dest.glob(".*.tmp")), "no leftover temp after atomic replace"


def test_materialize_failure_returns_false_and_leaves_no_partial(tmp_path):
    dest = tmp_path / "out"; dest.mkdir()
    f = _bare_fetcher(dest, rr.OutputFilter.FULL)

    assert f._materialize("A01", tmp_path / "does_not_exist.zip") is False
    assert not (dest / "A01_out.zip").exists()          # nothing truncated in place
    assert not list(dest.glob(".A01_out.zip.tmp"))      # temp cleaned up


def test_materialize_csv_only_is_noop_true(tmp_path):
    dest = tmp_path / "out"; dest.mkdir()
    f = _bare_fetcher(dest, rr.OutputFilter.CSV_ONLY)
    assert f._materialize("A01", tmp_path / "whatever.zip") is True
    assert not (dest / "A01_out.zip").exists()


# ── E14 ─────────────────────────────────────────────────────────────────
def test_bad_scratch_override_warns_and_falls_back(tmp_path, monkeypatch, caplog):
    a_file = tmp_path / "afile"; a_file.write_text("x")
    bad = a_file / "sub"                    # parent is a file → mkdir fails
    monkeypatch.setenv("ALLWELL_SCRATCH_DIR", str(bad))
    with caplog.at_level(logging.WARNING, logger="services.remote_runner"):
        p = rr._scratch_dir()
    assert p == Path(tempfile.gettempdir())
    assert any("unusable" in r.getMessage() for r in caplog.records)
