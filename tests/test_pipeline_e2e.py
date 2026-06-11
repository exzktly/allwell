"""End-to-end pipeline test: real StarDist segmentation over synthetic raw images.

Exercises the *core* of the analysis pipeline — ``process_image_group`` —
through the real path: flat-field -> background subtraction
(``microscopy_background``) -> StarDist nuclear segmentation -> per-cell
quantification -> record assembly + output-image writing.

Requires the ML stack (TensorFlow + StarDist); skipped automatically where it's
absent (the lean CI). Marked ``slow`` (model load + inference) so a fast local
loop can exclude it with ``-m "not slow"``. First run downloads the
``2D_versatile_fluo`` weights (~5 MB) via StarDist's cache.

Note: this path imports TensorFlow but NOT Torch — the two cannot coexist in
some sandboxes (OpenMP/random_device abort), and the StarDist path never needs
Torch (Cellpose/label-free is lazily imported only when selected).
"""

from __future__ import annotations

import pytest

# Skip cleanly when the ML stack isn't installed (e.g. the lean CI job).
pytest.importorskip("tensorflow")
pytest.importorskip("stardist")
pytest.importorskip("tifffile")

import process_microscopy as pm  # noqa: E402
from tests.fixtures.synthetic_pipeline import (  # noqa: E402
    DEFAULT_CENTERS,
    write_synthetic_fov,
)

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def stardist_worker():
    """Initialise the StarDist model once for the module (CPU, deterministic)."""
    pm._worker_init(force_cpu=True)
    return True


def _run(out_root):
    raw = out_root / "raw"
    nuclear_path, fluor_path = write_synthetic_fov(raw, well="A01", fov="1", timepoint="0")
    out = out_root / "out"
    out.mkdir()
    schema = "experiment:channel:well:fov:timepoint".split(":")
    records = pm.process_image_group(
        nuclear_path=nuclear_path,
        fluor_paths=[fluor_path],
        fluor_tokens=["GFP"],
        output_dir=out,
        nuclear_token="DAPI",
        tophat_nir=False,
        tophat_radius_nir=50,
        tophat_fluor=[False],
        tophat_radius_fluor=[50],
        schema=schema,
        sep="_",
        segmentation_method="stardist_nuclei",
        background_method="none",
        flatfield_mode="none",
        min_nucleus_area_px=20,
    )
    return records, out


def test_pipeline_detects_all_synthetic_nuclei(tmp_path, stardist_worker):
    records, _ = _run(tmp_path)
    # 9 synthetic nuclei -> StarDist should recover them all (deterministic on CPU).
    assert len(records) >= len(DEFAULT_CENTERS) - 1
    assert len({r["nucleus_id"] for r in records}) == len(records)


def test_pipeline_record_schema_and_geometry(tmp_path, stardist_worker):
    records, _ = _run(tmp_path)
    assert records
    r = records[0]
    # Filename-parsed metadata.
    for r_ in records:
        assert r_["well"] == "A01"
        assert r_["fov"] == "1"
        assert str(r_["timepoint"]) == "0"
    # Required quantification + shape/QC columns are present.
    for key in (
        "nucleus_id", "area_px", "filename",
        "gfp_mean_intensity", "gfp_total_intensity", "gfp_max_intensity",
        "shape_solidity", "qc_region_area",
    ):
        assert key in r, f"missing column: {key}"
    # Geometry: disk radius 12 -> area ≈ π·144 ≈ 452; allow segmentation slack.
    for r_ in records:
        assert 250 <= r_["area_px"] <= 800


def test_pipeline_quantifies_fluor_signal(tmp_path, stardist_worker):
    records, _ = _run(tmp_path)
    # Fluor is bright (≈800) inside nuclei, dim (≈200) outside; per-cell means
    # must sit well above the background floor.
    means = [r["gfp_mean_intensity"] for r in records]
    assert all(m > 500.0 for m in means)
    assert all(t > 0.0 for t in (r["gfp_total_intensity"] for r in records))


def test_pipeline_writes_output_images(tmp_path, stardist_worker):
    _, out = _run(tmp_path)
    written = {p.name for p in out.iterdir()}
    # Label image + per-channel top-hat outputs + overlay.
    assert any(n.endswith("_labels.tif") for n in written)
    assert any(n.endswith("_overlay.png") for n in written)
    assert any("DAPI" in n and n.endswith("_tophat.tif") for n in written)
    assert any("GFP" in n and n.endswith("_tophat.tif") for n in written)
