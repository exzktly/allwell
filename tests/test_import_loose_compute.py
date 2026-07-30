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


def test_convert_to_new_destination_writes_every_well_in_parallel(tmp_path):
    """Enough wells to force >1 worker (default auto-sizing), each written
    from its own thread — every well's archive must still land intact with
    no cross-well interference."""
    src = tmp_path / "loose"
    dest = tmp_path / "dest"
    wells = [f"{r}{c:02d}" for r in "ABCDEFGH" for c in range(1, 3)]  # 16 wells
    _write_loose(src, wells, timepoints=("0h", "24h"))

    summary = il_apply.convert_to_new_destination(
        src, dest, filename_schema=SCHEMA, filename_sep=SEP,
    )
    assert f"{len(wells)} well(s)" in summary

    for well in wells:
        with zipfile.ZipFile(dest / "in" / f"{well}.zip") as zf:
            members = set(zf.namelist())
        assert members == {f"exp_GFP_{well}_F001_0h.tif", f"exp_GFP_{well}_F001_24h.tif"}


def test_convert_to_new_destination_respects_explicit_worker_count(tmp_path):
    src = tmp_path / "loose"
    dest = tmp_path / "dest"
    _write_loose(src, ["A01", "B02", "C03"], timepoints=("0h",))

    summary = il_apply.convert_to_new_destination(
        src, dest, filename_schema=SCHEMA, filename_sep=SEP, workers=1,
    )
    assert "3 well(s)" in summary
    assert (dest / "in" / "B02.zip").exists()


def test_convert_to_new_destination_stops_on_cancellation(tmp_path):
    src = tmp_path / "loose"
    dest = tmp_path / "dest"
    _write_loose(src, ["A01", "B02", "C03", "D04"], timepoints=("0h",))

    with pytest.raises(ValueError, match="Cancelled"):
        il_apply.convert_to_new_destination(
            src, dest, filename_schema=SCHEMA, filename_sep=SEP,
            is_cancelled=lambda: True,
        )
    # Nothing not-yet-started when cancellation hit gets written.
    assert not (dest / "in").exists() or not any((dest / "in").iterdir())


def test_convert_to_new_destination_callback_invocations_are_not_corrupted(tmp_path):
    """Each individual report()/log() call fires under a lock — protecting a
    caller's own shared counter (e.g. ImportLooseWorker._report's `nonlocal
    done`) from a torn increment — not preventing two different wells'
    "writing"..."done" spans from overlapping in time, which is expected and
    is the whole point of running them in parallel. What must hold
    regardless of thread interleaving: every well reports exactly one
    "writing" then one "done", with nothing duplicated, dropped, or
    corrupted by a concurrent append."""
    src = tmp_path / "loose"
    dest = tmp_path / "dest"
    wells = [f"{r}{c:02d}" for r in "ABCD" for c in range(1, 5)]  # 16 wells
    _write_loose(src, wells, timepoints=("0h",))

    events: list[tuple[str, str]] = []

    def _progress(well: str, stage: str) -> None:
        events.append((well, stage))

    il_apply.convert_to_new_destination(
        src, dest, filename_schema=SCHEMA, filename_sep=SEP, progress=_progress,
    )

    assert len(events) == 2 * len(wells)
    by_well: dict[str, list[str]] = {}
    for well, stage in events:
        by_well.setdefault(well, []).append(stage)
    assert set(by_well) == set(wells)
    assert all(stages == ["writing", "done"] for stages in by_well.values())


def test_convert_in_place_writes_real_zips_not_bare_folders(tmp_path):
    """Regression for the reported "sits stuck, no zips made" bug: apply.py's
    convert_in_place() used to call
    services.input_resolution_service._consolidate_mixed_to_zips after
    run_zipper, which is a documented no-op when zero zips already exist —
    exactly the state after a fresh WellPlateZipper.py run (it only ever
    produces per-well folders, never zips). Every well was left as a bare
    folder forever, with the run still reporting success. This test drives
    the real subprocess-backed run_zipper (stdlib-only, no imaging stack, the
    same real path tests/test_loose_in_folder.py exercises) end to end and
    asserts actual .zip files land on disk."""
    src = tmp_path / "loose"
    _write_loose(src, ["A01", "B02", "C03"], timepoints=("0h", "24h"))

    summary = il_apply.convert_in_place(src, filename_schema=SCHEMA, filename_sep=SEP)

    assert "3 per-well zip(s)" in summary
    for well in ("A01", "B02", "C03"):
        zip_path = src / f"{well}.zip"
        assert zip_path.exists(), f"{well}: expected {zip_path} on disk, found a bare folder instead"
        assert not (src / well).exists()  # folder consumed by the zip step
        with zipfile.ZipFile(zip_path) as zf:
            members = set(zf.namelist())
        assert members == {f"exp_GFP_{well}_F001_0h.tif", f"exp_GFP_{well}_F001_24h.tif"}


def test_convert_in_place_is_idempotent_on_rerun(tmp_path):
    """A well already zipped from a prior run is left alone, not re-zipped or
    duplicated, on a second convert_in_place() pass over the same folder."""
    src = tmp_path / "loose"
    _write_loose(src, ["A01"], timepoints=("0h",))

    il_apply.convert_in_place(src, filename_schema=SCHEMA, filename_sep=SEP)
    first_mtime = (src / "A01.zip").stat().st_mtime_ns

    summary = il_apply.convert_in_place(src, filename_schema=SCHEMA, filename_sep=SEP)

    assert "0 per-well zip(s)" in summary
    assert (src / "A01.zip").stat().st_mtime_ns == first_mtime


def test_zip_well_folders_reports_done_for_an_already_zipped_well(tmp_path):
    """Regression guard: _zip_well_folders used to silently skip the
    progress report for a well that already has a matching zip (the
    idempotent-rerun/crash-resume case) -- if that well was still part of
    wellsPlanned (its loose TIFs hadn't all been moved/deleted when a prior
    attempt crashed), the dashboard would sit stuck showing it at "writing"
    forever even though the overall run succeeded."""
    well_dir = tmp_path / "A01"
    well_dir.mkdir()
    (well_dir / "exp_GFP_A01_F001_0h.tif").write_bytes(b"img")
    with zipfile.ZipFile(tmp_path / "A01.zip", "w") as zf:
        zf.writestr("exp_GFP_A01_F001_0h.tif", b"img")  # already zipped

    reports = []
    zipped = il_apply._zip_well_folders(tmp_path, progress=lambda w, s: reports.append((w, s)))

    assert zipped == 0  # not re-zipped
    assert reports == [("A01", "done")]


def test_group_files_by_well_and_scan_agree(tmp_path):
    _write_loose(tmp_path, ["A01", "B02", "C03"], timepoints=("0h", "24h"))
    preview = ilc.scan_folder(tmp_path, filename_schema=SCHEMA, filename_sep=SEP)
    by_well = ilc.group_files_by_well(tmp_path, filename_schema=SCHEMA, filename_sep=SEP)
    assert set(by_well) == set(preview.wells)
    assert sum(len(v) for v in by_well.values()) == preview.matched
