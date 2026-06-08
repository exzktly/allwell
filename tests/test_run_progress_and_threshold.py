"""Run-progress fixes (grouping progress when already grouped, well 'pending'
status) and the integer-rounding of the calculated auto-threshold.

Covers three reported issues:
  1) Grouping never showed progress when the inputs were already grouped/zipped
     — the zipper is skipped, so per-well group progress must be recovered from
     the resolved input dir (``iter_well_tokens``).
  2) Wells jumped straight from empty to processing — a grouped/queued well must
     render in a distinct 'pending' colour, separate from the 'processing' amber.
  3) The calculated auto-threshold must be rounded to the nearest integer before
     it is saved.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np
import pytest


# ── 1) iter_well_tokens: recover wells when grouping is skipped ───────────────

def test_iter_well_tokens_from_zips_and_folders(tmp_path):
    from services.input_resolution_service import iter_well_tokens

    # per-well zips + a per-well folder + a non-well zip (ignored)
    (tmp_path / "H12.zip").write_bytes(b"")
    (tmp_path / "A1.zip").write_bytes(b"")          # short form → canonicalises to A01
    (tmp_path / "B02").mkdir()
    (tmp_path / "archive.zip").write_bytes(b"")     # not a well — skipped
    (tmp_path / "notes").mkdir()                    # not a well — skipped

    toks = iter_well_tokens(tmp_path)
    assert toks == ["A01", "B02", "H12"]            # canonical + sorted + deduped
    assert iter_well_tokens(tmp_path / "does_not_exist") == []


# ── 2) RunDashboard: grouped == pending (distinct from processing) ────────────

@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_grouped_well_is_pending_not_processing(qapp):
    from widgets.run_dashboard import (
        RunDashboard, _COL_PENDING, _COL_PROCESSING, _COL_FETCHED)
    d = RunDashboard()
    d.reset(remote=False, total=3)

    # a grouped (queued) well shows the pending colour, NOT the processing amber
    d.set_well_stage("A01", "group")
    assert d._well_color("A01") == _COL_PENDING
    assert "pending" in d._well_tooltip("A01") or "grouped" in d._well_tooltip("A01")

    # once it starts processing it flips to amber, then blue when processed
    d.set_well_stage("A01", "active")
    assert d._well_color("A01") == _COL_PROCESSING
    d.set_well_stage("A01", "process")
    assert d._well_color("A01") == _COL_FETCHED

    # an untouched well stays uncoloured (truly empty, distinct from pending)
    assert d._well_color("H12") is None


def test_grouped_well_is_pending_remote(qapp):
    from widgets.run_dashboard import (
        RunDashboard, _COL_PENDING, _COL_PROCESSING)
    d = RunDashboard()
    d.reset(remote=True, total=2)
    d.set_well_stage("A01", "group")
    assert d._well_color("A01") == _COL_PENDING       # queued, not yet uploaded
    d.set_well_stage("A01", "upload")
    assert d._well_color("A01") == _COL_PROCESSING     # uploading / active


# ── 3) Auto-threshold rounds to the nearest integer before saving ────────────

def _write_tif_bytes(arr: np.ndarray) -> bytes:
    import tifffile
    buf = io.BytesIO()
    tifffile.imwrite(buf, arr.astype(np.float32))
    return buf.getvalue()


def _make_out_zip(path: Path) -> None:
    """A minimal ``*_out.zip`` with one FOV: a 2-label mask + its tophat fluor,
    so the standalone estimator pools a non-trivial cell+bg distribution."""
    schema_stem = "exp_GFP_A01_F001_T01"
    labels = np.zeros((16, 16), np.int32)
    labels[2:6, 2:6] = 1
    labels[10:14, 10:14] = 2
    fluor = np.zeros((16, 16), np.float32)
    fluor[2:6, 2:6] = 137.0        # bright cell
    fluor[10:14, 10:14] = 11.0     # dim cell
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{schema_stem}_labels.tif", _write_tif_bytes(labels))
        zf.writestr(f"{schema_stem}_tophat.tif", _write_tif_bytes(fluor))


def test_standalone_auto_threshold_is_rounded_to_integer(tmp_path):
    pytest.importorskip("skimage")
    import logging
    from process_microscopy import _estimate_thresholds_standalone

    _make_out_zip(tmp_path / "A01_out.zip")
    thr = _estimate_thresholds_standalone(
        tmp_path,
        fluor_channels=["GFP"],
        filename_schema="experiment:channel:well:fov:timepoint",
        filename_sep="_",
        log=logging.getLogger("test"),
        rng_seed=0,
    )
    assert "gfp" in thr
    v = thr["gfp"]
    # the saved value is an integer-valued float (rounded, no fractional part)
    assert v == float(round(v))
    assert v == int(v)
