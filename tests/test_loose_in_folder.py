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

from services.input_resolution_service import resolve_input_output

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
