"""Headless checks for the Convert CZI page, its controller and its wiring.

Two rules from CLAUDE.md §7.6/§7.8 shape what is here. Every case goes through
the **real** ``CziImportController``, which builds the real page — a page whose
builder raises is a tab that renders blank and a lane that stays green. And the
controls are *driven* rather than inspected: selecting the grid well-source
through the combo, not calling the slot it happens to be connected to, because
"a control is offered" and "a control works" are the two things a Qt callback
bug sits between.
"""

from __future__ import annotations

import builtins
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication  # noqa: E402

from tests._czi_fixture import (  # noqa: E402
    plate_metadata_xml,
    plate_tiles,
    write_czi,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def controller(qapp):
    """The real controller, torn down after each test."""
    from datasets.czi_import.controller import CziImportController

    ctrl = CziImportController(on_open_in_analyze=lambda *_a: None,
                               on_add_to_queue=lambda *_a: None)
    try:
        yield ctrl
    finally:
        ctrl.shutdown()
        ctrl.page.deleteLater()


def _named_czi(tmp_path, wells=("C07", "A02"), channels=("DAPI", "GFP"),
               fovs=1, timepoints=1):
    tiles = plate_tiles(wells=len(wells), fovs=fovs, timepoints=timepoints,
                        channels=len(channels))
    tmp_path.mkdir(parents=True, exist_ok=True)
    return write_czi(
        tmp_path / "Plate7.czi", tiles,
        xml=plate_metadata_xml(
            scene_wells={i: w for i, w in enumerate(wells)},
            channels=list(channels)),
    )


def _bare_czi(tmp_path, positions=4):
    return write_czi(tmp_path / "bare.czi",
                     plate_tiles(wells=positions, fovs=1, timepoints=1,
                                 channels=1))


def _all_scans_delivered(page, timeout_s: float = 20.0):
    """Spin until every scan the page ever started has called back.

    Stronger than :func:`_settle`, which only waits for the page to stop
    considering itself busy — and a *stale* scan landing later is exactly the
    thing that is not covered by that. Without this, the test below asserted
    before the slow scan had returned at all and passed against a page with no
    staleness check whatsoever.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if set(page._scan_bridges) <= page._scan_delivered:
            break
        time.sleep(0.002)
    QApplication.processEvents()
    assert set(page._scan_bridges) <= page._scan_delivered, (
        "a scan never called back")
    return page


def _settle(page, timeout_s: float = 20.0):
    """Spin the event loop until the page's background source scan has landed.

    The page reads its files off the UI thread — a plate on a network share is
    one round trip per file and freezing the window for it is the thing that
    change exists to prevent — so a test that sets a source and reads the
    verdict in the next statement is reading the "Reading…" state. Waiting on
    the page's own predicate rather than on a duration is what keeps that from
    being either a flake or a fixed delay in every one of these.
    """
    deadline = time.monotonic() + timeout_s
    while page.is_scanning() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.002)
    QApplication.processEvents()
    assert not page.is_scanning(), "the source scan never finished"
    return page


def _set_source(page, path, tmp_path):
    page.set_source_path(str(path))
    page.set_destination_path(str(tmp_path / "dest"))
    _settle(page)


# ── the page builds, and its controls do something ───────────────────────────

def test_the_real_page_builds_and_carries_its_controls(controller):
    """The builder is what the app calls; a stubbed seam underneath it would
    never notice a missing argument or a renamed helper in this function."""
    page = controller.page
    labels = [w.text() for w in page.findChildren(type(page._readout))]

    assert "Convert CZI" in labels
    assert page.well_source() == "file"
    assert page._grid_host.isVisibleTo(page) is False


def test_choosing_the_grid_through_the_combo_reveals_the_grid_fields(controller):
    """Driven through ``setCurrentIndex``, so the ``currentIndexChanged``
    connection is part of what is asserted. Calling ``_on_well_source_changed``
    directly would pass against a combo wired to nothing at all."""
    page = controller.page
    index = page._well_source_cb.findData("grid")
    assert index >= 0

    page._well_source_cb.setCurrentIndex(index)

    assert page.well_source() == "grid"
    assert page._grid_host.isVisibleTo(page) is True
    assert page.params().well_source == "grid"


# ── the readout ──────────────────────────────────────────────────────────────

def test_a_named_plate_reads_its_wells_out_of_the_file(controller, tmp_path):
    page = controller.page
    _set_source(page, _named_czi(tmp_path), tmp_path)

    assert "2 scene(s)" in page._readout.text()
    assert "wells named by the file(s) (2)" in page._readout.text()
    verdict = page._verdict.text()
    assert "C07→A02" in verdict            # scene order, not plate order
    assert "named by the file" in verdict
    assert page._run_btn.isEnabled()


def test_an_unnamed_plate_on_the_file_source_refuses_and_says_where_to_go(
        controller, tmp_path):
    page = controller.page
    _set_source(page, _bare_czi(tmp_path), tmp_path)

    assert "Switch the well source to the plate grid" in page._verdict.text()
    assert not page._run_btn.isEnabled()
    assert not page._queue_btn.isEnabled()


def test_the_grid_fields_turn_an_unnamed_plate_into_a_runnable_plan(
        controller, tmp_path):
    page = controller.page
    _set_source(page, _bare_czi(tmp_path, positions=4), tmp_path)
    page._well_source_cb.setCurrentIndex(page._well_source_cb.findData("grid"))
    page._rows_edit.setText("2")
    page._cols_edit.setText("2")
    _settle(page)

    assert "A01→B01" in page._verdict.text()   # 2×2 serpentine ends at B01
    assert "from the plate grid" in page._verdict.text()
    assert page._run_btn.isEnabled()


def test_a_grid_that_does_not_divide_the_file_blocks_the_run(controller,
                                                             tmp_path):
    page = controller.page
    _set_source(page, _bare_czi(tmp_path, positions=5), tmp_path)
    page._well_source_cb.setCurrentIndex(page._well_source_cb.findData("grid"))
    page._rows_edit.setText("2")
    page._cols_edit.setText("2")
    _settle(page)

    assert "not divisible by the well grid" in page._verdict.text()
    assert not page._run_btn.isEnabled()


def test_the_plate_shows_the_mapping_before_the_run(controller, tmp_path):
    """The page's whole answer to "did it read my wells right?". Asserted as
    the wells themselves, not as a count: a plate showing *two* wells that are
    the wrong two would satisfy any count."""
    page = controller.page
    _set_source(page, _named_czi(tmp_path, wells=("H11", "B05")), tmp_path)

    assert sorted(page._dashboard._fraction) == ["B05", "H11"]


def test_an_unreadable_source_says_so_rather_than_showing_a_stale_plan(
        controller, tmp_path):
    page = controller.page
    broken = tmp_path / "broken.czi"
    broken.write_bytes(b"II*\0" + b"\0" * 200)
    _set_source(page, broken, tmp_path)

    assert "not a CZI file" in page._verdict.text()
    assert page._readout.text() == ""
    assert sorted(page._dashboard._fraction) == []
    assert not page._run_btn.isEnabled()


def test_an_incomplete_file_is_named_on_the_page_and_still_convertible(
        controller, tmp_path):
    """It used to be refused here, and refused again at run time. Refusing
    lost more than it protected: on a split export one file short of a plane
    took the whole plate with it, and the news arrived only after the read.

    So the page says what will be missing, before the run, while the source
    can still be re-exported — and leaves the decision to the person who knows
    whether that well matters. What it must never do is stay quiet: a plate
    that will come out short and reads as whole is CLAUDE.md §7's cardinal
    failure.
    """
    tiles = plate_tiles(wells=2, fovs=1, timepoints=1, channels=2)
    del tiles[1]
    path = write_czi(tmp_path / "short.czi", tiles,
                     xml=plate_metadata_xml(scene_wells={0: "A01", 1: "A02"},
                                            channels=["DAPI", "GFP"]))
    page = controller.page
    _set_source(page, path, tmp_path)

    verdict = page._verdict.text()
    assert "3 of the 4 image(s)" in verdict, verdict
    assert "are missing" in verdict, verdict
    assert page._run_btn.isEnabled()


# ── channels ─────────────────────────────────────────────────────────────────

def test_channel_roles_are_seeded_from_the_names_and_handed_to_analyze(
        controller, tmp_path):
    page = controller.page
    _set_source(page, _named_czi(tmp_path, channels=("DAPI", "GFP")), tmp_path)

    assert [name for name, _cb in page._channel_rows] == ["DAPI", "GFP"]
    # DAPI seeds as the nuclear channel, which takes it *out* of
    # ``fluor_tokens`` (``util.channel_roles.channel_tokens_from_roles``: the
    # nuclear channel leaves, a phase one stays). A phase-less plate carries no
    # phase token at all. Asserted as the exact dict Analyze receives rather
    # than as "a dict was produced", which an empty one also satisfies.
    assert page.channel_tokens_dict() == {"nuclear_token": "DAPI",
                                          "fluor_tokens": ["GFP"]}


def test_changing_a_role_changes_what_analyze_is_handed(controller, tmp_path):
    page = controller.page
    _set_source(page, _named_czi(tmp_path, channels=("DAPI", "GFP")), tmp_path)
    _name, combo = page._channel_rows[1]

    combo.setCurrentIndex(combo.findData("phase"))

    assert page.channel_tokens_dict()["phase_token"] == "GFP"


# ── the queue hand-off ───────────────────────────────────────────────────────

def test_the_spec_a_queued_job_carries_round_trips_into_a_fresh_page(
        controller, qapp, tmp_path):
    """``collect_spec`` → ``show_job`` is what the Job Queue's edit session
    does. A field that survives the first and not the second is a setting the
    user edits and the run ignores.

    Into a **second, untouched** controller rather than back into the same
    page. Wiping the first page by calling ``set_params`` with the defaults
    looked equivalent and was not: ``origin`` defaults to ``"A01"``, so a
    ``set_params`` that skipped the origin line left ``B03`` sitting in the
    field from before and the round-trip compared a value that had never been
    reset. Mutating that line away left this test green.
    """
    from datasets.czi_import.controller import CziImportController

    page = controller.page
    _set_source(page, _bare_czi(tmp_path), tmp_path)
    page._well_source_cb.setCurrentIndex(page._well_source_cb.findData("grid"))
    page._rows_edit.setText("2")
    page._cols_edit.setText("2")
    _settle(page)
    page._origin_edit.setText("B03")
    page._serpentine_cb.setChecked(False)
    page._direction_cb.setCurrentIndex(page._direction_cb.findData("col"))
    page._experiment_edit.setText("Run9")
    page._workers_edit.setText("3")
    page._stride_edit.setText("4")
    spec = controller.collect_spec()

    assert spec["params"]["origin"] == "B03"
    assert spec["params"]["serpentine"] is False
    assert spec["params"]["direction"] == "col"
    assert spec["params"]["workers"] == 3
    assert spec["params"]["experiment"] == "Run9"
    # 4 rather than the default 1, so a ``set_params`` that skipped the line
    # would leave the fresh page's blank box reading 1 and be caught below.
    assert spec["params"]["timepoint_stride"] == 4

    other = CziImportController()
    try:
        assert other.collect_spec() != spec         # it really is untouched
        other.show_job(spec)
        assert other.collect_spec() == spec
        assert other.page._origin_edit.text() == "B03"
        assert other.page._stride_edit.text() == "4"
    finally:
        other.shutdown()
        other.page.deleteLater()


def test_queueing_sends_this_tool_s_own_kind(qapp, tmp_path):
    from datasets.czi_import.controller import CziImportController

    sent: list[tuple[str, dict]] = []
    ctrl = CziImportController(on_add_to_queue=lambda kind, spec:
                               sent.append((kind, spec)))
    try:
        ctrl.page.queueRequested.emit()
        assert [kind for kind, _spec in sent] == ["czi_import"]
    finally:
        ctrl.shutdown()
        ctrl.page.deleteLater()


def test_a_launch_with_no_destination_still_reports_the_job_as_finished(
        controller, tmp_path):
    """The queue is waiting on ``jobFinished`` and nothing else will send one;
    without it the whole queue stops behind this job for the session."""
    finished: list[tuple[bool, str, str]] = []
    controller.jobFinished.connect(lambda *args: finished.append(args))
    controller.page.set_source_path(str(_named_czi(tmp_path)))

    controller._on_run()

    assert finished == [(False, "No destination folder selected.", "")]
    assert controller.page._dashboard.outcome() == "failed"


# ── a whole run, through the controller ──────────────────────────────────────

def test_a_run_converts_the_plate_and_offers_it_to_analyze(controller,
                                                           tmp_path):
    """End to end on the real worker: the page reports done, the archives are
    on disk under the file's own well names, and Open in Analyze appears."""
    page = controller.page
    _set_source(page, _named_czi(tmp_path, wells=("C07", "A02")), tmp_path)

    controller._launch()
    assert controller._worker is not None
    controller._worker.wait(30_000)
    QApplication.processEvents()

    dest = tmp_path / "dest"
    assert sorted(p.name for p in (dest / "in").glob("*.zip")) == [
        "A02.zip", "C07.zip"]
    assert page._dashboard.outcome() == "done"
    assert page._open_btn.isVisible() or page._dest_root == str(dest)
    assert not (dest / ".aw_czi_in_progress").exists()


def test_a_second_run_into_the_same_folder_is_refused_rather_than_merged(
        controller, tmp_path):
    """Two plates in one folder, with nothing in it saying so, is the failure
    the destination check exists for — and the ``out/`` measurements beside
    them would then describe inputs that are no longer there."""
    page = controller.page
    _set_source(page, _named_czi(tmp_path, wells=("C07", "A02")), tmp_path)
    controller._launch()
    controller._worker.wait(30_000)
    QApplication.processEvents()

    _set_source(page, _named_czi(tmp_path, wells=("H11", "B05")), tmp_path)
    controller._launch()
    controller._worker.wait(30_000)
    QApplication.processEvents()

    assert page._dashboard.outcome() == "failed"
    assert "Already holds converted wells" in page._dashboard.headline_text()
    assert sorted(p.name for p in (tmp_path / "dest" / "in").glob("*.zip")) == [
        "A02.zip", "C07.zip"]


def test_analyze_redirects_a_raw_czi_to_this_page(qapp, tmp_path):
    """Analyze processes folders, so a `.czi` dropped into its input field has
    to send the user here. Without it the refusal reads "Not a directory",
    which names neither the problem nor the page that solves it."""
    from analyze.tab import AnalyzeTab

    path = _named_czi(tmp_path)
    tab = AnalyzeTab()
    try:
        tab._input_edit.setText(str(path))
        hint = tab._packed_input_hint()

        assert hint is not None
        assert "Convert CZI" in hint
        # Not the other converter's page: it would refuse the file.
        assert "Convert OME-TIFF" not in hint
        assert tab._output_lbl.text() == "—"
        assert "Convert CZI" in tab._layout_lbl.text()
    finally:
        tab.deleteLater()


def test_the_plate_still_shows_the_finished_run_after_it_ends(controller,
                                                              tmp_path):
    """The readout refresh that follows a run must not wipe the run.

    ``_on_finished`` drops the running flag and refreshes the readout, which
    re-resolves the same plan and re-seeds the plate — so a conversion that had
    just reported "Wrote 2 image(s) for 2 well(s)" sat above a plate reading
    "Pending: 2, Converted: 0" with the bar back at zero. Asserted as the
    counts and the bar, not as the outcome, because the outcome was `done`
    throughout and is what the existing run test already checks.
    """
    page = controller.page
    _set_source(page, _named_czi(tmp_path, wells=("C07", "A02")), tmp_path)

    controller._launch()
    controller._worker.wait(30_000)
    QApplication.processEvents()

    dashboard = page._dashboard
    assert dashboard.outcome() == "done"
    assert dict((state, n) for state, _label, n in dashboard._counts()) == {
        "pending": 0, "converting": 0, "converted": 2}
    # The bar counts wells, which is what the plate and the rail count; the
    # worker's plane count is reported as text beside it, because two units on
    # one bar is what "2 / 4" at the end of a finished run looked like.
    assert dashboard._bar.value() == dashboard._bar.maximum() == 2
    assert dashboard._status.text() == "4 of 4 image(s)"


def test_a_changed_mapping_does_reset_the_plate(controller, tmp_path):
    """The other direction: the no-op above must not freeze the preview.
    A different well set is a different conversion and resets."""
    page = controller.page
    _set_source(page, _named_czi(tmp_path, wells=("C07", "A02")), tmp_path)
    assert sorted(page._dashboard._fraction) == ["A02", "C07"]

    page.set_source_path(str(_named_czi(tmp_path / "second",
                                        wells=("H11", "B05"))))
    _settle(page)

    assert sorted(page._dashboard._fraction) == ["B05", "H11"]


# ── a plate split into one file per scene ────────────────────────────────────

def _split_folder(tmp_path, wells=("C07", "A02", "H11")):
    folder = tmp_path / "Exp7_split"
    folder.mkdir(parents=True, exist_ok=True)
    for well in wells:
        write_czi(folder / f"Exp7_{well}.czi",
                  plate_tiles(wells=1, fovs=1, timepoints=1, channels=2),
                  xml=plate_metadata_xml(scene_wells={0: well},
                                         channels=["DAPI", "GFP"]))
    return folder


def _browse(page, path):
    """What the Browse button does — ``set_path`` plus the changed signal.

    ``set_source_path`` deliberately does not fire it, because that is the Job
    Queue's repopulate path and a spec carries its own destination.
    """
    page._src_field.set_path(str(path))
    page._on_source_changed()
    _settle(page)


def test_picking_one_file_of_a_split_export_offers_the_whole_folder(
        controller, tmp_path):
    folder = _split_folder(tmp_path)
    page = controller.page

    _browse(page, folder / "Exp7_A02.czi")

    assert page._folder_cb.isVisibleTo(page) is True
    assert "all 3 .czi files" in page._folder_cb.text()
    # Ticked by default: a folder of per-scene files is a plate, and converting
    # one well of three while reporting success is the worse answer.
    assert page.use_folder() is True
    assert sorted(page._dashboard._fraction) == ["A02", "C07", "H11"]
    assert "across 3 file(s)" in page._verdict.text()


def test_unticking_it_converts_the_one_file(controller, tmp_path):
    folder = _split_folder(tmp_path)
    page = controller.page
    _browse(page, folder / "Exp7_A02.czi")

    page._folder_cb.setChecked(False)
    _settle(page)

    assert page.use_folder() is False
    assert sorted(page._dashboard._fraction) == ["A02"]
    assert "across" not in page._verdict.text()


def test_the_box_follows_a_new_source_until_the_user_touches_it(
        controller, tmp_path):
    """Unticking it once must not make every later plate convert one file of
    its folder — but an answer the user gave about *this* source has to stick
    while they go on editing the grid and the experiment name."""
    page = controller.page
    first = _split_folder(tmp_path)
    _browse(page, first / "Exp7_A02.czi")
    page._folder_cb.setChecked(False)
    _settle(page)

    page.refresh_readout()                      # any other keystroke
    assert page.use_folder() is False           # their answer stands

    second = _split_folder(tmp_path / "later", wells=("B05", "D04"))
    _browse(page, second / "Exp7_B05.czi")
    assert page.use_folder() is True            # a new source, asked afresh


def test_a_lone_czi_is_not_offered_a_folder_it_has_no_siblings_in(
        controller, tmp_path):
    page = controller.page
    _browse(page, _named_czi(tmp_path))

    assert page._folder_cb.isVisibleTo(page) is False
    assert page.use_folder() is False


def test_selecting_the_folder_itself_converts_all_of_it(controller, tmp_path):
    """The folder *is* the set, so there is nothing to offer and nothing the
    box could usefully say — an unticked box must not convert one scene of it."""
    folder = _split_folder(tmp_path)
    page = controller.page

    _browse(page, folder)

    assert page._folder_cb.isVisibleTo(page) is False
    assert page.use_folder() is True
    assert sorted(page._dashboard._fraction) == ["A02", "C07", "H11"]


def test_the_destination_default_names_the_plate_not_one_scene(controller,
                                                               tmp_path):
    folder = _split_folder(tmp_path)
    page = controller.page

    _browse(page, folder / "Exp7_A02.czi")

    assert page.destination_path() == str(tmp_path / "Exp7_split_dataset")


def test_a_split_export_converts_end_to_end_from_the_page(controller, tmp_path):
    folder = _split_folder(tmp_path)
    page = controller.page
    _browse(page, folder / "Exp7_A02.czi")
    page.set_destination_path(str(tmp_path / "dest"))

    controller._launch()
    controller._worker.wait(30_000)
    QApplication.processEvents()

    assert page._dashboard.outcome() == "done"
    assert sorted(p.name for p in (tmp_path / "dest" / "in").glob("*.zip")) == [
        "A02.zip", "C07.zip", "H11.zip"]


def test_files_that_disagree_are_reported_on_the_page(controller, tmp_path):
    folder = tmp_path / "mixed"
    folder.mkdir()
    write_czi(folder / "a_A01.czi",
              plate_tiles(wells=1, fovs=1, timepoints=1, channels=2),
              xml=plate_metadata_xml(scene_wells={0: "A01"},
                                     channels=["DAPI", "GFP"]))
    write_czi(folder / "b_A02.czi",
              plate_tiles(wells=1, fovs=1, timepoints=1, channels=1),
              xml=plate_metadata_xml(scene_wells={0: "A02"}, channels=["DAPI"]))
    page = controller.page

    _browse(page, folder)

    assert "number of channels" in page._verdict.text()
    assert not page._run_btn.isEnabled()


def test_a_folder_holding_no_czi_files_says_so(controller, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    page = controller.page

    _browse(page, empty)

    assert "no .czi files" in page._src_field.message()
    assert not page._run_btn.isEnabled()


# ── the plate arrives before the plate has been read ─────────────────────────

def test_the_plate_is_painted_from_the_headers_before_the_full_read_lands(
        controller, tmp_path, monkeypatch):
    """The whole point of the two-stage scan.

    The full read is held open here, so what is asserted is the state of the
    page *while* it is still running: the wells are already on the plate, from
    the file headers, and the verdict says the answer is provisional. Without
    the peek stage this plate would be empty until every file had been round-
    tripped, which on a network share is the wait the stage exists to remove.
    """
    import datasets.czi_import.view as view_module

    released = threading.Event()
    real_read = view_module.read_source

    def _slow_read(src, params, **kwargs):
        released.wait(20)
        return real_read(src, params, **kwargs)

    monkeypatch.setattr(view_module, "read_source", _slow_read)
    page = controller.page
    folder = _split_folder(tmp_path, wells=("C07", "A02", "H11"))

    page._src_field.set_path(str(folder))
    page._on_source_changed()

    deadline = time.monotonic() + 20
    while not page._dashboard._fraction and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.002)

    assert sorted(page._dashboard._fraction) == ["A02", "C07", "H11"]
    assert page.is_scanning() is True
    assert "named in the file headers" in page._verdict.text()
    # Provisional, and **said** to be provisional. Convert is available on the
    # strength of headers alone, because the run reads and checks the plate
    # itself before it writes anything and the page's scan only describes it —
    # on a 661-file export over a share, gating the button on that description
    # was an hour of an unusable window. The pair is the rule: offering the
    # button while claiming the plate had been checked would be the silent
    # substitution, and offering nothing was the bug.
    assert "not yet checked" in page._verdict.text()
    assert page._run_btn.isEnabled()

    released.set()
    _settle(page)
    assert "across 3 file(s)" in page._verdict.text()
    assert "not yet checked" not in page._verdict.text()
    assert page._run_btn.isEnabled()


def test_a_keystroke_after_the_scan_reads_no_files(controller, tmp_path):
    """The readout runs on every keystroke in the grid and experiment fields.
    Re-reading the plate there is what made a network share unusable: those
    keystrokes cannot change which files a run reads, so they must not cost a
    round trip each."""
    page = controller.page
    _browse(page, _split_folder(tmp_path))
    page._well_source_cb.setCurrentIndex(page._well_source_cb.findData("grid"))
    _settle(page)

    folder = str(page.source_path())
    touched: list[str] = []
    real = {"open": builtins.open, "stat": os.stat,
            "scandir": os.scandir, "listdir": os.listdir}

    def _watching(name):
        def _watch(path, *args, **kwargs):
            text = str(path)
            # Every kind of round trip a share would charge for, not just the
            # reads. The resume signature stats each file; the source
            # validator and the folder tick-box *list* the folder, which is a
            # scandir and costs nothing locally — so a test counting only
            # stats and opens passed against a readout that re-listed a
            # 96-file plate on every character typed.
            if text.endswith(".czi") or text == folder:
                touched.append(f"{name} {text}")
            return real[name](path, *args, **kwargs)
        return _watch

    builtins.open = _watching("open")
    os.stat, os.scandir, os.listdir = (
        _watching("stat"), _watching("scandir"), _watching("listdir"))
    try:
        page._rows_edit.setText("1")
        page._cols_edit.setText("3")
        page._experiment_edit.setText("Run9")
    finally:
        builtins.open = real["open"]
        os.stat, os.scandir, os.listdir = (
            real["stat"], real["scandir"], real["listdir"])

    assert touched == [], (
        f"three keystrokes cost {len(touched)} filesystem call(s): "
        f"{touched[:4]}")
    assert page.is_scanning() is False


def _pump_until(predicate, timeout_s: float = 20.0):
    """Spin the event loop until *predicate* holds, or fail saying it did not."""
    deadline = time.monotonic() + timeout_s
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.002)
    QApplication.processEvents()
    assert predicate(), "the condition never became true"


