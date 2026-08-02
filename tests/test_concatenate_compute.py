"""Fast-lane tests for the Concatenate Datasets planning + execution.

Both the pure planner (:mod:`datasets.concatenate.concatenate_compute`) and the
Qt-free executor (:mod:`datasets.concatenate.apply`) are exercised on tiny
``tmp_path`` datasets — no Qt, no app, so this stays in the fast lane. The Qt
``QThread`` wrapper (``worker.py``) and the controller's confirmation dialog are
deliberately not imported here.

Fixture CSVs use the **real** seven-column shape ``process_microscopy.py``'s
``parse_filename`` actually writes (``filename, experiment, channel, well, fov,
timepoint, timepoint_hours``, plus ``nucleus_id``/``area_px``) — a fixture with
only ``well,fov,timepoint,nucleus_id`` exercises a CSV shape that never occurs
on disk and would have hidden the ``timepoint_hours``/``filename`` staleness bug
this module fixes.
"""

from __future__ import annotations

import csv
import dataclasses
import json
import zipfile
from pathlib import Path

import pytest

from datasets.concatenate import apply
from datasets.concatenate import concatenate_compute as cc
from tests.fixtures.dataset_builder import JUNK_MEMBERS
from tests.fixtures.dataset_builder import BASE_INFO as _BASE_INFO
from tests.fixtures.dataset_builder import ROW_COLUMNS as _ROW_COLUMNS
from tests.fixtures.dataset_builder import build_dataset as _make_dataset
from util.timepoint import parse_hours


# ── timepoint math ───────────────────────────────────────────────────────────
def test_offset_continues_after_last_plus_interval():
    info1 = {"available_timepoints": ["0", "24h", "48h"]}
    info2 = {"available_timepoints": ["0", "24h"]}
    offset, last, interval = cc.compute_offset(info1, info2)
    assert last == 48.0
    assert interval == 24.0          # median gap
    assert offset == 72.0            # ds2 first (0) -> 48 + 24


def test_interval_uses_median_gap():
    info1 = {"available_timepoints": ["0", "24h", "48h", "96h"]}   # gaps 24,24,48 -> median 24
    info2 = {"available_timepoints": ["0"]}
    offset, last, interval = cc.compute_offset(info1, info2)
    assert last == 96.0
    assert interval == 24.0
    assert offset == 120.0           # 96 + 24 - 0


def test_single_timepoint_dataset1_raises():
    with pytest.raises(cc.ConcatError, match="only one timepoint"):
        cc.compute_offset({"available_timepoints": ["0"]}, {"available_timepoints": ["0", "24h"]})


def test_unparseable_timepoint_raises():
    with pytest.raises(cc.ConcatError, match="can't be read as a time"):
        cc.compute_offset({"available_timepoints": ["0", "24h"]}, {"available_timepoints": ["treated_arm"]})


def test_nan_inf_timepoint_refused():
    with pytest.raises(cc.ConcatError, match="can't be read as a time"):
        cc.compute_offset({"available_timepoints": ["0", "24h"]}, {"available_timepoints": ["nan"]})


# ── timepoint format detection + rendering ───────────────────────────────────
def test_detect_format_compound():
    fmt, warning = cc.detect_timepoint_format(["00d00h00m", "00d08h00m", "02d00h00m"])
    assert fmt.kind == "compound"
    assert warning is None


def test_detect_format_mixed_bare_and_hours_suffix_is_legitimate():
    # "0" and "24h" mixing is the repo's own documented example -- must not error.
    fmt, warning = cc.detect_timepoint_format(["0", "24h", "48h"])
    assert fmt.kind in ("hours", "bare")
    assert warning is None


def test_detect_format_ordinal_falls_back_with_warning():
    fmt, warning = cc.detect_timepoint_format(["T01", "T02"])
    assert fmt.kind == "fallback"
    assert warning is not None


def test_render_timepoint_round_trips_through_parse_hours():
    fmt = cc.TimepointFormat(kind="compound", day_width=2, hour_width=2, min_width=2)
    for hours in (0.0, 0.5, 8.5, 23.983333333333334, 72.0, 100.0, 1 / 3):
        label = cc.render_timepoint(hours, fmt)
        assert parse_hours(label) == pytest.approx(hours, abs=1 / 60)


def test_remap_timepoint_label_matches_dataset1_format():
    fmt, _ = cc.detect_timepoint_format(["00d00h00m", "00d08h00m"])
    new_label = cc.remap_timepoint_label("0", 72.0, fmt)
    assert new_label == "03d00h00m"
    assert parse_hours(new_label) == 72.0


# ── zip-member filename rewrite (the traps the shipped version hit) ──────────
def _schema():
    from well_viewer.data.image_identity import load_schema
    return load_schema(_BASE_INFO)


