"""Fast-lane Qt-free tests for `datasets.validate.validate_compute` and
`datasets.validate.apply`.

Every scenario builds a real dataset on disk via
``tests.fixtures.dataset_builder.build_dataset`` and then breaks one thing —
never a synthetic report object — so these tests exercise the same code path
Validate's own worker calls.
"""

from __future__ import annotations

import csv
import json
import zipfile

import pytest

from datasets.validate import validate_compute as vc
from datasets.validate.apply import apply_repairs
from tests.fixtures.dataset_builder import build_dataset


def _truncate_well_to_first_timepoint(root, well: str) -> None:
    """Simulate a crashed/partial Analyze run: keep only the first timepoint's
    images and CSV rows for *well*, leaving the sidecar untouched (as a real
    crash would — write_pipeline_info runs before any image work)."""
    out_zip = root / "out" / f"{well}_out.zip"
    with zipfile.ZipFile(out_zip) as zf:
        members = zf.namelist()
    keep = sorted(members)[: len(members) // 3 or len(members)]
    # Keep whichever timepoint the first kept member names, for both zip and CSV.
    first_tp_members = [m for m in members if m.split("_")[-1].split(".")[0] == "0h"]
    with zipfile.ZipFile(out_zip, "w") as zf:
        for m in first_tp_members:
            zf.writestr(m, b"x")

    csv_path = root / "out" / f"measurements_{well}.csv"
    with csv_path.open() as f:
        rows = list(csv.DictReader(f))
    fieldnames = list(rows[0].keys())
    rows = [r for r in rows if r["timepoint"] == "0h"]
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def test_healthy_dataset_reports_clean(tmp_path):
    build_dataset(tmp_path, wells=["A01", "B02"], timepoints=["0h", "24h", "48h"])
    report = vc.build_report(tmp_path)
    assert report.is_clean
    assert not report.repairable
    assert all(w.complete for w in report.wells)


def test_truncated_well_reports_exact_missing_pairs(tmp_path):
    build_dataset(tmp_path, wells=["A01"], timepoints=["0h", "24h", "48h"])
    _truncate_well_to_first_timepoint(tmp_path, "A01")

    report = vc.build_report(tmp_path)
    assert not report.is_clean
    well = next(w for w in report.wells if w.well == "A01")
    assert set(well.missing) == {("F001", "24h"), ("F001", "48h")}
    codes = {f.code for f in report.findings}
    assert "missing_fov_tp" in codes


def test_not_a_dataset_folder_raises_clear_error(tmp_path):
    with pytest.raises(vc.ValidateError, match="not a dataset"):
        vc.build_report(tmp_path)


def test_missing_sidecar_raises_clear_error(tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "out").mkdir()
    with pytest.raises(vc.ValidateError, match="pipeline_info.json"):
        vc.build_report(tmp_path)


def test_phantom_csv_flagged_not_counted_as_well_data(tmp_path):
    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    (tmp_path / "out" / "plate_A01.csv").write_text("notes\nsomething\n")
    report = vc.build_report(tmp_path)
    phantom = [f for f in report.findings if f.code == "phantom_csv"]
    assert len(phantom) == 1
    assert "plate_A01.csv" in phantom[0].message


def test_mixed_csv_prefix_flagged(tmp_path):
    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"], csv_prefix="measurements")
    (tmp_path / "out" / "run2_A01.csv").write_text(
        "well,fov,timepoint,timepoint_hours\nA01,F001,0h,0\n"
    )
    report = vc.build_report(tmp_path)
    codes = {f.code for f in report.findings}
    assert "mixed_csv_prefix" in codes


def test_absent_empty_and_unreadable_csv_are_distinct_findings(tmp_path):
    build_dataset(tmp_path, wells=["A01", "B02", "C03"], timepoints=["0h"])
    # A01: no CSV at all.
    (tmp_path / "out" / "measurements_A01.csv").unlink()
    # B02: CSV present, header only, zero rows.
    (tmp_path / "out" / "measurements_B02.csv").write_text(
        "filename,experiment,channel,well,fov,timepoint,timepoint_hours,nucleus_id,area_px\n"
    )
    # C03: CSV present but unparseable (binary garbage, invalid utf-8).
    (tmp_path / "out" / "measurements_C03.csv").write_bytes(b"\xff\xfe\x00\x01garbage")

    report = vc.build_report(tmp_path)
    states = {w.well: w.csv_state for w in report.wells}
    assert states["A01"] == "missing"
    assert states["B02"] == "empty"
    assert states["C03"] == "unreadable"


def test_folder_mode_output_counts_as_done_without_a_csv_row(tmp_path):
    """Regression guard: _well_done used to union only CSV rows and out-zip
    members, never a folder-mode well's output folder (out/<WELL>/, written
    when compressed_output=False) -- contradicting this module's own
    docstring, which claims to mirror
    pipeline.incremental_keys.incremental_done_fov_keys's three-way union
    (CSV rows |UNION| out-zip members |UNION| output folder). A folder-mode
    well that legitimately segmented zero cells at one timepoint still gets
    an image written to out/<WELL>/, but no measurements row -- exactly the
    case this module's own no_csv finding already treats as legitimate for
    zip-mode wells (an out-zip's image member supplies the "done" evidence
    there). Without reading the output folder too, a folder-mode well got
    no such evidence and was falsely reported as still missing that
    timepoint."""
    from tests.fixtures.dataset_builder import BASE_INFO, ROW_COLUMNS
    from util.timepoint import parse_hours

    exp, sep = "exp", "_"
    root = tmp_path
    in_dir, out_dir = root / "in", root / "out"
    in_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)
    info = dict(BASE_INFO)
    info["available_timepoints"] = ["0h", "24h"]
    info["available_fovs"] = ["F001"]
    (out_dir / "pipeline_info.json").write_text(json.dumps(info))

    # B02: folder-mode output (no _out.zip). Both 0h and 24h were actually
    # processed (an image exists for both in out/B02/), but 24h segmented
    # zero cells so it has no measurements row -- legitimate, not a gap.
    with zipfile.ZipFile(in_dir / "B02.zip", "w") as zf:
        for tp in ("0h", "24h"):
            zf.writestr(f"{exp}_GFP_B02_F001_{tp}.tif", b"img")
    b02_out = out_dir / "B02"
    b02_out.mkdir()
    for tp in ("0h", "24h"):
        (b02_out / f"{exp}_GFP_B02_F001_{tp}_labels.tif").write_bytes(b"mask")
    with (out_dir / "measurements_B02.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ROW_COLUMNS)
        writer.writeheader()
        writer.writerow({
            "filename": f"{exp}_GFP_B02_F001_0h.tif", "experiment": exp, "channel": "GFP",
            "well": "B02", "fov": "F001", "timepoint": "0h",
            "timepoint_hours": parse_hours("0h"), "nucleus_id": 1, "area_px": 150,
        })

    report = vc.build_report(root)
    b02 = next(w for w in report.wells if w.well == "B02")
    assert b02.has_out_folder is True
    assert b02.missing == (), f"expected no missing pairs, got {b02.missing}"


def test_no_fov_field_skips_fov_check_without_false_alarm(tmp_path):
    build_dataset(
        tmp_path, wells=["A01"], timepoints=["0h", "24h"],
        info_extra={"schema_fields": ["experiment", "channel", "well", "timepoint"]},
    )
    report = vc.build_report(tmp_path)
    assert not report.fov_field_present
    codes = {f.code for f in report.findings}
    assert "no_fov_field" in codes
    assert "missing_fov_tp" not in codes


def test_stale_available_timepoints_flagged_and_repaired(tmp_path):
    build_dataset(tmp_path, wells=["A01"], timepoints=["0h", "24h"])
    info_path = tmp_path / "out" / "pipeline_info.json"
    info = json.loads(info_path.read_text())
    info["available_timepoints"] = ["0h", "24h", "999h"]
    info["cell_gating"] = {"threshold": 5}
    info["sample_definitions"] = {"groups": ["control"]}
    info_path.write_text(json.dumps(info))

    report = vc.build_report(tmp_path)
    assert report.stale_available_timepoints == ("999h",)
    assert report.repairable

    result = apply_repairs(tmp_path, report)
    assert result.rebuilt_availability

    new_info = json.loads(info_path.read_text())
    assert new_info["available_timepoints"] == ["0h", "24h"]
    assert new_info["cell_gating"] == {"threshold": 5}
    assert new_info["sample_definitions"] == {"groups": ["control"]}

    report_after = vc.build_report(tmp_path)
    assert not report_after.stale_available_timepoints
    assert not report_after.repairable


def test_repair_rebuilds_correctly_on_a_legacy_schema_sidecar(tmp_path):
    """Regression guard: apply_repairs used to resolve schema_fields via a
    bare info.get("schema_fields") or [] -- unlike build_report, which
    resolves it through load_schema (legacy "schema" colon-string / index-only
    / default-fields fallbacks). A sidecar with no schema_fields key at all
    (only the legacy "schema" string) still diffs correctly in build_report,
    but apply_repairs's own schema_fields resolved to [], and
    collect_available_schema_values([], ...) returns [] unconditionally --
    silently wiping a real, correct available_timepoints list to empty
    instead of rebuilding it from what's actually on disk."""
    build_dataset(tmp_path, wells=["A01"], timepoints=["0h", "24h"])
    info_path = tmp_path / "out" / "pipeline_info.json"
    info = json.loads(info_path.read_text())
    assert info.get("schema") == "experiment:channel:well:fov:timepoint"
    del info["schema_fields"]  # legacy sidecar: only the colon-string form
    info["available_timepoints"] = ["0h", "24h", "999h"]
    info_path.write_text(json.dumps(info))

    report = vc.build_report(tmp_path)
    assert report.stale_available_timepoints == ("999h",)
    assert report.repairable

    result = apply_repairs(tmp_path, report)
    assert result.rebuilt_availability

    new_info = json.loads(info_path.read_text())
    assert new_info["available_timepoints"] == ["0h", "24h"], (
        f"expected the real on-disk timepoints, got {new_info['available_timepoints']!r}"
    )


def test_repair_is_idempotent(tmp_path):
    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    report = vc.build_report(tmp_path)
    result = apply_repairs(tmp_path, report)
    assert result.summary == "nothing to repair"


def test_stale_cell_override_flagged_and_pruned(tmp_path):
    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    out_zip = tmp_path / "out" / "A01_out.zip"
    overrides = {
        "version": 2,
        "overrides": [
            {"well": "A01", "fov": "F001", "tp": "0h", "nucleus_id": "1",
             "included": "0", "seg_fp": "not-the-real-mtime"},
        ],
    }
    (tmp_path / "out" / "cell_overrides.json").write_text(json.dumps(overrides))

    report = vc.build_report(tmp_path)
    assert report.stale_override_count == 1
    assert report.repairable

    result = apply_repairs(tmp_path, report)
    assert result.pruned_overrides == 1

    remaining = json.loads((tmp_path / "out" / "cell_overrides.json").read_text())
    assert remaining["overrides"] == []


def test_scan_of_many_wells_runs_in_parallel_and_stays_deterministic(tmp_path):
    """Enough wells to force >1 worker (default auto-sizing). The Check scan
    is read-only with no live per-well dashboard callback, so the only
    invariant that matters is that the report's well/finding ordering stays
    identical regardless of worker count -- .map() preserves submission
    order even though wells scan out of order."""
    wells = [f"{r}{c:02d}" for r in "ABCD" for c in range(1, 5)]  # 16 wells
    build_dataset(tmp_path, wells=wells, timepoints=["0h", "24h"])
    # Truncate a handful of wells so there's real per-well finding content to
    # compare, not just sixteen identical "complete" reports.
    for well in ("A01", "B03", "D04"):
        _truncate_well_to_first_timepoint(tmp_path, well)

    auto_report = vc.build_report(tmp_path)
    single_threaded_report = vc.build_report(tmp_path, workers=1)

    assert [w.well for w in auto_report.wells] == wells
    assert auto_report.wells == single_threaded_report.wells
    assert auto_report.findings == single_threaded_report.findings


def test_build_report_respects_explicit_worker_count(tmp_path):
    build_dataset(tmp_path, wells=["A01", "B02", "C03"], timepoints=["0h"])
    report = vc.build_report(tmp_path, workers=1)
    assert report.is_clean
    assert {w.well for w in report.wells} == {"A01", "B02", "C03"}