@pytest.mark.parametrize("slow_stage", ["peek", "full"])
def test_a_scan_of_the_previous_source_never_lands_on_the_new_one(
        controller, tmp_path, monkeypatch, slow_stage):
    """A slow share and an impatient user.

    The first source's scan must not land on a page that has moved on: it
    would paint the previous plate over the current one and leave the readout
    describing a file nobody selected. Both stages are held in turn, because
    each has its own staleness check and a test that only overlapped one left
    the other free to be deleted.

    The release is deliberately **after** the second scan has settled. An
    earlier version set it before pumping any events, so the "slow" call never
    blocked, the two scans never overlapped, and deleting either check left
    the test green.
    """
    import datasets.czi_import.view as view_module

    started = threading.Event()
    released = threading.Event()
    first = _split_folder(tmp_path / "first", wells=("C07", "A02"))
    second = _named_czi(tmp_path / "second", wells=("H11", "B05"))
    #: The view calls these two by name; the parametrisation picks which one
    #: to hold open.
    names = {"peek": "peek_source", "full": "read_source"}
    real = {"peek": view_module.peek_source, "full": view_module.read_source}

    def _hold(src, params, **kwargs):
        if str(src) == str(first):
            started.set()
            released.wait(20)
        return real[slow_stage](src, params, **kwargs)

    monkeypatch.setattr(view_module, names[slow_stage], _hold)
    page = controller.page

    page._src_field.set_path(str(first))
    page._on_source_changed()
    _pump_until(started.is_set)

    page._src_field.set_path(str(second))
    page._on_source_changed()
    _settle(page)
    assert sorted(page._dashboard._fraction) == ["B05", "H11"]

    released.set()
    _all_scans_delivered(page)

    assert sorted(page._dashboard._fraction) == ["B05", "H11"]
    assert "2 scene(s)" in page._readout.text()
    assert "across" not in page._verdict.text()