def test_rewrite_member_name_handles_fractional_hour_and_dotted_experiment():
    """Path.suffixes-based rewriting silently no-op'd on these two shapes in
    the shipped version -- images kept their pre-merge names."""
    schema = _schema()
    fmt = cc.TimepointFormat(kind="hours")
    new_name, rewrote = cc.rewrite_member_name(
        "exp_GFP_A01_F001_1.5h.tif", schema=schema, channels=(), offset_hours=72.0, tp_format=fmt,
    )
    assert rewrote and new_name == "exp_GFP_A01_F001_73.5h.tif"

    new_name, rewrote = cc.rewrite_member_name(
        "Exp2.1_NIR_A01_1_0h.tif", schema=schema, channels=(), offset_hours=72.0, tp_format=fmt,
    )
    assert rewrote and new_name == "Exp2.1_NIR_A01_1_72h.tif"


def test_rewrite_member_name_handles_role_suffixed_and_channel_scoped():
    schema = _schema()
    fmt = cc.TimepointFormat(kind="compound", day_width=2, hour_width=2, min_width=2)
    new_name, rewrote = cc.rewrite_member_name(
        "exp_GFP_A01_F001_00d08h00m_labels.tif", schema=schema, channels=("gfp",),
        offset_hours=72.0, tp_format=fmt,
    )
    assert rewrote and new_name == "exp_GFP_A01_F001_03d08h00m_labels.tif"

    new_name, rewrote = cc.rewrite_member_name(
        "exp_GFP_A01_F001_00d08h00m_tophat_gfp.tif", schema=schema, channels=("gfp", "nir"),
        offset_hours=72.0, tp_format=fmt,
    )
    assert rewrote and new_name == "exp_GFP_A01_F001_03d08h00m_tophat_gfp.tif"


def test_rewrite_member_name_passes_through_non_conformant_names():
    """A positional rewrite that ignores structure risks silently corrupting
    the wrong field (parse_hours("F001") == 1.0) -- non-conformant names must
    be left untouched, not guessed at."""
    schema = _schema()
    fmt = cc.TimepointFormat(kind="hours")
    for name in ("random_notes.txt", "F001_junk.tif"):
        new_name, rewrote = cc.rewrite_member_name(
            name, schema=schema, channels=(), offset_hours=72.0, tp_format=fmt,
        )
        assert not rewrote
        assert new_name == name


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

    merged = cc.merge_info(info1, info2, offset_hours=48.0, tp_format=cc.TimepointFormat(kind="hours"))
    assert merged["available_timepoints"] == ["0", "24h", "48h", "72h"]
    assert merged["fluor_tokens"] == ["nir", "gfp", "rfp"]
    assert merged["cell_gating"] == {"thresh_frac_on": {"gfp": 5.0}}


# ── dataset resolution / enumeration ─────────────────────────────────────────
def test_resolve_dataset_requires_in_and_out(tmp_path):
    (tmp_path / "in").mkdir()
    with pytest.raises(cc.ConcatError, match="not a dataset"):
        cc.resolve_dataset(tmp_path, label="dataset 1")


def test_dataset_wells_enumerated(tmp_path):
    _make_dataset(tmp_path, wells=["A01", "B02"], timepoints=["0", "24h"])
    paths = cc.resolve_dataset(tmp_path, label="d")
    assert cc.dataset_wells(paths) == {"A01", "B02"}


def test_folder_mode_output_refused(tmp_path):
    _make_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0", "24h"])
    _make_dataset(tmp_path / "d2", wells=["A01"], timepoints=["0", "24h"])
    (tmp_path / "d1" / "out" / "A01").mkdir()  # folder-mode QC output
    with pytest.raises(cc.ConcatError, match="per-well folders"):
        cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest")


def test_column_header_mismatch_warned(tmp_path):
    _make_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0", "24h"], columns=_ROW_COLUMNS)
    extra_cols = _ROW_COLUMNS + ["gfp_smfish_count"]
    _make_dataset(tmp_path / "d2", wells=["A01"], timepoints=["0", "24h"], columns=extra_cols)
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest")
    assert any("gfp_smfish_count" in w for w in plan.column_warnings)


# ── destination state / restart detection ────────────────────────────────────
def test_fresh_destination_is_not_a_restart(tmp_path):
    _make_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0", "24h"])
    _make_dataset(tmp_path / "d2", wells=["A01"], timepoints=["0", "24h"])
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest")
    assert plan.dest_is_restart is False


def test_marker_present_is_a_restart(tmp_path):
    _make_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0", "24h"])
    _make_dataset(tmp_path / "d2", wells=["A01"], timepoints=["0", "24h"])
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / cc.IN_PROGRESS_MARKER).write_text("")
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", dest)
    assert plan.dest_is_restart is True


def test_unrecognized_content_without_marker_is_refused_not_deleted(tmp_path):
    """A zipped-but-unanalyzed plate must never be silently rmtree'd just
    because it lacks a finished sidecar -- only the in-progress marker
    authorizes clearing a destination."""
    _make_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0", "24h"])
    _make_dataset(tmp_path / "d2", wells=["A01"], timepoints=["0", "24h"])
    dest = tmp_path / "dest"
    (dest / "in").mkdir(parents=True)
    with zipfile.ZipFile(dest / "in" / "Z09.zip", "w") as zf:
        zf.writestr("some.tif", b"x")
    with pytest.raises(cc.ConcatError, match="weren't created by a previous concatenation"):
        cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", dest)
    assert (dest / "in" / "Z09.zip").exists()  # untouched


