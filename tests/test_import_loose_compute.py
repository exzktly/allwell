"""Fast-lane Qt-free tests for `datasets.import_loose`.

Tests write minimal schema-named ``.tif`` placeholder files directly (content
is irrelevant here — this feature only groups by filename, it never reads
image data), rather than the heavier ``tests/fixtures/synthetic_pipeline.py``
generator meant for real segmentation E2E tests.
"""

from __future__ import annotations

import zipfile

import pytest

from datasets.import_loose import apply as il_apply
from datasets.import_loose import import_loose_compute as ilc

SCHEMA = "experiment:channel:well:fov:timepoint"
SEP = "_"


def _write_loose(folder, wells, timepoints=("0h",), fovs=("F001",), exp="exp", channel="GFP"):
    folder.mkdir(parents=True, exist_ok=True)
    for well in wells:
        for fov in fovs:
            for tp in timepoints:
                (folder / f"{exp}_{channel}_{well}_{fov}_{tp}.tif").write_bytes(b"img")


def test_scan_reports_wells_fovs_timepoints_channels(tmp_path):
    _write_loose(tmp_path, ["A01", "B02"], timepoints=("0h", "24h"), fovs=("F001", "F002"))
    preview = ilc.scan_folder(tmp_path, filename_schema=SCHEMA, filename_sep=SEP)
    assert preview.wells == ("A01", "B02")
    assert preview.timepoints == ("0h", "24h")
    assert preview.fovs == ("F001", "F002")
    assert preview.channels == ("GFP",)
    assert preview.matched == 8
    assert preview.unparsed_files == ()
    assert preview.unreadable_count == 0


def test_wrong_separator_reports_everything_unparsed_no_exception(tmp_path):
    _write_loose(tmp_path, ["A01"])
    preview = ilc.scan_folder(tmp_path, filename_schema=SCHEMA, filename_sep="|")
    assert preview.matched == 0
    assert preview.wells == ()
    assert len(preview.unparsed_files) == 1


def test_invalid_well_token_is_unparsed_not_matched(tmp_path):
    _write_loose(tmp_path, ["A01"])
    (tmp_path / "exp_GFP_Z99_F001_0h.tif").write_bytes(b"img")
    preview = ilc.scan_folder(tmp_path, filename_schema=SCHEMA, filename_sep=SEP)
    assert "Z99" not in preview.wells
    assert any("Z99" in p for p in preview.unparsed_files)


def test_dangling_symlink_counted_not_crashed_on(tmp_path):
    _write_loose(tmp_path, ["A01"])
    target = tmp_path / "gone.tif"
    target.write_bytes(b"img")
    link = tmp_path / "exp_GFP_A01_F001_48h.tif"
    link.symlink_to(target)
    target.unlink()

    preview = ilc.scan_folder(tmp_path, filename_schema=SCHEMA, filename_sep=SEP)
    assert preview.unreadable_count == 1
    assert "48h" not in preview.timepoints


def test_non_tif_files_ignored(tmp_path):
    _write_loose(tmp_path, ["A01"])
    (tmp_path / "notes.txt").write_text("hi")
    (tmp_path / ".DS_Store").write_bytes(b"")
    preview = ilc.scan_folder(tmp_path, filename_schema=SCHEMA, filename_sep=SEP)
    assert preview.matched == 1


def test_non_recursive_by_default(tmp_path):
    _write_loose(tmp_path, ["A01"])
    sub = tmp_path / "subdir"
    _write_loose(sub, ["B02"])
    preview = ilc.scan_folder(tmp_path, filename_schema=SCHEMA, filename_sep=SEP)
    assert preview.wells == ("A01",)

    preview_r = ilc.scan_folder(tmp_path, filename_schema=SCHEMA, filename_sep=SEP, recursive=True)
    assert set(preview_r.wells) == {"A01", "B02"}


def test_convert_to_new_destination_matches_preview_and_leaves_source(tmp_path):
    src = tmp_path / "loose"
    dest = tmp_path / "dest"
    _write_loose(src, ["A01", "B02"], timepoints=("0h", "24h"))
    preview = ilc.scan_folder(src, filename_schema=SCHEMA, filename_sep=SEP)

    before = sorted(p.name for p in src.iterdir())
    summary = il_apply.convert_to_new_destination(
        src, dest, filename_schema=SCHEMA, filename_sep=SEP,
    )
    assert f"{len(preview.wells)} well(s)" in summary

    with zipfile.ZipFile(dest / "in" / "A01.zip") as zf:
        members = set(zf.namelist())
    assert members == {"exp_GFP_A01_F001_0h.tif", "exp_GFP_A01_F001_24h.tif"}

    after = sorted(p.name for p in src.iterdir())
    assert before == after


def test_convert_refuses_when_nothing_matches(tmp_path):
    src = tmp_path / "loose"
    dest = tmp_path / "dest"
    src.mkdir()
    (src / "readme.txt").write_text("hi")
    with pytest.raises(ValueError, match="No files matched"):
        il_apply.convert_to_new_destination(src, dest, filename_schema=SCHEMA, filename_sep=SEP)


def test_group_files_by_well_and_scan_agree(tmp_path):
    _write_loose(tmp_path, ["A01", "B02", "C03"], timepoints=("0h", "24h"))
    preview = ilc.scan_folder(tmp_path, filename_schema=SCHEMA, filename_sep=SEP)
    by_well = ilc.group_files_by_well(tmp_path, filename_schema=SCHEMA, filename_sep=SEP)
    assert set(by_well) == set(preview.wells)
    assert sum(len(v) for v in by_well.values()) == preview.matched