def test_a_scan_that_lands_after_teardown_touches_nothing(qapp, tmp_path,
                                                          monkeypatch):
    """``stop_scanning`` bumps the generation and *keeps* the bridge.

    Dropping the bridge is what a first attempt did, and a result queued from
    the worker then fired on a collected ``QObject`` — a segmentation fault
    with no Python traceback, which is the failure the Datasets shutdown path
    exists to avoid.
    """
    import datasets.czi_import.view as view_module
    from datasets.czi_import.controller import CziImportController

    released = threading.Event()
    real_read = view_module.read_source

    def _slow_read(src, params, **kwargs):
        released.wait(20)
        return real_read(src, params, **kwargs)

    monkeypatch.setattr(view_module, "read_source", _slow_read)
    ctrl = CziImportController()
    ctrl.page._src_field.set_path(str(_split_folder(tmp_path)))
    ctrl.page._on_source_changed()

    ctrl.shutdown()                 # drops the generation the scan was started on
    released.set()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
        if not ctrl.page._scanning:
            break
    QApplication.processEvents()

    assert ctrl.page.is_scanning() is False
    ctrl.page.deleteLater()


# ── the two ways a source is picked ──────────────────────────────────────────

def test_browse_picks_a_file_and_the_second_button_picks_a_folder(
        controller, tmp_path, monkeypatch):
    """One dialog cannot do both, and the version that tried could do neither.

    ``QFileDialog`` in ``Directory`` mode shows files when asked but will not
    *accept* one — ``accept()`` leaves ``result() == 0`` with a ``.czi``
    selected — and ``PathField``'s edit is read-only, so Browse was the only
    way in and the single-file workflow was unreachable while the docstring and
    the manual both said otherwise. Nothing in thirty-three page tests pressed
    Browse (CLAUDE.md §7.6).

    Both routes are driven here through the dialogs they really call, so a
    change to either one's mode has to come past this.
    """
    from PySide6.QtWidgets import QFileDialog

    page = controller.page
    solo = _named_czi(tmp_path / "solo", wells=("C07", "A02"))
    folder = _split_folder(tmp_path / "split")

    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(solo), "")))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(folder)))

    assert page._pick_source("") == str(solo)

    # Clicked, not called: a method that works behind a button connected to
    # nothing is the other half of what this test exists for.
    page._pick_folder_btn.click()
    _settle(page)
    assert page.source_path() == str(folder)
    assert sorted(page._dashboard._fraction) == ["A02", "C07", "H11"]


