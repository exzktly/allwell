"""Concatenate's "Add dataset 2 to dataset 1 in place" (tracker M14).

Dataset 1's archives are the thing changed, so every assertion that matters
is about their bytes: that what was there before is still there, at the same
offsets; that dataset 2's images arrived under their shifted names with their
own bytes; and that a failure — or a crash, undone by the next open — leaves
each archive hashing exactly as it did before the run. The fixtures carry
what real plates carry: ``.DS_Store``, AppleDouble sidecars, directory
entries, a ZIP64 archive, and a well only dataset 2 has.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest

from datasets.concatenate import in_place
from datasets.concatenate import concatenate_compute as cc
from util.zip_append import AppendRun, locate_tail

JUNK = {".DS_Store": b"\x00\x01junk", "__MACOSX/._sidecar": b"appledouble"}


def _raw(root: Path, *, wells=("A01", "B02"), timepoints=("0h", "24h"), tag="d1",
         fields=("f0", "f1"), channels=("DAPI", "GFP"), zip64=False) -> Path:
    """A converter-shaped raw plate whose every image has bytes of its own."""
    (root / "in").mkdir(parents=True)
    for well in wells:
        saved = zipfile.ZIP64_LIMIT
        if zip64:
            zipfile.ZIP64_LIMIT = 64
        try:
            with zipfile.ZipFile(root / "in" / f"{well}.zip", "w") as archive:
                archive.writestr(zipfile.ZipInfo("thumbs/"), b"")
                for name, data in JUNK.items():
                    archive.writestr(name, data)
                for tp in timepoints:
                    for field in fields:
                        for channel in channels:
                            name = f"E_{channel}_{well}_{field}_{tp}.tif"
                            archive.writestr(name, f"{tag}:{name}".encode() * 20)
                            archive.writestr(f"__MACOSX/._{name}", b"appledouble")
        finally:
            zipfile.ZIP64_LIMIT = saved
    return root


def _record(root: Path, start: str) -> None:
    """An acquisition record naming every tile of well A01's fields at 0h."""
    from util.acquisition_record import new_record, new_timepoint, write_record
    record = new_record()
    record["timepoints"]["0h"] = new_timepoint(started_at=start)
    record["tiles"] = [{"well": "A01", "field": field, "timepoint": "0h",
                        "mosaic_xy_px": [i * 2048, 0], "stage_xy_um": None}
                       for i, field in enumerate(("f0", "f1"))]
    write_record(root, record)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hashes(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): _sha(p) for p in sorted(root.rglob("*")) if p.is_file()}


def _names(archive: Path) -> list[str]:
    with zipfile.ZipFile(archive) as reader:
        return reader.namelist()


def _append(tmp_path: Path, **kwargs):
    plan = in_place.build_append_plan(tmp_path / "d1", tmp_path / "d2", **kwargs)
    lines: list[str] = []
    summary = in_place.append_in_place(plan, lines.append)
    return plan, summary, lines


@pytest.fixture
def pair(tmp_path):
    _raw(tmp_path / "d1", timepoints=("0h", "24h"), tag="d1")
    _raw(tmp_path / "d2", wells=("A01", "B02", "C03"), timepoints=("0h",), tag="d2")
    return tmp_path


# ── what lands where ─────────────────────────────────────────────────────────
def test_dataset_2_lands_shifted_in_dataset_1_s_own_archives_and_no_folder_is_written(pair):
    plan, summary, _lines = _append(pair, gap_hours=24.0)
    assert plan.in_place and plan.dest.root == pair / "d1"
    names = _names(pair / "d1" / "in" / "A01.zip")
    for field in ("f0", "f1"):
        for channel in ("DAPI", "GFP"):
            assert f"E_{channel}_A01_{field}_48h.tif" in names
    with zipfile.ZipFile(pair / "d1" / "in" / "A01.zip") as reader:
        assert reader.read("E_GFP_A01_f1_48h.tif") == b"d2:E_GFP_A01_f1_0h.tif" * 20
        assert reader.read("E_GFP_A01_f1_0h.tif") == b"d1:E_GFP_A01_f1_0h.tif" * 20
        # Dataset 2's debris is not carried; dataset 1's own stays where it was.
        assert names.count(".DS_Store") == 1
        assert not any(n.startswith("__MACOSX/._") and n.endswith("_48h.tif") for n in names)
        assert reader.testzip() is None
    assert "in place (dataset 2 shifted +48h)" in summary
    assert sorted(p.name for p in (pair / "d1").iterdir()) == ["in"]


