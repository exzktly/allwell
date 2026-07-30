"""Fast-lane Qt-free tests for `datasets.subset.subset_compute` and
`datasets.subset.apply`.
"""

from __future__ import annotations

import csv
import json
import zipfile

import pytest

from datasets.subset import apply as subset_apply
from datasets.subset import subset_compute as sc
from tests.fixtures.dataset_builder import build_dataset


def _build(root, **kw):
    kw.setdefault("wells", ["A01", "B02", "C03"])
    kw.setdefault("timepoints", ["0h", "24h", "48h", "72h"])
    kw.setdefault("fovs", ("F001", "F002"))
    return build_dataset(root, **kw)


def test_subset_keeps_original_hours_no_rewriting(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src)
    filt = sc.SubsetFilter(
        wells=frozenset({"A01"}), timepoint_labels=frozenset({"24h", "48h"}),
        fovs=frozenset({"F001"}),
    )
    plan = sc.build_subset_plan(src, dest, filt)
    subset_apply.execute_plan(plan)

    with (src / "out" / "measurements_A01.csv").open() as f:
        src_rows = {r["filename"]: r for r in csv.DictReader(f)}
    with (dest / "out" / "measurements_A01.csv").open() as f:
        dest_rows = list(csv.DictReader(f))

    assert {r["timepoint"] for r in dest_rows} == {"24h", "48h"}
    assert {r["fov"] for r in dest_rows} == {"F001"}
    for row in dest_rows:
        src_row = src_rows[row["filename"]]
        assert row["timepoint"] == src_row["timepoint"]
        assert row["timepoint_hours"] == src_row["timepoint_hours"]
        assert row["filename"] == src_row["filename"]

    with zipfile.ZipFile(dest / "in" / "A01.zip") as zf:
        dest_members = set(zf.namelist())
    with zipfile.ZipFile(src / "in" / "A01.zip") as zf:
        src_members = set(zf.namelist())
    assert dest_members == {m for m in src_members if "_F001_" in m and (m.endswith("_24h.tif") or m.endswith("_48h.tif"))}


def test_source_untouched(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src)
    with zipfile.ZipFile(src / "in" / "A01.zip") as zf:
        before = set(zf.namelist())

    filt = sc.SubsetFilter(wells=frozenset({"A01"}), timepoint_labels=frozenset({"0h"}))
    plan = sc.build_subset_plan(src, dest, filt)
    subset_apply.execute_plan(plan)

    with zipfile.ZipFile(src / "in" / "A01.zip") as zf:
        after = set(zf.namelist())
    assert before == after


def test_only_selected_wells_kept(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src)
    filt = sc.SubsetFilter(wells=frozenset({"A01", "C03"}))
    plan = sc.build_subset_plan(src, dest, filt)
    assert plan.wells_kept == ("A01", "C03")
    subset_apply.execute_plan(plan)
    assert (dest / "out" / "measurements_A01.csv").exists()
    assert (dest / "out" / "measurements_C03.csv").exists()
    assert not (dest / "out" / "measurements_B02.csv").exists()


def test_no_filter_keeps_everything(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src)
    plan = sc.build_subset_plan(src, dest, sc.SubsetFilter())
    assert set(plan.wells_kept) == {"A01", "B02", "C03"}
    subset_apply.execute_plan(plan)
    with (src / "out" / "measurements_A01.csv").open() as f:
        src_rows = list(csv.DictReader(f))
    with (dest / "out" / "measurements_A01.csv").open() as f:
        dest_rows = list(csv.DictReader(f))
    assert len(dest_rows) == len(src_rows)


def test_destination_same_as_source_refused(tmp_path):
    src = tmp_path / "src"
    _build(src)
    with pytest.raises(sc.SubsetError, match="different folder"):
        sc.build_subset_plan(src, src, sc.SubsetFilter())


def test_destination_already_finished_dataset_refused(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src)
    _build(dest, wells=["Z09"])
    with pytest.raises(sc.SubsetError, match="already holds a finished dataset"):
        sc.build_subset_plan(src, dest, sc.SubsetFilter())


def test_filter_matching_nothing_refused(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src)
    with pytest.raises(sc.SubsetError, match="No wells match"):
        sc.build_subset_plan(src, dest, sc.SubsetFilter(wells=frozenset({"H12"})))