def test_the_directory_dialog_would_not_have_accepted_a_file(qapp, tmp_path):
    """The mechanism behind the bug, pinned so it cannot be reintroduced by
    someone reaching for one dialog again. Nothing in the page under test —
    this is a statement about Qt that the page's design depends on."""
    from PySide6.QtWidgets import QFileDialog

    solo = _named_czi(tmp_path / "solo", wells=("A01",))
    dialog = QFileDialog(None, "t", str(solo.parent))
    dialog.setFileMode(QFileDialog.Directory)
    dialog.setOption(QFileDialog.DontUseNativeDialog, True)
    dialog.setOption(QFileDialog.ShowDirsOnly, False)
    dialog.selectFile(str(solo))
    dialog.accept()

    assert dialog.result() == 0, (
        "a Directory-mode dialog now accepts a file; the page could go back "
        "to one dialog for both kinds")


# ── a source that changed on disk, and a share that blinked ──────────────────

def test_re_picking_a_source_re_reads_it(controller, tmp_path):
    """The scan key is ``(source, use_folder)`` and carries no mtime on
    purpose — building one is a stat per file, on the path a keystroke takes.
    Re-picking the source is therefore the one moment a plate re-exported in
    place gets noticed, and without this the readout described 48 wells while
    the run converted 96."""
    folder = _split_folder(tmp_path, wells=("A01", "A02"))
    page = controller.page
    _browse(page, folder)
    assert sorted(page._dashboard._fraction) == ["A01", "A02"]

    for well in ("A03", "A04"):             # re-exported in place
        write_czi(folder / f"Exp7_{well}.czi",
                  plate_tiles(wells=1, fovs=1, timepoints=1, channels=2),
                  xml=plate_metadata_xml(scene_wells={0: well},
                                         channels=["DAPI", "GFP"]))
    _browse(page, folder)

    assert sorted(page._dashboard._fraction) == ["A01", "A02", "A03", "A04"]