def test_destination_with_finished_sidecar_is_refused(tmp_path):
    _make_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0", "24h"])
    _make_dataset(tmp_path / "d2", wells=["A01"], timepoints=["0", "24h"])
    _make_dataset(tmp_path / "dest", wells=["Q01"], timepoints=["0"])
    with pytest.raises(cc.ConcatError, match="already holds a finished dataset"):
        cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest")


def test_destination_cannot_equal_a_source(tmp_path):
    _make_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0", "24h"])
    _make_dataset(tmp_path / "d2", wells=["A01"], timepoints=["0", "24h"])
    with pytest.raises(cc.ConcatError, match="different folder"):
        cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "d1")


# ── end-to-end execution ──────────────────────────────────────────────────────
def test_execute_plan_shifts_timepoint_hours_not_just_the_label(tmp_path):
    """The bug this module exists to fix: timepoint_hours (the actual
    plotting key) must shift with the label, not stay at dataset 2's
    original value."""
    _make_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0h", "24h", "48h"])
    _make_dataset(tmp_path / "d2", wells=["A01"], timepoints=["0h", "24h"])
    dest = tmp_path / "dest"
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", dest)
    apply.execute_plan(plan)

    with (dest / "out" / "measurements_A01.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    hours = sorted(float(r["timepoint_hours"]) for r in rows)
    assert hours == [0.0, 24.0, 48.0, 72.0, 96.0]
    assert len(set(hours)) == 5  # dataset 2 did not collapse onto dataset 1


def test_execute_plan_keeps_filename_column_in_sync_with_zip_members(tmp_path):
    """The other half of the same bug: a stale filename column breaks
    row-to-image linkage (Cell Browser / Scatter)."""
    _make_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0h", "24h"])
    _make_dataset(tmp_path / "d2", wells=["A01"], timepoints=["0h", "24h"])
    dest = tmp_path / "dest"
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", dest)
    apply.execute_plan(plan)

    with (dest / "out" / "measurements_A01.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    with zipfile.ZipFile(dest / "in" / "A01.zip") as zf:
        names = set(zf.namelist())
    for row in rows:
        assert row["filename"] in names


def test_execute_plan_preserves_dataset1_format_never_mixes(tmp_path):
    _make_dataset(tmp_path / "d1", wells=["A01"], timepoints=["00d00h00m", "00d08h00m", "00d16h00m"])
    _make_dataset(tmp_path / "d2", wells=["A01"], timepoints=["00d00h00m", "00d08h00m"])
    dest = tmp_path / "dest"
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", dest)
    assert plan.tp_format.kind == "compound"
    apply.execute_plan(plan)

    with (dest / "out" / "measurements_A01.csv").open(newline="") as handle:
        labels = [r["timepoint"] for r in csv.DictReader(handle)]
    assert labels == ["00d00h00m", "00d08h00m", "00d16h00m", "01d00h00m", "01d08h00m"]


def test_execute_plan_copies_unique_wells_and_shifts_ds2_only(tmp_path):
    _make_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0h", "24h", "48h"])
    _make_dataset(tmp_path / "d2", wells=["B02"], timepoints=["0h", "24h"])
    dest = tmp_path / "dest"
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", dest)
    apply.execute_plan(plan)

    with (dest / "out" / "measurements_A01.csv").open(newline="") as handle:
        assert [r["timepoint"] for r in csv.DictReader(handle)] == ["0h", "24h", "48h"]
    with (dest / "out" / "measurements_B02.csv").open(newline="") as handle:
        assert [r["timepoint"] for r in csv.DictReader(handle)] == ["72h", "96h"]


def test_execute_plan_backfills_missing_timepoint_hours_column(tmp_path):
    legacy_cols = ["well", "fov", "timepoint", "nucleus_id"]
    _make_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0h", "24h"], columns=legacy_cols)
    _make_dataset(tmp_path / "d2", wells=["A01"], timepoints=["0h", "24h"], columns=legacy_cols)
    dest = tmp_path / "dest"
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", dest)
    apply.execute_plan(plan)

    with (dest / "out" / "measurements_A01.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert "timepoint_hours" in rows[0]
    assert [float(r["timepoint_hours"]) for r in rows] == [0.0, 24.0, 48.0, 72.0]


def test_execute_plan_carries_over_cell_overrides_with_shifted_tp(tmp_path):
    _make_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0h", "24h"])
    _make_dataset(tmp_path / "d2", wells=["A01"], timepoints=["0h", "24h"])
    (tmp_path / "d1" / "out" / "cell_overrides.json").write_text(json.dumps({
        "version": 2,
        "overrides": [{"well": "A01", "fov": "F001", "tp": "0h", "nucleus_id": "1",
                       "included": "0", "seg_fp": "stale1"}],
    }))
    (tmp_path / "d2" / "out" / "cell_overrides.json").write_text(json.dumps({
        "version": 2,
        "overrides": [{"well": "A01", "fov": "F001", "tp": "0h", "nucleus_id": "1",
                       "included": "0", "seg_fp": "stale2"}],
    }))
    dest = tmp_path / "dest"
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", dest)
    apply.execute_plan(plan)

    merged = json.loads((dest / "out" / "cell_overrides.json").read_text())
    tps = sorted(o["tp"] for o in merged["overrides"])
    assert tps == ["0h", "48h"]  # ds1's untouched + ds2's shifted (48h = 24h last + 24h interval + 0h)
    real_mtime = str((dest / "out" / "A01_out.zip").stat().st_mtime_ns)
    assert all(o["seg_fp"] == real_mtime for o in merged["overrides"])


def test_execute_plan_drops_malformed_dataset2_override_tp_rather_than_carry_it_over_wrong(tmp_path):
    """_merge_cell_overrides's documented behaviour (its own module docstring:
    "a malformed entry (unparseable tp) is dropped rather than carried over
    with a wrong value") has no test anywhere in the suite. A stale override
    file referencing an old, now-unparseable timepoint token is a realistic
    real-world shape -- cell_overrides.json is hand/UI-edited independently of
    the measurements CSVs -- and the alternative failure mode (silently
    carrying the entry over with its original, un-shifted tp) would land a
    cell override on the wrong timepoint after the merge with no error at all.
    A dataset-1 entry is unaffected, since dataset 1's overrides are carried
    over unshifted; only dataset-2 entries go through remap_timepoint_label."""
    _make_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0h", "24h"])
    _make_dataset(tmp_path / "d2", wells=["A01"], timepoints=["0h", "24h"])
    (tmp_path / "d1" / "out" / "cell_overrides.json").write_text(json.dumps({
        "version": 2,
        "overrides": [{"well": "A01", "fov": "F001", "tp": "0h", "nucleus_id": "1",
                       "included": "0", "seg_fp": "stale1"}],
    }))
    (tmp_path / "d2" / "out" / "cell_overrides.json").write_text(json.dumps({
        "version": 2,
        "overrides": [
            {"well": "A01", "fov": "F001", "tp": "0h", "nucleus_id": "1",
             "included": "0", "seg_fp": "stale2"},
            {"well": "A01", "fov": "F001", "tp": "not_a_timepoint", "nucleus_id": "2",
             "included": "0", "seg_fp": "stale3"},
        ],
    }))
    dest = tmp_path / "dest"
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", dest)
    apply.execute_plan(plan)

    merged = json.loads((dest / "out" / "cell_overrides.json").read_text())
    nucleus_ids = sorted(o["nucleus_id"] for o in merged["overrides"])
    assert nucleus_ids == ["1", "1"]  # nucleus_id "2" (the malformed entry) is gone, not mis-shifted
    tps = sorted(o["tp"] for o in merged["overrides"])
    assert tps == ["0h", "48h"]
    assert all(o["tp"] != "not_a_timepoint" for o in merged["overrides"])


def test_execute_plan_regression_guard_catches_a_broken_offset(tmp_path):
    """Simulates the shipped bug directly: force a MergePlan whose offset
    wasn't actually applied to timepoint_hours, and confirm verification
    catches it before the sidecar is written."""
    _make_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0h", "24h", "48h"])
    _make_dataset(tmp_path / "d2", wells=["A01"], timepoints=["0h", "24h"])
    dest = tmp_path / "dest"
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", dest)

    broken_plan = dataclasses.replace(plan, offset_hours=0.0)
    with pytest.raises(cc.ConcatError):
        apply.execute_plan(broken_plan)
    assert not (dest / "out" / "pipeline_info.json").exists()  # never written on verify failure


def test_execute_plan_restart_clears_stale_orphan_well(tmp_path):
    _make_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0h", "24h", "48h"])
    _make_dataset(tmp_path / "d2", wells=["A01"], timepoints=["0h", "24h"])

    dest = tmp_path / "dest"
    (dest / "in").mkdir(parents=True)
    (dest / "out").mkdir(parents=True)
    (dest / cc.IN_PROGRESS_MARKER).write_text("")
    with zipfile.ZipFile(dest / "in" / "Z09.zip", "w") as zf:
        zf.writestr("junk.tif", b"orphan")
    (dest / "out" / "measurements_Z09.csv").write_text("well,timepoint\nZ09,0\n")

    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", dest)
    assert plan.dest_is_restart is True
    apply.execute_plan(plan)

    assert not (dest / "in" / "Z09.zip").exists()
    assert not (dest / "out" / "measurements_Z09.csv").exists()
    assert (dest / "in" / "A01.zip").exists()
    assert not (dest / cc.IN_PROGRESS_MARKER).exists()  # cleared on success


def test_execute_plan_cancellation_leaves_marker_for_resume(tmp_path):
    _make_dataset(tmp_path / "d1", wells=["A01", "B02"], timepoints=["0h", "24h"])
    _make_dataset(tmp_path / "d2", wells=["A01", "B02"], timepoints=["0h", "24h"])
    dest = tmp_path / "dest"
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", dest)

    with pytest.raises(cc.ConcatError, match="Cancelled"):
        apply.execute_plan(plan, is_cancelled=lambda: True)
    assert (dest / cc.IN_PROGRESS_MARKER).exists()
    assert not (dest / "out" / "pipeline_info.json").exists()


# ── zero-rows guard: a well's images existing without its CSV ────────────────
def _make_dataset_with_image_only_well(root: Path, *, normal_wells, image_only_well, timepoints) -> None:
    """*normal_wells* get a full in/ zip + out/ QC zip + measurements CSV.
    *image_only_well* (may be None) gets in/ + out/ QC zip but deliberately NO
    measurements CSV -- a well that segmented zero cells at every timepoint,
    or (in the naming-mismatch tests below) a CSV out_csvs() can't match."""
    in_dir, out_dir = root / "in", root / "out"
    in_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)
    info = dict(_BASE_INFO)
    info["available_timepoints"] = list(timepoints)
    (out_dir / "pipeline_info.json").write_text(json.dumps(info))

    def _write_images(well):
        with zipfile.ZipFile(in_dir / f"{well}.zip", "w") as zf:
            for tp in timepoints:
                zf.writestr(f"exp_GFP_{well}_F001_{tp}.tif", b"img")
        with zipfile.ZipFile(out_dir / f"{well}_out.zip", "w") as zf:
            for tp in timepoints:
                zf.writestr(f"exp_GFP_{well}_F001_{tp}_labels.tif", b"mask")

    for well in normal_wells:
        _write_images(well)
        with (out_dir / f"measurements_{well}.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=_ROW_COLUMNS)
            writer.writeheader()
            for tp in timepoints:
                writer.writerow({
                    "filename": f"exp_GFP_{well}_F001_{tp}.tif", "experiment": "exp",
                    "channel": "GFP", "well": well, "fov": "F001", "timepoint": tp,
                    "timepoint_hours": parse_hours(tp), "nucleus_id": 1, "area_px": 150,
                })

    if image_only_well:
        _write_images(image_only_well)


def test_execute_plan_zero_cell_well_alongside_normal_well_still_succeeds(tmp_path):
    """A well with images but no CSV (zero cells segmented) is legitimate on
    its own -- the merge must still succeed for the wells that do have data,
    and note the image-only well rather than silently dropping it."""
    _make_dataset_with_image_only_well(
        tmp_path / "d1", normal_wells=["A01"], image_only_well="E05", timepoints=["0h", "24h", "48h"],
    )
    _make_dataset_with_image_only_well(
        tmp_path / "d2", normal_wells=["A01"], image_only_well=None, timepoints=["0h", "24h"],
    )
    dest = tmp_path / "dest"
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", dest)
    log: list[str] = []
    summary = apply.execute_plan(plan, log.append)

    assert "Concatenated 2 well(s)" in summary
    assert any("E05 (dataset 1)" in line for line in log)
    assert (dest / "out" / "measurements_A01.csv").exists()
    assert not (dest / "out" / "measurements_E05.csv").exists()
    assert (dest / "in" / "E05.zip").exists()  # images still carried over


def test_execute_plan_zero_rows_everywhere_fails_loudly(tmp_path):
    """The regression this guard exists for: if out_csvs() matches no CSV for
    ANY well in EITHER dataset (e.g. every well is image-only, or a naming
    mismatch hides real CSVs from the matcher), the merge must fail loudly
    instead of silently reporting success with an empty destination."""
    _make_dataset_with_image_only_well(
        tmp_path / "d1", normal_wells=[], image_only_well="A01", timepoints=["0h", "24h", "48h"],
    )
    _make_dataset_with_image_only_well(
        tmp_path / "d2", normal_wells=[], image_only_well="A01", timepoints=["0h", "24h"],
    )
    dest = tmp_path / "dest"
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", dest)

    with pytest.raises(cc.ConcatError, match="zero measurement rows"):
        apply.execute_plan(plan)
    assert not (dest / "out" / "pipeline_info.json").exists()  # never reports success


# ── progress callback: per-well events driving the status dashboard ─────────
def test_execute_plan_progress_reports_merging_then_merged_per_well(tmp_path):
    _make_dataset(tmp_path / "d1", wells=["A01", "A02"], timepoints=["0", "24h"])
    _make_dataset(tmp_path / "d2", wells=["A01", "A02"], timepoints=["0", "24h"])
    dest = tmp_path / "dest"
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", dest)

    events: list[tuple[str, str]] = []
    apply.execute_plan(plan, progress=lambda well, stage: events.append((well, stage)))

    # each well reports "merging" before its terminal state, and every well
    # reaches a terminal state exactly once
    seen_terminal = set()
    for i, (well, stage) in enumerate(events):
        if stage == "merging":
            assert (well, "merged") in events[i + 1:] or (well, "warned") in events[i + 1:]
        else:
            assert stage in ("merged", "warned")
            assert well not in seen_terminal
            seen_terminal.add(well)
    assert seen_terminal == {"A01", "A02"}


def test_execute_plan_sub_progress_is_one_rising_sweep_per_well(tmp_path):
    """``sub_progress`` is the per-well dial behind ``progress``'s stage
    events. Merging one well is six container passes (dataset 2's read,
    dataset 1's read and the merged write, twice over — raw input then QC
    archive); they are fused into a single sweep, so each well's fractions
    must rise monotonically and end at full rather than filling six times."""
    _make_dataset(tmp_path / "d1", wells=["A01", "A02"], timepoints=["0", "24h"])
    _make_dataset(tmp_path / "d2", wells=["A01", "A02"], timepoints=["0", "24h"])
    dest = tmp_path / "dest"
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", dest)

    fractions: dict[str, list[tuple[int, int]]] = {}
    apply.execute_plan(
        plan,
        sub_progress=lambda well, done, total: fractions.setdefault(well, []).append((done, total)),
    )

    assert set(fractions) == {"A01", "A02"}
    for well, seq in fractions.items():
        values = [done for done, _total in seq]
        assert values == sorted(values), f"{well} dial went backwards: {values}"
        assert seq[-1][0] == seq[-1][1], f"{well} dial never reached full: {seq[-1]}"


def test_execute_plan_progress_reports_warned_for_image_only_well(tmp_path):
    _make_dataset_with_image_only_well(
        tmp_path / "d1", normal_wells=["A01"], image_only_well="E05", timepoints=["0h", "24h"],
    )
    _make_dataset_with_image_only_well(
        tmp_path / "d2", normal_wells=["A01"], image_only_well=None, timepoints=["0h", "24h"],
    )
    dest = tmp_path / "dest"
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", dest)

    events: list[tuple[str, str]] = []
    apply.execute_plan(plan, progress=lambda well, stage: events.append((well, stage)))

    assert ("E05", "warned") in events
    assert ("A01", "merged") in events


def test_execute_plan_progress_defaults_to_a_no_op(tmp_path):
    """The kwarg must stay optional -- every pre-existing call site (and all
    the tests above it) omits it and must keep working unchanged."""
    _make_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0", "24h"])
    _make_dataset(tmp_path / "d2", wells=["A01"], timepoints=["0", "24h"])
    dest = tmp_path / "dest"
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", dest)
    summary = apply.execute_plan(plan)
    assert "Concatenated 1 well(s)" in summary


def test_execute_plan_merges_many_wells_in_parallel(tmp_path):
    """Enough wells to force >1 worker (default auto-sizing) -- every well
    must still land correctly with no cross-well interference, and every
    well must still report exactly one merging + one terminal event."""
    wells = [f"{r}{c:02d}" for r in "ABCD" for c in range(1, 5)]  # 16 wells
    _make_dataset(tmp_path / "d1", wells=wells, timepoints=["0", "24h"])
    _make_dataset(tmp_path / "d2", wells=wells, timepoints=["0", "24h"])
    dest = tmp_path / "dest"
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", dest)

    events: list[tuple[str, str]] = []
    summary = apply.execute_plan(plan, progress=lambda well, stage: events.append((well, stage)))
    assert f"Concatenated {len(wells)} well(s)" in summary

    by_well: dict[str, list[str]] = {}
    for well, stage in events:
        by_well.setdefault(well, []).append(stage)
    assert set(by_well) == set(wells)
    for well, stages in by_well.items():
        assert stages[0] == "merging"
        assert stages[1] in ("merged", "warned")
        assert len(stages) == 2

    for well in wells:
        with zipfile.ZipFile(dest / "in" / f"{well}.zip") as zf:
            assert len(zf.namelist()) > 0
        with (dest / "out" / f"measurements_{well}.csv").open() as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 4  # 2 timepoints x 2 datasets


def test_execute_plan_respects_explicit_worker_count(tmp_path):
    _make_dataset(tmp_path / "d1", wells=["A01", "B02", "C03"], timepoints=["0", "24h"])
    _make_dataset(tmp_path / "d2", wells=["A01", "B02", "C03"], timepoints=["0", "24h"])
    dest = tmp_path / "dest"
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", dest)
    summary = apply.execute_plan(plan, workers=1)
    assert "Concatenated 3 well(s)" in summary


# ── collision / silent-drop regression (reported bug) ─────────────────────────
#
# Reported symptom: pointing dataset 1 and dataset 2 at datasets that share a
# real filename produced an output the same size as the input, with only one
# copy of the files -- no error, no warning. Traced to ``rewrite_member_name``
# leaving a non-schema-conformant member's name unchanged, so when both sides
# happen to carry an identically-named non-conformant file, ``write_zip``'s
# dedup logic (``if name in written: continue``) silently keeps only whichever
# side was written first and drops the other, with no count, no warning, and
# no verification check that would catch it (``_verify``'s ``filename_mismatches``
# only confirms the name exists somewhere in the archive, not that it's the
# right side's bytes).

def _make_dataset_with_stray_member(root: Path, *, well: str, timepoints,
                                    stray_content: bytes, exp: str = "exp") -> Path:
    """Like :func:`_make_dataset`, plus one extra zip member whose name
    ("notes.tif") does not match the schema's 5-field naming -- deliberately
    non-conformant, so :func:`rewrite_member_name` leaves it unchanged."""
    _make_dataset(root, wells=[well], timepoints=timepoints, exp=exp)
    with zipfile.ZipFile(root / "in" / f"{well}.zip", "a") as zf:
        zf.writestr("notes.tif", stray_content)
    return root


def test_execute_plan_does_not_silently_drop_a_colliding_nonconformant_member(tmp_path):
    """A non-schema-conformant member that collides by name across datasets 1
    and 2 must never vanish without a trace: the merge must either fail
    loudly (ConcatError) or genuinely preserve both sides' data -- it must
    never silently keep only one side's bytes under a name that looks fine."""
    _make_dataset_with_stray_member(
        tmp_path / "d1", well="A01", timepoints=["0h", "24h"], stray_content=b"d1-notes",
    )
    _make_dataset_with_stray_member(
        tmp_path / "d2", well="A01", timepoints=["0h", "24h"], stray_content=b"d2-notes",
    )
    dest = tmp_path / "dest"
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", dest)

    events: list[tuple[str, str]] = []
    try:
        apply.execute_plan(plan, progress=lambda well, stage: events.append((well, stage)))
    except cc.ConcatError:
        # Loud failure is an acceptable outcome -- but the per-well progress
        # callback (already collected before the raise, since _verify runs
        # after the per-well loop) must still have flagged the well, so a
        # live dashboard watching mid-run would have shown "warned" rather
        # than nothing at all.
        assert ("A01", "warned") in events
        return

    # If it didn't raise, both sides' bytes must actually be present -- not
    # just a name that happens to exist (that's exactly what the old
    # ``filename_mismatches`` check couldn't tell apart).
    with zipfile.ZipFile(dest / "in" / "A01.zip") as zf:
        contents = {info.filename: zf.read(info.filename) for info in zf.infolist()}
    stray_bytes = [v for k, v in contents.items() if "notes" in k]
    assert set(stray_bytes) == {b"d1-notes", b"d2-notes"}, (
        "dataset 2's colliding member was silently dropped -- only "
        f"{stray_bytes!r} survived in the merged archive"
    )


def test_build_merge_plan_rejects_identical_source_roots(tmp_path):
    """The exact scenario from the bug report: dataset 1 and dataset 2
    pointed at the same directory. This must fail clearly at planning time,
    not silently merge a dataset with itself."""
    _make_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0h", "24h"])
    with pytest.raises(cc.ConcatError, match="[Ss]ame"):
        cc.build_merge_plan(tmp_path / "d1", tmp_path / "d1", tmp_path / "dest")


# ── the merge moves bytes, it does not rebuild them ──────────────────────────
def _zip_members(path):
    with zipfile.ZipFile(path) as zf:
        return {i.filename: zf.read(i.filename) for i in zf.infolist() if not i.is_dir()}


def test_a_merged_well_carries_both_datasets_bytes_unaltered(tmp_path):
    """Concatenate renames dataset 2's members to offset their timepoint and
    renames nothing on dataset 1's. Neither side's data may change: a name
    lives in a member's headers, not in its compressed payload."""
    ds1, ds2, dest = tmp_path / "ds1", tmp_path / "ds2", tmp_path / "dest"
    _make_dataset(ds1, wells=["A01"], timepoints=["0h", "24h"], junk=True)
    _make_dataset(ds2, wells=["A01"], timepoints=["0h", "24h"], junk=True)

    plan = cc.build_merge_plan(ds1, ds2, dest)
    apply.execute_plan(plan)

    merged = _zip_members(dest / "in" / "A01.zip")
    first = _zip_members(ds1 / "in" / "A01.zip")
    for name, data in first.items():
        assert merged[name] == data, f"dataset 1's {name} came back different"
    # Dataset 2's members arrive under timepoint-offset names, bytes untouched.
    second = _zip_members(ds2 / "in" / "A01.zip")
    offset = {n for n in merged if n not in first}
    assert offset, "dataset 2 contributed no members"
    for name in offset:
        assert merged[name] in second.values(), f"dataset 2's {name} came back different"


def test_a_well_on_one_side_only_is_copied_whole(tmp_path):
    ds1, ds2, dest = tmp_path / "ds1", tmp_path / "ds2", tmp_path / "dest"
    _make_dataset(ds1, wells=["A01"], timepoints=["0h", "24h"], junk=True)
    _make_dataset(ds2, wells=["B02"], timepoints=["0h", "24h"], junk=True)

    apply.execute_plan(cc.build_merge_plan(ds1, ds2, dest))

    assert (dest / "in" / "A01.zip").read_bytes() == (ds1 / "in" / "A01.zip").read_bytes()


# ── OS debris must not block a merge ─────────────────────────────────────────
def test_two_plates_that_have_been_opened_in_finder_can_be_merged(tmp_path):
    """Regression guard: both datasets carrying a ``.DS_Store`` used to be a
    hard failure.

    The two identically-named copies collided in the merged archive, one was
    dropped by first-write-wins, and ``_verify`` read that drop as dataset 2's
    data failing to survive — so two ordinary plates could not be concatenated
    at all, over files neither the pipeline nor Review has ever read. Any plate
    whose folder has been opened in Finder has one.
    """
    ds1, ds2, dest = tmp_path / "ds1", tmp_path / "ds2", tmp_path / "dest"
    _make_dataset(ds1, wells=["A01"], timepoints=["0h", "24h"], junk=True)
    _make_dataset(ds2, wells=["A01"], timepoints=["0h", "24h"], junk=True)

    log = []
    summary = apply.execute_plan(cc.build_merge_plan(ds1, ds2, dest), log.append)

    assert "1 well" in summary
    merged = _zip_members(dest / "in" / "A01.zip")
    # Dataset 1's copies are there; dataset 2's duplicates were left out
    # rather than colliding.
    for name, data in JUNK_MEMBERS.items():
        assert merged[name] == data
    # The run says so rather than dropping them silently.
    assert any("operating-system file" in line for line in log), log


def test_a_real_name_collision_still_fails_the_merge(tmp_path):
    """Skipping OS debris must not loosen the collision check it was blocking.

    A member that carries data and collides is still real data loss, and a
    merge that discarded it must not report success.
    """
    ds1, ds2, dest = tmp_path / "ds1", tmp_path / "ds2", tmp_path / "dest"
    _make_dataset(ds1, wells=["A01"], timepoints=["0h", "24h"])
    _make_dataset(ds2, wells=["A01"], timepoints=["0h", "24h"])
    for root, payload in ((ds1, b"from-ds1"), (ds2, b"from-ds2")):
        with zipfile.ZipFile(root / "in" / "A01.zip", "a") as zf:
            zf.writestr("hand_written_notes.txt", payload)

    with pytest.raises(cc.ConcatError, match="collided by name"):
        apply.execute_plan(cc.build_merge_plan(ds1, ds2, dest))


def test_junk_is_recognised_by_name_not_by_being_unparseable():
    """The predicate names specific OS files rather than "anything that isn't
    an image" — a file a user put in an archive on purpose is not debris."""
    from datasets._common.member_names import is_os_junk_member

    for name in (
        ".DS_Store", "frames/.DS_Store", "Thumbs.db", "desktop.ini",
        "__MACOSX/._exp_GFP_A01_F001_24h.tif", "._exp_GFP_A01_F001_24h.tif",
        ".Spotlight-V100/store.db",
        # Backslash-separated, as a Windows-authored archive writes them. The
        # normalisation for these was a no-op ("\\\\" in a non-raw literal is
        # two characters, not one), so they were missed — and being missed is
        # not harmless: two datasets each carrying "frames\\.DS_Store" collide,
        # and the merge is refused for exactly the reason this skip exists.
        "frames\\.DS_Store", "__MACOSX\\._exp_GFP_A01_F001_24h.tif",
    ):
        assert is_os_junk_member(name), name
    for name in (
        "exp_GFP_A01_F001_24h.tif", "hand_written_notes.txt", "readme.md",
        "protocol.pdf", "frames/exp_GFP_A01_F001_24h.tif", "",
    ):
        assert not is_os_junk_member(name), name


def test_a_well_only_dataset_2_has_keeps_its_own_debris(tmp_path):
    """The debris skip resolves a conflict; it is not a cleanup pass.

    On a well dataset 1 doesn't have there is nothing to collide with and
    nothing to prefer, so skipping would simply delete a file — and, for an
    archive holding nothing else, would leave no destination archive at all
    while the run still reported the well as having image data.
    """
    ds1, ds2, dest = tmp_path / "ds1", tmp_path / "ds2", tmp_path / "dest"
    _make_dataset(ds1, wells=["A01"], timepoints=["0h", "24h"], junk=True)
    _make_dataset(ds2, wells=["B02"], timepoints=["0h", "24h"], junk=True)

    log = []
    apply.execute_plan(cc.build_merge_plan(ds1, ds2, dest), log.append)

    copied = _zip_members(dest / "in" / "B02.zip")
    for name, data in JUNK_MEMBERS.items():
        assert copied[name] == data, f"{name} was deleted from a ds2-only well"
    assert not any("operating-system file" in line for line in log), log
