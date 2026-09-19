"""End-to-end pipeline test: real StarDist segmentation over synthetic raw images.

Exercises the *core* of the analysis pipeline — ``process_image_group`` —
through the real path: flat-field -> background subtraction
(``pipeline.background``) -> StarDist nuclear segmentation -> per-cell
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

import process_microscopy as pm
from tests.fixtures.synthetic_pipeline import (
    DEFAULT_CENTERS,
    write_synthetic_fov,
    write_synthetic_well_folder,
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
    records, quality = pm.process_image_group(
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
    return records, out, quality


def test_pipeline_detects_all_synthetic_nuclei(tmp_path, stardist_worker):
    records, _, _ = _run(tmp_path)
    # 9 synthetic nuclei -> StarDist should recover them all (deterministic on CPU).
    assert len(records) >= len(DEFAULT_CENTERS) - 1
    assert len({r["nucleus_id"] for r in records}) == len(records)


def test_pipeline_record_schema_and_geometry(tmp_path, stardist_worker):
    records, _, _ = _run(tmp_path)
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
    records, _, _ = _run(tmp_path)
    # Fluor is bright (≈800) inside nuclei, dim (≈200) outside; per-cell means
    # must sit well above the background floor.
    means = [r["gfp_mean_intensity"] for r in records]
    assert all(m > 500.0 for m in means)
    assert all(t > 0.0 for t in (r["gfp_total_intensity"] for r in records))


def test_the_group_reports_its_own_field_quality(tmp_path, stardist_worker):
    """Excludes the wiring that returns an empty mapping and looks fine.

    Every metric here is measured against a real StarDist segmentation of a
    real frame, which is the only place the acquisition half and the
    biological half can be seen to come from the same FOV-group. The wrong
    implementation that passes "a dict came back" is one that returns ``{}``
    or one whose values are all ``None``, so each key is asserted to be a
    number and ``nuc_count`` is asserted against the synthetic truth.
    """
    records, _, quality = _run(tmp_path)
    from pipeline.field_quality import METRIC_KEYS

    assert set(METRIC_KEYS) <= set(quality)
    for key in METRIC_KEYS:
        assert quality[key] is not None, f"{key} was not measured"
    assert quality["nuc_count"] == len(records)
    # The address, so a field in the block joins a row in the CSV.
    assert quality["fov"] == "1"
    assert str(quality["timepoint"]) == "0"
    # The synthetic frame is written as uint16 and is nowhere near clipping,
    # so saturation is measured AND is zero — which is a different statement
    # from "could not be measured", and the one the ceiling exists to make.
    assert quality["sat_frac"] == 0.0
    assert quality["focus"] > 0.0
    assert quality["dyn_range"] > 0.0


def test_pipeline_writes_output_images(tmp_path, stardist_worker):
    _, out, _ = _run(tmp_path)
    written = {p.name for p in out.iterdir()}
    # Label image + per-channel top-hat outputs + overlay.
    assert any(n.endswith("_labels.tif") for n in written)
    assert any(n.endswith("_overlay.png") for n in written)
    assert any("DAPI" in n and n.endswith("_tophat.tif") for n in written)
    assert any("GFP" in n and n.endswith("_tophat.tif") for n in written)


# ── Directory-level (multi-well) pipeline: folder discovery -> CSV assembly ───
#
# These drive ``process_well_folder_task`` — the picklable per-well lifecycle the
# worker pool dispatches — directly (in-process, after one _worker_init), so the
# higher orchestration above ``process_image_group`` is exercised: FOV-group
# discovery in a well folder, multi-FOV processing, per-well CSV assembly, and
# the staging -> final output-folder rename. Two wells confirm the partitioning.

SCHEMA = "experiment:channel:well:fov:timepoint".split(":")


def _template():
    return dict(
        fluor_tokens=["GFP"],
        nuclear_token="DAPI",
        tophat_nir=False, tophat_radius_nir=50,
        tophat_fluor=[False], tophat_radius_fluor=[50],
        schema=SCHEMA, sep="_",
        segmentation_method="stardist_nuclei",
        background_method="none", flatfield_mode="none",
        min_nucleus_area_px=20,
    )


def _run_well(well, n_fovs, in_root, out_dir, worker):
    folder = write_synthetic_well_folder(in_root, well=well, n_fovs=n_fovs)
    return pm.process_well_folder_task(
        well, folder, out_dir, _template(), "DAPI", ["GFP"], "measurements",
        schema=SCHEMA, sep="_",
    )


def test_directory_pipeline_assembles_per_well_csvs(tmp_path, stardist_worker):
    import csv as _csv
    in_root = tmp_path / "input"
    out = tmp_path / "out"
    out.mkdir()

    recs_a, failed_a = _run_well("A01", 2, in_root, out, stardist_worker)
    recs_b, failed_b = _run_well("B02", 1, in_root, out, stardist_worker)

    assert failed_a == [] and failed_b == []
    # 9 nuclei per FOV: A01 has 2 FOVs (18), B02 has 1 (9).
    assert len(recs_a) >= 16 and len(recs_b) >= 8

    csv_a = out / "measurements_A01.csv"
    csv_b = out / "measurements_B02.csv"
    assert csv_a.exists() and csv_b.exists()

    rows_a = list(_csv.DictReader(csv_a.open()))
    rows_b = list(_csv.DictReader(csv_b.open()))
    # CSV row counts match the returned records; A01 spans both FOVs.
    assert len(rows_a) == len(recs_a)
    assert sorted({r["fov"] for r in rows_a}) == ["1", "2"]
    assert all(r["well"] == "A01" for r in rows_a)
    assert all(r["well"] == "B02" for r in rows_b)

    # Per-well output is compressed to <well>_out.zip (compression is always
    # on — folder mode zips the staged output folder before returning).
    import zipfile as _zipfile
    for well in ("A01", "B02"):
        out_zip = out / f"{well}_out.zip"
        assert out_zip.exists() and out_zip.stat().st_size > 0
        assert not (out / well).exists()  # staging folder consumed by the zip
    with _zipfile.ZipFile(out / "A01_out.zip") as zf:
        assert any(n.endswith("_labels.tif") for n in zf.namelist())


def test_pipeline_info_sidecar_written(tmp_path, stardist_worker):
    import json
    in_root = tmp_path / "input"
    out = tmp_path / "out"
    out.mkdir()
    _run_well("A01", 1, in_root, out, stardist_worker)

    path = pm.write_pipeline_info(
        out,
        input_dir=in_root,
        filename_schema="experiment:channel:well:fov:timepoint",
        filename_sep="_",
        nuclear_token="DAPI",
        fluor_tokens=["GFP"],
        smfish_tokens=[],
        segmentation_method="stardist_nuclei",
        cytoplasm_token="",
        min_nucleus_area_px=20,
    )
    assert path == out / "pipeline_info.json"
    info = json.loads(path.read_text())
    assert info["nuclear_token"] == "DAPI"
    assert info["segmentation_method"] == "stardist_nuclei"
    assert "GFP" in info["fluor_tokens"]
    assert info["schema"] == "experiment:channel:well:fov:timepoint"