def test_a_failed_scan_is_recovered_by_re_picking_and_not_by_typing(
        controller, tmp_path, monkeypatch):
    """A share that blinked once left the page unable to read anything again,
    because the key was kept and ``refresh_readout`` matched it, found no set,
    and returned at "a scan is in flight".

    The fix is that re-picking the source always rescans — **not** clearing the
    key on failure, which was tried first and is a retry storm: a keystroke
    refreshes the readout, so every character typed would start another scan
    against a filer that just refused. Both halves are asserted here, because
    the wrong fix passes a test that only checks the recovery.
    """
    import datasets.czi_import.view as view_module

    folder = _split_folder(tmp_path)
    page = controller.page

    calls: list[int] = []

    def _boom(src, params, **kwargs):
        calls.append(1)
        raise RuntimeError("share went away")

    monkeypatch.setattr(view_module, "peek_source", _boom)
    _browse(page, folder)
    assert "Could not read the source" in page._verdict.text()
    assert not page._run_btn.isEnabled()

    # A keystroke must **not** retry. Clearing the scan key on failure looks
    # like the fix and is a worse bug: every character typed starts another
    # scan against a share that just refused.
    scans = len(calls)
    page._rows_edit.setText("2")
    _settle(page)
    assert len(calls) == scans, (
        f"{len(calls) - scans} extra scan(s) from one keystroke after a "
        f"failure — a dead share would be re-asked per character")

    # Re-picking the source is what retries, and it always rescans.
    monkeypatch.undo()
    _browse(page, folder)

    assert sorted(page._dashboard._fraction) == ["A02", "C07", "H11"]
    assert page._run_btn.isEnabled()


