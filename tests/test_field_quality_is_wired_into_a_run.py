"""The path from one FOV-group's pixels to the block in ``pipeline_info.json``.

``tests/test_field_quality.py`` measures the statistics and
``tests/test_pipeline_e2e.py`` drives ``process_image_group`` against a real
StarDist — but that file is skipped wherever TensorFlow is not installed, which
is everywhere CI runs. So the wiring itself, which is where this feature can be
complete and still measure nothing, is pinned here with the segmenter stubbed
and the loader real.

CLAUDE.md §7.7 is the shape of every test below: a branch that exists is not a
branch the app takes. Each one names the wrong implementation it excludes.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from tifffile import imwrite

import process_microscopy as pm
from pipeline.field_quality import (
    ACQUISITION_METRICS, BIOLOGICAL_METRICS, FIELD_QUALITY_BLOCK,
    METRIC_KEYS, collect_field_quality,
)

SCHEMA = ["experiment", "channel", "well", "fov", "timepoint"]


def _frame(n=96, *, cells=40, seed=0, dtype=np.uint16):
    """A uint16 field with nuclei, noise and a pedestal, written as a camera
    would write it — so the ceiling the metrics need genuinely exists."""
    r = np.random.default_rng(seed)
    img = np.full((n, n), 300.0)
    yy, xx = np.mgrid[0:n, 0:n]
    for y, x in zip(r.integers(8, n - 8, cells), r.integers(8, n - 8, cells)):
        img += 2500.0 * np.exp(-((yy - y) ** 2 + (xx - x) ** 2) / 8.0)
    img += r.normal(0, 25.0, img.shape)
    return np.clip(img, 0, np.iinfo(dtype).max).astype(dtype)


def _write_group(tmp_path, well="A01", fov="1", tp="0"):
    raw = tmp_path / "raw"
    raw.mkdir(exist_ok=True)
    nuc = raw / f"exp_DAPI_{well}_f{fov}_t{tp}.tif"
    flu = raw / f"exp_GFP_{well}_f{fov}_t{tp}.tif"
    imwrite(str(nuc), _frame(seed=1))
    imwrite(str(flu), _frame(seed=2))
    return nuc, flu


@pytest.fixture
def stubbed_segmenter(monkeypatch):
    """Everything downstream of the loader, stubbed — the loader itself is not.

    The point of this file is the half that reads pixels, so
    ``_load_image_group`` runs for real (it is where the acquisition metrics
    are measured and where the camera ceiling still exists). What is replaced
    is StarDist, the image writes and the quantification, none of which this
    file has anything to say about.
    """
    labels = np.zeros((96, 96), np.int32)
    labels[10:20, 10:20] = 1
    labels[40:50, 40:50] = 2
    labels[70:80, 70:80] = 3
    monkeypatch.setattr(
        pm, "_segment_image_group", lambda **k: (labels, None, None, None))
    monkeypatch.setattr(pm, "_write_processed_images", lambda **k: None)
    # The records carry the per-cell focus column the real quantifier emits,
    # because the field's ``focus`` is now the median of exactly these values
    # rather than anything measured from the frame.
    from pipeline.field_quality import FOCUS_COLUMN
    monkeypatch.setattr(
        pm, "_quantify_image_group",
        lambda **k: [{"nucleus_id": i, "area": 100.0 * i,
                      FOCUS_COLUMN: 0.30 + 0.01 * i} for i in (1, 2, 3)])
    return labels


def _run_group(nuc, flu, out):
    return pm.process_image_group(
        nuclear_path=nuc, fluor_paths=[flu], fluor_tokens=["GFP"],
        output_dir=out, nuclear_token="DAPI", tophat_nir=False,
        tophat_radius_nir=50, tophat_fluor=[False], tophat_radius_fluor=[50],
        schema=SCHEMA, sep="_", segmentation_method="stardist_nuclei",
        background_method="none", flatfield_mode="none",
    )


# ── one FOV-group ───────────────────────────────────────────────────────────

def test_a_group_returns_a_quality_record_with_every_metric_measured(
        tmp_path, stubbed_segmenter):
    """Excludes the contract change that returns an empty mapping.

    A dict came back is not a measurement, and neither is a dict of ``None``.
    Every declared metric must be a number here, because this fixture gives
    every one of them something to measure.
    """
    nuc, flu = _write_group(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    records, quality = _run_group(nuc, flu, out)

    assert len(records) == 3
    assert set(METRIC_KEYS) <= set(quality)
    missing = [k for k in METRIC_KEYS if quality[k] is None]
    assert not missing, f"not measured: {missing}"
    assert quality["nuc_count"] == 3
    assert quality["nuc_area_median"] == 200.0
    # The fov token is the filename's own -- "f1", not "1". The address in
    # the block is the same string the CSV rows carry, which is the point:
    # a field here and a row there join without a second spelling.
    assert quality["fov"] == "f1" and str(quality["timepoint"]) == "t0"


def test_saturation_is_measured_rather_than_defeated_by_the_float_cast(
        tmp_path, stubbed_segmenter):
    """Excludes the ``sat_frac`` that is 0.0 for every field of every plate.

    ``load_2d_gray`` casts to ``float32`` unconditionally, so "at the dtype
    maximum" would mean "at 3.4e38" — identically zero everywhere, for ever,
    and indistinguishable from a plate with no clipping. The wrong
    implementation that passes "sat_frac is a number" is exactly that one, so
    this writes a frame that IS clipped and asserts the fraction it clipped.
    """
    raw = tmp_path / "raw"
    raw.mkdir()
    arr = _frame(seed=3)
    arr[:10, :] = np.iinfo(np.uint16).max          # 10 of 96 rows at ceiling
    nuc = raw / "exp_DAPI_A01_f1_t0.tif"
    flu = raw / "exp_GFP_A01_f1_t0.tif"
    imwrite(str(nuc), arr)
    imwrite(str(flu), _frame(seed=4))
    out = tmp_path / "out"
    out.mkdir()

    _records, quality = _run_group(nuc, flu, out)
    assert quality["sat_frac"] == pytest.approx(10 / 96, abs=1e-6)


def test_the_fields_focus_is_the_median_of_its_cells_own_outlines(
        tmp_path, stubbed_segmenter):
    """Excludes the whole-frame focus statistic this replaced.

    The first implementation measured a normalised Tenengrad over the entire
    frame, and two measurements retired it: nucleus size moved it 7.8× over a
    fourfold radius range against a defocus signal of 3.7×, and a frame with
    half of it blurred at σ=2 moved it only 14 %. The field's number is now
    the median of what each nucleus's own outline reported, and each of those
    is a property of the optics rather than of the cell.

    The wrong implementation that would still pass "focus is a number" is any
    function of the image, so this asserts the **exact median of the stubbed
    per-cell values** — which no image statistic can produce by chance.
    """
    nuc, flu = _write_group(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    records, quality = _run_group(nuc, flu, out)

    from pipeline.field_quality import FOCUS_COLUMN
    cells = sorted(r[FOCUS_COLUMN] for r in records)
    assert quality["focus"] == pytest.approx(cells[len(cells) // 2])
    assert quality["focus"] == pytest.approx(0.32)


def test_the_focus_column_is_absent_rather_than_zero_where_unmeasurable(
        tmp_path, stubbed_segmenter, monkeypatch):
    """A cell nobody could measure and a cell measured and found soft are
    different facts (§7 #3), and so are a field of each."""
    from pipeline.field_quality import FOCUS_COLUMN

    monkeypatch.setattr(
        pm, "_quantify_image_group",
        lambda **k: [{"nucleus_id": i, "area": 100.0, FOCUS_COLUMN: None}
                     for i in (1, 2, 3)])
    nuc, flu = _write_group(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    _records, quality = _run_group(nuc, flu, out)
    assert quality["focus"] is None, "no cell measurable is not focus zero"


def test_the_real_quantifier_emits_a_focus_value_per_cell(tmp_path):
    """Excludes the column that is declared and never written.

    Drives the real ``_quantify_image_group`` — no stub — against a frame with
    three discs, and asserts every cell got a number. The wrong implementation
    this excludes is the one where the per-cell measurement lives in
    ``field_quality`` and nothing in the pipeline calls it.
    """
    from scipy.ndimage import gaussian_filter
    from pipeline.field_quality import FOCUS_COLUMN

    n = 96
    yy, xx = np.mgrid[0:n, 0:n]
    labels = np.zeros((n, n), np.int32)
    img = np.full((n, n), 300.0)
    for k, (cy, cx) in enumerate(((24, 24), (24, 70), (70, 48)), start=1):
        d = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
        labels[d <= 9] = k
        img += 2500.0 * (d <= 9)
    img = gaussian_filter(img, 0.8)

    records = pm._quantify_image_group(
        labels=labels, fluor_tophat=[img], fluor_tokens=["GFP"],
        nir_corr=img, nuclear_path=Path("exp_DAPI_A01_f1_t0.tif"),
        schema=SCHEMA, sep="_", shape_features=[])
    assert len(records) == 3
    got = [r.get(FOCUS_COLUMN) for r in records]
    assert all(v is not None and v > 0 for v in got), got

    # And a blurred copy of the same frame reports lower, which is the whole
    # point — a column that is present but constant would pass the line above.
    soft = pm._quantify_image_group(
        labels=labels, fluor_tophat=[img], fluor_tokens=["GFP"],
        nir_corr=gaussian_filter(img, 2.5),
        nuclear_path=Path("exp_DAPI_A01_f1_t0.tif"),
        schema=SCHEMA, sep="_", shape_features=[])
    assert max(r[FOCUS_COLUMN] for r in soft) < min(got)


# ── the well, and the gap a resume must not hide ────────────────────────────

def test_a_well_records_every_field_it_measured(tmp_path, stubbed_segmenter):
    """Excludes the worker that computes quality per group and drops it."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "staging").mkdir()
    groups = []
    for fov in ("1", "2", "3"):
        nuc, flu = _write_group(tmp_path, fov=fov)
        groups.append((nuc, [flu]))

    pm._process_groups_in_worker(
        groups, dict(fluor_tokens=["GFP"], output_dir=out / "staging",
                     nuclear_token="DAPI", tophat_nir=False,
                     tophat_radius_nir=50, tophat_fluor=[False],
                     tophat_radius_fluor=[50], schema=SCHEMA, sep="_",
                     segmentation_method="stardist_nuclei",
                     background_method="none", flatfield_mode="none"),
        "A01", errors_dir=out)

    block = collect_field_quality(out)
    assert set(block["wells"]) == {"A01"}
    rec = block["wells"]["A01"]
    assert rec["measured"] == 3 and rec["measured_of"] == 3
    assert sorted(f["fov"] for f in rec["fields"]) == ["f1", "f2", "f3"]
    assert block["partial_wells"] == []


def test_a_well_whose_groups_failed_declares_the_shortfall(
        tmp_path, stubbed_segmenter, monkeypatch):
    """Excludes the block that reports two fields as though they were the well.

    This is the defect the whole record layer exists for. Two of three groups
    fail; the well still writes a CSV and still looks complete on the plate
    map, and a quality block covering only the survivor — scored plate-wide —
    is a robust z computed against a subset chosen by which frames happened to
    break. The wrong implementation that passes "a record was written" is the
    one that sets ``measured_of`` to the number it managed.
    """
    out = tmp_path / "out"
    out.mkdir()
    (out / "staging").mkdir()
    groups = []
    for fov in ("1", "2", "3"):
        nuc, flu = _write_group(tmp_path, fov=fov)
        groups.append((nuc, [flu]))

    real = pm.process_image_group

    def _fails_two(nuclear_path, fluor_paths, **kwargs):
        if "_f1_" not in Path(nuclear_path).name:
            raise ValueError("bad frame")
        return real(nuclear_path, fluor_paths, **kwargs)

    monkeypatch.setattr(pm, "process_image_group", _fails_two)
    _recs, failed, _reason, _skipped = pm._process_groups_in_worker(
        groups, dict(fluor_tokens=["GFP"], output_dir=out / "staging",
                     nuclear_token="DAPI", tophat_nir=False,
                     tophat_radius_nir=50, tophat_fluor=[False],
                     tophat_radius_fluor=[50], schema=SCHEMA, sep="_",
                     segmentation_method="stardist_nuclei",
                     background_method="none", flatfield_mode="none"),
        "A01", errors_dir=out)

    assert len(failed) == 2
    block = collect_field_quality(out)
    rec = block["wells"]["A01"]
    assert rec["measured"] == 1
    assert rec["measured_of"] == 3, "the well HAS three fields, whatever ran"
    assert block["partial_wells"] == ["A01"]


def test_a_partly_measured_plate_is_refused_rather_than_scored(
        tmp_path, stubbed_segmenter, monkeypatch):
    """The end of the same thread: the Review-side scorer must decline.

    A block that says it is partial and a scorer that averages it anyway is
    the silent substitution with one extra step in it.
    """
    from pipeline.field_quality import records_for_scoring
    from review.domain.field_quality_scores import summarise

    out = tmp_path / "out"
    out.mkdir()
    (out / "staging").mkdir()
    groups = []
    for fov in ("1", "2", "3"):
        nuc, flu = _write_group(tmp_path, fov=fov)
        groups.append((nuc, [flu]))

    real = pm.process_image_group
    monkeypatch.setattr(
        pm, "process_image_group",
        lambda n, f, **k: (real(n, f, **k) if "_f1_" in Path(n).name
                           else (_ for _ in ()).throw(ValueError("bad"))))
    pm._process_groups_in_worker(
        groups, dict(fluor_tokens=["GFP"], output_dir=out / "staging",
                     nuclear_token="DAPI", tophat_nir=False,
                     tophat_radius_nir=50, tophat_fluor=[False],
                     tophat_radius_fluor=[50], schema=SCHEMA, sep="_",
                     segmentation_method="stardist_nuclei",
                     background_method="none", flatfield_mode="none"),
        "A01", errors_dir=out)

    summary = summarise(records_for_scoring(collect_field_quality(out)))
    assert summary.declined
    assert "only part of this plate" in summary.declined


# ── the block in the sidecar ────────────────────────────────────────────────

def test_the_block_reaches_pipeline_info_and_the_staging_dir_is_cleared(
        tmp_path, stubbed_segmenter):
    """Excludes the well records that are written and never collected.

    ``finalize_field_quality`` is the only thing that moves them, and without
    it every run would leave a hidden directory of JSON that nothing reads —
    the feature complete, and the dataset carrying no quality at all.
    """
    import logging
    from pipeline.field_quality import finalize_field_quality
    from pipeline.io import _safe_atomic_text_write

    out = tmp_path / "out"
    out.mkdir()
    (out / "staging").mkdir()
    nuc, flu = _write_group(tmp_path)
    pm._process_groups_in_worker(
        [(nuc, [flu])],
        dict(fluor_tokens=["GFP"], output_dir=out / "staging",
             nuclear_token="DAPI", tophat_nir=False, tophat_radius_nir=50,
             tophat_fluor=[False], tophat_radius_fluor=[50], schema=SCHEMA,
             sep="_", segmentation_method="stardist_nuclei",
             background_method="none", flatfield_mode="none"),
        "A01", errors_dir=out)

    info = out / "pipeline_info.json"
    info.write_text(json.dumps({"schema": ":".join(SCHEMA)}))
    finalize_field_quality(out, info, atomic_writer=_safe_atomic_text_write,
                           log=logging.getLogger("t"))

    got = json.loads(info.read_text())
    assert got["schema"] == ":".join(SCHEMA), "the merge kept what was there"
    block = got[FIELD_QUALITY_BLOCK]
    assert set(block["wells"]) == {"A01"}
    assert block["wells"]["A01"]["measured"] == 1
    # And the drops are gone, so a second run cannot inherit them.
    assert not (out / ".aw_field_quality").exists()


def test_the_two_metric_groups_both_reach_the_block(tmp_path, stubbed_segmenter):
    """The split is the feature. A block carrying only the half measured in
    one place would be scored as though it carried both."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "staging").mkdir()
    nuc, flu = _write_group(tmp_path)
    pm._process_groups_in_worker(
        [(nuc, [flu])],
        dict(fluor_tokens=["GFP"], output_dir=out / "staging",
             nuclear_token="DAPI", tophat_nir=False, tophat_radius_nir=50,
             tophat_fluor=[False], tophat_radius_fluor=[50], schema=SCHEMA,
             sep="_", segmentation_method="stardist_nuclei",
             background_method="none", flatfield_mode="none"),
        "A01", errors_dir=out)

    field = collect_field_quality(out)["wells"]["A01"]["fields"][0]
    for key in ACQUISITION_METRICS + BIOLOGICAL_METRICS:
        assert field.get(key) is not None, f"{key} never reached the block"
