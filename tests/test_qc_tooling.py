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
    apply_tophat,
    estimate_gaussian_flatfield,
    estimate_basic_flatfield,
    estimate_background_field,
    subtract_background,
    combined_empty_well_correct,
    BACKGROUND_METHODS,
    compute_region_areas,
    build_flatfield_models,
    build_background_models,
    sample_empty_well_images,
    _normalize_flatfield,
    _FLATFIELD_FLOOR,
    _POOLED_TP,
    _empty_well_field,
    _split_background_wells,
)
from services.pipeline_service import build_pipeline_args
from pathlib import Path
import io
import zipfile
import tifffile


# ── bright-region area (debris StarDist shatters into many small cells) ──────

def test_compute_region_areas_merges_shattered_debris():
    """An isolated nucleus sits in a small bright region; a bright debris blob
    that StarDist split into 4 small labels has all 4 share one large region."""
    img = np.zeros((40, 40), dtype=np.float32)
    lab = np.zeros((40, 40), dtype=np.int32)
    # isolated nucleus (label 1) on its own small bright spot
    img[2:7, 2:7] = 1000.0
    lab[2:7, 2:7] = 1
    # one bright 12x12 debris blob, shattered into 4 small labels (2..5)
    img[25:37, 25:37] = 1000.0
    lab[26:29, 26:29] = 2
    lab[26:29, 33:36] = 3
    lab[33:36, 26:29] = 4
    lab[33:36, 33:36] = 5

    region = compute_region_areas(img, lab)
    # the four debris fragments share one large region, much bigger than the
    # isolated nucleus's
    big = {region[2], region[3], region[4], region[5]}
    assert len(big) == 1                       # all four equal (same blob)
    assert region[2] >= 140                     # ~12x12 = 144
    assert region[1] <= 40                       # ~5x5 = 25 (+closing halo)
    assert region[2] > 3 * region[1]            # blob dwarfs a single nucleus


def test_compute_region_areas_empty_and_flat():
    assert compute_region_areas(np.zeros((10, 10), np.float32),
                                np.zeros((10, 10), np.int32)) == {}
    # flat image (no Otsu split) → labels still anchor their own components
    lab = np.zeros((10, 10), np.int32); lab[1:4, 1:4] = 1
    out = compute_region_areas(np.ones((10, 10), np.float32), lab)
    assert out[1] >= 9


# ── flat-field estimation / application ──────────────────────────────────────

def test_normalize_flatfield():
    ff = _normalize_flatfield(np.full((8, 8), 7.0, np.float32))
    assert abs(float(ff.mean()) - 1.0) < 1e-5
    assert _normalize_flatfield(np.zeros((4, 4))) is None


def test_normalize_flatfield_floors_tiny_values():
    # A dim empty-well region → tiny mean-1 value → huge img/ff amplification →
    # OOD StarDist input → ClipperLib abort. The floor caps amplification.
    field = np.full((16, 16), 1000.0, np.float32)
    field[0, 0] = 1.0          # ~0.001 of the mean → would be ~1000x amplification
    field[1, 1] = 5.0
    ff = _normalize_flatfield(field)
    assert ff.min() >= _FLATFIELD_FLOOR - 1e-6          # floored
    assert 1.0 / ff.min() <= 1.0 / _FLATFIELD_FLOOR + 1e-3  # amplification ≤ 1/floor
    assert np.all(np.isfinite(ff))


def test_normalize_flatfield_preserves_gentle_vignette():
    # Real vignetting (<3x falloff) keeps the field well above the floor — no
    # pixel should be clamped, so legitimate correction is untouched.
    grad = np.tile(np.linspace(0.4, 1.6, 32).astype(np.float32), (32, 1))
    ff = _normalize_flatfield(grad)
    assert ff.min() > _FLATFIELD_FLOOR + 0.1           # nothing hit the floor
    assert abs(float(ff.mean()) - 1.0) < 0.05


