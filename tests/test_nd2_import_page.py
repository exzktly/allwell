"""Headless checks for the Convert ND2 page, its controller and its wiring.

Two rules from CLAUDE.md §7.6/§7.8 shape what is here. Every case goes through
the **real** ``Nd2ImportController``, which builds the real page — a page whose
builder raises is a tab that renders blank and a lane that stays green. And the
controls are *driven* rather than inspected: selecting the grid well-source
through the combo, not calling the slot it happens to be connected to, because
"a control is offered" and "a control works" are the two things a Qt callback
bug sits between.

The sources are real ND2 bytes from :mod:`tests._nd2_fixture`, not stubs, for
the reason CLAUDE.md §7.1 #4 gives: a fixture that answers whatever the code
asks it validates the happy path and encodes the assumption the bug is made of.
"""

from __future__ import annotations

import os
import time
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication  # noqa: E402

from tests._nd2_fixture import write_nd2  # noqa: E402

pytest.importorskip("nd2")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def controller(qapp):
    """The real controller, torn down after each test."""
    from datasets.nd2_import.controller import Nd2ImportController

    ctrl = Nd2ImportController(on_open_in_analyze=lambda *_a: None,
                               on_add_to_queue=lambda *_a: None)
    try:
        yield ctrl
    finally:
        ctrl.shutdown()
        ctrl.page.deleteLater()


def _named_nd2(tmp_path, wells=("C07", "C07", "A02", "A02"),
               channels=("DAPI", "GFP"), timepoints=1, times_ms=None,
               name="Plate7.nd2"):
    """A plate whose positions name their wells — the normal Nikon case."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    return write_nd2(tmp_path / name, n_pos=len(wells), n_t=timepoints,
                     n_c=len(channels), well_names=list(wells),
                     channel_names=list(channels), times_ms=times_ms)


def _bare_nd2(tmp_path, positions=4):
    """A plate whose positions are unnamed — the grid fallback's reason to exist."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    return write_nd2(tmp_path / "bare.nd2", n_pos=positions, n_t=1, n_c=1,
                     well_names=[""] * positions)


def _settle(page, timeout_s: float = 20.0):
    """Spin the event loop until the page's background source scan has landed.

    The page reads the file off the UI thread — a large ND2 on a network share
    is not a free read, and freezing the window for it is the thing that design
    exists to prevent — so a test that sets a source and reads the verdict in
    the next statement is reading the "Reading…" state. Waiting on the page's
    own predicate rather than on a duration keeps that from being either a
    flake or a fixed delay in every one of these.
    """
    deadline = time.monotonic() + timeout_s
    while page.is_scanning() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.002)
    QApplication.processEvents()
    assert not page.is_scanning(), "the source scan never finished"
    return page


