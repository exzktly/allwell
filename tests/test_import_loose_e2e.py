"""End-to-end test for Import Loose Folder: a folder of loose schema-named
TIFs converted into a dataset's in/ zips, then read back the way Analyze's
own resolve_input_output / iter_well_tokens would see it.

Qt-free and dependency-free, runs in the fast lane.
"""

from __future__ import annotations

import zipfile

from datasets.import_loose.apply import convert_to_new_destination
from datasets.import_loose.import_loose_compute import scan_folder
from services.input_resolution_service import iter_well_tokens

SCHEMA = "experiment:channel:well:fov:timepoint"
SEP = "_"


def _write_loose(folder, wells, timepoints, fovs=("F001",), exp="exp", channel="GFP"):
    folder.mkdir(parents=True, exist_ok=True)
    for well in wells:
        for fov in fovs:
            for tp in timepoints:
                (folder / f"{exp}_{channel}_{well}_{fov}_{tp}.tif").write_bytes(b"img")


def test_import_loose_end_to_end_then_readable_by_analyze_input_resolution(tmp_path):
    src = tmp_path / "loose"
    dest = tmp_path / "dataset"
    _write_loose(src, ["A01", "B02", "C03"], timepoints=("0h", "24h", "48h"), fovs=("F001", "F002"))

    preview = scan_folder(src, filename_schema=SCHEMA, filename_sep=SEP)
    assert preview.wells == ("A01", "B02", "C03")
    assert preview.channels == ("GFP",)

    summary = convert_to_new_destination(src, dest, filename_schema=SCHEMA, filename_sep=SEP)
    assert "3 well(s)" in summary

    # The exact function Analyze's input resolution uses to enumerate an
    # already-grouped in/ folder — proves the output layout is the one the
    # rest of the app actually reads, not just "zip files exist".
    tokens = iter_well_tokens(dest / "in")
    assert tokens == ["A01", "B02", "C03"]

    with zipfile.ZipFile(dest / "in" / "A01.zip") as zf:
        members = sorted(zf.namelist())
    assert len(members) == 2 * 3  # 2 FOVs x 3 timepoints
    assert all(m.startswith("exp_GFP_A01_") for m in members)

    # Source untouched.
    assert sorted(p.name for p in src.iterdir()) == sorted(
        f"exp_GFP_{w}_{fov}_{tp}.tif"
        for w in ("A01", "B02", "C03") for fov in ("F001", "F002") for tp in ("0h", "24h", "48h")
    )


def test_convert_reports_a_rising_sub_progress_sweep_per_well(tmp_path):
    """``sub_progress`` is the per-well dial behind ``progress``'s
    writing/done stages: a well fills as its files go into the archive,
    rather than jumping straight from empty to done. Every planned well must
    reach full — a well that finished but reported nothing would leave its
    dial looking stalled."""
    src = tmp_path / "loose"
    dest = tmp_path / "dataset"
    _write_loose(src, ["A01", "B02"], timepoints=("0h", "24h", "48h"), fovs=("F001", "F002"))

    fractions: dict[str, list[tuple[int, int]]] = {}
    convert_to_new_destination(
        src, dest, filename_schema=SCHEMA, filename_sep=SEP,
        sub_progress=lambda well, done, total: fractions.setdefault(well, []).append((done, total)),
    )

    assert set(fractions) == {"A01", "B02"}
    for well, seq in fractions.items():
        values = [done for done, _total in seq]
        assert values == sorted(values), f"{well} dial went backwards: {values}"
        assert seq[-1][0] == seq[-1][1], f"{well} dial never reached full: {seq[-1]}"