def test_typing_a_stride_into_the_box_reaches_the_params_a_run_uses(
        controller, tmp_path):
    """Driven through the widget, not through ``RawFields``. The box is the
    only way a user can reach this setting, and a box wired to nothing looks
    identical to one wired correctly until something reads it (§7.6)."""
    page = controller.page
    _set_source(page, _bare_czi(tmp_path), tmp_path)

    assert page.params().timepoint_stride == 1, "blank must mean every timepoint"
    page._stride_edit.setText("3")
    assert page.params().timepoint_stride == 3
    page._stride_edit.setText("")
    assert page.params().timepoint_stride == 1


def test_a_stride_of_zero_is_refused_with_a_reason_the_page_can_show(
        controller, tmp_path):
    page = controller.page
    _set_source(page, _bare_czi(tmp_path), tmp_path)
    page._stride_edit.setText("0")
    problems = page.raw_fields().problems()
    assert any("Keep every Nth timepoint" in p for p in problems), problems


def test_the_readout_says_how_many_timepoints_a_stride_keeps(
        controller, tmp_path):
    """The page's own words, not the plan's — this is the sentence a user
    reads before pressing Convert on a run that drops two thirds of a
    timelapse."""
    page = controller.page
    _set_source(page, _named_czi(tmp_path / "src", timepoints=6), tmp_path)
    page._stride_edit.setText("3")
    _settle(page)
    # The verdict line, which is where ``plan.summary()`` lands: ``_readout``
    # above it describes the *source* and rightly still says 6.
    assert "2 of 6 timepoint(s) (t0, t3)" in page._verdict.text(), \
        page._verdict.text()
    assert "6 timepoint(s)" in page._readout.text()