def test_existing_members_are_never_moved_or_rewritten(pair):
    archive = pair / "d1" / "in" / "A01.zip"
    before = archive.read_bytes()
    floor = locate_tail(archive).offset
    with zipfile.ZipFile(archive) as reader:
        old = [(i.filename, i.header_offset, i.CRC, i.compress_size) for i in reader.infolist()]
    _append(pair, gap_hours=24.0)
    assert archive.read_bytes()[:floor] == before[:floor]
    with zipfile.ZipFile(archive) as reader:
        now = [(i.filename, i.header_offset, i.CRC, i.compress_size) for i in reader.infolist()]
    assert now[:len(old)] == old


def test_a_zip64_dataset_1_archive_is_appended_to(tmp_path):
    _raw(tmp_path / "d1", wells=("A01",), timepoints=("0h", "24h"), zip64=True)
    _raw(tmp_path / "d2", wells=("A01",), timepoints=("0h",), tag="d2")
    archive = tmp_path / "d1" / "in" / "A01.zip"
    assert b"PK\x06\x06" in archive.read_bytes()[-200:]
    floor, before = locate_tail(archive).offset, archive.read_bytes()
    _append(tmp_path, gap_hours=24.0)
    assert archive.read_bytes()[:floor] == before[:floor]
    with zipfile.ZipFile(archive) as reader:
        assert reader.read("E_DAPI_A01_f0_48h.tif") == b"d2:E_DAPI_A01_f0_0h.tif" * 20
        assert reader.testzip() is None


def test_a_well_only_dataset_2_has_becomes_a_new_archive_in_dataset_1(pair):
    _append(pair, gap_hours=24.0)
    names = _names(pair / "d1" / "in" / "C03.zip")
    assert sorted(n for n in names if n.endswith(".tif") and not n.startswith("__MACOSX")) == sorted(
        f"E_{c}_C03_{f}_48h.tif" for c in ("DAPI", "GFP") for f in ("f0", "f1"))
    assert ".DS_Store" not in names


def test_dataset_2_is_left_exactly_as_it_was(pair):
    before = _hashes(pair / "d2")
    _append(pair, gap_hours=24.0)
    assert _hashes(pair / "d2") == before


def test_the_log_ends_with_every_source_file_grouped_per_well(pair):
    _plan, _summary, lines = _append(pair, gap_hours=24.0)
    heading = [i for i, line in enumerate(lines) if "you can delete them by hand" in line]
    assert len(heading) == 1, lines
    listed = lines[heading[0] + 1:]
    assert listed == [
        f"  {well}: {pair / 'd2' / 'in' / f'{well}.zip'} (4 file(s); 6 operating-system "
        "file(s) in it were not needed)"
        for well in ("A01", "B02", "C03")
    ]


def test_a_folder_well_in_dataset_2_lists_each_file(tmp_path):
    _raw(tmp_path / "d1", wells=("A01",))
    folder = tmp_path / "d2" / "in" / "A01"
    folder.mkdir(parents=True)
    for name in ("E_GFP_A01_f0_0h.tif", "E_DAPI_A01_f0_0h.tif"):
        (folder / name).write_bytes(f"d2 folder {name}".encode())
    (folder / ".DS_Store").write_bytes(b"junk")
    _plan, _summary, lines = _append(tmp_path, gap_hours=24.0)
    start = next(i for i, line in enumerate(lines) if "you can delete them by hand" in line)
    assert lines[start + 1] == f"  A01: {folder}/ (2 file(s))"
    assert sorted(lines[start + 2:start + 4]) == sorted(
        f"      {folder / n}" for n in ("E_GFP_A01_f0_0h.tif", "E_DAPI_A01_f0_0h.tif"))
    with zipfile.ZipFile(tmp_path / "d1" / "in" / "A01.zip") as reader:
        assert reader.read("E_GFP_A01_f0_48h.tif") == b"d2 folder E_GFP_A01_f0_0h.tif"
        assert reader.getinfo("E_GFP_A01_f0_48h.tif").compress_type == zipfile.ZIP_STORED


