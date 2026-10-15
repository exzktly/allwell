"""Concatenating raw datasets: converted plates nobody has analysed yet.

The fixture is a real converter-shaped plate (``in/<WELL>.zip`` holding
``<exp>_<channel>_<well>_<field>_<timepoint>.tif``, nothing under ``out/``).
Every assertion is made on the archives the merge wrote, because that is all a
raw dataset is.
"""

from __future__ import annotations

import zipfile

import pytest

from datasets.concatenate import apply
from datasets.concatenate import concatenate_compute as cc
from datasets.concatenate.raw_compute import is_raw_dataset


def _raw(root, *, wells=("A01",), timepoints=("0h",), fields=("f0", "f1"),
         channels=("DAPI", "GFP"), exp="E", extra=None):
    (root / "in").mkdir(parents=True)
    for well in wells:
        with zipfile.ZipFile(root / "in" / f"{well}.zip", "w") as archive:
            for tp in timepoints:
                for field in fields:
                    for channel in channels:
                        archive.writestr(f"{exp}_{channel}_{well}_{field}_{tp}.tif",
                                         f"{well}{field}{channel}{tp}".encode())
            for name, data in (extra or {}).items():
                archive.writestr(name, data)
    return root


def _members(dest, well="A01"):
    with zipfile.ZipFile(dest / "in" / f"{well}.zip") as archive:
        return sorted(archive.namelist())


def _merge(tmp_path, **kwargs):
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest", **kwargs)
    return plan, apply.execute_plan(plan)


def test_two_raw_datasets_merge_into_a_raw_destination_with_dataset_2_shifted(tmp_path):
    _raw(tmp_path / "d1", timepoints=["0h"])
    _raw(tmp_path / "d2", timepoints=["0h"])
    plan, summary = _merge(tmp_path, gap_hours=24.0)
    assert plan.raw is True and "raw" in summary
    names = _members(tmp_path / "dest")
    assert names == sorted(f"E_{c}_A01_{f}_{t}.tif" for c in ("DAPI", "GFP")
                           for f in ("f0", "f1") for t in ("0h", "24h"))
    assert not (tmp_path / "dest" / "out").exists()      # no analysis output, no sidecar


def test_each_image_keeps_its_own_bytes_under_the_shifted_name(tmp_path):
    _raw(tmp_path / "d1", timepoints=["0h"])
    _raw(tmp_path / "d2", timepoints=["0h"])
    _merge(tmp_path, gap_hours=24.0)
    with zipfile.ZipFile(tmp_path / "dest" / "in" / "A01.zip") as archive:
        assert archive.read("E_GFP_A01_f1_0h.tif") == b"A01f1GFP0h"          # dataset 1's
        assert archive.read("E_GFP_A01_f1_24h.tif") == b"A01f1GFP0h"         # dataset 2's, renamed
        assert archive.getinfo("E_GFP_A01_f1_0h.tif").file_size == archive.getinfo(
            "E_GFP_A01_f1_24h.tif").file_size


def test_wells_held_by_one_dataset_only_are_carried_across(tmp_path):
    _raw(tmp_path / "d1", wells=["A01", "B02"], timepoints=["0h"])
    _raw(tmp_path / "d2", wells=["A01", "C03"], timepoints=["0h"])
    plan, _ = _merge(tmp_path, gap_hours=24.0)
    assert (plan.wells_ds1_only, plan.wells_ds2_only, plan.wells_both) == (("B02",), ("C03",), ("A01",))
    assert _members(tmp_path / "dest", "B02")[0].endswith("_0h.tif")
    assert _members(tmp_path / "dest", "C03")[0].endswith("_24h.tif")   # shifted, though alone


def test_a_single_timepoint_dataset_1_needs_no_interval_when_raw(tmp_path):
    _raw(tmp_path / "d1", timepoints=["0h"])
    _raw(tmp_path / "d2", timepoints=["0h"])
    with pytest.raises(cc.ConcatError, match="enter the time gap"):
        cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest")
    assert cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest",
                               gap_hours=6.0).offset_hours == 6.0


def test_a_raw_dataset_1_beside_an_analysed_dataset_2_is_refused_with_the_order_to_use(tmp_path):
    from tests.fixtures.dataset_builder import build_dataset
    _raw(tmp_path / "d1", timepoints=["0h"])
    build_dataset(tmp_path / "d2", wells=["A01"], timepoints=["0h", "24h"])
    with pytest.raises(cc.ConcatError, match="Put the analysed dataset first"):
        cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest", gap_hours=6.0)


