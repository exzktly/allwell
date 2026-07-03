"""Audit E9 — a biased auto-threshold must report itself.

``_estimate_thresholds_standalone`` reads per-cell label + tophat image
pairs out of each ``*_out.zip`` and computes an Otsu threshold. A decode
failure on a pair was only logged at DEBUG (invisible at the default level),
so a channel whose tophat images are largely corrupt would silently estimate
its threshold from a small, biased subset. The fix counts decode failures per
channel and emits a WARNING when a meaningful fraction were dropped.
"""
from __future__ import annotations

import io
import logging
import zipfile

import numpy as np
import tifffile

import process_microscopy


def _valid_tif_bytes(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    tifffile.imwrite(buf, arr)
    return buf.getvalue()


def _make_out_zip(path, *, n_pairs: int, n_corrupt: int) -> None:
    """Write an ``*_out.zip`` with *n_pairs* (label, tophat) pairs, of which
    *n_corrupt* have a corrupt tophat member."""
    labels = np.zeros((8, 8), dtype=np.uint16)
    labels[1:4, 1:4] = 1
    labels[5:7, 5:7] = 2
    fluor = (labels * 100).astype(np.uint16)
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(n_pairs):
            stem = f"a1_f{i}_0_cy5"
            zf.writestr(f"{stem}_labels.tif", _valid_tif_bytes(labels))
            if i < n_corrupt:
                zf.writestr(f"{stem}_tophat.tif", b"not a tiff at all")
            else:
                zf.writestr(f"{stem}_tophat.tif", _valid_tif_bytes(fluor))


def _run(tmp_path, caplog, *, n_pairs, n_corrupt):
    _make_out_zip(tmp_path / "a1_out.zip", n_pairs=n_pairs, n_corrupt=n_corrupt)
    log = logging.getLogger("test_auto_threshold_e9")
    with caplog.at_level(logging.WARNING, logger=log.name):
        process_microscopy._estimate_thresholds_standalone(
            tmp_path,
            fluor_channels=["cy5"],
            filename_schema="well:fov:timepoint:channel",
            filename_sep="_",
            log=log,
            rng_seed=0,
        )
    return caplog


def test_mostly_corrupt_channel_warns(tmp_path, caplog):
    caplog = _run(tmp_path, caplog, n_pairs=4, n_corrupt=3)
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("failed to decode" in m and "CY5" in m for m in msgs), msgs


def test_all_clean_channel_does_not_warn(tmp_path, caplog):
    caplog = _run(tmp_path, caplog, n_pairs=4, n_corrupt=0)
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert not any("failed to decode" in m for m in msgs), msgs


# ── Viewer-side mirror (well_viewer/data/auto_threshold.py) ───────────────
def test_viewer_side_decode_failures_warn(tmp_path, monkeypatch, caplog):
    from well_viewer.data import auto_threshold as vat

    (tmp_path / "a1_out.zip").write_bytes(b"placeholder")  # so _iter_well_zips finds it

    def _fake_gather(target, channels, pipeline_info):
        refs = {("f0", "0"): "ref"}
        return {ch: (dict(refs), dict(refs)) for ch in channels}

    monkeypatch.setattr(vat, "_gather_via_zip", _fake_gather)
    # Every open fails → decode_failed == attempted for the channel.
    monkeypatch.setattr(vat, "_open_image_at", lambda _ref: None)

    with caplog.at_level(logging.WARNING, logger=vat.logger.name):
        vat.compute_auto_thresholds(
            tmp_path, fluor_channels=["cy5"], rng_seed=0,
        )
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("failed to decode" in m and "CY5" in m for m in msgs), msgs
