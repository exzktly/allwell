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