def test_filenames_laid_out_differently_are_not_joined(tmp_path):
    _raw(tmp_path / "d1", timepoints=["0h"])
    # Same plate, but the channel and well slots swapped: a different schema.
    (tmp_path / "d2" / "in").mkdir(parents=True)
    with zipfile.ZipFile(tmp_path / "d2" / "in" / "A01.zip", "w") as archive:
        for f in ("f0", "f1"):
            for c in ("DAPI", "GFP"):
                archive.writestr(f"A01_E_{c}_{f}_0h.tif", b"x")
    with pytest.raises(cc.ConcatError, match="not laid out the same way|schema"):
        cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest", gap_hours=6.0)


def test_names_that_do_not_say_where_the_timepoint_is_are_refused(tmp_path):
    (tmp_path / "d1" / "in").mkdir(parents=True)
    with zipfile.ZipFile(tmp_path / "d1" / "in" / "A01.zip", "w") as archive:
        archive.writestr("picture1.tif", b"x")
        archive.writestr("picture2.tif", b"x")
    _raw(tmp_path / "d2", timepoints=["0h"])
    with pytest.raises(cc.ConcatError, match="dataset 1"):
        cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest", gap_hours=6.0)


def test_a_dataset_with_analysis_output_is_not_raw(tmp_path):
    _raw(tmp_path / "d1", timepoints=["0h"])
    (tmp_path / "d1" / "out").mkdir()
    assert is_raw_dataset(tmp_path / "d1") is True        # empty out/
    (tmp_path / "d1" / "out" / "x.csv").write_text("a")
    assert is_raw_dataset(tmp_path / "d1") is False


def test_a_dropped_image_fails_verification_rather_than_passing_as_a_merge(tmp_path):
    _raw(tmp_path / "d1", timepoints=["0h"], extra={"notes.txt": b"one"})
    _raw(tmp_path / "d2", timepoints=["0h"], extra={"notes.txt": b"two"})
    with pytest.raises(cc.ConcatError, match="Verification failed"):
        _merge(tmp_path, gap_hours=24.0)


# ── through the real page ────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def qapp():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_the_page_accepts_raw_datasets_merges_them_and_offers_no_review(qapp, tmp_path):
    from datasets.concatenate.controller import ConcatenateController
    from tests._datasets_worker import run_worker_to_completion

    _raw(tmp_path / "d1", timepoints=["0h"])
    _raw(tmp_path / "d2", timepoints=["0h"])
    ctrl = ConcatenateController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._ds1_field.set_path(str(tmp_path / "d1"))
    page._ds2_field.set_path(str(tmp_path / "d2"))
    page._dest_field.set_path(str(tmp_path / "dest"))
    for field in (page._ds1_field, page._ds2_field, page._dest_field):
        field.changed.emit()
    assert page._ds1_field.is_valid() and page._ds2_field.is_valid()
    assert page._run_btn.isEnabled() is False               # needs a gap

    page._gap_edit.setText("24h")
    assert page._run_btn.isEnabled() is True
    page.runRequested.emit()
    run_worker_to_completion(ctrl)
    assert _members(tmp_path / "dest")[-1].endswith("_24h.tif")
    assert page._open_btn.isHidden()                        # Review has nothing to open
    ctrl.shutdown()


def test_a_schema_the_names_only_half_determine_is_refused_not_guessed(tmp_path):
    """``well:timepoint`` names carry a timepoint slot but no channel: the
    inference is not confident, and shifting by a guess could rename wrongly."""
    (tmp_path / "d1" / "in").mkdir(parents=True)
    with zipfile.ZipFile(tmp_path / "d1" / "in" / "A01.zip", "w") as archive:
        archive.writestr("A01_0h.tif", b"x")
        archive.writestr("A01_24h.tif", b"x")
    _raw(tmp_path / "d2", timepoints=["0h"])
    with pytest.raises(cc.ConcatError, match="don't determine a schema"):
        cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest", gap_hours=6.0)


