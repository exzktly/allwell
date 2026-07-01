"""Multi-well + per-timepoint empty-well background / flat-field correction.

The empty-well modes now (a) pool several blank/reference wells for a cleaner
estimate and (b) build a field PER TIMEPOINT, since background drifts over a
timecourse. A timepoint the blank wells lack falls back to the global pooled
median. Single-well / single-timepoint datasets stay byte-compatible.

Pipeline-side only (synthetic loose TIFFs); needs the pipeline's own deps.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("scipy")
pytest.importorskip("skimage")
import tifffile  # noqa: E402

from process_microscopy import (  # noqa: E402
    sample_empty_well_images,
    build_background_models,
    build_flatfield_models,
    _split_background_wells,
    _empty_well_tp_key,
    _empty_well_field,
    _POOLED_TP,
)

SCHEMA = ["experiment", "channel", "well", "fov", "timepoint"]
SEP = "_"


def _write(path: Path, level: float, *, size=32):
    tifffile.imwrite(str(path), np.full((size, size), level, np.float32))


def _make_plate(tmp_path: Path, *, wells, fovs, tps_levels):
    """Loose TIFFs ``exp_GFP_<well>_<fov>_<tp>.tif`` at the per-(well,tp) level."""
    for well in wells:
        for fov in fovs:
            for tp, level in tps_levels.items():
                _write(tmp_path / f"exp_GFP_{well}_{fov}_{tp}.tif", level)


# ── CSV / tp-key helpers ─────────────────────────────────────────────────────

def test_split_background_wells_csv_dedupe():
    assert _split_background_wells("H12, G12 ,h12") == ["H12", "G12"]
    assert _split_background_wells("H12") == ["H12"]              # single → list
    assert _split_background_wells(["H12", " G12 "]) == ["H12", "G12"]
    assert _split_background_wells("") == []


def test_tp_key_parity_with_sample_naming():
    # Empty-well frame labels and sample labels collapse to the SAME key, so the
    # per-timepoint lookup matches regardless of the label style used.
    assert _empty_well_tp_key("T01") == _empty_well_tp_key("1") == "1"
    assert _empty_well_tp_key("24h") == _empty_well_tp_key("24") == "24"
    assert _empty_well_tp_key("") == ""
    # prefixed ordinals collapse to the numeric index (T01/day1 == 1)
    assert _empty_well_tp_key("day1") == _empty_well_tp_key("T01") == "1"
    # a label with no digits is unparseable → passes through stripped
    assert _empty_well_tp_key(" ctrl ") == "ctrl"


# ── multi-well pooling + per-timepoint grouping ──────────────────────────────

def test_sample_pools_multiple_wells_grouped_by_timepoint(tmp_path):
    # Two blank wells × 2 FOV × 2 timepoints → frames pool across wells, group
    # by tp, plus a pooled-all-timepoints bucket.
    _make_plate(tmp_path, wells=("H12", "G12"), fovs=("F001", "F002"),
                tps_levels={"T01": 50.0, "T02": 150.0})
    # a non-blank well must be ignored
    _make_plate(tmp_path, wells=("A01",), fovs=("F001",),
                tps_levels={"T01": 999.0, "T02": 999.0})

    stacks = sample_empty_well_images(
        tmp_path, tokens=["GFP"], schema=SCHEMA, sep=SEP,
        background_wells=["H12", "G12"])

    assert set(stacks) == {"gfp"}
    by_tp = stacks["gfp"]
    assert set(by_tp) == {"1", "2", _POOLED_TP}
    # 2 wells × 2 FOV = 4 frames per timepoint; pooled = 8.
    assert len(by_tp["1"]) == 4
    assert len(by_tp["2"]) == 4
    assert len(by_tp[_POOLED_TP]) == 8
    # frames came from the blanks (≈50 / ≈150), never the A01 decoy (999).
    assert all(abs(float(a.mean()) - 50.0) < 1 for a in by_tp["1"])
    assert all(abs(float(a.mean()) - 150.0) < 1 for a in by_tp["2"])


def test_models_are_per_timepoint_and_differ(tmp_path):
    _make_plate(tmp_path, wells=("H12", "G12"), fovs=("F001", "F002"),
                tps_levels={"T01": 50.0, "T02": 150.0})

    bgm = build_background_models(tmp_path, tokens=["GFP"], schema=SCHEMA, sep=SEP,
                                  background_wells=["H12", "G12"])
    ffm = build_flatfield_models(tmp_path, tokens=["GFP"], schema=SCHEMA, sep=SEP,
                                 mode="empty_well", background_wells=["H12", "G12"])

    assert set(bgm) == {"gfp"} and set(ffm) == {"gfp"}
    assert set(bgm["gfp"]) == {"1", "2", _POOLED_TP}
    assert set(ffm["gfp"]) == {"1", "2", _POOLED_TP}

    # Additive background keeps the measured level → tracks the per-tp drift.
    f1 = _empty_well_field(bgm, "gfp", "1")
    f2 = _empty_well_field(bgm, "gfp", "2")
    assert abs(float(f1.mean()) - 50.0) < 2
    assert abs(float(f2.mean()) - 150.0) < 2
    assert float(f2.mean()) - float(f1.mean()) > 50.0      # background drifted up


def test_missing_timepoint_falls_back_to_pooled(tmp_path):
    _make_plate(tmp_path, wells=("H12",), fovs=("F001",),
                tps_levels={"T01": 50.0, "T02": 150.0})
    bgm = build_background_models(tmp_path, tokens=["GFP"], schema=SCHEMA, sep=SEP,
                                  background_wells=["H12"])

    # exact-tp hit
    assert _empty_well_field(bgm, "gfp", "1") is not None
    # a timepoint the blank lacks → pooled fallback (not None), == pooled field
    fb = _empty_well_field(bgm, "gfp", "99")
    assert fb is not None
    assert np.array_equal(fb, bgm["gfp"][_POOLED_TP])
    # an unknown channel has no model at all
    assert _empty_well_field(bgm, "mcherry", "1") is None


def test_single_well_single_tp_back_compat(tmp_path):
    # One well, one timepoint: the per-tp field and the pooled fallback are the
    # same single field the old time-collapsed path produced.
    _make_plate(tmp_path, wells=("H12",), fovs=("F001", "F002", "F003"),
                tps_levels={"24h": 70.0})
    bgm = build_background_models(tmp_path, tokens=["GFP"], schema=SCHEMA, sep=SEP,
                                  background_wells="H12")
    assert set(bgm["gfp"]) == {"24", _POOLED_TP}
    assert np.array_equal(bgm["gfp"]["24"], bgm["gfp"][_POOLED_TP])
    assert abs(float(_empty_well_field(bgm, "gfp", "24").mean()) - 70.0) < 2
