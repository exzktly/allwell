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


def test_filename_not_in_input_flagged_for_a_stale_csv_row(tmp_path):
    """A measurements-CSV row can reference an input image that no longer
    exists in the well's raw ``in/<WELL>.zip`` (renamed or deleted after the
    row was written). The (fov, timepoint) pair itself is still "done" --
    the CSV row supplies that evidence, so no missing_fov_tp finding fires --
    so ``_row_content_findings``'s filename_not_in_input check is the only
    thing in the whole report that would ever catch this kind of stale row.
    No existing test drove this code path (nor tp_hours_mismatch,
    unreadable_in_container, or sidecar_fovs_drift, which are equally
    untouched by the suite)."""
    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    csv_path = tmp_path / "out" / "measurements_A01.csv"
    with csv_path.open() as f:
        rows = list(csv.DictReader(f))
    fieldnames = list(rows[0].keys())
    rows[0]["filename"] = "exp_GFP_A01_F001_0h_RENAMED.tif"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    report = vc.build_report(tmp_path)
    a01 = next(w for w in report.wells if w.well == "A01")
    assert a01.complete  # the (fov, timepoint) pair is still "done" via the CSV row
    findings = [f for f in report.findings if f.code == "filename_not_in_input"]
    assert len(findings) == 1
    assert findings[0].well == "A01"
    assert "exp_GFP_A01_F001_0h_RENAMED.tif" in findings[0].message
    assert "A01.zip" in findings[0].message


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


def test_no_fov_schema_still_detects_a_genuinely_partial_well(tmp_path):
    """Regression guard: a schema with no 'fov' field used to skip
    missing-timepoint detection entirely (`missing` was unconditionally
    `set()`), so a well done at every declared timepoint and one done at
    only half of them reported identically "complete" — exactly what a
    single-FOV pipeline (a common real setup, not an edge case) always hits.
    `build_dataset` always embeds a fov token in its filenames regardless of
    the declared schema, so it can't exercise this — hand-build filenames
    that genuinely match a 4-field, fov-less schema instead."""
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    out_dir.mkdir()
    info = {
        "schema_fields": ["experiment", "channel", "well", "timepoint"],
        "separator": "_",
        "available_timepoints": ["0h", "8h", "16h", "24h"],
        "available_fovs": ["1"],
    }
    (out_dir / "pipeline_info.json").write_text(json.dumps(info))

    all_tps = ["0h", "8h", "16h", "24h"]
    hours = {"0h": 0, "8h": 8, "16h": 16, "24h": 24}

    def write_well(well: str, done_tps: list[str]) -> None:
        with zipfile.ZipFile(in_dir / f"{well}.zip", "w") as zf:
            for tp in all_tps:
                zf.writestr(f"exp_GFP_{well}_{tp}.tif", b"img")
        with zipfile.ZipFile(out_dir / f"{well}_out.zip", "w") as zf:
            for tp in done_tps:
                zf.writestr(f"exp_GFP_{well}_{tp}_labels.tif", b"mask")
        with (out_dir / f"measurements_{well}.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "filename", "experiment", "channel", "well", "fov",
                "timepoint", "timepoint_hours", "nucleus_id", "area_px"])
            writer.writeheader()
            for tp in done_tps:
                writer.writerow({
                    "filename": f"exp_GFP_{well}_{tp}.tif", "experiment": "exp",
                    "channel": "GFP", "well": well, "fov": "1", "timepoint": tp,
                    "timepoint_hours": hours[tp], "nucleus_id": 1, "area_px": 150,
                })

    write_well("A01", all_tps)          # genuinely finished: all 4 timepoints
    write_well("B02", ["0h", "8h"])     # genuinely partial: missing 16h, 24h

    report = vc.build_report(tmp_path)
    by_well = {w.well: w for w in report.wells}
    assert by_well["A01"].complete
    assert not by_well["B02"].complete
    assert {tp for _fov, tp in by_well["B02"].missing} == {"16h", "24h"}
    b02_findings = [
        f for f in report.findings if f.code == "missing_fov_tp" and f.well == "B02"
    ]
    assert len(b02_findings) == 1
    assert "16h" in b02_findings[0].message
    assert "24h" in b02_findings[0].message


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


def test_schema_drift_reports_a_diagnostic_instead_of_a_wall_of_false_missing(tmp_path):
    """Regression guard: intent (parsed from in/ filenames using the CURRENT
    pipeline_info.json schema) and done's CSV-column values are only
    guaranteed to agree when that schema actually matches how the well's
    real filenames are structured. If it doesn't -- schema_fields drifted
    since the files were written, or was simply wrong -- intent's positional
    parse silently reads the wrong token as "fov"/"timepoint", producing
    hundreds of individually-false missing_fov_tp findings for a well that
    may be entirely done, with nothing telling the user why. Simulates the
    drift directly: filenames written under a channel/fov field order that
    doesn't match the CURRENT schema_fields, so intent's fov-position parse
    grabs the channel token instead — while the CSV's own 'fov' column
    (a literal recorded value, immune to schema drift) still holds the
    real fov tokens."""
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    out_dir.mkdir()
    info = {
        "schema_fields": ["experiment", "channel", "well", "fov", "timepoint"],
        "separator": "_",
        "available_timepoints": [f"t{i}" for i in range(13)],
        "available_fovs": [f"f{i}" for i in range(6)],
    }
    (out_dir / "pipeline_info.json").write_text(json.dumps(info))

    fovs = [f"f{i}" for i in range(6)]
    tps = [f"t{i}" for i in range(13)]

    # Real filenames: experiment_well_fov_channel_timepoint -- channel comes
    # AFTER fov here, not before it as schema_fields (declared above) assumes.
    with zipfile.ZipFile(in_dir / "E11.zip", "w") as zf:
        for fov in fovs:
            for tp in tps:
                zf.writestr(f"exp_E11_{fov}_GFP_{tp}.tif", b"img")
    with zipfile.ZipFile(out_dir / "E11_out.zip", "w") as zf:
        for fov in fovs:
            for tp in tps:
                zf.writestr(f"exp_E11_{fov}_GFP_{tp}_labels.tif", b"mask")
    with (out_dir / "measurements_E11.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "filename", "experiment", "channel", "well", "fov",
            "timepoint", "timepoint_hours", "nucleus_id", "area_px"])
        writer.writeheader()
        for fov in fovs:
            for tp in tps:
                writer.writerow({
                    "filename": f"exp_E11_{fov}_GFP_{tp}.tif", "experiment": "exp",
                    "channel": "GFP", "well": "E11", "fov": fov, "timepoint": tp,
                    "timepoint_hours": int(tp[1:]), "nucleus_id": 1, "area_px": 150,
                })

    report = vc.build_report(tmp_path)
    mismatch_findings = [
        f for f in report.findings
        if f.code == "schema_mismatch_suspected" and f.well == "E11"
    ]
    assert len(mismatch_findings) == 1
    assert "pipeline_info.json" in mismatch_findings[0].message
    assert "schema_fields" in mismatch_findings[0].message