def test_verification_catches_a_member_that_arrived_as_someone_elses_bytes(tmp_path):
    _raw(tmp_path / "d1", timepoints=["0h"])
    _raw(tmp_path / "d2", timepoints=["0h"])
    plan, _ = _merge(tmp_path, gap_hours=24.0)
    dest_zip = tmp_path / "dest" / "in" / "A01.zip"
    with zipfile.ZipFile(dest_zip) as archive:
        members = {n: archive.read(n) for n in archive.namelist()}
    members["E_GFP_A01_f0_24h.tif"] = b"not dataset 2's bytes"
    with zipfile.ZipFile(dest_zip, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    with pytest.raises(cc.ConcatError, match="different bytes"):
        apply._verify_raw(plan, {})


# ── differing tile plans and pixel sizes (M8) ────────────────────────────────
def _scaled_record(root, token, *, pixel_um, tile_px, start):
    from util.acquisition_record import new_record, new_timepoint, write_record
    record = new_record()
    entry = new_timepoint(started_at=start)
    entry["pixel_size_um"] = {"x": pixel_um, "y": pixel_um}
    entry["tile_size_px"] = {"x": tile_px, "y": tile_px}
    record["timepoints"][token] = entry
    record["tiles"] = [{"well": "A01", "field": field, "timepoint": token,
                        "mosaic_xy_px": [i * tile_px, 0], "stage_xy_um": None}
                       for i, field in enumerate(("f0", "f1"))]
    write_record(root, record)


def test_acquisitions_with_different_tile_plans_merge_without_complaint(tmp_path):
    _raw(tmp_path / "d1", timepoints=["0h"], fields=("f0", "f1"))
    _raw(tmp_path / "d2", timepoints=["0h"], fields=("f0", "f1"))
    _scaled_record(tmp_path / "d1", "0h", pixel_um=0.65, tile_px=2048, start="2026-03-01T09:00:00+00:00")
    _scaled_record(tmp_path / "d2", "0h", pixel_um=0.65, tile_px=1024, start="2026-03-02T09:00:00+00:00")
    plan, _ = _merge(tmp_path)                       # the gap comes from the records: 24h
    assert (plan.gap_hours, plan.gap_source) == (24.0, cc.GAP_RECORDED)
    assert plan.scale_warning == ""                  # same pixel size; a smaller tile is fine
    assert _members(tmp_path / "dest")[-1].endswith("_24h.tif")


def test_a_different_pixel_size_warns_in_the_preview_and_the_plan_but_still_merges(tmp_path):
    _raw(tmp_path / "d1", timepoints=["0h"])
    _raw(tmp_path / "d2", timepoints=["0h"])
    _scaled_record(tmp_path / "d1", "0h", pixel_um=0.65, tile_px=2048, start="2026-03-01T09:00:00+00:00")
    _scaled_record(tmp_path / "d2", "0h", pixel_um=1.30, tile_px=2048, start="2026-03-02T09:00:00+00:00")
    preview = cc.gap_preview(tmp_path / "d1", tmp_path / "d2", "")
    assert "different pixel sizes" in preview.warning and "0.65" in preview.warning and "1.3" in preview.warning
    assert preview.error == ""
    plan, _ = _merge(tmp_path)
    assert "1.3" in plan.scale_warning


def test_an_unrecorded_pixel_size_is_not_a_mismatch(tmp_path):
    _raw(tmp_path / "d1", timepoints=["0h"])
    _raw(tmp_path / "d2", timepoints=["0h"])
    _scaled_record(tmp_path / "d1", "0h", pixel_um=0.65, tile_px=2048, start="2026-03-01T09:00:00+00:00")
    assert cc.gap_preview(tmp_path / "d1", tmp_path / "d2", "24h").warning == ""


# ── the merged acquisition record (M9) ───────────────────────────────────────
def _raw_with_record(root, token, *, start, pixel_um, tile_px, offset_um):
    from util.acquisition_record import new_record, new_timepoint, write_record
    _raw(root, timepoints=[token])
    record = new_record(source_files=[f"{root.name}.czi"])
    entry = new_timepoint(started_at=start)
    entry["pixel_size_um"] = {"x": pixel_um, "y": pixel_um}
    entry["tile_size_px"] = {"x": tile_px, "y": tile_px}
    record["timepoints"][token] = entry
    record["tiles"] = [{"well": "A01", "field": field, "timepoint": token,
                        "mosaic_xy_px": [i * tile_px, 0],
                        "stage_xy_um": [offset_um + i * 1000.0, 5.0],
                        "source_file": f"{root.name}.czi"}
                       for i, field in enumerate(("f0", "f1"))]
    write_record(root, record)


def _merged_record(tmp_path):
    from util.acquisition_record import read_record
    return read_record(tmp_path / "dest")


def test_the_merged_record_keeps_each_timepoints_own_clock_calibration_and_positions(tmp_path):
    _raw_with_record(tmp_path / "d1", "0h", start="2026-03-01T09:00:00+00:00",
                     pixel_um=0.65, tile_px=2048, offset_um=100.0)
    _raw_with_record(tmp_path / "d2", "0h", start="2026-03-02T11:30:00+00:00",
                     pixel_um=0.65, tile_px=1024, offset_um=777.0)     # the plate went back differently
    _merge(tmp_path, gap_hours=26.5)
    record = _merged_record(tmp_path)
    assert sorted(record["timepoints"]) == ["0h", "26.5h"]
    assert record["timepoints"]["0h"]["tile_size_px"]["x"] == 2048          # dataset 1's own
    assert record["timepoints"]["26.5h"]["tile_size_px"]["x"] == 1024       # dataset 2's own
    starts = {e["started_at"] for e in record["timepoints"].values()}
    assert starts == {"2026-03-01T09:00:00+00:00", "2026-03-02T11:30:00+00:00"}   # each its own, unshifted
    sizes = sorted(e["tile_size_px"]["x"] for e in record["timepoints"].values())
    assert sizes == [1024, 2048]                                                  # plans differ and stay different
    shifted = [t for t in record["tiles"] if t["timepoint"] == "26.5h"]
    assert sorted(t["stage_xy_um"][0] for t in shifted) == [777.0, 1777.0]        # dataset 2's own positions
    assert {t["source_file"] for t in record["tiles"]} == {"dataset1/d1.czi", "dataset2/d2.czi"}
    assert record["concatenations"] == [{"gap_hours": 26.5, "gap_source": "entered",
                                         "offset_hours": 26.5}]


def test_the_record_names_the_same_tiles_as_the_merged_images(tmp_path):
    from util.acquisition_record import validate_against_archives
    _raw_with_record(tmp_path / "d1", "0h", start="2026-03-01T09:00:00+00:00",
                     pixel_um=0.65, tile_px=2048, offset_um=0.0)
    _raw_with_record(tmp_path / "d2", "0h", start="2026-03-02T09:00:00+00:00",
                     pixel_um=0.65, tile_px=2048, offset_um=0.0)
    _merge(tmp_path, gap_hours=24.0)
    assert validate_against_archives(tmp_path / "dest", _merged_record(tmp_path),
                                     schema="experiment:channel:well:fov:timepoint").ok


def test_a_dataset_without_a_record_leaves_none_and_says_so(tmp_path):
    _raw_with_record(tmp_path / "d1", "0h", start="2026-03-01T09:00:00+00:00",
                     pixel_um=0.65, tile_px=2048, offset_um=0.0)
    _raw(tmp_path / "d2", timepoints=["0h"])
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest", gap_hours=24.0)
    lines: list[str] = []
    apply.execute_plan(plan, lines.append)
    assert _merged_record(tmp_path) is None
    assert any("has no acquisition record" in line for line in lines)


def test_two_raw_datasets_without_records_say_the_gap_is_recorded_nowhere_else(tmp_path):
    # A raw merge has no sidecar for derived_from to live in, so with no acquisition records either
    # the gap and its source exist only in the run log. That has to be said, not left implicit.
    _raw(tmp_path / "d1", timepoints=["0h"])
    _raw(tmp_path / "d2", timepoints=["0h"])
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest", gap_hours=24.0)
    lines: list[str] = []
    apply.execute_plan(plan, lines.append)
    assert _merged_record(tmp_path) is None
    notes = [line for line in lines if "acquisition record" in line]
    assert len(notes) == 1
    assert "24h" in notes[0] and "entered" in notes[0] and "nowhere except this log" in notes[0]


def test_an_image_with_no_timepoint_is_named_in_the_log_and_the_summary(tmp_path):
    # It lands unshifted, so verification (whose expected name is the original by the same rule)
    # cannot call it wrong; the user has to be told.
    _raw(tmp_path / "d1", timepoints=["0h"])
    _raw(tmp_path / "d2", timepoints=["0h"], extra={"E_DAPI_A01_f0.tif": b"x", "notes.txt": b"hi"})
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest", gap_hours=24.0)
    lines: list[str] = []
    summary = apply.execute_plan(plan, lines.append)
    note = next(line for line in lines if "no timepoint in their file name" in line)
    assert "1 dataset 2 image(s)" in note and "A01/E_DAPI_A01_f0.tif" in note
    assert "notes.txt" not in note                          # not an image: nothing to shift
    assert "1 image(s) had no timepoint to shift" in summary


def test_two_datasets_spelling_a_well_differently_are_refused(tmp_path):
    _raw(tmp_path / "d1", wells=["A01"], timepoints=["0h"])
    _raw(tmp_path / "d2", wells=["A1"], timepoints=["0h"])
    with pytest.raises(cc.ConcatError, match="spell the same well differently") as caught:
        cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest", gap_hours=24.0)
    assert "'A01.zip'" in str(caught.value) and "'A1.zip'" in str(caught.value)


def test_analysed_datasets_carry_their_records_too_with_the_gap_source(tmp_path):
    from tests.fixtures.dataset_builder import build_dataset
    from tests.test_concatenate_gap import _record
    build_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0h", "24h"])
    build_dataset(tmp_path / "d2", wells=["A01"], timepoints=["0h"])
    _record(tmp_path / "d1", {"0h": "2026-03-01T09:00:00+00:00", "24h": "2026-03-02T09:00:00+00:00"})
    _record(tmp_path / "d2", {"0h": "2026-03-03T15:00:00+00:00"})
    plan, _ = _merge(tmp_path)                      # the gap comes from the records
    record = _merged_record(tmp_path)
    assert sorted(record["timepoints"]) == ["0h", "24h", "54h"]
    assert record["concatenations"][-1]["gap_source"] == "recorded"
    assert record["concatenations"][-1]["gap_hours"] == 30.0


# ── an analysed dataset 1 with a raw dataset 2 (M12) ─────────────────────────
def _analysed_then_raw(tmp_path):
    from tests.fixtures.dataset_builder import build_dataset
    build_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0h", "24h"])
    _raw(tmp_path / "d2", timepoints=["0h"], fields=("F001",), channels=("GFP", "NIR"), exp="exp")


def _pending(dest, well="A01"):
    """What the pipeline's incremental run would still have to process."""
    from pipeline.incremental_keys import fov_keys_from_member_names, read_done_fov_keys
    schema, sep = "experiment:channel:well:fov:timepoint", "_"
    with zipfile.ZipFile(dest / "in" / f"{well}.zip") as archive:
        present = fov_keys_from_member_names(archive.namelist(), schema=schema, sep=sep,
                                             images_only=True)
    return present - read_done_fov_keys(dest / "out" / f"measurements_{well}.csv")


def test_dataset_1_s_results_are_kept_and_exactly_dataset_2_s_timepoints_are_left_to_analyse(tmp_path):
    import csv
    _analysed_then_raw(tmp_path)
    plan, summary = _merge(tmp_path, gap_hours=24.0)
    assert plan.ds2_raw is True and "Run Analyze" in summary
    with (tmp_path / "dest" / "out" / "measurements_A01.csv").open() as handle:
        timepoints = {row["timepoint"] for row in csv.DictReader(handle)}
    assert timepoints == {"0h", "24h"}                       # dataset 1's rows, nothing invented
    # The pipeline's own incremental rule: dataset 2's shifted timepoint is what is left.
    assert _pending(tmp_path / "dest") == {("F001", "48h")}


def test_the_sidecar_stays_dataset_1_s_and_takes_nothing_from_a_filename_reading(tmp_path):
    import json
    _analysed_then_raw(tmp_path)
    _merge(tmp_path, gap_hours=24.0)
    merged = json.loads((tmp_path / "dest" / "out" / "pipeline_info.json").read_text())
    original = json.loads((tmp_path / "d1" / "out" / "pipeline_info.json").read_text())
    assert merged["available_timepoints"] == original["available_timepoints"] == ["0h", "24h"]
    assert merged["fluor_tokens"] == original["fluor_tokens"]   # not unioned with the inferred channels


def test_a_well_only_dataset_2_holds_is_added_as_images_and_no_well_is_flagged_for_missing_rows(tmp_path):
    from tests.fixtures.dataset_builder import build_dataset
    build_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0h", "24h"])
    _raw(tmp_path / "d2", wells=["A01", "B02"], timepoints=["0h"], fields=("F001",),
         channels=("GFP", "NIR"), exp="exp")
    plan = cc.build_merge_plan(tmp_path / "d1", tmp_path / "d2", tmp_path / "dest", gap_hours=24.0)
    stages: list[tuple[str, str]] = []
    apply.execute_plan(plan, progress=lambda well, stage: stages.append((well, stage)))
    assert ("A01", "warned") not in stages and ("B02", "warned") not in stages
    assert all(name.endswith("_F001_48h.tif") for name in _members(tmp_path / "dest", "B02"))


def test_dataset_2_s_images_are_verified_in_a_mixed_merge_too(tmp_path, monkeypatch):
    """The image check is what covers dataset 2 when it has no rows to cross-check."""
    _analysed_then_raw(tmp_path)
    calls = []
    real = apply._verify_raw
    monkeypatch.setattr(apply, "_verify_raw", lambda plan, dropped: (calls.append(plan.ds2_raw), real(plan, dropped)))
    _merge(tmp_path, gap_hours=24.0)
    assert calls == [True]