def test_apply_flatfield_bounded_amplification():
    # img/ff can amplify at most 1/_FLATFIELD_FLOOR after the floor.
    field = np.full((16, 16), 1000.0, np.float32)
    field[0, 0] = 0.5          # near-black empty-well pixel
    ff = _normalize_flatfield(field)
    img = np.full((16, 16), 200.0, np.float32)
    out = apply_flatfield(img, ff)
    assert out.max() <= img.max() / _FLATFIELD_FLOOR + 1.0   # no ~400000x blow-up
    # the combined empty-well model routes through apply_flatfield → same bound
    out2 = combined_empty_well_correct(img, None, ff)
    assert out2.max() <= img.max() / _FLATFIELD_FLOOR + 1.0


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


# ── G3: dead in-module debris gating removed ─────────────────────────────────

def test_filter_debris_removed_and_params_not_forwarded():
    """G3 — ``filter_debris`` was never called and ``process_image_group``'s
    ``qc_min_area``/``qc_max_area``/``qc_min_solidity`` were never read (QC is
    deferred to the Review layer via the ``qc_options`` sidecar). Both were
    deleted; lock that in so they aren't re-added as no-ops."""
    import inspect

    import process_microscopy as pm

    assert not hasattr(pm, "filter_debris")
    params = inspect.signature(pm.process_image_group).parameters
    for dead in ("qc_min_area", "qc_max_area", "qc_min_solidity"):
        assert dead not in params
    # The Review-layer knob stays — it's read (logs) and written to the sidecar.
    assert "qc_remove_debris" in params


def test_qc_bounds_still_reach_the_sidecar():
    """The CLI ``--qc_*`` bounds are NOT dead: ``build_parser`` still accepts
    them so they flow into ``pipeline_info``'s ``qc_options`` for the Review
    QC layer."""
    import process_microscopy as pm

    args = pm.build_parser().parse_args([
        "--input_dir", ".", "--output_dir", ".",
        "--qc_min_area", "12", "--qc_max_area", "34", "--qc_min_solidity", "0.5",
    ])
    assert (args.qc_min_area, args.qc_max_area, args.qc_min_solidity) == (12.0, 34.0, 0.5)


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

    # basic: builds a field for gfp across all wells (time-collapsed → pooled key)
    models = build_flatfield_models(tmp_path, tokens=["GFP"], schema=schema,
                                    sep="_", mode="basic")
    assert set(models) == {"gfp"}
    gfp = models["gfp"][_POOLED_TP]
    assert gfp[:, -1].mean() > gfp[:, 0].mean()

    # empty_well: only A02 is the reference (nested {tp_key: field, pooled: field})
    models_bg = build_flatfield_models(tmp_path, tokens=["GFP"], schema=schema,
                                       sep="_", mode="empty_well", background_wells="A02")
    assert set(models_bg) == {"gfp"}
    assert _empty_well_field(models_bg, "gfp", "24") is not None

    # empty_well with no background_well → nothing
    assert build_flatfield_models(tmp_path, tokens=["GFP"], schema=schema,
                                  sep="_", mode="empty_well") == {}
    # none/gaussian never precompute
    assert build_flatfield_models(tmp_path, tokens=["GFP"], schema=schema,
                                  sep="_", mode="gaussian") == {}


def _zip_well(zip_path: Path, well: str, tokens, n=3, level=60.0):
    """Write a per-well input zip (the default input layout) of TIFFs."""
    with zipfile.ZipFile(zip_path, "w") as zf:
        for tok in tokens:
            for f in range(n):
                b = io.BytesIO()
                tifffile.imwrite(b, np.full((32, 32), level, np.float32))
                zf.writestr(f"{well}_s{f}_{tok}.tif", b.getvalue())