def test_the_page_warns_when_the_timepoints_lost_their_elapsed_times(
        controller, tmp_path):
    """A CZI whose subblocks carry no acquisition stamps converts to a dataset
    with no time axis in Review. The page says so before the run, while the
    source can still be re-exported — not only in the log afterwards."""
    page = controller.page
    _set_source(page, _named_czi(tmp_path / "src", timepoints=3), tmp_path)
    _settle(page)
    assert "named by position" in page._verdict.text(), page._verdict.text()


def test_the_page_says_nothing_extra_when_the_elapsed_times_are_usable(
        controller, tmp_path):
    from tests._czi_fixture import Tile, plate_metadata_xml, write_czi
    import numpy as np

    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    tiles = [Tile(pixels=np.full((4, 6), t, dtype="uint16"), scene=0, t=t,
                  acquired=f"2024-06-12T{10 + 4 * t:02d}:00:00.0000000Z")
             for t in range(3)]
    path = write_czi(src / "Timed.czi", tiles,
                     xml=plate_metadata_xml(scene_wells={0: "C07"},
                                            channels=["DAPI"]))
    page = controller.page
    _set_source(page, path, tmp_path)
    _settle(page)
    assert "named by position" not in page._verdict.text(), page._verdict.text()
    assert "3 timepoint(s)" in page._verdict.text()


