"""Degraded data must not be shaped like good data.

Four findings in the data layer shared one failure: something could not be
computed, and the value handed onward was a perfectly ordinary one that a
consumer had no way to question.

* a truncated archive scan returned a short inventory that looks exactly like a
  well which genuinely holds that few frames;
* a verdict that raised during a *re*-projection left the previous parameters'
  ``QC_Pass`` attached, so plots filtered by settings the UI no longer showed;
* a binary-mask threshold that could not be evaluated for a field returned an
  all-``False`` map, which every renderer paints as the same black silhouette it
  paints for "measured, and nothing passed";
* a well label the token parser cannot read disabled the loose-file per-well
  filter entirely, so the well rendered a *different* well's image.

Each fix gives the shortfall a shape of its own — a flag, an absent column, a
``None``, a skipped file — so nothing downstream can mistake it for a result.
"""
from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from well_viewer.data.cache_projectors import project_verdict_column
from well_viewer.data.zip_members import ScanResult, merge_partial


# ── item 23: a failed re-projection must not leave the old verdict ───────────

def _cache_ctx(df):
    return SimpleNamespace(state=SimpleNamespace(dataset=SimpleNamespace(cache={"A01": df})))


def test_a_successful_projection_still_writes_the_column():
    df = pd.DataFrame({"nid": [1, 2, 3]})
    failed = project_verdict_column(
        _cache_ctx(df), "A01", column="QC_Pass",
        verdict=lambda d, lbl: pd.Series([1, 1, 0]), layer="qc",
    )
    assert list(df["QC_Pass"]) == [1, 1, 0]
    assert failed == []


def test_a_failed_reprojection_drops_the_stale_verdict(caplog):
    df = pd.DataFrame({"nid": [1, 2, 3]})
    ctx = _cache_ctx(df)
    project_verdict_column(ctx, "A01", column="QC_Pass",
                           verdict=lambda d, lbl: pd.Series([1, 1, 0]), layer="qc")
    assert list(df["QC_Pass"]) == [1, 1, 0]

    def boom(_d, _lbl):
        raise KeyError("GFP_mean_intensity")

    with caplog.at_level(logging.ERROR, logger="well_viewer"):
        failed = project_verdict_column(ctx, "A01", column="QC_Pass",
                                        verdict=boom, layer="qc")
    # Keeping [1, 1, 0] would filter every plot by the PREVIOUS thresholds while
    # the UI shows the new ones, and look identical to a frame that had just been
    # recomputed and agreed.
    assert "QC_Pass" not in ctx.state.dataset.cache["A01"].columns
    assert failed == ["A01"]


def test_a_first_projection_that_fails_is_not_an_error():
    # Nothing to drop; ``errors="ignore"`` is the point.
    df = pd.DataFrame({"nid": [1]})
    failed = project_verdict_column(
        _cache_ctx(df), "A01", column="Crop_Pass",
        verdict=lambda d, lbl: (_ for _ in ()).throw(ValueError("no region")),
        layer="crop",
    )
    assert failed == ["A01"]
    assert "Crop_Pass" not in df.columns


# ── item 12: a partial scan says it is partial ───────────────────────────────

def _tif_bytes():
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(np.zeros((4, 4), dtype=np.uint16)).save(buf, format="TIFF")
    return buf.getvalue()


@pytest.fixture()
def well_zip(tmp_path):
    pytest.importorskip("PIL")
    zp = tmp_path / "A01.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for fov in range(3):
            z.writestr(f"A01_GFP_f{fov}_t0.tif", _tif_bytes())
    return zp


def test_a_complete_scan_is_not_flagged(well_zip):
    from well_viewer.data.image_discovery import scan_zip_members

    fluor, *_rest = scan_zip_members(well_zip, "gfp")
    assert isinstance(fluor, ScanResult)
    assert fluor.partial is False
    assert fluor.reason == ""


def test_a_truncated_zip_scan_says_it_is_truncated(well_zip, monkeypatch):
    import well_viewer.data.image_discovery as ID

    real = ID.classify_member
    seen = {"n": 0}

    def flaky(*a, **k):
        seen["n"] += 1
        if seen["n"] > 1:
            raise OSError("Bad CRC-32 for file 'A01_GFP_f1_t0.tif'")
        return real(*a, **k)

    monkeypatch.setattr(ID, "classify_member", flaky)
    fluor, *_rest = ID.scan_zip_members(well_zip, "gfp")
    # The inventory is the same SHAPE as a complete one — that is the whole
    # problem, and why the flag rather than the length has to carry the fact.
    assert fluor.partial is True
    assert "Bad CRC-32" in fluor.reason


def test_the_flag_survives_the_merge_and_the_sort():
    from well_viewer.data.image_discovery import _merge_scan

    truncated = ScanResult({("1", "0"): "ref"})
    truncated.mark_partial("scanning A01.zip failed: Bad CRC-32")
    targets = (ScanResult(), ScanResult(), ScanResult(), ScanResult())
    _merge_scan(targets, (truncated, ScanResult(), ScanResult(),
                          ScanResult(), ScanResult()))
    assert targets[0].partial is True

    # ``derive`` is what ``find_well_images_and_masks`` returns through; a plain
    # ``dict(sorted(...))`` would drop the flag and put the caller back where it
    # started.
    out = targets[0].derive(sorted(targets[0].items()))
    assert isinstance(out, ScanResult) and out.partial is True
    assert dict(out) == {("1", "0"): "ref"}