def test_empty_well_models_read_zipped_wells(tmp_path):
    # Regression: in the default zip-per-well mode the empty-well flat-field /
    # background models were built only from loose-on-disk TIFFs, so the well
    # stayed compressed, every model came back empty, and empty_well silently
    # no-op'd (corrected == raw). The sampler must now read inside the zip.
    schema = ["well", "field", "channel"]
    _zip_well(tmp_path / "C11.zip", "C11", ["DAPI", "GFP"], level=60.0)
    _zip_well(tmp_path / "B02.zip", "B02", ["DAPI", "GFP"], level=200.0)

    stacks = sample_empty_well_images(tmp_path, tokens=["DAPI", "GFP"],
                                      schema=schema, sep="_", background_wells="C11")
    assert set(stacks) == {"dapi", "gfp"}
    # this schema has no timepoint field → all frames pool under one key.
    assert _POOLED_TP in stacks["dapi"]
    # sampled from C11 (≈60), not B02 (≈200)
    assert all(abs(float(a.mean()) - 60.0) < 1 for a in stacks["dapi"][_POOLED_TP])

    bgm = build_background_models(tmp_path, tokens=["DAPI", "GFP"], schema=schema,
                                  sep="_", background_wells="C11")
    ffm = build_flatfield_models(tmp_path, tokens=["DAPI", "GFP"], schema=schema,
                                 sep="_", mode="empty_well", background_wells="C11")
    assert set(bgm) == {"dapi", "gfp"} and set(ffm) == {"dapi", "gfp"}
    # the additive field keeps the measured level → real subtraction, not a no-op
    assert abs(float(_empty_well_field(bgm, "dapi", "").mean()) - 60.0) < 2


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


# ── background-subtraction methods ───────────────────────────────────────────

def test_subtract_background_method_dispatch_and_default():
    from process_microscopy import _isotropic_disk_tophat
    img = (np.random.default_rng(1).random((48, 48)) * 100).astype(np.float32)
    # explicit top_hat is still byte-for-byte the legacy square top-hat
    assert np.allclose(subtract_background(img, 8, "top_hat"), apply_tophat(img, 8))
    # the DEFAULT is now the isotropic sliding_disk, NOT the square top_hat
    assert np.allclose(subtract_background(img, 8), _isotropic_disk_tophat(img, 8))
    assert not np.allclose(subtract_background(img, 8), apply_tophat(img, 8))


def test_subtract_background_fov_median_subtracts_scalar_offset():
    # A flat background of 50 under a few bright cells: fov_median subtracts the
    # FOV median (≈50, robust to the bright cells), flooring background to ~0 and
    # leaving the cells reduced by exactly that scalar offset. No radius is used.
    img = np.full((64, 64), 50.0, np.float32)
    img[10:14, 10:14] = 900.0          # sparse bright cells don't move the median
    img[40:44, 40:44] = 700.0
    out = subtract_background(img, 0, "fov_median")
    assert out.shape == img.shape and out.min() >= 0.0
    assert float(np.median(out)) < 1.0                 # background floored to ~0
    assert abs(float(out[11, 11]) - (900.0 - 50.0)) < 1e-3   # uniform offset only
    assert abs(float(out[41, 41]) - (700.0 - 50.0)) < 1e-3
    # radius is irrelevant for fov_median (scalar offset, no morphology)
    assert np.array_equal(out, subtract_background(img, 999, "fov_median"))
    # non-finite pixels are zeroed, not propagated (StarDist-safety)
    nan_img = img.copy(); nan_img[0, 0] = np.nan
    assert np.all(np.isfinite(subtract_background(nan_img, 0, "fov_median")))


def test_subtract_background_sliding_disk_smooths_gradient():
    # sliding_disk is now an isotropic disk white-top-hat at full resolution.
    # A smooth illumination gradient + flat signal: it should remove the gradient,
    # leaving a near-flat (low-variance) background-subtracted image.
    y = np.linspace(0, 300, 64, dtype=np.float32)
    img = np.tile(y, (64, 1))                     # pure left→right gradient
    rb = subtract_background(img, 20, "sliding_disk")
    assert rb.min() >= 0.0 and rb.shape == img.shape
    assert float(rb.std()) < float(img.std())     # gradient flattened
    # Preserves a small bright object on a smooth background.
    obj = np.tile(np.linspace(40, 160, 96, dtype=np.float32), (96, 1)).copy()
    obj[44:52, 44:52] += 400.0
    out = subtract_background(obj, 30, "sliding_disk")
    assert float(out[4:12, 4:12].mean()) < 40       # smooth background removed
    assert float(out[45:51, 45:51].mean()) > 200    # object preserved