# ── refusing a second run ────────────────────────────────────────────────────
def test_running_the_same_append_twice_on_a_raw_dataset_1_is_refused(pair):
    _append(pair, gap_hours=24.0)
    after_first = _hashes(pair / "d1")
    # Dataset 1's last timepoint is now 48h, so the shifted names are new
    # (72h); only the contents repeat.
    with pytest.raises(cc.ConcatError, match="already in dataset 1") as caught:
        _append(pair, gap_hours=24.0)
    assert "same bytes as 'E_DAPI_A01_f0_48h.tif'" in str(caught.value)
    assert _hashes(pair / "d1") == after_first


def test_a_name_already_in_the_archive_is_refused_and_named(tmp_path):
    from tests.fixtures.dataset_builder import build_dataset
    build_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0h", "24h"])
    _raw(tmp_path / "d2", wells=("A01",), timepoints=("0h",), fields=("F001",),
         channels=("GFP", "NIR"), tag="d2")
    with zipfile.ZipFile(tmp_path / "d1" / "in" / "A01.zip", "a") as archive:
        archive.writestr("E_GFP_A01_F001_48h.tif", b"someone put this here already")
    before = _hashes(tmp_path / "d1")
    with pytest.raises(cc.ConcatError) as caught:
        _append(tmp_path, gap_hours=24.0)
    assert "A01: 'E_GFP_A01_F001_48h.tif' is already in A01.zip" in str(caught.value)
    assert _hashes(tmp_path / "d1") == before


# ── all or nothing ───────────────────────────────────────────────────────────
def test_a_well_that_fails_rolls_back_every_well_byte_for_byte(pair, monkeypatch):
    before = _hashes(pair / "d1")
    real = in_place.verify_well

    def fail_on_b02(item):
        real(item)
        if item.well == "B02":
            raise cc.ConcatError("Verification failed: simulated for B02")

    monkeypatch.setattr(in_place, "verify_well", fail_on_b02)
    plan = in_place.build_append_plan(pair / "d1", pair / "d2", gap_hours=24.0)
    lines: list[str] = []
    with pytest.raises(cc.ConcatError, match="restored exactly as it was"):
        in_place.append_in_place(plan, lines.append, workers=1)
    assert _hashes(pair / "d1") == before                   # C03.zip gone, A01/B02 restored
    assert "Undoing the append:" in lines
    assert not any("delete them by hand" in line for line in lines)


def test_a_copy_that_dies_part_way_through_a_member_is_undone(pair, monkeypatch):
    before = _hashes(pair / "d1")
    real = in_place.copy_member_raw
    calls = {"n": 0}

    def die_on_the_fifth(src, info, dst, name):
        calls["n"] += 1
        if calls["n"] == 5:
            dst.fp.write(b"part of a member")
            raise OSError("No space left on device")
        return real(src, info, dst, name)

    monkeypatch.setattr(in_place, "copy_member_raw", die_on_the_fifth)
    plan = in_place.build_append_plan(pair / "d1", pair / "d2", gap_hours=24.0)
    with pytest.raises(cc.ConcatError, match="No space left on device"):
        in_place.append_in_place(plan, workers=1)
    assert _hashes(pair / "d1") == before


def test_a_record_that_fails_after_the_archives_restores_archives_and_record(tmp_path, monkeypatch):
    _raw(tmp_path / "d1", wells=("A01",), timepoints=("0h",))
    _raw(tmp_path / "d2", wells=("A01",), timepoints=("0h",), tag="d2")
    for root, start in ((tmp_path / "d1", "2026-03-01T09:00:00+00:00"),
                        (tmp_path / "d2", "2026-03-02T09:00:00+00:00")):
        _record(root, start)
    before = _hashes(tmp_path / "d1")

    def broken(plan, emit):
        (plan.dest.root / "in" / "acquisition.json").write_text("{half written")
        raise cc.ConcatError("Verification failed: simulated record failure")

    monkeypatch.setattr(in_place, "_merge_acquisition_records", broken)
    plan = in_place.build_append_plan(tmp_path / "d1", tmp_path / "d2")
    with pytest.raises(cc.ConcatError, match="simulated record failure"):
        in_place.append_in_place(plan)
    assert _hashes(tmp_path / "d1") == before


