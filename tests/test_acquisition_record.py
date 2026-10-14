"""Acquisition metadata and archive coverage through the real on-disk APIs."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from util.acquisition_record import (AcquisitionRecordError, new_record, read_record,
                                     record_path, validate_against_archives,
                                     validate_record, write_record)


def _record():
    record = new_record(source_files=["Plate.czi"])
    record.update(pixel_size_um={"x": .65, "y": None}, tile_size_px={"x": 2048, "y": 1024},
                  objective="10x", declared_overlap_percent={"x": 10, "y": 15},
                  timepoints={"0h": {"started_at": "2026-10-14T10:00:00-04:00"},
                              "24h": {"started_at": None}})
    record["tiles"] = [dict(well=well, field=field, timepoint=token,
                            mosaic_xy_px=[0, -100] if field == "f0" else None,
                            stage_xy_um=[123.5, -200.5], source_file="Plate.czi")
                       for well in ["A01", "B02"] for field in ["f0", "f1"]
                       for token in ["0h", "24h"]]
    return record


def _archives(root, record, *, omit=(), extra=()):
    directory = root / "in"
    directory.mkdir(parents=True, exist_ok=True)
    tiles = list(record["tiles"]) + list(extra)
    for well in sorted({tile["well"] for tile in tiles}):
        with zipfile.ZipFile(directory / f"{well}.zip", "w") as archive:
            for tile in tiles:
                key = tile["well"], tile["field"], tile["timepoint"]
                if tile["well"] != well or key in omit:
                    continue
                for channel in ["phase", "DAPI"]:
                    name = f"Plate_{channel}_{well}_{tile['field']}_{tile['timepoint']}.tif"
                    archive.writestr(name, b"headers-only fixture; not pixel data")
            archive.writestr("notes.txt", "non-image metadata")
            archive.writestr("._Plate_phase_A01_f999_0h.tif", "AppleDouble")
            archive.writestr("__MACOSX/Plate_phase_A01_f998_0h.tif", "sidecar")


def test_real_writer_reader_preserve_unknowns_zero_positions_and_provenance(tmp_path):
    record = _record()
    record["provenance"] = {"native_unit": "documented micrometres"}
    write_record(tmp_path, record)
    assert record_path(tmp_path) == tmp_path / "in" / "acquisition.json"
    loaded = read_record(tmp_path)
    assert loaded == record
    assert loaded["pixel_size_um"]["y"] is None
    assert loaded["timepoints"]["24h"]["started_at"] is None
    assert loaded["tiles"][0]["mosaic_xy_px"] == [0, -100]
    assert loaded["timepoints"]["0h"]["started_at"].endswith("-04:00")
    assert not list((tmp_path / "in").glob("__aw_tmp_*"))


def test_missing_corrupt_and_unsupported_records_are_three_distinct_states(tmp_path):
    assert read_record(tmp_path) is None
    path = record_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not valid JSON")
    with pytest.raises(AcquisitionRecordError, match="Cannot read"):
        read_record(tmp_path)
    record = _record()
    record["format_version"] = 2
    path.write_text(json.dumps(record))
    with pytest.raises(AcquisitionRecordError, match="Unsupported"):
        read_record(tmp_path)


def test_invalid_metadata_does_not_replace_a_good_record(tmp_path):
    good = _record()
    write_record(tmp_path, good)
    before = record_path(tmp_path).read_bytes()
    invalid = copy.deepcopy(good)
    invalid["pixel_size_um"]["x"] = 0
    with pytest.raises(AcquisitionRecordError, match="pixel_size_um.x"):
        write_record(tmp_path, invalid)
    assert record_path(tmp_path).read_bytes() == before


def test_atomic_replace_failure_preserves_old_content_and_cleans_staging(tmp_path, monkeypatch):
    import util.json_write as json_write

    record = _record()
    write_record(tmp_path, record)
    before = record_path(tmp_path).read_bytes()
    changed = copy.deepcopy(record)
    changed["objective"] = "20x"

    def cannot_publish(*args):
        raise OSError("simulated interrupted publish")

    monkeypatch.setattr(json_write.os, "replace", cannot_publish)
    failure = None
    try:
        write_record(tmp_path, changed)
    except OSError as error:
        failure = error
    assert record_path(tmp_path).read_bytes() == before, "A failed publish must preserve the prior acquisition record"
    assert failure is not None and "interrupted publish" in str(failure)
    assert not list((tmp_path / "in").glob("__aw_tmp_*"))


@pytest.mark.parametrize("change,fragment", [
    (lambda r: r.update(format_version=True), "format_version"),
    (lambda r: r["pixel_size_um"].update(x=float("nan")), "pixel_size_um.x"),
    (lambda r: r["pixel_size_um"].update(x=10 ** 400), "pixel_size_um.x"),
    (lambda r: r["tile_size_px"].update(x=2.5), "tile_size_px.x"),
    (lambda r: r["declared_overlap_percent"].update(x=101), "overlap_percent.x"),
    (lambda r: r["tiles"].append(copy.deepcopy(r["tiles"][0])), "Duplicate tile"),
    (lambda r: r["tiles"][0].update(timepoint="72h"), "undeclared timepoint"),
    (lambda r: r["tiles"][0].update(stage_xy_um=[True, 1]), "stage_xy_um"),
    (lambda r: r["tiles"][0].update(source_file="another.czi"), "source_file"),
    (lambda r: r["timepoints"]["0h"].update(started_at="10:30"), "acquisition start"),
])
def test_bad_metadata_is_reported_without_silent_defaults(change, fragment):
    record = _record()
    change(record)
    result = validate_record(record)
    assert not result.ok and any(fragment in issue for issue in result.issues)


def test_channel_planes_share_one_tile_and_sidecars_do_not_create_phantom_fields(tmp_path):
    record = _record()
    _archives(tmp_path, record)
    result = validate_against_archives(tmp_path, record)
    assert result.ok, result.issues


def test_record_extra_and_missing_wells_fields_and_times_are_all_named(tmp_path):
    record = _record()
    _archives(tmp_path, record, omit=[("A01", "f1", "24h")],
              extra=[dict(well="A01", field="f2", timepoint="0h"),
                     dict(well="C03", field="f0", timepoint="0h")])
    # A record-only well is missing from the dataset, while C03 is archive-only.
    record["tiles"].append(dict(well="D04", field="f0", timepoint="0h",
                                mosaic_xy_px=None, stage_xy_um=None))
    result = validate_against_archives(tmp_path, record)
    assert not result.ok
    text = "\n".join(result.issues)
    assert "absent well archive D04" in text and "missing well archive C03" in text
    assert "absent tile ('A01', 'f1', '24h')" in text
    assert "missing tile ('A01', 'f2', '0h')" in text
    assert "missing tile ('C03', 'f0', '0h')" in text


def test_custom_filename_schema_is_used_without_numeric_token_normalisation(tmp_path):
    record = new_record()
    record["timepoints"] = {"t0": {"started_at": None}}
    record["tiles"] = [dict(well="A01", field="f01", timepoint="t0",
                            mosaic_xy_px=None, stage_xy_um=None)]
    path = tmp_path / "in" / "A01.zip"
    path.parent.mkdir()
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("t0-A01-f01-phase.png", "header")
    assert validate_against_archives(tmp_path, record, schema="timepoint:well:fov:channel", sep="-").ok
    record["tiles"][0]["field"] = "f1"
    result = validate_against_archives(tmp_path, record, schema="timepoint:well:fov:channel", sep="-")
    assert not result.ok and len(result.issues) == 2, "f1 and f01 must remain different field identities"


def test_unreadable_archive_is_not_reported_as_a_measured_empty_well(tmp_path):
    record = _record()
    _archives(tmp_path, record)
    (tmp_path / "in" / "A01.zip").write_bytes(b"damaged zip")
    result = validate_against_archives(tmp_path, record)
    assert not result.ok and any("Cannot inspect archive A01.zip" in issue for issue in result.issues)
    assert not any("absent tile ('A01'" in issue for issue in result.issues)


def test_import_is_numpy_and_qt_free_in_a_fresh_interpreter():
    command = "import sys; import util.acquisition_record; assert 'numpy' not in sys.modules; assert 'PySide6' not in sys.modules"
    completed = subprocess.run([sys.executable, "-c", command], cwd=Path(__file__).resolve().parents[1],
                               capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_duplicate_json_metadata_keys_are_refused_not_silently_overwritten(tmp_path):
    record = _record()
    write_record(tmp_path, record)
    path = record_path(tmp_path)
    text = path.read_text().replace('"objective": "10x"', '"objective": "20x", "objective": "10x"')
    path.write_text(text)
    with pytest.raises(AcquisitionRecordError, match="Duplicate JSON key.*objective"):
        read_record(tmp_path)