def test_sliding_disk_isotropic_no_bright_disk_on_confluent_patch():
    """The bug we fixed: a raised ball-scale patch (a confluent region) must be
    flattened, not left as a bright disk. The downscale trick shifted the
    sliding-disk climb threshold and left such patches ~10x too bright; the
    full-res disk opening flattens a patch wider than ~2·radius like the ball."""
    N = 256
    yy, xx = np.ogrid[:N, :N]
    r = np.hypot(yy - N / 2, xx - N / 2)
    # raised soft-edged disk Ø≈140 px on a flat background; radius 40 → 2·r=80 < 140
    img = (np.full((N, N), 100.0, np.float32)
           + 300.0 * 0.5 * (1 - np.tanh((r - 70) / 4))).astype(np.float32)
    out = subtract_background(img, 40, "sliding_disk")
    centre = out[N // 2 - 8:N // 2 + 8, N // 2 - 8:N // 2 + 8].mean()
    assert float(centre) < 30.0          # patch flattened, NOT a bright disk

    # Isotropy: the response around a point object is rotationally even, unlike
    # the square top_hat. Compare a horizontal vs diagonal arm of a star pattern.
    star = np.zeros((N, N), np.float32)
    star[N // 2, :] = 500.0
    for i in range(N):
        star[i, i] = 500.0
    rb = subtract_background(star, 12, "sliding_disk")
    horiz = float(rb[N // 2, 20:40].mean())
    diag = float(np.mean([rb[i, i] for i in range(20, 40)]))
    assert abs(horiz - diag) < 0.30 * max(horiz, diag) + 5  # near-isotropic


def test_subtract_background_empty_well_subtracts_field():
    img = np.full((32, 32), 80.0, np.float32)
    field = np.full((32, 32), 50.0, np.float32)
    assert np.allclose(subtract_background(img, 5, "empty_well", bg_field=field), 30.0)
    # no field → no-op (channel absent from the empty well)
    assert np.allclose(subtract_background(img, 5, "empty_well", bg_field=None), img)
    # shape mismatch → no-op
    assert np.allclose(subtract_background(img, 5, "empty_well",
                                          bg_field=np.zeros((4, 4), np.float32)), img)


def test_empty_well_subtraction_is_nonneg_and_finite():
    # negatives floored to 0 (image dimmer than the reference field)
    img = np.full((16, 16), 30.0, np.float32)
    field = np.full((16, 16), 50.0, np.float32)
    out = subtract_background(img, 5, "empty_well", bg_field=field)
    assert out.min() >= 0.0 and np.all(out == 0.0)
    # a NON-FINITE reference pixel must not survive — np.clip alone would keep
    # the NaN/Inf and crash StarDist's ClipperLib NMS / poison quantification.
    bad = np.full((16, 16), 50.0, np.float32)
    bad[0, 0] = np.nan
    bad[1, 1] = np.inf
    bad[2, 2] = -np.inf
    out = subtract_background(np.full((16, 16), 80.0, np.float32), 5,
                             "empty_well", bg_field=bad)
    assert np.all(np.isfinite(out)) and out.min() >= 0.0
    # combined model is guarded the same way
    ff = np.ones((16, 16), np.float32)
    out2 = combined_empty_well_correct(np.full((16, 16), 80.0, np.float32), bad, ff)
    assert np.all(np.isfinite(out2)) and out2.min() >= 0.0


def test_estimate_background_field_keeps_measured_level():
    # unlike the flat-field estimator (mean 1), the background field keeps the
    # measured level so it can be subtracted
    f = estimate_background_field([np.full((16, 16), 12.0, np.float32)] * 4)
    assert f is not None and abs(float(f.mean()) - 12.0) < 0.5
    assert estimate_background_field([]) is None


def test_build_pipeline_args_background_mapping():
    P = Path("/x/process_microscopy.py")
    # the method is now ALWAYS passed explicitly (no silent CLI-default fallback);
    # the unspecified default is the isotropic sliding_disk.
    a = build_pipeline_args(P, Path("/in"), Path("/out"), _base_opts())
    assert a[a.index("--background") + 1] == "sliding_disk"
    # top_hat is still selectable and passed through verbatim
    a = build_pipeline_args(P, Path("/in"), Path("/out"),
                            _base_opts(background="top_hat"))
    assert a[a.index("--background") + 1] == "top_hat"
    # sliding_disk → flag, no background well
    a = build_pipeline_args(P, Path("/in"), Path("/out"),
                            _base_opts(background="sliding_disk"))
    assert a[a.index("--background") + 1] == "sliding_disk"
    assert "--background_well" not in a
    # empty_well background carries the background well even with flatfield=none
    a = build_pipeline_args(P, Path("/in"), Path("/out"),
                            _base_opts(background="empty_well", background_well="H12"))
    assert a[a.index("--background") + 1] == "empty_well"
    assert a[a.index("--background_well") + 1] == "H12"
    # fov_median → flag, no background well (a scalar offset; no reference well)
    a = build_pipeline_args(P, Path("/in"), Path("/out"),
                            _base_opts(background="fov_median"))
    assert a[a.index("--background") + 1] == "fov_median"
    assert "--background_well" not in a
    # explicit "none" disables background subtraction entirely
    a = build_pipeline_args(P, Path("/in"), Path("/out"), _base_opts(background="none"))
    assert a[a.index("--background") + 1] == "none"
    assert set(BACKGROUND_METHODS) == {
        "none", "top_hat", "sliding_disk", "fov_median", "empty_well",
        "robust_mesh", "fft_bandpass"}
    # The two object-size-agnostic methods reuse the radius (large-structure /
    # tile scale); fft_bandpass also forwards the small-structure cutoffs.
    for m in ("robust_mesh", "fft_bandpass"):
        a = build_pipeline_args(P, Path("/in"), Path("/out"), _base_opts(background=m))
        assert a[a.index("--background") + 1] == m
    a = build_pipeline_args(P, Path("/in"), Path("/out"),
                            _base_opts(background="fft_bandpass",
                                       fft_small_nir="3", fft_small_fluor="5"))
    assert a[a.index("--fft_small_nir") + 1] == "3"
    assert a[a.index("--fft_small_fluor") + 1] == "5"
    # robust_mesh forwards its tile-ratio / sigma-clip tuning.
    a = build_pipeline_args(P, Path("/in"), Path("/out"),
                            _base_opts(background="robust_mesh",
                                       mesh_ratio="4", mesh_sigma="2.5"))
    assert float(a[a.index("--mesh_ratio") + 1]) == 4.0
    assert float(a[a.index("--mesh_sigma") + 1]) == 2.5


def test_subtract_background_none_is_noop():
    img = (np.random.default_rng(3).random((24, 24)) * 100).astype(np.float32)
    assert np.allclose(subtract_background(img, 8, "none"), img)


def test_combined_empty_well_recovers_signal():
    # raw = gain·signal + B, with the additive background B tracking the
    # illumination gain (B = gain·k) — the assumption behind the combined model.
    # Then (raw − B)/(B/mean(B)) should null the empty background and recover the
    # signal, where naive divide-then-subtract would not.
    H = W = 48
    gain = np.tile(np.linspace(0.5, 1.5, W, dtype=np.float32), (H, 1))
    empty = [(gain * 80.0).astype(np.float32) for _ in range(5)]
    B = estimate_background_field(empty)            # ≈ gain·80 (un-normalized)
    ff = estimate_basic_flatfield(empty)            # ≈ (gain·80)/mean  (mean 1)
    signal = np.zeros((H, W), np.float32); signal[14:22, 14:22] = 300.0
    raw = (gain * signal + gain * 80.0).astype(np.float32)
    out = combined_empty_well_correct(raw, B, ff)
    assert float(out[0:8, 0:8].mean()) < 25            # empty region → ~0
    assert 200 < float(out[15:21, 15:21].mean()) < 400  # signal recovered (~300)


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

    # multiple (comma-separated) background wells pass through as one arg; the
    # pipeline splits them via _split_background_wells.
    a = build_pipeline_args(P, Path("/in"), Path("/out"),
                            _base_opts(flatfield="empty_well", background_well="H12, G12"))
    assert _split_background_wells(a[a.index("--background_well") + 1]) == ["H12", "G12"]

    # debris with bounds (zero bounds omitted)
    a = build_pipeline_args(P, Path("/in"), Path("/out"),
                            _base_opts(qc_remove_debris=True, qc_min_area=20.0,
                                       qc_max_area=0.0, qc_min_solidity=0.4))
    assert "--qc_remove_debris" in a
    assert a[a.index("--qc_min_area") + 1] == "20.0"
    assert "--qc_max_area" not in a       # 0 → no bound
    assert a[a.index("--qc_min_solidity") + 1] == "0.4"


# ── Analyze QC section UI: presence + bound gating ───────────────────────────

@pytest.mark.qt
def test_analyze_qc_section_present_and_gated():
    """The QC cleaning-step options are built and discoverable, and the debris
    size/solidity bounds are gated on the 'Remove debris' toggle (mirroring the
    Background-well field's gating on the empty_well flat-field mode)."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel
    QApplication.instance() or QApplication([])
    from analyze_tab import AnalyzeTab

    tab = AnalyzeTab()
    # All QC widgets exist and the section title renders.
    for attr in ("_flatfield_cb", "_background_well_edit", "_qc_remove_debris_cb",
                 "_qc_min_area_edit", "_qc_max_area_edit", "_qc_min_solidity_edit"):
        assert getattr(tab, attr, None) is not None
    titles = {l.text() for l in tab.findChildren(QLabel)}
    assert "Quality Control" in titles

    # Debris bounds hidden until the checkbox is on, then re-hidden when off.
    # (``isHidden`` is the offscreen-robust check — ``isVisible`` is False for
    # every widget when the top-level window is never shown.)
    ra, rs = tab._qc_debris_rows
    assert tab._qc_remove_debris_cb.isChecked() is False
    assert ra.isHidden() and rs.isHidden()
    tab._qc_remove_debris_cb.setChecked(True)
    assert not ra.isHidden() and not rs.isHidden()
    tab._qc_remove_debris_cb.setChecked(False)
    assert ra.isHidden()

    # Background well gates on the empty_well flat-field / background modes.
    # Flat-field now defaults to empty_well, so the field starts enabled; drop to
    # a non-empty_well pair to confirm the gate still disables it, then back.
    assert tab._background_well_edit.isEnabled() is True
    tab._flatfield_cb.setCurrentText("none")
    tab._background_cb.setCurrentText("sliding_disk")
    assert tab._background_well_edit.isEnabled() is False
    tab._flatfield_cb.setCurrentText("empty_well")
    assert tab._background_well_edit.isEnabled() is True

    # Radius rows show only for the radius-based methods, and the whole row —
    # the "Nuclear radius"/"Fluor radius" *labels* included — hides for the
    # others (regression: hiding only the entry cell stranded the labels).
    radius_labels = {"Nuclear radius", "Fluor radius"}
    tab._background_cb.setCurrentText("top_hat")
    assert all(not r.isHidden() for r in tab._bg_radius_rows)
    for method in ("none", "fov_median", "empty_well"):
        tab._background_cb.setCurrentText(method)
        assert all(r.isHidden() for r in tab._bg_radius_rows), method
        shown = {l.text() for r in tab._bg_radius_rows
                 for l in r.findChildren(QLabel) if not r.isHidden()}
        assert not (radius_labels & shown), f"labels stranded for {method}"
    tab._background_cb.setCurrentText("sliding_disk")
    assert all(not r.isHidden() for r in tab._bg_radius_rows)


def test_analyze_default_background_is_robust_mesh_end_to_end(qapp):
    """Regression: the dropdown defaults to robust_mesh (sigma-clipped local
    background on a coarse grid) and that selection survives all the way to the
    CLI args — so a default run uses the configured default, not a stale one."""
    from analyze_tab import AnalyzeTab
    from services.pipeline_service import build_pipeline_args
    tab = AnalyzeTab()
    assert tab._background_cb.currentText() == "robust_mesh"
    opts = tab._collect_run_options()
    assert opts.get("background") == "robust_mesh"
    a = build_pipeline_args(Path("/x/process_microscopy.py"),
                            Path("/in"), Path("/out"), opts)
    assert a[a.index("--background") + 1] == "robust_mesh"


# ── background-well presence (empty_well requires a real, existing well) ──────

def test_well_present_in_input_layouts(tmp_path):
    from services.input_resolution_service import well_present_in_input
    # zip layout (default): <input>/<WELL>.zip
    (tmp_path / "zipmode").mkdir()
    zd = tmp_path / "zipmode"
    with zipfile.ZipFile(zd / "C11.zip", "w") as z:
        z.writestr("C11_s0_DAPI.tif", b"x")
    assert well_present_in_input(zd, "c11")          # case/zero-pad insensitive
    assert well_present_in_input(zd, "C11")
    assert not well_present_in_input(zd, "H12")
    # folder layout
    fd = tmp_path / "foldermode"; (fd / "B02").mkdir(parents=True)
    assert well_present_in_input(fd, "b2")           # unpadded canonicalises
    assert not well_present_in_input(fd, "B03")
    # loose-TIFF layout (parsed via the schema 'well' field)
    ld = tmp_path / "loose"; ld.mkdir()
    _write_tif(ld / "A01_s0_DAPI.tif", np.zeros((4, 4), np.float32))
    schema = ["well", "field", "channel"]
    assert well_present_in_input(ld, "A01", schema=schema, sep="_")
    assert not well_present_in_input(ld, "A02", schema=schema, sep="_")
    # invalid token never matches
    assert not well_present_in_input(zd, "")
    assert not well_present_in_input(zd, "ZZ9")


def test_background_well_validation_blocks_run(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from analyze_tab import AnalyzeTab

    with zipfile.ZipFile(tmp_path / "H12.zip", "w") as z:
        z.writestr("H12_s0_DAPI.tif", b"x")
    tab = AnalyzeTab()
    tab._input_edit.setText(str(tmp_path))

    # neither uses empty_well → no error
    tab._flatfield_cb.setCurrentText("none")
    tab._background_cb.setCurrentText("top_hat")
    assert tab._background_well_validation_error() is None

    # empty_well background, blank well → required
    tab._background_cb.setCurrentText("empty_well")
    tab._background_well_edit.setText("")
    err = tab._background_well_validation_error()
    assert err and "requires a Background well" in err

    # specified but absent → not found
    tab._background_well_edit.setText("A01")
    err = tab._background_well_validation_error()
    assert err and "was not found" in err

    # specified + present → valid
    tab._background_well_edit.setText("h12")
    assert tab._background_well_validation_error() is None

    # empty_well flat-field path triggers it too (message names flat-field)
    tab._background_cb.setCurrentText("top_hat")
    tab._flatfield_cb.setCurrentText("empty_well")
    tab._background_well_edit.setText("")
    err = tab._background_well_validation_error()
    assert err and "flat-field correction" in err
