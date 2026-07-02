"""load_well_csv circularity backfill: derive shape_circularity from
shape_perimeter + an area column for datasets processed before circularity
became always-on, so the Segmentation QC circularity gate works without a
full reprocess.
"""

from __future__ import annotations

import math

import pytest

pd = pytest.importorskip("pandas")

from well_viewer.data.data_loading import detect_shape_features, load_well_csv


def _write(tmp_path, header, rows):
    p = tmp_path / "measurements_A01.csv"
    lines = [",".join(header)] + [",".join(str(v) for v in r) for r in rows]
    p.write_text("\n".join(lines) + "\n")
    return p


def test_circularity_derived_from_perimeter_and_area(tmp_path):
    # area=100, perimeter=40 → 4π·100/1600 ≈ 0.785
    p = _write(tmp_path,
               ["filename", "area_px", "shape_perimeter"],
               [["img_0.tif", 100, 40], ["img_1.tif", 314, 62.8]])
    df = load_well_csv(p)
    assert "shape_circularity" in df.columns
    assert df["shape_circularity"].iloc[0] == pytest.approx(4 * math.pi * 100 / (40 * 40))
    # ~circle: area 314, perimeter 62.8 → ≈ 1.0
    assert df["shape_circularity"].iloc[1] == pytest.approx(1.0, abs=0.02)
    # And it's now detected as an available shape feature.
    assert "shape_circularity" in detect_shape_features(df)


def test_circularity_prefers_shape_area(tmp_path):
    p = _write(tmp_path,
               ["filename", "area_px", "shape_area", "shape_perimeter"],
               [["i.tif", 999, 100, 40]])
    df = load_well_csv(p)
    assert df["shape_circularity"].iloc[0] == pytest.approx(4 * math.pi * 100 / 1600)


def test_no_backfill_without_perimeter(tmp_path):
    p = _write(tmp_path, ["filename", "area_px"], [["i.tif", 100]])
    df = load_well_csv(p)
    assert "shape_circularity" not in df.columns   # cannot derive → reprocess


def test_existing_circularity_not_overwritten(tmp_path):
    p = _write(tmp_path,
               ["filename", "area_px", "shape_perimeter", "shape_circularity"],
               [["i.tif", 100, 40, 0.5]])
    df = load_well_csv(p)
    assert df["shape_circularity"].iloc[0] == pytest.approx(0.5)


def test_zero_perimeter_is_nan_not_crash(tmp_path):
    p = _write(tmp_path,
               ["filename", "area_px", "shape_perimeter"],
               [["i.tif", 100, 0]])
    df = load_well_csv(p)
    assert "shape_circularity" in df.columns
    assert pd.isna(df["shape_circularity"].iloc[0])   # perimeter 0 → undefined


def test_zero_perimeter_yields_nan_without_warning(tmp_path, recwarn):
    # Audit S8: a zero-perimeter row must give NaN circularity (masked before
    # the divide) — no inf, no RuntimeWarning.
    import numpy as np
    p = _write(tmp_path,
               ["filename", "area_px", "shape_perimeter"],
               [["ok.tif", 100, 40], ["bad.tif", 50, 0]])
    df = load_well_csv(p)
    assert df["shape_circularity"].iloc[0] == pytest.approx(4 * math.pi * 100 / 1600)
    assert np.isnan(df["shape_circularity"].iloc[1])       # per==0 → NaN, not inf
    assert not any(issubclass(w.category, RuntimeWarning) for w in recwarn.list)
