"""Loose-TIFs-inside-an-``in/``-folder grouping + discovery progress.

Regression for the reported "20 wells but it says Grouping 0/1, then hangs"
case: the user put ungrouped TIFs inside an ``in/`` folder, which the app used
to mistake for already-grouped input (so the grouper never ran). These tests
drive the real ``resolve_input_output`` (which shells out to WellPlateZipper —
stdlib only, no imaging stack) and the offscreen AnalyzeTab counter/preview.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

from services.input_resolution_service import (
    append_loose_images_to_zips,
    resolve_input_output,
)

SCHEMA = "experiment:channel:well:fov:timepoint"
SEP = "_"


def _make_loose(folder: Path, wells, tps=("T01", "T02")) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    n = 0
    for w in wells:
        for tp in tps:
            (folder / f"exp_NIR_{w}_1_{tp}.tif").write_bytes(b"x")
            n += 1
    return n


def test_loose_tifs_in_named_in_folder_are_grouped(tmp_path):
    in_dir = tmp_path / "in"
    _make_loose(in_dir, ("A01", "A02", "B03", "C04"))
    logs: list[str] = []

    input_dir, output_dir, on_success = resolve_input_output(
        in_dir, log_fn=logs.append,
        filename_schema=SCHEMA, filename_sep=SEP)

    assert input_dir == in_dir
    assert output_dir == tmp_path / "out"
    # Grouped into per-well folders in place (4 wells).
    for w in ("A01", "A02", "B03", "C04"):
        assert (in_dir / w).is_dir()
        assert any((in_dir / w).glob("*.tif"))
    # Discovery progress is surfaced (no silent hang).
    joined = "".join(logs)
    assert "Scanning" in joined
    assert "Discovery complete" in joined
    # Copy-mode: loose originals remain until the post-success cleanup fires.
    assert list(in_dir.glob("*.tif"))
    assert on_success is not None
    on_success()
    assert not list(in_dir.glob("*.tif"))      # loose originals removed
    assert (in_dir / "A01").is_dir()           # grouped folders kept


def test_loose_tifs_in_in_subfolder_grouped_via_parent(tmp_path):
    raw = tmp_path / "experiment"
    in_dir = raw / "in"
    _make_loose(in_dir, ("A01", "B02"))

    input_dir, output_dir, on_success = resolve_input_output(
        raw, filename_schema=SCHEMA, filename_sep=SEP)

    assert input_dir == in_dir
    assert output_dir == raw / "out"
    assert (in_dir / "A01").is_dir() and (in_dir / "B02").is_dir()


def test_partial_grouped_in_folder_consumes_remaining_loose(tmp_path):
    # Interrupted prior run: some wells already grouped into in/<well>/, the
    # rest still loose inside in/. The remaining loose must get grouped too
    # (this used to stall at "0/5" with the leftovers ignored).
    in_dir = tmp_path / "in"
    for w in ("A01", "A02"):                      # 2 wells already grouped
        (in_dir / w).mkdir(parents=True)
        (in_dir / w / f"exp_NIR_{w}_1_T01.tif").write_bytes(b"x")
    _make_loose(in_dir, ("B03", "C04", "D05"))    # 3 wells still loose in in/

    input_dir, output_dir, on_success = resolve_input_output(
        in_dir, filename_schema=SCHEMA, filename_sep=SEP)

    assert input_dir == in_dir
    for w in ("A01", "A02", "B03", "C04", "D05"):  # all 5 now grouped
        assert (in_dir / w).is_dir() and any((in_dir / w).glob("*.tif"))
    on_success()
    assert not list(in_dir.glob("*.tif"))          # leftovers consumed + cleaned


def test_expected_well_count_unions_grouped_and_loose(tmp_path):
    from services.input_resolution_service import count_expected_wells
    in_dir = tmp_path / "in"
    for w in ("A01", "A02"):                       # 2 grouped
        (in_dir / w).mkdir(parents=True)
        (in_dir / w / f"exp_NIR_{w}_1_T01.tif").write_bytes(b"x")
    _make_loose(in_dir, ("B03", "C04", "D05"))     # 3 loose
    # Selecting the parent of in/
    assert count_expected_wells(
        tmp_path, filename_schema=SCHEMA, filename_sep=SEP) == 5
    # Selecting the in/ folder directly
    assert count_expected_wells(
        in_dir, filename_schema=SCHEMA, filename_sep=SEP) == 5


def _zip_well(path: Path, well: str) -> None:
    import zipfile
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"exp_NIR_{well}_1_T01.tif", b"x")


def test_mixed_in_folder_is_consolidated_to_zips(tmp_path):
    # Hard-killed run left some wells as <well>.zip and others as bare folders.
    # The folder-only wells must be zipped so the zip-preferring pipeline reads
    # them (instead of silently dropping them).
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    _zip_well(in_dir / "A01.zip", "A01")
    _zip_well(in_dir / "A02.zip", "A02")
    for w in ("B03", "C04"):                       # bare folders, no zip yet
        (in_dir / w).mkdir()
        (in_dir / w / f"exp_NIR_{w}_1_T01.tif").write_bytes(b"x")

    input_dir, output_dir, on_success = resolve_input_output(
        in_dir, filename_schema=SCHEMA, filename_sep=SEP)

    assert input_dir == in_dir
    # every well is now a zip; the bare folders were consolidated + removed
    for w in ("A01", "A02", "B03", "C04"):
        assert (in_dir / f"{w}.zip").exists()
    assert not (in_dir / "B03").exists()
    assert not (in_dir / "C04").exists()


def test_mixed_with_leftover_loose_consolidates_everything(tmp_path):
    # The nastiest state: some zips, some folders, AND loose TIFs — all in in/.
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    _zip_well(in_dir / "A01.zip", "A01")           # already a zip
    (in_dir / "B02").mkdir()                        # a bare folder
    (in_dir / "B02" / "exp_NIR_B02_1_T01.tif").write_bytes(b"x")
    _make_loose(in_dir, ("C03", "D04"))            # loose for 2 more wells

    input_dir, _, on_success = resolve_input_output(
        in_dir, filename_schema=SCHEMA, filename_sep=SEP)

    # Two-phase contract: a well zip already present means ``resolve`` defers
    # loose-file handling to ``append_loose_images_to_zips`` (so the append can
    # report which wells gained data and drive FOV-level incremental). ``resolve``
    # alone consolidates the bare folder; the loose files are folded by append.
    assert on_success is None
    assert not (in_dir / "B02").exists()           # folder consolidated to a zip
    for w in ("A01", "B02"):
        assert (in_dir / f"{w}.zip").exists()
    assert sorted(p.name for p in in_dir.glob("*.tif"))  # C03/D04 still loose

    append_loose_images_to_zips(in_dir, filename_schema=SCHEMA, filename_sep=SEP)

    # After the append, all four wells are zips and the loose originals are gone.
    for w in ("A01", "B02", "C03", "D04"):
        assert (in_dir / f"{w}.zip").exists()
    assert not list(in_dir.glob("*.tif"))


def test_pure_folders_are_left_as_folders(tmp_path):
    # No zips present → not mixed → keep folder mode (don't needlessly zip).
    in_dir = tmp_path / "in"
    for w in ("A01", "B02"):
        (in_dir / w).mkdir(parents=True)
        (in_dir / w / f"exp_NIR_{w}_1_T01.tif").write_bytes(b"x")
    resolve_input_output(in_dir, filename_schema=SCHEMA, filename_sep=SEP)
    assert (in_dir / "A01").is_dir() and (in_dir / "B02").is_dir()
    assert not list(in_dir.glob("*.zip"))


def test_already_grouped_in_folder_is_not_regrouped(tmp_path):
    # Pre-grouped well folders inside in/ must be used as-is (no on_success,
    # no re-run of the grouper).
    in_dir = tmp_path / "in"
    (in_dir / "A01").mkdir(parents=True)
    (in_dir / "A01" / "exp_NIR_A01_1_T01.tif").write_bytes(b"x")

    input_dir, output_dir, on_success = resolve_input_output(
        in_dir, filename_schema=SCHEMA, filename_sep=SEP)

    assert input_dir == in_dir
    assert on_success is None


# ── AnalyzeTab counter + preview (offscreen Qt) ───────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_expected_well_count_counts_loose_in_named_in(qapp, tmp_path):
    from analyze_tab import AnalyzeTab
    tab = AnalyzeTab()
    in_dir = tmp_path / "in"
    _make_loose(in_dir, ("A01", "A02", "B03"))
    opts = {"raw": in_dir, "filename_schema": SCHEMA, "filename_sep": SEP}
    assert tab._expected_well_count(opts) == 3   # not the old "1" fallback


def test_refresh_output_previews_loose_in_folder(qapp, tmp_path):
    from analyze_tab import AnalyzeTab
    tab = AnalyzeTab()
    in_dir = tmp_path / "in"
    _make_loose(in_dir, ("A01", "A02"))          # 4 files > 3 threshold
    tab._input_edit.setText(str(in_dir))
    tab._refresh_output()
    assert "group" in tab._layout_lbl.text().lower()
    assert "loose" in tab._layout_lbl.text().lower()