def test_the_merged_record_describes_the_appended_images(tmp_path):
    from util.acquisition_record import read_record, validate_against_archives
    _raw(tmp_path / "d1", wells=("A01",), timepoints=("0h",))
    _raw(tmp_path / "d2", wells=("A01",), timepoints=("0h",), tag="d2")
    for root, start in ((tmp_path / "d1", "2026-03-01T09:00:00+00:00"),
                        (tmp_path / "d2", "2026-03-02T09:00:00+00:00")):
        _record(root, start)
    plan, _summary, _lines = _append(tmp_path)                 # the gap comes from the records
    assert plan.gap_hours == 24.0
    merged = read_record(tmp_path / "d1")
    assert sorted(merged["timepoints"]) == ["0h", "24h"]
    assert validate_against_archives(tmp_path / "d1", merged,
                                     schema="experiment:channel:well:fov:timepoint").ok


def test_cancelling_restores_dataset_1(pair):
    before = _hashes(pair / "d1")
    plan = in_place.build_append_plan(pair / "d1", pair / "d2", gap_hours=24.0)
    asked = {"n": 0}

    def cancel_once_copying_has_begun():
        asked["n"] += 1
        return asked["n"] > 3

    with pytest.raises(cc.ConcatError, match="Cancelled"):
        in_place.append_in_place(plan, is_cancelled=cancel_once_copying_has_begun, workers=1)
    assert _hashes(pair / "d1") == before


# ── a crash, undone by the next open ─────────────────────────────────────────
def _crash_mid_append(archive: Path) -> None:
    """Leave *archive* as a killed append would: journalled, cut off part-way through a member."""
    floor = locate_tail(archive).offset
    run = AppendRun(archive.parent)
    run.__enter__()
    run.append(archive, lambda zf: zf.writestr("E_GFP_A01_f0_48h.tif", b"half" * 100))
    os.truncate(archive, floor + 60)          # no central directory on disk any more
    run._release()                            # the process is gone: nothing rolled back, nothing held


def test_the_next_concatenate_run_undoes_a_crashed_append_then_appends(pair):
    from datasets.concatenate.worker import ConcatenateWorker
    archive = pair / "d1" / "in" / "A01.zip"
    original = _sha(archive)
    _crash_mid_append(archive)
    with pytest.raises(zipfile.BadZipFile):
        zipfile.ZipFile(archive)
    worker = ConcatenateWorker(str(pair / "d1"), str(pair / "d2"), "", gap_text="24h", in_place=True)
    lines, outcome = [], {}
    worker.logLine.connect(lines.append)
    worker.succeeded.connect(lambda root, summary: outcome.update(ok=(root, summary)))
    worker.failed.connect(lambda message: outcome.update(failed=message))
    worker.run()                                          # synchronously, on this thread
    assert "failed" not in outcome, outcome
    assert any("Recovery (d1)" in line and "restored" in line for line in lines)
    # The undo gave back the original archive, and the run then appended to it.
    with zipfile.ZipFile(archive) as reader:
        assert reader.read("E_GFP_A01_f0_48h.tif") == b"d2:E_GFP_A01_f0_0h.tif" * 20
    assert outcome["ok"][0] == str(pair / "d1")
    assert original != _sha(archive)


def test_recover_dataset_restores_the_bytes_and_says_so(pair):
    archive = pair / "d1" / "in" / "A01.zip"
    original = _sha(archive)
    _crash_mid_append(archive)
    lines: list[str] = []
    in_place.recover_dataset(pair / "d1", lines.append)
    assert _sha(archive) == original
    assert lines[0] == "Recovery (d1): An in-place append into this dataset did not finish; undoing it."


def test_the_analyze_runner_undoes_a_crashed_append_before_reading(pair):
    from services.pipeline_runner import PipelineRunner
    archive = pair / "d1" / "in" / "A01.zip"
    original = _sha(archive)
    _crash_mid_append(archive)

    def resolver(_opts, _log_q, **_kw):
        return pair / "d1" / "in", pair / "d1" / "out"

    runner = PipelineRunner()
    runner.start(Path("pm.py"), {}, resolve_dirs=resolver)
    runner._thread.join(timeout=30)
    assert _sha(archive) == original
    events = []
    while not runner.log_q.empty():
        events.append(runner.log_q.get_nowait())
    assert any(kind == "line" and "[recover]" in str(text) and "restored" in str(text)
               for kind, text in events)