def test_merge_partial_leaves_a_complete_target_alone():
    target = ScanResult()
    merge_partial(target, ScanResult(), ScanResult())
    assert target.partial is False


# ── item 56: an unparseable label must not admit another well's image ────────

@pytest.fixture()
def loose_files(tmp_path):
    from PIL import Image

    for name in ("A01_GFP_f1_t0.tif", "B02_GFP_f1_t0.tif", "C03_GFP_f1_t0.tif"):
        Image.fromarray(np.zeros((4, 4), dtype=np.uint16)).save(
            tmp_path / name, format="TIFF")
    return tmp_path


def _names(refs):
    return sorted(Path(str(r.disk_path)).name for r in refs.values())


def test_a_parseable_label_still_finds_its_own_well(loose_files):
    from well_viewer.data.image_discovery import find_well_images_and_masks

    fluor, *_rest = find_well_images_and_masks(loose_files, "plate_A01", "GFP")
    assert _names(fluor) == ["A01_GFP_f1_t0.tif"]


def test_an_unparseable_label_does_not_admit_another_wells_image(loose_files):
    from well_viewer.data.image_discovery import find_well_images_and_masks

    # The three files collide on (fov, tp) and the merge is first-hit-wins, so
    # the unfiltered scan did not show a MIX — it showed A01's image, one frame,
    # under a label that is not A01, with nothing on screen to say so.
    fluor, *_rest = find_well_images_and_masks(loose_files, "measurements", "GFP")
    assert _names(fluor) == []


def test_files_naming_no_well_are_still_admitted_under_an_unparseable_label(tmp_path):
    from PIL import Image

    from well_viewer.data.image_discovery import find_well_images_and_masks

    # A single-well directory whose filenames carry no well field: nothing here
    # contradicts the label, so this is the case the fallback exists for and it
    # must keep working.
    for name in ("sample_GFP_t0.tif", "sample_GFP_t1.tif"):
        Image.fromarray(np.zeros((4, 4), dtype=np.uint16)).save(
            tmp_path / name, format="TIFF")
    fluor, *_rest = find_well_images_and_masks(tmp_path, "Sample-XYZ", "GFP")
    assert len(fluor) >= 1


# ── item 2: unmeasured is not "nothing above threshold" ──────────────────────

def _threshold_ctx(df):
    """The minimal ctx ``review_build_threshold_map`` reads (see
    ``test_review_image_draw_web_ctx``)."""
    return SimpleNamespace(
        state=SimpleNamespace(
            dataset=SimpleNamespace(cache={"A01": df}, well_paths={"A01": "x.csv"},
                                    pipeline_info={}),
            channel=SimpleNamespace(
                fluor_channels=["gfp"], active_image_channel="gfp",
                active_metric="mean_intensity",
                active_val_col="gfp_mean_intensity", active_channel="gfp",
                ratio_index={}, threshold=-1.0,
            ),
            cell_gating=SimpleNamespace(fluor_gates={},
                                        thresh_frac_on={"gfp": 50.0}),
            review_image=SimpleNamespace(image_source="corrected"),
            preview=SimpleNamespace(fluor={}, tophat_fluor={}, mask={}),
        ),
        runtime=SimpleNamespace(review=SimpleNamespace(included_overrides={})),
    )


_MASK = np.array([[0, 1, 2], [1, 1, 2]], dtype=np.int32)


def _rows(records):
    return pd.DataFrame(records, columns=["fov", "timepoint", "nucleus_id",
                                          "Included", "gfp_mean_intensity"])


def test_rows_that_cover_the_field_still_return_a_measured_map():
    from well_viewer.features.image_browser.review_image.collect import (
        review_build_threshold_map,
    )

    df = _rows([("1", "0", "1", "1", 10.0), ("1", "0", "2", "1", 20.0)])
    out = review_build_threshold_map(_threshold_ctx(df), _MASK, "A01", "1", "0")
    # Measured, and nothing passed. An all-False dict is the RIGHT answer here,
    # and it has to keep meaning only this.
    assert out == {1: False, 2: False}


def test_a_field_with_no_rows_is_unmeasured_not_empty():
    from well_viewer.features.image_browser.review_image.collect import (
        review_build_threshold_map,
    )

    # Rows exist for the well but cover FOV 9, not the displayed FOV 1.
    df = _rows([("9", "0", "1", "1", 80.0)])
    out = review_build_threshold_map(_threshold_ctx(df), _MASK, "A01", "1", "0")
    assert out is None


def test_a_well_with_no_rows_at_all_is_unmeasured():
    from well_viewer.features.image_browser.review_image.collect import (
        review_build_threshold_map,
    )

    out = review_build_threshold_map(_threshold_ctx(_rows([])), _MASK, "A01", "1", "0")
    assert out is None