# ── Convert does not wait on the description ─────────────────────────────────

def test_convert_is_available_before_a_single_file_has_been_read(
        controller, tmp_path, monkeypatch):
    """The reported case: 661 files of about 450 MB on a network share, where
    naming the wells alone took hours, and for all of them the button was off.

    Nothing about that wait is a permission. The run reads and checks the
    plate itself and writes nothing until it has, so the page's scan is a
    description — and a description is not what a Convert button is waiting
    for. Here even the *peek* is held open, so the page has read no file at
    all, and Convert is still available.
    """
    import datasets.czi_import.view as view_module

    released = threading.Event()
    real_peek = view_module.peek_source

    def _held(src, params, **kwargs):
        released.wait(20)
        return real_peek(src, params, **kwargs)

    monkeypatch.setattr(view_module, "peek_source", _held)
    page = controller.page
    folder = _split_folder(tmp_path)

    page._src_field.set_path(str(folder))
    page._on_source_changed()
    QApplication.processEvents()

    try:
        assert page.is_scanning() is True
        assert not page._dashboard.well_ids(), "no file has been read yet"
        assert page._run_btn.isEnabled()
        assert page._queue_btn.isEnabled()
        # Available is not the same as checked, and the page has to say which
        # it means — a button offered beside a silent verdict would be the
        # page asserting a judgement it has not made.
        assert "has not been read yet" in page._verdict.text()
    finally:
        released.set()
        _settle(page)


def test_a_folder_with_no_czi_files_still_has_convert_off(controller, tmp_path):
    """The cheap checks are still checks. What can be answered from a
    directory listing is answered before the button is offered; it is only the
    reading of files that the button no longer waits for."""
    empty = tmp_path / "nothing"
    empty.mkdir()
    page = controller.page

    page._src_field.set_path(str(empty))
    page._on_source_changed()
    QApplication.processEvents()

    assert not page._run_btn.isEnabled()
    assert not page._queue_btn.isEnabled()


def test_a_malformed_grid_still_has_convert_off(controller, tmp_path,
                                                monkeypatch):
    """The form's own fields are the other thing answerable without reading:
    a grid the page cannot parse cannot be converted against, scan or no
    scan."""
    import datasets.czi_import.view as view_module

    released = threading.Event()
    real_peek = view_module.peek_source

    def _held(src, params, **kwargs):
        released.wait(20)
        return real_peek(src, params, **kwargs)

    monkeypatch.setattr(view_module, "peek_source", _held)
    page = controller.page
    page._src_field.set_path(str(_split_folder(tmp_path)))
    page._on_source_changed()
    QApplication.processEvents()
    assert page._run_btn.isEnabled(), "the premise: it is on before the edit"

    page._well_source_cb.setCurrentIndex(page._well_source_cb.findData("grid"))
    page._rows_edit.setText("0")
    page.refresh_readout()
    QApplication.processEvents()

    try:
        assert not page._run_btn.isEnabled()
    finally:
        released.set()
        _settle(page)


def test_completed_conversion_exposes_actual_acquisition_coverage(controller, tmp_path):
    page = controller.page
    _set_source(page, _named_czi(tmp_path, wells=("A01",), channels=("phase",)), tmp_path)
    assert "X unknown" in page._readout.text()
    assert "preview labels are provisional" in page._readout.text()
    controller._launch()
    controller._worker.wait(30_000)
    QApplication.processEvents()
    assert page._dashboard.outcome() == "done"
    assert "Recorded pixel size" in page._readout.text()
    assert "Positions: 1/1 mosaic" in page._readout.text()
    assert "Shared starts: 0/1 timepoints" in page._readout.text()
    assert "Some shared acquisition starts are unknown" in page._readout.text()
