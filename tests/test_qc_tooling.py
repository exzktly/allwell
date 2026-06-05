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
    filter_debris,
    build_flatfield_models,
    build_background_models,
    sample_empty_well_images,
    _normalize_flatfield,
    _FLATFIELD_FLOOR,
)
from services.pipeline_service import build_pipeline_args
from pathlib import Path
import io
import zipfile
import tifffile


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
                                      schema=schema, sep="_", background_well="C11")
    assert set(stacks) == {"dapi", "gfp"}
    # sampled from C11 (≈60), not B02 (≈200)
    assert all(abs(float(a.mean()) - 60.0) < 1 for a in stacks["dapi"])

    bgm = build_background_models(tmp_path, tokens=["DAPI", "GFP"], schema=schema,
                                  sep="_", background_well="C11")
    ffm = build_flatfield_models(tmp_path, tokens=["DAPI", "GFP"], schema=schema,
                                 sep="_", mode="empty_well", background_well="C11")
    assert set(bgm) == {"dapi", "gfp"} and set(ffm) == {"dapi", "gfp"}
    # the additive field keeps the measured level → real subtraction, not a no-op
    assert abs(float(bgm["dapi"].mean()) - 60.0) < 2


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

def test_subtract_background_top_hat_is_backcompat():
    img = (np.random.default_rng(1).random((48, 48)) * 100).astype(np.float32)
    # default method must be byte-for-byte the legacy top-hat
    assert np.allclose(subtract_background(img, 8, "top_hat"), apply_tophat(img, 8))
    assert np.allclose(subtract_background(img, 8), apply_tophat(img, 8))


def test_subtract_background_rolling_ball_smooths_gradient():
    # a smooth illumination gradient + flat signal: rolling-ball should remove
    # the gradient, leaving a near-flat (low-variance) background-subtracted image
    y = np.linspace(0, 300, 64, dtype=np.float32)
    img = np.tile(y, (64, 1))                     # pure left→right gradient
    rb = subtract_background(img, 20, "rolling_ball")
    assert rb.min() >= 0.0 and rb.shape == img.shape
    assert float(rb.std()) < float(img.std())     # gradient flattened


def test_rolling_ball_downscaled_path_removes_bg_keeps_object():
    # radius 50 on a 128px image triggers the downscale fast path (f>1). It must
    # still remove a smooth background and preserve a bright object, at the input
    # resolution.
    from process_microscopy import _estimate_rolling_ball_bg
    H = W = 128
    grad = np.tile(np.linspace(40, 160, W, dtype=np.float32), (H, 1))  # smooth bg
    img = grad.copy()
    img[60:70, 60:70] += 400.0                    # a bright object
    # the helper actually downscales at this radius/size
    assert max(1, min(50 // 25, min(H, W) // 32)) > 1
    out = subtract_background(img, 50, "rolling_ball")
    assert out.shape == img.shape and out.min() >= 0.0
    assert float(out[5:15, 5:15].mean()) < 40       # smooth background removed
    assert float(out[62:68, 62:68].mean()) > 200    # object preserved
    # the estimated background tracks the gradient (low → high, left → right)
    bg = _estimate_rolling_ball_bg(grad, 50.0)
    assert bg[:, -1].mean() > bg[:, 0].mean()


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
    # default top_hat → no --background flag
    a = build_pipeline_args(P, Path("/in"), Path("/out"), _base_opts())
    assert "--background" not in a
    # rolling_ball → flag, no background well
    a = build_pipeline_args(P, Path("/in"), Path("/out"),
                            _base_opts(background="rolling_ball"))
    assert a[a.index("--background") + 1] == "rolling_ball"
    assert "--background_well" not in a
    # empty_well background carries the background well even with flatfield=none
    a = build_pipeline_args(P, Path("/in"), Path("/out"),
                            _base_opts(background="empty_well", background_well="H12"))
    assert a[a.index("--background") + 1] == "empty_well"
    assert a[a.index("--background_well") + 1] == "H12"
    # explicit "none" disables background subtraction entirely
    a = build_pipeline_args(P, Path("/in"), Path("/out"), _base_opts(background="none"))
    assert a[a.index("--background") + 1] == "none"
    assert set(BACKGROUND_METHODS) == {"none", "top_hat", "rolling_ball", "empty_well"}


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

    # Debris bounds disabled until the checkbox is on, then re-disabled when off.
    ra, rs = tab._qc_debris_rows
    assert tab._qc_remove_debris_cb.isChecked() is False
    assert ra.isEnabled() is False and rs.isEnabled() is False
    tab._qc_remove_debris_cb.setChecked(True)
    assert ra.isEnabled() is True and rs.isEnabled() is True
    tab._qc_remove_debris_cb.setChecked(False)
    assert ra.isEnabled() is False

    # Background well still gates on the empty_well flat-field mode.
    assert tab._background_well_edit.isEnabled() is False
    tab._flatfield_cb.setCurrentText("empty_well")
    assert tab._background_well_edit.isEnabled() is True

    # Radius rows show only for the radius-based methods, and the whole row —
    # the "Nuclear radius"/"Fluor radius" *labels* included — hides for the
    # others (regression: hiding only the entry cell stranded the labels).
    radius_labels = {"Nuclear radius", "Fluor radius"}
    tab._background_cb.setCurrentText("top_hat")
    assert all(not r.isHidden() for r in tab._bg_radius_rows)
    for method in ("none", "empty_well"):
        tab._background_cb.setCurrentText(method)
        assert all(r.isHidden() for r in tab._bg_radius_rows), method
        shown = {l.text() for r in tab._bg_radius_rows
                 for l in r.findChildren(QLabel) if not r.isHidden()}
        assert not (radius_labels & shown), f"labels stranded for {method}"
    tab._background_cb.setCurrentText("rolling_ball")
    assert all(not r.isHidden() for r in tab._bg_radius_rows)


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