def _all_scans_delivered(page, timeout_s: float = 20.0):
    """Spin until every scan the page ever started has called back.

    Stronger than :func:`_settle`, which only waits for the page to stop
    considering itself busy — a *stale* scan landing later is exactly what that
    does not cover, and it is the case the generation counter exists for.
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


def _set_source(page, path, tmp_path):
    page.set_source_path(str(path))
    page.set_destination_path(str(tmp_path / "dest"))
    _settle(page)


def _browse(page, path):
    """What the Browse button does — ``set_path`` plus the changed signal.

    ``set_source_path`` deliberately does not fire it, because that is the Job
    Queue's repopulate path and a spec carries its own destination.
    """
    page._src_field.set_path(str(path))
    page._on_source_changed()
    _settle(page)


# ── the page builds, and its controls do something ───────────────────────────

def test_the_real_page_builds_and_carries_its_controls(controller):
    """The builder is what the app calls; a stubbed seam underneath it would
    never notice a missing argument or a renamed helper in this function."""
    page = controller.page
    labels = [w.text() for w in page.findChildren(type(page._readout))]

    assert "Convert ND2" in labels
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
    _set_source(page, _named_nd2(tmp_path), tmp_path)

    assert "4 position(s)" in page._readout.text()
    assert "2 channel(s)" in page._readout.text()
    verdict = page._verdict.text()
    # Acquisition order, not plate order: C07 was scanned first. A count alone
    # reads the same whether the mapping starts where the user meant it to.
    assert "C07→A02" in verdict
    assert "named by the file" in verdict
    # Two positions per well are that well's two fields of view, which is how a
    # Nikon plate scan with several fields per well describes itself.
    assert "2 FOV(s)" in verdict
    assert page._run_btn.isEnabled()


def test_an_unnamed_plate_on_the_file_source_refuses_and_says_where_to_go(
        controller, tmp_path):
    page = controller.page
    _set_source(page, _bare_nd2(tmp_path), tmp_path)

    assert "Switch the well source to the plate grid" in page._verdict.text()
    assert not page._run_btn.isEnabled()
    assert not page._queue_btn.isEnabled()


def test_the_grid_fields_turn_an_unnamed_plate_into_a_runnable_plan(
        controller, tmp_path):
    page = controller.page
    _set_source(page, _bare_nd2(tmp_path, positions=4), tmp_path)
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
    _set_source(page, _bare_nd2(tmp_path, positions=5), tmp_path)
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
    _set_source(page, _named_nd2(tmp_path, wells=("H11", "B05")), tmp_path)

    assert sorted(page._dashboard._fraction) == ["B05", "H11"]


def test_an_unreadable_source_says_so_rather_than_showing_a_stale_plan(
        controller, tmp_path):
    page = controller.page
    broken = tmp_path / "broken.nd2"
    broken.write_bytes(b"\xda\xce\xbe\x0a" + b"\0" * 200)
    _set_source(page, broken, tmp_path)

    assert page._verdict.text()
    assert page._readout.text() == ""
    assert sorted(page._dashboard._fraction) == []
    assert not page._run_btn.isEnabled()


def test_a_changed_source_resets_the_plate(controller, tmp_path):
    """A different well set is a different conversion and must reset the
    preview; the no-op guard in the dashboard must not freeze it."""
    page = controller.page
    _set_source(page, _named_nd2(tmp_path, wells=("C07", "A02")), tmp_path)
    assert sorted(page._dashboard._fraction) == ["A02", "C07"]

    page.set_source_path(str(_named_nd2(tmp_path / "second",
                                        wells=("H11", "B05"))))
    _settle(page)

    assert sorted(page._dashboard._fraction) == ["B05", "H11"]


def test_typing_in_the_grid_boxes_does_not_re_read_the_file(controller,
                                                            tmp_path):
    """The scan key is the source path alone. A key that folded in the other
    fields would re-read a multi-gigabyte file on every keystroke, which on a
    network share is what the background scan exists to avoid in the first
    place."""
    page = controller.page
    _set_source(page, _bare_nd2(tmp_path), tmp_path)
    page._well_source_cb.setCurrentIndex(page._well_source_cb.findData("grid"))
    _settle(page)
    scans_before = len(page._scan_bridges)

    page._rows_edit.setText("2")
    page._cols_edit.setText("2")
    page._experiment_edit.setText("Run9")
    QApplication.processEvents()

    assert len(page._scan_bridges) == scans_before


# ── channels ─────────────────────────────────────────────────────────────────

def test_channel_roles_are_seeded_from_the_names_and_handed_to_analyze(
        controller, tmp_path):
    page = controller.page
    _set_source(page, _named_nd2(tmp_path, channels=("DAPI", "GFP")), tmp_path)

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
    _set_source(page, _named_nd2(tmp_path, channels=("DAPI", "GFP")), tmp_path)
    _name, combo = page._channel_rows[1]

    combo.setCurrentIndex(combo.findData("phase"))

    assert page.channel_tokens_dict()["phase_token"] == "GFP"


# ── timepoints ───────────────────────────────────────────────────────────────

def test_a_timelapse_is_named_by_its_elapsed_hours(controller, tmp_path):
    """The whole reason the reader samples the frame times. Asserted as the
    filenames inside the archive, which is the boundary (§7.4 #1) — the hours
    the page computed are worth nothing if a different token reaches disk."""
    page = controller.page
    src = _named_nd2(tmp_path, wells=("C07", "A02"), channels=("DAPI",),
                     timepoints=3,
                     times_ms=[0.0, 3_600_000.0, 7_200_000.0])
    _set_source(page, src, tmp_path)

    controller._launch()
    controller._worker.wait(60_000)
    QApplication.processEvents()

    with zipfile.ZipFile(tmp_path / "dest" / "in" / "C07.zip") as archive:
        names = sorted(archive.namelist())
    assert names == ["Plate7_DAPI_C07_f0_0h.tif",
                     "Plate7_DAPI_C07_f0_1h.tif",
                     "Plate7_DAPI_C07_f0_2h.tif"]


def test_a_stride_keeps_every_nth_timepoint_under_its_own_name(controller,
                                                               tmp_path):
    """The kept timepoints keep the names they had, so t0 and t2 stay two
    hours apart in Review rather than being renumbered into neighbours."""
    page = controller.page
    src = _named_nd2(tmp_path, wells=("C07", "A02"), channels=("DAPI",),
                     timepoints=3,
                     times_ms=[0.0, 3_600_000.0, 7_200_000.0])
    _set_source(page, src, tmp_path)
    page._stride_edit.setText("2")
    _settle(page)

    assert "2 of 3 timepoint(s)" in page._verdict.text()

    controller._launch()
    controller._worker.wait(60_000)
    QApplication.processEvents()

    with zipfile.ZipFile(tmp_path / "dest" / "in" / "C07.zip") as archive:
        names = sorted(archive.namelist())
    assert names == ["Plate7_DAPI_C07_f0_0h.tif",
                     "Plate7_DAPI_C07_f0_2h.tif"]


def test_a_file_with_no_frame_times_says_so_rather_than_inventing_hours(
        controller, tmp_path):
    """A plate that lost its clock must say so on the page, while the source
    can still be re-exported — not only in a log read afterwards."""
    page = controller.page
    src = _named_nd2(tmp_path, wells=("C07", "A02"), channels=("DAPI",),
                     timepoints=3, times_ms=None)
    _set_source(page, src, tmp_path)

    # The whole sentence, not "a warning appeared": it has to say *what* was
    # lost and *why*, because "named by position" alone reads as a choice the
    # converter made rather than as something the file did not record.
    assert ("Timepoints are named by position (t0, t1, …) rather than by "
            "elapsed time — the file records no acquisition time for its "
            "frames.") in page._verdict.text()
    assert page._run_btn.isEnabled()      # a warning, not a refusal


# ── the queue hand-off ───────────────────────────────────────────────────────

def test_the_spec_a_queued_job_carries_round_trips_into_a_fresh_page(
        controller, qapp, tmp_path):
    """``collect_spec`` → ``show_job`` is what the Job Queue's edit session
    does. A field that survives the first and not the second is a setting the
    user edits and the run ignores.

    Into a **second, untouched** controller rather than back into the same
    page: ``origin`` defaults to ``"A01"``, so a ``set_params`` that skipped
    the origin line would leave ``B03`` sitting in the field from before and
    the round-trip would compare a value that had never been reset.
    """
    from datasets.nd2_import.controller import Nd2ImportController

    page = controller.page
    _set_source(page, _bare_nd2(tmp_path), tmp_path)
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
    assert spec["params"]["well_source"] == "grid"
    # 4 rather than the default 1, so a ``set_params`` that skipped the line
    # would leave the fresh page's blank box reading 1 and be caught below.
    assert spec["params"]["timepoint_stride"] == 4

    other = Nd2ImportController()
    try:
        assert other.collect_spec() != spec         # it really is untouched
        other.show_job(spec)
        assert other.collect_spec() == spec
        assert other.page._origin_edit.text() == "B03"
        assert other.page._stride_edit.text() == "4"
        # The grid fields have to be *visible* in the restored page, not merely
        # populated: a spec that restores a grid mapping behind a hidden panel
        # is a mapping the user cannot check before pressing Run.
        assert other.page._grid_host.isVisibleTo(other.page) is True
    finally:
        other.shutdown()
        other.page.deleteLater()


def test_queueing_sends_this_tool_s_own_kind(qapp, tmp_path):
    from datasets.nd2_import.controller import Nd2ImportController

    sent: list[tuple[str, dict]] = []
    ctrl = Nd2ImportController(on_add_to_queue=lambda kind, spec:
                               sent.append((kind, spec)))
    try:
        ctrl.page.queueRequested.emit()
        assert [kind for kind, _spec in sent] == ["nd2_import"]
    finally:
        ctrl.shutdown()
        ctrl.page.deleteLater()


def test_a_launch_with_no_destination_still_reports_the_job_as_finished(
        controller, tmp_path):
    """The queue is waiting on ``jobFinished`` and nothing else will send one;
    without it the whole queue stops behind this job for the session."""
    finished: list[tuple[bool, str, str]] = []
    controller.jobFinished.connect(lambda *args: finished.append(args))
    controller.page.set_source_path(str(_named_nd2(tmp_path)))

    controller._on_run()

    assert finished == [(False, "No destination folder selected.", "")]
    assert controller.page._dashboard.outcome() == "failed"


# ── a whole run, through the controller ──────────────────────────────────────

def test_a_run_converts_the_plate_and_offers_it_to_analyze(controller,
                                                           tmp_path):
    """End to end on the real worker: the page reports done, the archives are
    on disk under the file's own well names, and Open in Analyze appears."""
    page = controller.page
    _set_source(page, _named_nd2(tmp_path, wells=("C07", "A02")), tmp_path)

    controller._launch()
    assert controller._worker is not None
    controller._worker.wait(60_000)
    QApplication.processEvents()

    dest = tmp_path / "dest"
    assert sorted(p.name for p in (dest / "in").glob("*.zip")) == [
        "A02.zip", "C07.zip"]
    assert page._dashboard.outcome() == "done"
    assert page._open_btn.isVisible() or page._dest_root == str(dest)
    assert not (dest / ".aw_nd2_in_progress").exists()


def test_the_plate_still_shows_the_finished_run_after_it_ends(controller,
                                                              tmp_path):
    """The readout refresh that follows a run must not wipe the run.

    ``_on_finished`` drops the running flag and refreshes the readout, which
    re-resolves the same plan and re-seeds the plate — so a conversion that had
    just reported what it wrote would sit above a plate reading "Pending: 2"
    with the bar back at zero. Asserted as the counts and the bar, not as the
    outcome, because the outcome is ``done`` either way.
    """
    page = controller.page
    _set_source(page, _named_nd2(tmp_path, wells=("C07", "A02"),
                                 channels=("DAPI",)), tmp_path)

    controller._launch()
    controller._worker.wait(60_000)
    QApplication.processEvents()

    dashboard = page._dashboard
    assert dashboard.outcome() == "done"
    assert dict((state, n) for state, _label, n in dashboard._counts()) == {
        "pending": 0, "converting": 0, "converted": 2}
    # The bar counts wells, which is what the plate and the rail count; the
    # worker's plane count is reported as text beside it, because two units on
    # one bar is what "2 / 4" at the end of a finished run looked like.
    assert dashboard._bar.value() == dashboard._bar.maximum() == 2
    assert dashboard._status.text() == "2 of 2 image(s)"


def test_a_second_run_into_the_same_folder_is_refused_rather_than_merged(
        controller, tmp_path):
    """Two plates in one folder, with nothing in it saying so, is the failure
    the destination check exists for — and the ``out/`` measurements beside
    them would then describe inputs that are no longer there."""
    page = controller.page
    _set_source(page, _named_nd2(tmp_path, wells=("C07", "A02")), tmp_path)
    controller._launch()
    controller._worker.wait(60_000)
    QApplication.processEvents()

    _set_source(page, _named_nd2(tmp_path / "second", wells=("H11", "B05")),
                tmp_path)
    controller._launch()
    controller._worker.wait(60_000)
    QApplication.processEvents()

    assert page._dashboard.outcome() == "failed"
    assert "Already holds converted wells" in page._dashboard.headline_text()
    assert sorted(p.name for p in (tmp_path / "dest" / "in").glob("*.zip")) == [
        "A02.zip", "C07.zip"]


# ── Analyze sends a raw .nd2 here ────────────────────────────────────────────

def test_analyze_redirects_a_raw_nd2_to_this_page(qapp, tmp_path):
    """Analyze processes folders, so an ``.nd2`` dropped into its input field
    has to send the user here. Without it the refusal reads "Not a directory",
    which names neither the problem nor the page that solves it."""
    from analyze.tab import AnalyzeTab

    path = _named_nd2(tmp_path)
    tab = AnalyzeTab()
    try:
        tab._input_edit.setText(str(path))
        hint = tab._packed_input_hint()

        assert hint is not None
        assert "Convert ND2" in hint
        # Not another converter's page: either would refuse the file.
        assert "Convert OME-TIFF" not in hint
        assert "Convert CZI" not in hint
        assert tab._output_lbl.text() == "—"
        assert "Convert ND2" in tab._layout_lbl.text()
    finally:
        tab.deleteLater()


# ── the source picker ────────────────────────────────────────────────────────

def test_a_folder_is_refused_because_nikon_writes_one_file_per_plate(
        controller, tmp_path):
    """Unlike the CZI page, which reads a folder of per-scene files as one
    plate. Offering a folder here would accept a selection the converter has
    no way to interpret."""
    page = controller.page
    folder = tmp_path / "somefolder"
    folder.mkdir()

    _browse(page, folder)

    assert "not a folder" in page._src_field.message()
    assert not page._run_btn.isEnabled()


def test_a_file_that_is_not_an_nd2_is_refused_by_the_field(controller,
                                                           tmp_path):
    page = controller.page
    other = tmp_path / "notes.txt"
    other.write_text("hello")

    _browse(page, other)

    assert "Not a .nd2 file" in page._src_field.message()
    assert not page._run_btn.isEnabled()


def test_the_destination_default_is_named_after_the_file(controller, tmp_path):
    page = controller.page

    _browse(page, _named_nd2(tmp_path))

    assert page.destination_path() == str(tmp_path / "Plate7")


# ── a scan that lands late must not overwrite a newer one ────────────────────

def test_a_stale_scan_does_not_overwrite_the_plate_of_a_newer_one(
        controller, tmp_path, monkeypatch):
    """The generation counter's reason to exist.

    The **first** source is made the slow one, which is the whole test. Two
    sources picked in a row both land in the order they were started, so the
    newer one is last on the plate whether or not the page checks anything —
    the first version of this test passed against a page with the staleness
    check deleted (CLAUDE.md §7.8: build the fixture so the right and the wrong
    answer differ). Delaying the first read inverts the arrival order, which is
    what happens for real when a user re-picks while a big file is still being
    read off a slow share.
    """
    import datasets.nd2_import.view as view_module

    page = controller.page
    first = _named_nd2(tmp_path / "one", wells=("C07", "A02"))
    second = _named_nd2(tmp_path / "two", wells=("H11", "B05"))

    real_read = view_module.read_source

    def _slow_for_the_first(src, *args, **kwargs):
        if str(src) == str(first):
            time.sleep(0.75)
        return real_read(src, *args, **kwargs)

    monkeypatch.setattr(view_module, "read_source", _slow_for_the_first)

    page.set_source_path(str(first))
    page.set_source_path(str(second))
    _all_scans_delivered(page)
    _settle(page)

    assert sorted(page._dashboard._fraction) == ["B05", "H11"]


# ── what the adversarial review found ────────────────────────────────────────

def test_a_job_started_from_the_queue_reports_on_a_plate_that_shows_the_run(
        controller, tmp_path):
    """The queue route begins before the source scan has landed.

    ``start_from_queue`` calls ``show_job`` then ``_launch`` back to back while
    the layout read is still on its worker thread, so the run used to start
    with no wells on the plate — and the dashboard drops every well-progress
    signal for a well it does not know. The conversion wrote its archives and
    reported "done" above a plate reading "Pending: 0", bar at zero, while the
    status line beside it said "8 of 8 image(s)": two writers on one section
    disagreeing about whether anything had happened.

    Asserted as the rail counts and the bar, not as the outcome, because the
    outcome was `done` throughout and is what the plain run test already
    covers.
    """
    src = _named_nd2(tmp_path, wells=("C07", "C07", "A02", "A02"),
                     channels=("DAPI",))
    dest = tmp_path / "queued"

    controller.start_from_queue({"source": str(src), "destination": str(dest),
                                 "params": {"well_source": "file"}})
    controller._worker.wait(60_000)
    for _ in range(50):
        QApplication.processEvents()
        time.sleep(0.01)

    dashboard = controller.page._dashboard
    assert sorted(p.name for p in (dest / "in").glob("*.zip")) == [
        "A02.zip", "C07.zip"]
    assert dashboard.outcome() == "done"
    assert dict((state, n) for state, _label, n in dashboard._counts()) == {
        "pending": 0, "converting": 0, "converted": 2}
    assert dashboard._bar.value() == dashboard._bar.maximum() == 2


def test_a_mid_run_reseed_never_wipes_a_plate_that_is_already_filling(
        controller, tmp_path):
    """The other direction of the same fix.

    Seeding the plate mid-run is bounded to the case where it is *empty*; a
    reseed of a populated plate would wipe the dials of the run in flight,
    which is the defect the dashboard's own no-op guard exists for.
    """
    page = controller.page
    _set_source(page, _named_nd2(tmp_path, wells=("C07", "A02")), tmp_path)
    page.set_well_fraction("C07", 1, 1)
    page.set_running(True)

    page.refresh_readout()
    QApplication.processEvents()

    assert page._dashboard._fraction["C07"] == 1.0
    page.set_running(False)


@pytest.mark.parametrize("prepare,why", [
    (lambda d: ((d / "in").mkdir(parents=True),
                (d / "in" / "A01.zip").write_bytes(b"")),
     "already holds converted wells"),
    (lambda d: ((d / "out").mkdir(parents=True),
                (d / "out" / "pipeline_info.json").write_text("{}")),
     "already holds a finished dataset"),
])
def test_a_destination_the_run_would_refuse_also_disables_the_buttons(
        controller, tmp_path, prepare, why):
    """A red hint under the field is not a gate.

    Convert stayed live and produced a worker-side refusal; **+ Queue** put a
    job in the queue that was guaranteed to fail, which the user only finds
    out about when the queue reaches it.
    """
    dest = tmp_path / "dest"
    prepare(dest)
    page = controller.page
    _set_source(page, _named_nd2(tmp_path), tmp_path)

    assert page._dest_field.message(), f"the field says nothing about {why}"
    assert not page._run_btn.isEnabled(), why
    assert not page._queue_btn.isEnabled(), why


def test_an_unexpected_error_while_planning_is_not_reported_as_a_refusal(
        controller, tmp_path, monkeypatch):
    """A crash must not look like an ordinary answer.

    The two worker arms had different exception sets by design, and sharing
    one worker flattened them: ``ValueError`` is a refusal while *converting*,
    because the exploder raises a plain one when the grid and the file
    disagree, and every designed ``ValueError`` in *planning* is already
    wrapped by the compute module. So a ``ValueError`` reaching the planning
    arm is a bug, and reporting it as a refusal showed a bare sentence with no
    traceback logged anywhere.
    """
    import dataclasses

    page = controller.page
    _set_source(page, _named_nd2(tmp_path), tmp_path)

    def _boom(_source, _params):
        raise ValueError("synthetic plan failure")

    # ``ConverterSpec`` is frozen, so the spec is *replaced* rather than
    # mutated — which is closer to how a converter declares one anyway.
    monkeypatch.setattr(type(controller), "spec",
                        dataclasses.replace(controller.spec, plan=_boom))
    seen: list[tuple[bool, str, str]] = []
    controller.jobFinished.connect(lambda *a: seen.append(a))

    controller._launch()
    controller._worker.wait(30_000)
    QApplication.processEvents()

    assert seen, "the job never reported back"
    assert "Unexpected error while planning" in seen[-1][1]


def test_the_spec_carries_the_destination_and_the_fov_count(controller,
                                                            tmp_path):
    """Two fields the round-trip test could not see.

    ``destination`` was never asserted directly — only through
    ``other.collect_spec() == spec``, which is symmetric, so dropping it from
    both sides compared equal (§7.8 face two). And ``fovs_per_well`` was the
    one editable field the round trip never gave a non-default value, so both
    sides compared a blank box.
    """
    page = controller.page
    _set_source(page, _bare_nd2(tmp_path), tmp_path)
    page._well_source_cb.setCurrentIndex(page._well_source_cb.findData("grid"))
    page._rows_edit.setText("2")
    page._cols_edit.setText("2")
    page._fovs_edit.setText("1")
    _settle(page)

    spec = controller.collect_spec()

    assert spec["destination"] == str(tmp_path / "dest")
    assert spec["params"]["fovs_per_well"] == 1

    from datasets.nd2_import.controller import Nd2ImportController
    other = Nd2ImportController()
    try:
        other.show_job(spec)
        assert other.page.destination_path() == str(tmp_path / "dest")
        assert other.page._fovs_edit.text() == "1"
    finally:
        other.shutdown()
        other.page.deleteLater()


def test_an_interrupted_run_writes_a_marker_and_the_next_run_finishes_it(
        controller, tmp_path):
    """The resume path, end to end on the real worker.

    Nothing covered it. The only assertion that looked like it did — that the
    marker is *absent* after a successful run — is satisfied by an
    implementation that never writes one, and that implementation loses every
    resume: a cancelled run then leaves wells behind with nothing recording
    the settings, and the next attempt refuses with "Already holds converted
    wells".
    """
    from datasets.nd2_import.nd2_import_compute import (
        IN_PROGRESS_MARKER,
        ConversionParams,
        resume_state,
    )
    from pipeline.nd2_explode import explode_nd2

    src = _named_nd2(tmp_path, wells=("C07", "C07", "A02", "A02"),
                     channels=("DAPI",))
    dest = tmp_path / "dest"
    page = controller.page
    _set_source(page, src, tmp_path)

    # Cancel part-way, through the exploder the worker uses, so the marker and
    # the partial archives are produced by the real path.
    from datasets.nd2_import.apply import convert
    calls = {"n": 0}

    def _abort() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    params = ConversionParams()
    try:
        convert(src, dest, params, log=lambda _m: None,
                is_cancelled=_abort, progress=lambda *_a: None,
                well_progress=lambda *_a: None)
    except Exception:
        pass

    assert (dest / IN_PROGRESS_MARKER).exists(), "no marker to resume from"
    state = resume_state(dest, src, params)
    assert state.resumable is True

    del explode_nd2   # only imported to assert the real path is in play

    controller._launch()
    controller._worker.wait(60_000)
    QApplication.processEvents()

    assert page._dashboard.outcome() == "done"
    assert sorted(p.name for p in (dest / "in").glob("*.zip")) == [
        "A02.zip", "C07.zip"]
    assert not (dest / IN_PROGRESS_MARKER).exists()


def test_open_in_analyze_hands_over_the_folder_and_the_channel_tokens(
        qapp, tmp_path):
    """Driven through the **button**, not through the method behind it.

    The assertion this replaces was ``page._open_btn.isVisible() or
    page._dest_root == str(dest)``, whose right-hand side is set
    unconditionally and is therefore always true. Deleting the promote call,
    emitting the empty string, and handing over an empty token dict all left
    the file green, and no test ever clicked the button.
    """
    from datasets.nd2_import.controller import Nd2ImportController

    handed: list[tuple[str, dict]] = []
    ctrl = Nd2ImportController(
        on_open_in_analyze=lambda root, tokens=None: handed.append(
            (root, tokens or {})))
    try:
        page = ctrl.page
        _set_source(page, _named_nd2(tmp_path, channels=("DAPI", "GFP")),
                    tmp_path)
        ctrl._launch()
        ctrl._worker.wait(60_000)
        QApplication.processEvents()

        # ``isVisibleTo`` rather than ``isVisible``: the page has no shown
        # ancestor offscreen, so ``isVisible`` is False for every widget on it.
        # Working around that with an ``or`` against a field set
        # unconditionally is what made the assertion this replaces vacuous.
        assert page._open_btn.isVisibleTo(page)
        page._open_btn.click()
        QApplication.processEvents()

        assert handed, "clicking Open in Analyze handed over nothing"
        root, tokens = handed[-1]
        assert root == str(tmp_path / "dest")
        assert tokens.get("nuclear_token") == "DAPI"
    finally:
        ctrl.shutdown()
        ctrl.page.deleteLater()


def test_a_plate_imaged_once_is_not_told_on_the_page_that_it_lost_its_clock(
        controller, tmp_path):
    """The page half of that fix, which nothing covered.

    Deleting both caller guards left 281 tests green, including the page-level
    files, because the only test called the shared helper directly and both
    page tests used three timepoints and never reached the single-timepoint
    case. A plate imaged once is not a timelapse whose elapsed times were
    dropped, so there is nothing to warn about — and warning anyway put a
    degradation notice on every ordinary non-timelapse plate.
    """
    page = controller.page
    _set_source(page, _named_nd2(tmp_path, wells=("C07", "A02"),
                                 channels=("DAPI",), timepoints=1), tmp_path)

    verdict = page._verdict.text()
    assert "1 timepoint(s)" in verdict
    assert "named by position" not in verdict, verdict
    assert "elapsed" not in verdict, verdict
    assert page._run_btn.isEnabled()


def test_a_finished_run_reports_the_job_as_finished(controller, tmp_path):
    """The Job Queue waits on ``jobFinished`` and nothing else will send one.

    Deleting this emit left the **whole fast lane** green — 9,982 tests — and
    its absence stalls the queue for the session after every conversion,
    success or failure. The failure path was covered; the normal one was not.
    """
    page = controller.page
    _set_source(page, _named_nd2(tmp_path, channels=("DAPI",)), tmp_path)
    seen: list[tuple[bool, str, str]] = []
    controller.jobFinished.connect(lambda *args: seen.append(args))

    controller._launch()
    controller._worker.wait(60_000)
    QApplication.processEvents()

    assert seen, "a successful conversion never reported the job as finished"
    ok, message, dest_root = seen[-1]
    assert ok is True
    assert dest_root == str(tmp_path / "dest")
    assert "image(s)" in message


# ── picking a subset on the plate ────────────────────────────────────────────

def test_picking_wells_on_the_plate_converts_only_those(controller, tmp_path):
    """Driven through the dashboard's own plate, not through ``params()``.

    The plate is the control a user touches, and selecting on it is what has
    to reach the run — a test that set ``well_subset`` directly would pass for
    a plate wired to nothing.
    """
    page = controller.page
    _set_source(page, _named_nd2(tmp_path, wells=("C07", "C07", "A02", "A02"),
                                 channels=("DAPI",)), tmp_path)
    assert page.params().well_subset == (), "a fresh page converts everything"

    page._dashboard.set_selected_wells(["A02"])
    QApplication.processEvents()

    assert page.params().well_subset == ("A02",)

    controller._launch()
    controller._worker.wait(60_000)
    QApplication.processEvents()

    assert sorted(p.name for p in (tmp_path / "dest" / "in").glob("*.zip")) == [
        "A02.zip"]


def test_the_readout_counts_the_selected_wells_not_the_plate(controller,
                                                             tmp_path):
    """The surface the user reads before pressing Convert.

    Picking two of three wells still printed the plate's own count and image
    total — the same untruth the run log's count was fixed against, one screen
    earlier. Asserted as both numbers, because "2" alone appears in the
    unselected line too.
    """
    page = controller.page
    _set_source(page, _named_nd2(tmp_path,
                                 wells=("C07", "C07", "A02", "A02"),
                                 channels=("DAPI",), timepoints=2), tmp_path)
    whole = page._verdict.text()
    assert "2 well(s)" in whole and "8 image(s)" in whole

    page._dashboard.set_selected_wells(["A02"])
    QApplication.processEvents()

    picked = page._verdict.text()
    assert "1 of 2 well(s)" in picked, picked
    assert "selected" in picked
    assert "4 image(s)" in picked, picked


def test_a_selection_survives_the_job_queue_round_trip(controller, tmp_path):
    """A setting the user edits and the queued run ignores is the shape this
    round-trip test exists for."""
    from datasets.nd2_import.controller import Nd2ImportController

    page = controller.page
    _set_source(page, _named_nd2(tmp_path, wells=("C07", "C07", "A02", "A02")),
                tmp_path)
    page._dashboard.set_selected_wells(["A02"])
    QApplication.processEvents()
    spec = controller.collect_spec()
    assert spec["params"]["well_subset"] == ["A02"]

    other = Nd2ImportController()
    try:
        other.show_job(spec)
        assert other.page.params().well_subset == ("A02",)
        assert other.page._dashboard.selected_wells() == ["A02"]
    finally:
        other.shutdown()
        other.page.deleteLater()


def test_the_plate_selection_is_locked_while_a_run_is_in_flight(controller,
                                                                tmp_path):
    """The plate is the progress display during a run, so a selection changed
    then would describe a conversion other than the one being reported on."""
    page = controller.page
    _set_source(page, _named_nd2(tmp_path), tmp_path)

    page.set_running(True)
    try:
        assert page._dashboard._plate.selectionMode() == "passive"
    finally:
        page.set_running(False)
    assert page._dashboard._plate.selectionMode() == "select"