def test_a_review_open_undoes_a_crashed_append_before_reading(pair, caplog, monkeypatch):
    """Through Review's real ``load_path``, stopped at the first thing it does after the undo
    (the "Opening dataset…" dialog), so what is measured is the open, not the helper."""
    import logging
    from types import SimpleNamespace
    from review.core import load_controller

    class _Opened(Exception):
        pass

    def opening_dialog(_ctx, _text):
        raise _Opened

    monkeypatch.setattr(load_controller, "_make_opening_dialog", opening_dialog)
    archive = pair / "d1" / "in" / "A01.zip"
    original = _sha(archive)
    _crash_mid_append(archive)
    ctx = SimpleNamespace(window=SimpleNamespace(_cleanup_tmp=lambda: None))
    with caplog.at_level(logging.WARNING, logger="review"), pytest.raises(_Opened):
        load_controller.load_path(ctx, pair / "d1")
    assert _sha(archive) == original
    assert any("Recovery (d1)" in r.getMessage() for r in caplog.records)


# ── analysed dataset 1: outputs kept, the new timepoint left for Analyze ─────
def _pending(root: Path, well="A01"):
    from pipeline.incremental_keys import fov_keys_from_member_names, read_done_fov_keys
    with zipfile.ZipFile(root / "in" / f"{well}.zip") as archive:
        present = fov_keys_from_member_names(
            archive.namelist(), schema="experiment:channel:well:fov:timepoint", sep="_",
            images_only=True)
    return present - read_done_fov_keys(root / "out" / f"measurements_{well}.csv")


def test_an_analysed_dataset_1_keeps_its_outputs_and_only_the_new_timepoint_is_left(tmp_path):
    from tests.fixtures.dataset_builder import build_dataset
    build_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0h", "24h"], junk=True)
    _raw(tmp_path / "d2", wells=("A01",), timepoints=("0h",), fields=("F001",),
         channels=("GFP", "NIR"), tag="d2")
    out_before = {k: v for k, v in _hashes(tmp_path / "d1" / "out").items()}
    assert _pending(tmp_path / "d1") == set()
    _plan, summary, _lines = _append(tmp_path, gap_hours=24.0)
    assert "Incremental" in summary
    after = _hashes(tmp_path / "d1" / "out")
    assert {k: after[k] for k in out_before} == out_before        # pipeline_info, CSV, QC kept
    assert set(after) - set(out_before) == {"provenance.jsonl"}
    with (tmp_path / "d1" / "out" / "measurements_A01.csv").open() as handle:
        assert {row["timepoint"] for row in csv.DictReader(handle)} == {"0h", "24h"}
    # The pipeline's own incremental rule: exactly the appended timepoint is left.
    assert _pending(tmp_path / "d1") == {("F001", "48h")}
    events = [json.loads(line) for line in
              (tmp_path / "d1" / "out" / "provenance.jsonl").read_text().splitlines()]
    assert events[-1]["detail"]["mode"] == "in_place"


def test_appledouble_twins_are_not_read_as_timepoints(tmp_path):
    """Each image in the fixture has a ``__MACOSX/._<name>`` twin, as an archive
    packed on a Mac does. Read as an image, ``._E_GFP_A01_f0_0h`` split into
    fields put ``f0`` in the timepoint slot, so a raw plate listed timepoints
    0h, f0, f1 and its default gap came out as one hour."""
    from datasets.concatenate.raw_compute import raw_info, raw_paths
    _raw(tmp_path / "d1", timepoints=("0h", "24h"))
    info = raw_info(raw_paths(tmp_path / "d1"), label="dataset 1", error_cls=cc.ConcatError)
    assert info["available_timepoints"] == ["0h", "24h"]


# ── which pairs can be appended ──────────────────────────────────────────────
def test_an_analysed_dataset_2_cannot_be_added_in_place(tmp_path):
    from tests.fixtures.dataset_builder import build_dataset
    build_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0h", "24h"])
    build_dataset(tmp_path / "d2", wells=["A01"], timepoints=["0h"])
    reason = in_place.in_place_refusal(tmp_path / "d1", tmp_path / "d2")
    assert "needs a raw dataset 2" in reason
    with pytest.raises(cc.ConcatError, match="needs a raw dataset 2"):
        in_place.build_append_plan(tmp_path / "d1", tmp_path / "d2", gap_hours=24.0)


def test_a_record_only_dataset_1_has_cannot_be_kept_in_place(tmp_path):
    _raw(tmp_path / "d1", wells=("A01",), timepoints=("0h",))
    _raw(tmp_path / "d2", wells=("A01",), timepoints=("0h",), tag="d2")
    _record(tmp_path / "d1", "2026-03-01T09:00:00+00:00")
    assert "Dataset 1 has an acquisition record and dataset 2 has none" in in_place.in_place_refusal(
        tmp_path / "d1", tmp_path / "d2")