def test_verify_fails_on_zero_rows(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src, wells=["A01"])
    filt = sc.SubsetFilter(timepoint_labels=frozenset({"999h"}))  # matches nothing
    plan = sc.build_subset_plan(src, dest, filt)
    with pytest.raises(sc.SubsetError, match="zero measurement rows"):
        subset_apply.execute_plan(plan)
    assert not (dest / "out" / "pipeline_info.json").exists()


def test_marker_survives_cancel(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src, wells=["A01"])
    plan = sc.build_subset_plan(src, dest, sc.SubsetFilter())
    with pytest.raises(sc.SubsetError, match="Cancelled"):
        subset_apply.execute_plan(plan, is_cancelled=lambda: True)
    assert (dest / sc.IN_PROGRESS_MARKER).exists()
    assert not (dest / "out" / "pipeline_info.json").exists()


def test_cell_overrides_filtered_and_restamped(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src, wells=["A01"], timepoints=["0h", "24h"], fovs=("F001",))
    overrides = {
        "version": 2,
        "overrides": [
            {"well": "A01", "fov": "F001", "tp": "0h", "nucleus_id": "1",
             "included": "0", "seg_fp": "old"},
            {"well": "A01", "fov": "F001", "tp": "24h", "nucleus_id": "2",
             "included": "0", "seg_fp": "old"},
        ],
    }
    (src / "out" / "cell_overrides.json").write_text(json.dumps(overrides))

    filt = sc.SubsetFilter(timepoint_labels=frozenset({"0h"}))
    plan = sc.build_subset_plan(src, dest, filt)
    subset_apply.execute_plan(plan)

    result = json.loads((dest / "out" / "cell_overrides.json").read_text())
    assert len(result["overrides"]) == 1
    kept = result["overrides"][0]
    assert kept["tp"] == "0h"
    dest_zip = dest / "out" / "A01_out.zip"
    assert kept["seg_fp"] == f"{dest_zip.stat().st_mtime_ns}"
    assert kept["seg_fp"] != "old"


def test_restart_detection_and_clear(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src, wells=["A01"])
    plan = sc.build_subset_plan(src, dest, sc.SubsetFilter())
    with pytest.raises(sc.SubsetError):
        subset_apply.execute_plan(plan, is_cancelled=lambda: True)

    assert sc.destination_has_marker(sc.DatasetPaths(root=dest, in_dir=dest / "in", out_dir=dest / "out"))
    plan2 = sc.build_subset_plan(src, dest, sc.SubsetFilter())
    assert plan2.dest_is_restart
    subset_apply.execute_plan(plan2)
    assert (dest / "out" / "pipeline_info.json").exists()


def test_execute_plan_copies_many_wells_in_parallel(tmp_path):
    """Enough wells to force >1 worker (default auto-sizing) -- every well
    must land correctly with no cross-well interference, and every well must
    report exactly one copying + one terminal event."""
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    wells = [f"{r}{c:02d}" for r in "ABCD" for c in range(1, 5)]  # 16 wells
    _build(src, wells=wells, timepoints=["0h", "24h"], fovs=("F001",))

    events: list[tuple[str, str]] = []
    plan = sc.build_subset_plan(src, dest, sc.SubsetFilter())
    summary = subset_apply.execute_plan(plan, progress=lambda well, stage: events.append((well, stage)))
    assert f"{len(wells)} well(s)" in summary

    by_well: dict[str, list[str]] = {}
    for well, stage in events:
        by_well.setdefault(well, []).append(stage)
    assert set(by_well) == set(wells)
    for stages in by_well.values():
        assert stages == ["copying", "done"]

    for well in wells:
        with (dest / "out" / f"measurements_{well}.csv").open() as f:
            assert len(list(csv.DictReader(f))) == 2


def test_execute_plan_respects_explicit_worker_count(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _build(src, wells=["A01", "B02", "C03"], timepoints=["0h"], fovs=("F001",))
    plan = sc.build_subset_plan(src, dest, sc.SubsetFilter())
    summary = subset_apply.execute_plan(plan, workers=1)
    assert "3 well(s)" in summary
