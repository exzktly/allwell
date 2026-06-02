"""QC tooling (§B): flat-field correction, debris removal, model precompute, args.

Pure-numpy/skimage image functions + the Analyze→CLI arg mapping. Needs
numpy/scipy/scikit-image/tifffile (the pipeline's own deps); skipped if absent.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("scipy")
pytest.importorskip("skimage")

from process_microscopy import (
    apply_flatfield,
    estimate_gaussian_flatfield,
    estimate_basic_flatfield,
    filter_debris,
    build_flatfield_models,
    _normalize_flatfield,
)
from services.pipeline_service import build_pipeline_args
from pathlib import Path


# ── flat-field estimation / application ──────────────────────────────────────

def test_normalize_flatfield():
    ff = _normalize_flatfield(np.full((8, 8), 7.0, np.float32))
    assert abs(float(ff.mean()) - 1.0) < 1e-5
    assert _normalize_flatfield(np.zeros((4, 4))) is None


def test_gaussian_flatfield_flattens_gradient():
    H = W = 256
    grad = np.tile(np.linspace(1, 3, W).astype(np.float32), (H, 1)) * 100
    corr = apply_flatfield(grad, estimate_gaussian_flatfield(grad, sigma=50))
    cv_before = grad.std() / grad.mean()
    cv_after = corr.std() / corr.mean()
    assert cv_after < cv_before * 0.4   # substantially flatter


def test_apply_flatfield_noops():
    img = np.ones((5, 5), np.float32)
    assert apply_flatfield(img, None) is img                 # no field
    assert apply_flatfield(img, np.ones((3, 3), np.float32)) is img  # shape mismatch


def test_basic_flatfield_tracks_shading():
    H = W = 64
    shade = np.tile(np.linspace(0.5, 1.5, W).astype(np.float32), (H, 1))
    rng = np.random.default_rng(0)
    stack = [rng.uniform(80, 120, (H, W)).astype(np.float32) * shade for _ in range(8)]
    ff = estimate_basic_flatfield(stack, sigma=10)
    assert abs(float(ff.mean()) - 1.0) < 1e-4
    assert ff[:, -1].mean() > ff[:, 0].mean()    # tracks the left→right shading
    assert estimate_basic_flatfield([]) is None


# ── debris removal (reuses §C regionprops) ───────────────────────────────────

def test_filter_debris_bounds():
    lab = np.zeros((40, 40), np.int32)
    lab[2:5, 2:5] = 1          # 9 px  → debris
    lab[10:30, 10:30] = 2      # 400 px → keep
    cleaned, n = filter_debris(lab, min_area=50)
    assert n == 1 and not np.any(cleaned == 1) and np.any(cleaned == 2)

    cleaned2, n2 = filter_debris(lab, max_area=100)
    assert n2 == 1 and np.any(cleaned2 == 1) and not np.any(cleaned2 == 2)

    assert filter_debris(lab)[1] == 0                                  # no bounds
    assert filter_debris(np.zeros((5, 5), np.int32), min_area=10)[1] == 0  # empty


def test_filter_debris_solidity():
    # solid square (solidity≈1) kept; a sparse cross (low solidity) removed
    lab = np.zeros((30, 30), np.int32)
    lab[2:12, 2:12] = 1
    lab[20, 0:30] = 2
    lab[0:30, 20] = 2          # plus-shaped label 2 → low solidity
    cleaned, n = filter_debris(lab, min_solidity=0.5)
    assert np.any(cleaned == 1) and not np.any(cleaned == 2) and n == 1


# ── per-channel model precompute (basic / empty_well) ────────────────────────

def _write_tif(path, arr):
    import tifffile
    tifffile.imwrite(str(path), arr.astype(np.float32))


def test_build_flatfield_models(tmp_path):
    schema = ["experiment", "channel", "well", "fov", "timepoint"]
    H = W = 48
    shade = np.tile(np.linspace(0.5, 1.5, W).astype(np.float32), (H, 1))
    rng = np.random.default_rng(1)
    # two wells, one channel (GFP)
    for well in ("A01", "A02"):
        for fov in ("F001", "F002", "F003"):
            img = rng.uniform(90, 110, (H, W)).astype(np.float32) * shade
            _write_tif(tmp_path / f"exp_GFP_{well}_{fov}_24h.tif", img)

    # basic: builds a field for gfp across all wells
    models = build_flatfield_models(tmp_path, tokens=["GFP"], schema=schema,
                                    sep="_", mode="basic")
    assert set(models) == {"gfp"}
    assert models["gfp"][:, -1].mean() > models["gfp"][:, 0].mean()

    # empty_well: only A02 is the reference
    models_bg = build_flatfield_models(tmp_path, tokens=["GFP"], schema=schema,
                                       sep="_", mode="empty_well", background_well="A02")
    assert set(models_bg) == {"gfp"}

    # empty_well with no background_well → nothing
    assert build_flatfield_models(tmp_path, tokens=["GFP"], schema=schema,
                                  sep="_", mode="empty_well") == {}
    # none/gaussian never precompute
    assert build_flatfield_models(tmp_path, tokens=["GFP"], schema=schema,
                                  sep="_", mode="gaussian") == {}


# ── Analyze → CLI arg mapping ────────────────────────────────────────────────

def _base_opts(**over):
    opts = dict(nuclear_token="NIR", fluor_tokens=["GFP"], csv_prefix="m",
                filename_schema="experiment:channel:well:fov:timepoint", filename_sep="_",
                segmentation_method="stardist_nuclei", min_nucleus_area_px=50,
                tophat_radius_nir="100", tophat_radius_fluor="100",
                smfish_tokens=[], shape_features=[], tf_threads="0", workers="0",
                compress_input_well_folders=True, compress_output_well_folders=True,
                flatfield="none", background_well="", qc_remove_debris=False,
                qc_min_area=0.0, qc_max_area=0.0, qc_min_solidity=0.0)
    opts.update(over)
    return opts


def test_build_pipeline_args_qc_mapping():
    P = Path("/x/process_microscopy.py")
    # none → no QC flags
    a = build_pipeline_args(P, Path("/in"), Path("/out"), _base_opts())
    assert "--flatfield" not in a and "--qc_remove_debris" not in a

    # gaussian flat-field
    a = build_pipeline_args(P, Path("/in"), Path("/out"), _base_opts(flatfield="gaussian"))
    assert a[a.index("--flatfield") + 1] == "gaussian"
    assert "--background_well" not in a   # only emitted for empty_well

    # empty_well carries the background well
    a = build_pipeline_args(P, Path("/in"), Path("/out"),
                            _base_opts(flatfield="empty_well", background_well="H12"))
    assert a[a.index("--flatfield") + 1] == "empty_well"
    assert a[a.index("--background_well") + 1] == "H12"

    # debris with bounds (zero bounds omitted)
    a = build_pipeline_args(P, Path("/in"), Path("/out"),
                            _base_opts(qc_remove_debris=True, qc_min_area=20.0,
                                       qc_max_area=0.0, qc_min_solidity=0.4))
    assert "--qc_remove_debris" in a
    assert a[a.index("--qc_min_area") + 1] == "20.0"
    assert "--qc_max_area" not in a       # 0 → no bound
    assert a[a.index("--qc_min_solidity") + 1] == "0.4"
