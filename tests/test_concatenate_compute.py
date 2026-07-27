"""Fast-lane tests for the Concatenate Datasets planning + execution.

Both the pure planner (:mod:`datasets.concatenate.concatenate_compute`) and the
Qt-free executor (:mod:`datasets.concatenate.apply`) are exercised on tiny
``tmp_path`` datasets — no Qt, no app, so this stays in the fast lane. The Qt
``QThread`` wrapper (``worker.py``) is deliberately not imported here.
"""

from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path

import pytest

from datasets.concatenate import apply
from datasets.concatenate import concatenate_compute as cc


# ── fixtures ─────────────────────────────────────────────────────────────────
_BASE_INFO = {
    "schema": "experiment:channel:well:fov:timepoint",
    "separator": "_",
    "schema_fields": ["experiment", "channel", "well", "fov", "timepoint"],
    "well_index": 2,
    "channel_index": 1,
    "fov_index": 3,
    "tp_index": 4,
    "nuclear_token": "nir",
    "segmentation_method": "stardist_nuclei",
    "fluor_tokens": ["nir", "gfp"],
    "smfish_tokens": [],
    "shape_features": [],
    "available_fovs": ["F001"],
}


def _make_dataset(root: Path, *, wells, timepoints, info_extra=None) -> Path:
    """Build a minimal dataset (in/ zips, out/ CSVs + QC zips + sidecar)."""
    in_dir = root / "in"
    out_dir = root / "out"
    in_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)

    info = dict(_BASE_INFO)
    info["available_timepoints"] = list(timepoints)
    if info_extra:
        info.update(info_extra)
    (out_dir / "pipeline_info.json").write_text(json.dumps(info))

    for well in wells:
        # in/ per-well zip with one TIF per timepoint
        with zipfile.ZipFile(in_dir / f"{well}.zip", "w") as zf:
            for tp in timepoints:
                zf.writestr(f"exp_GFP_{well}_F001_{tp}.tif", b"img")
        # out/ QC zip
        with zipfile.ZipFile(out_dir / f"{well}_out.zip", "w") as zf:
            for tp in timepoints:
                zf.writestr(f"exp_GFP_{well}_F001_{tp}_labels.tif", b"mask")
        # out/ measurements CSV
        with (out_dir / f"measurements_{well}.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["well", "fov", "timepoint", "nucleus_id"])
            writer.writeheader()
            for tp in timepoints:
                writer.writerow({"well": well, "fov": "F001", "timepoint": tp, "nucleus_id": 1})
    return root


# ── timepoint math ───────────────────────────────────────────────────────────
def test_timepoint_map_continues_after_last_plus_interval():
    info1 = {"available_timepoints": ["0", "24h", "48h"]}
    info2 = {"available_timepoints": ["0", "24h"]}
    mapping, offset, last, interval = cc.build_timepoint_map(info1, info2)
    assert last == 48.0
    assert interval == 24.0          # median gap
    assert offset == 72.0            # ds2 first (0) -> 48 + 24
    assert mapping == {"0": "72h", "24h": "96h"}


def test_interval_uses_median_gap():
    # gaps 24, 24, 48 -> median 24
    info1 = {"available_timepoints": ["0", "24h", "48h", "96h"]}
    info2 = {"available_timepoints": ["0"]}
    _mapping, offset, last, interval = cc.build_timepoint_map(info1, info2)
    assert last == 96.0
    assert interval == 24.0
    assert offset == 120.0           # 96 + 24 - 0


def test_single_timepoint_dataset1_raises():
    with pytest.raises(cc.ConcatError, match="only one timepoint"):
        cc.build_timepoint_map({"available_timepoints": ["0"]},
                               {"available_timepoints": ["0", "24h"]})


def test_unparseable_timepoint_raises():
    with pytest.raises(cc.ConcatError, match="can't be read as a time"):
        cc.build_timepoint_map({"available_timepoints": ["0", "24h"]},
                               {"available_timepoints": ["treated_arm"]})


def test_new_labels_round_trip_and_stay_after_ds1():
    from util.timepoint import parse_hours
    info1 = {"available_timepoints": ["0", "36h"]}   # interval 36, last 36
    info2 = {"available_timepoints": ["0", "12h"]}
    mapping, _offset, last, _interval = cc.build_timepoint_map(info1, info2)
    for new_label in mapping.values():
        assert parse_hours(new_label) > last        # strictly after ds1


# ── compatibility ────────────────────────────────────────────────────────────
def test_incompatible_schema_refused():
    with pytest.raises(cc.ConcatError, match="not analysed the same way"):
        cc.check_compatible({"tp_index": 4}, {"tp_index": 3})


def test_compatible_schema_passes():
    cc.check_compatible(dict(_BASE_INFO), dict(_BASE_INFO))  # no raise


# ── sidecar merge ────────────────────────────────────────────────────────────
def test_merge_info_unions_and_keeps_user_blocks():
    info1 = dict(_BASE_INFO)
    info1["available_timepoints"] = ["0", "24h"]
    info1["cell_gating"] = {"thresh_frac_on": {"gfp": 5.0}}
    info2 = dict(_BASE_INFO)
    info2["available_timepoints"] = ["0", "24h"]
    info2["fluor_tokens"] = ["nir", "gfp", "rfp"]
    tp_map = {"0": "48h", "24h": "72h"}

    merged = cc.merge_info(info1, info2, tp_map)
    assert merged["available_timepoints"] == ["0", "24h", "48h", "72h"]
    assert merged["fluor_tokens"] == ["nir", "gfp", "rfp"]     # union preserves order
    assert merged["cell_gating"] == {"thresh_frac_on": {"gfp": 5.0}}   # from ds1


# ── filename rewrite ─────────────────────────────────────────────────────────
def test_rewrite_timepoint_in_name():
    tp_map = {"24h": "96h"}
    assert cc.rewrite_timepoint_in_name("exp_GFP_A01_F001_24h.tif", "_", 4, tp_map) == \
        "exp_GFP_A01_F001_96h.tif"
    # a timepoint not in the map passes through unchanged
    assert cc.rewrite_timepoint_in_name("exp_GFP_A01_F001_0.tif", "_", 4, tp_map) == \
        "exp_GFP_A01_F001_0.tif"


# ── dataset resolution / enumeration ─────────────────────────────────────────
def test_resolve_dataset_requires_in_and_out(tmp_path):
    (tmp_path / "in").mkdir()
    with pytest.raises(cc.ConcatError, match="not a dataset"):
        cc.resolve_dataset(tmp_path, label="dataset 1")


def test_dataset_wells_enumerated(tmp_path):
    _make_dataset(tmp_path, wells=["A01", "B02"], timepoints=["0", "24h"])
    paths = cc.resolve_dataset(tmp_path, label="d")
    assert cc.dataset_wells(paths) == {"A01", "B02"}


# ── end-to-end plan + execute ────────────────────────────────────────────────
def test_build_merge_plan_partitions_wells(tmp_path):
    _make_dataset(tmp_path / "d1", wells=["A01", "B02"], timepoints=["0", "24h", "48h"])
    _make_dataset(tmp_path / "d2", wells=["B02", "C03"], timepoints=["0", "24h"])
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest")
    assert plan.wells_ds1_only == ("A01",)
    assert plan.wells_ds2_only == ("C03",)
    assert plan.wells_both == ("B02",)
    assert plan.offset_hours == 72.0


def test_execute_plan_stitches_shared_well(tmp_path):
    _make_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0", "24h", "48h"])
    _make_dataset(tmp_path / "d2", wells=["A01"], timepoints=["0", "24h"])
    dest = tmp_path / "dest"
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", dest)
    summary = apply.execute_plan(plan)

    assert "1 well" in summary
    # merged CSV: 3 ds1 rows (0/24h/48h) + 2 ds2 rows remapped to 72h/96h
    with (dest / "out" / "measurements_A01.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    timepoints = [r["timepoint"] for r in rows]
    assert timepoints == ["0", "24h", "48h", "72h", "96h"]

    # in/ zip carries ds1 members verbatim + ds2 members with remapped tp tokens
    with zipfile.ZipFile(dest / "in" / "A01.zip") as zf:
        names = set(zf.namelist())
    assert "exp_GFP_A01_F001_48h.tif" in names       # ds1 unchanged
    assert "exp_GFP_A01_F001_72h.tif" in names       # ds2 (was 0)
    assert "exp_GFP_A01_F001_96h.tif" in names       # ds2 (was 24h)

    # merged sidecar extends the timeline
    info = json.loads((dest / "out" / "pipeline_info.json").read_text())
    assert info["available_timepoints"] == ["0", "24h", "48h", "72h", "96h"]


def test_execute_plan_copies_unique_wells(tmp_path):
    _make_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0", "24h", "48h"])
    _make_dataset(tmp_path / "d2", wells=["B02"], timepoints=["0", "24h"])
    dest = tmp_path / "dest"
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", dest)
    apply.execute_plan(plan)

    # ds1-only well unchanged
    with (dest / "out" / "measurements_A01.csv").open(newline="") as handle:
        assert [r["timepoint"] for r in csv.DictReader(handle)] == ["0", "24h", "48h"]
    # ds2-only well shifted by the offset (72h)
    with (dest / "out" / "measurements_B02.csv").open(newline="") as handle:
        assert [r["timepoint"] for r in csv.DictReader(handle)] == ["72h", "96h"]


def test_destination_cannot_equal_a_source(tmp_path):
    _make_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0", "24h"])
    _make_dataset(tmp_path / "d2", wells=["A01"], timepoints=["0", "24h"])
    with pytest.raises(cc.ConcatError, match="different folder"):
        cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "d1")
