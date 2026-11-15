"""The field-quality record is built from what the run actually measured.

The full-app review (pull request #1343), rows C3 and C10: the record's median nuclear
area read a key no cell record carries, so it was ``None`` on every field of every run; its nucleus count was
the largest label id, which over-counts whenever the ids have gaps; and a cell with no perimeter was given a
circularity of 0.0.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

import process_microscopy as pm
from pipeline import field_quality as fq
from util.schema import DEFAULT_SCHEMA, parse_schema


def _field():
    labels = np.zeros((128, 128), np.int32)
    labels[10:22, 10:22] = 1          # 144 px
    labels[40:60, 40:60] = 2          # 400 px
    labels[80:110, 80:110] = 5        # 900 px — ids 3 and 4 unused, as seeded watershed leaves them
    labels[120, 120] = 9              # one pixel: no perimeter
    rng = np.random.default_rng(0)
    nir = ((labels > 0) * 800.0 + 100 + rng.normal(0, 2, labels.shape)).astype(np.float32)
    records = pm._quantify_image_group(
        labels=labels, fluor_tophat=[nir], fluor_tokens=["GFP"], nir_corr=nir,
        nuclear_path=Path("exp_DAPI_A01_f1_t0.tif"), schema=parse_schema(DEFAULT_SCHEMA), sep="_",
        shape_features=["circularity"])
    return labels, nir, records


def test_the_record_s_areas_are_the_cells_areas():
    _labels, _nir, records = _field()
    assert sorted(fq.record_areas(records)) == [1.0, 144.0, 400.0, 900.0]


def test_the_median_area_and_the_count_describe_the_rows_written():
    labels, nir, records = _field()
    record = fq.group_record(
        acquisition={}, labels=labels, corrected_image=nir, areas=fq.record_areas(records),
        cell_focus=[r.get(fq.FOCUS_COLUMN) for r in records], fov="f1", timepoint="t0")
    assert record["nuc_area_median"] == 272.0            # median of 1, 144, 400, 900
    assert record["nuc_count"] == len(records) == 4      # not 9, the largest label id


def test_a_cell_with_no_perimeter_has_no_circularity():
    _labels, _nir, records = _field()
    by_id = {r["nucleus_id"]: r for r in records}
    assert by_id[9]["shape_circularity"] is None
    assert 0.5 < by_id[2]["shape_circularity"] <= 1.3     # a real cell still has one