def test_a_folder_well_in_dataset_1_cannot_be_appended_to(tmp_path):
    _raw(tmp_path / "d1", wells=("B02",))
    folder = tmp_path / "d1" / "in" / "A01"
    folder.mkdir()
    (folder / "E_GFP_A01_f0_0h.tif").write_bytes(b"x")
    (folder / "E_GFP_A01_f0_24h.tif").write_bytes(b"y")
    _raw(tmp_path / "d2", wells=("A01",), timepoints=("0h",), tag="d2")
    assert "keeps well A01's images in a folder" in in_place.in_place_refusal(
        tmp_path / "d1", tmp_path / "d2")


# ── through the real page ────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _page_with(ctrl, d1, d2, dest=""):
    page = ctrl.page
    page._ds1_field.set_path(str(d1))
    page._ds2_field.set_path(str(d2))
    page._dest_field.set_path(str(dest))
    for field in (page._ds1_field, page._ds2_field, page._dest_field):
        field.changed.emit()
    return page


def test_the_page_offers_in_place_and_its_run_appends_to_dataset_1(qapp, pair):
    from datasets.concatenate.controller import ConcatenateController
    from tests._datasets_worker import run_worker_to_completion

    ctrl = ConcatenateController(on_open_in_review=lambda _p: None)
    page = _page_with(ctrl, pair / "d1", pair / "d2")
    assert page._mode_new.isChecked() and page._mode_in_place.isEnabled()
    page._gap_edit.setText("24h")
    assert page._run_btn.isEnabled() is False                # new dataset: no destination yet
    page._mode_in_place.click()
    assert page.in_place() and page._dest_field.isEnabled() is False
    assert "nothing is copied" in page._mode_hint.text()
    assert page._run_btn.isEnabled() is True
    page._run_btn.click()
    run_worker_to_completion(ctrl)
    assert "E_GFP_A01_f0_48h.tif" in _names(pair / "d1" / "in" / "A01.zip")
    assert ctrl._last_result[0] is True and ctrl._last_result[2] == str(pair / "d1")
    assert ctrl.collect_spec()["in_place"] is True
    ctrl.shutdown()


def test_the_page_will_not_offer_in_place_for_an_analysed_dataset_2(qapp, tmp_path):
    from datasets.concatenate.controller import ConcatenateController
    from tests.fixtures.dataset_builder import build_dataset
    build_dataset(tmp_path / "d1", wells=["A01"], timepoints=["0h", "24h"])
    build_dataset(tmp_path / "d2", wells=["A01"], timepoints=["0h"])
    ctrl = ConcatenateController()
    page = _page_with(ctrl, tmp_path / "d1", tmp_path / "d2")
    assert page._mode_in_place.isEnabled() is False
    assert page._mode_hint.text().startswith("Adding in place is unavailable: Adding in place needs a raw")
    ctrl.shutdown()


def test_a_queued_in_place_job_stays_in_place(qapp, pair):
    from datasets.concatenate.controller import ConcatenateController
    ctrl = ConcatenateController()
    ctrl.show_job({"dataset1": str(pair / "d1"), "dataset2": str(pair / "d2"),
                   "destination": "", "gap": "24h", "in_place": True})
    assert ctrl.page.in_place() is True
    ctrl.show_job({"dataset1": str(pair / "d1"), "dataset2": str(pair / "d2"),
                   "destination": str(pair / "dest"), "gap": "24h"})
    assert ctrl.page.in_place() is False
    ctrl.shutdown()


def test_a_new_dataset_run_with_no_destination_is_refused_not_written_to_the_cwd(pair, monkeypatch):
    """``Path("")`` is the working directory, so a worker handed an empty destination
    for a new dataset would write ``in/`` wherever the app was started from."""
    from datasets.concatenate.worker import ConcatenateWorker
    monkeypatch.chdir(pair)
    worker = ConcatenateWorker(str(pair / "d1"), str(pair / "d2"), "", gap_text="24h")
    failed: list[str] = []
    worker.failed.connect(failed.append)
    worker.run()
    assert failed == ["Choose a destination folder for the new dataset."]
    assert not (pair / "in").exists()
