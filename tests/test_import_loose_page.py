"""Offscreen controller/page tests for the Import Loose Folder feature.

Same pattern as ``tests/test_validate_page.py`` / ``tests/test_subset_page.py``.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from tests._datasets_worker import run_worker_to_completion  # noqa: E402

SCHEMA = "experiment:channel:well:fov:timepoint"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _write_loose(folder, wells, timepoints=("0h",)):
    folder.mkdir(parents=True, exist_ok=True)
    for well in wells:
        for tp in timepoints:
            (folder / f"exp_GFP_{well}_F001_{tp}.tif").write_bytes(b"img")


# One shared implementation — the six copies of this helper all failed
# open the same two ways. See tests/_datasets_worker.py.
_run_worker_to_completion = run_worker_to_completion


def test_dashboard_reveals_on_a_scanned_source_and_hides_without_one(qapp, tmp_path):
    """Regression guard (UX audit P1-2): the status strip below the Convert
    button used to either stay hidden until Convert was pressed (a source
    folder already picked, already scanned by the live readout above — the
    page had every fact needed to answer "what would this convert?" and
    showed nothing), or, before that, be visible from page load with an
    idle headline over an empty plate map (nothing to show at all yet).
    It must hide with no matching source, and reveal seeded with the real
    scanned wells the moment there is one. Uses isHidden() (a local,
    explicit hide/show flag) rather than isVisible(), since the page is
    never actually shown in this test — isVisible() would report False for
    the whole tree regardless of these setVisible() calls."""
    from datasets.import_loose.controller import ImportLooseController

    src = tmp_path / "loose"
    _write_loose(src, ["A01"])
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    ctrl = ImportLooseController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    assert page.dashboard.isHidden() is True

    page._src_field.set_path(str(src))
    page._on_source_changed()
    assert page.dashboard.isHidden() is False  # a real scan -> a real preview
    assert page.dashboard._wells == ["A01"]
    assert "Ready" in page.dashboard._headline.text()

    page._dest_field.set_path(str(tmp_path / "dest"))
    page._refresh_run_enabled()
    page.runRequested.emit()
    assert page.dashboard.isHidden() is False  # shown the moment Convert starts
    _run_worker_to_completion(ctrl)
    assert page.dashboard.isHidden() is False  # stays up to show the result

    page._src_field.set_path(str(empty_dir))
    page._on_source_changed()
    assert page.dashboard.isHidden() is True  # nothing scanned -> nothing to preview
    ctrl.shutdown()


def test_readout_updates_on_source_change(qapp, tmp_path):
    from datasets.import_loose.controller import ImportLooseController

    src = tmp_path / "loose"
    _write_loose(src, ["A01", "B02"], timepoints=("0h", "24h"))
    ctrl = ImportLooseController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()
    assert "2 well" in page._readout.text()
    ctrl.shutdown()


def test_run_gate_requires_destination_unless_in_place(qapp, tmp_path):
    from datasets.import_loose.controller import ImportLooseController

    src = tmp_path / "loose"
    _write_loose(src, ["A01"])
    ctrl = ImportLooseController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()
    assert not page._run_btn.isEnabled()

    page._dest_field.set_path(str(tmp_path / "dest"))
    page._refresh_run_enabled()
    assert page._run_btn.isEnabled()

    page._in_place_cb.setChecked(True)
    assert page._run_btn.isEnabled()
    # Disabled, not hidden (UX audit P2-6) -- a layout jump was the only
    # signal this destructive mode had been armed; the field stays visible
    # but inert, next to an explicit inline warning.
    assert not page._dest_field.isHidden()
    assert not page._dest_field.isEnabled()
    assert not page._in_place_warning.isHidden()

    page._in_place_cb.setChecked(False)
    assert page._dest_field.isEnabled()
    assert page._in_place_warning.isHidden()
    ctrl.shutdown()


def test_run_gate_refuses_a_destination_that_already_has_content(qapp, tmp_path):
    """Regression guard: the destination PathField had no validator at all
    (unlike Concatenate's/Merge Plates'), and _can_run() only checked
    bool(destination_path()) -- a destination already holding another
    dataset's files was accepted by the run gate, and the actual convert
    would then silently overwrite that dataset's in/<WELL>.zip (see
    tests/test_import_loose_compute.py's apply.py-level regression test)."""
    from tests.fixtures.dataset_builder import build_dataset

    from datasets.import_loose.controller import ImportLooseController

    src = tmp_path / "loose"
    _write_loose(src, ["A01"])
    existing = tmp_path / "existing"
    build_dataset(existing, wells=["A01"], timepoints=["0h"])

    ctrl = ImportLooseController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()

    page._dest_field.set_path(str(existing))
    page._refresh_run_enabled()
    assert not page._run_btn.isEnabled()
    assert not page._dest_field.isHidden()
    # The shared rejection, word for word — not a re-typed copy of it. This page
    # kept its own validator and its own phrasing for the same verdict, which is
    # what `_common/dataset_fields` exists to stop: a user moving between two
    # Datasets tools should not be told the same thing two different ways.
    from datasets.subset.view import _is_destination as _subset_destination
    assert page._dest_field.message() == _subset_destination(existing)
    assert "empty folder" in page._dest_field.message()

    page._dest_field.set_path(str(tmp_path / "fresh_dest"))
    page._refresh_run_enabled()
    assert page._run_btn.isEnabled()
    ctrl.shutdown()


def test_convert_to_new_destination_through_the_page(qapp, tmp_path):
    import zipfile

    from datasets.import_loose.controller import ImportLooseController

    src = tmp_path / "loose"
    dest = tmp_path / "dest"
    _write_loose(src, ["A01", "B02"])

    ctrl = ImportLooseController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()
    page._dest_field.set_path(str(dest))
    page._refresh_run_enabled()

    page.runRequested.emit()
    _run_worker_to_completion(ctrl)

    with zipfile.ZipFile(dest / "in" / "A01.zip") as zf:
        assert zf.namelist() == ["exp_GFP_A01_F001_0h.tif"]
    assert not page._open_btn.isHidden()
    # Regression guard: the button used to read the generic "Open in
    # Analyze" -- missing the noun every other tool's post-run open button
    # names ("Open dataset in Analyze", "Open subset in Review", "Open
    # merged dataset in Review") -- so it read as if nothing had been
    # produced.
    assert page._open_btn.text() == "Open dataset in Analyze"
    # The per-well plate map (Analyze-style graphical progress) reflects the
    # real conversion, not just a plain progress bar: both wells the scan
    # found are tracked and land in the "done" state.
    assert page.dashboard._wells == ["A01", "B02"]
    assert page.dashboard._well_stage == {"A01": "done", "B02": "done"}
    assert page.dashboard.overall_maximum() == 2
    # Regression guard (UX audit P2-3): the dashboard headline shows a
    # basename, not the full destination path buried in the raw summary
    # ("Wrote N well(s) into <dest_root>/in.").
    assert str(dest) not in page.dashboard._headline.text()
    assert dest.name in page.dashboard._headline.text()
    assert str(dest) in page.dashboard._headline.toolTip()
    ctrl.shutdown()


def test_successful_run_shows_a_neutral_note_not_the_raw_already_has_files_warning(qapp, tmp_path):
    """Regression guard (UX audit P1-3): a completed new-destination convert
    writes its output into the destination, but nothing ever re-ran the
    destination validator afterward -- the button just silently disabled on
    a second Convert attempt with the field's inline hint still blank, and
    once fixed to revalidate, the raw validator wording ("Already has files
    under 'in' or 'out' — choose an empty folder.") would describe the run's
    own success as an error. It must show a neutral note instead."""
    from datasets.import_loose.controller import ImportLooseController

    src = tmp_path / "loose"
    dest = tmp_path / "dest"
    _write_loose(src, ["A01"])

    ctrl = ImportLooseController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()
    page._dest_field.set_path(str(dest))
    page._refresh_run_enabled()

    page.runRequested.emit()
    _run_worker_to_completion(ctrl)

    assert ctrl._last_result[0] is True
    assert page._dest_field._hint.objectName() == "Muted"
    assert "already has files" not in page._dest_field._hint.text().lower()
    assert "now holds the zipped dataset" in page._dest_field._hint.text()
    assert not page._dest_field.is_valid()
    assert not page._run_btn.isEnabled()  # the run gate itself is unaffected
    ctrl.shutdown()


def test_group_in_place_through_the_page_zips_and_deletes_sources(qapp, tmp_path):
    """Regression for the reported "sits stuck, no zips made" bug (fixed in
    apply.py's convert_in_place/_zip_well_folders) plus the adjacent bug found
    alongside it: the checkbox label ("delete loose TIFs on success") and the
    confirm dialog both promise deletion, but the controller used to hardcode
    move_source_tifs=False regardless of in_place — so WellPlateZipper's
    folder_well() always copied (never moved) originals, leaving them behind
    even on a fully successful run. Drives the real page + worker +
    subprocess-backed run_zipper end to end (stdlib-only, no imaging stack)."""
    import zipfile

    from datasets.import_loose.controller import ImportLooseController

    src = tmp_path / "loose"
    _write_loose(src, ["A01", "B02"], timepoints=("0h", "24h"))

    ctrl = ImportLooseController(on_open_in_analyze=lambda _p: None)
    ctrl._confirm_in_place = lambda: True  # bypass the modal QMessageBox
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()
    page._in_place_cb.setChecked(True)
    page._refresh_run_enabled()

    page.runRequested.emit()
    _run_worker_to_completion(ctrl)

    for well in ("A01", "B02"):
        zip_path = src / f"{well}.zip"
        assert zip_path.exists(), f"{well}: expected a zip, found none"
        with zipfile.ZipFile(zip_path) as zf:
            assert set(zf.namelist()) == {f"exp_GFP_{well}_F001_0h.tif", f"exp_GFP_{well}_F001_24h.tif"}
    assert not list(src.glob("*.tif")), "loose TIF originals should be deleted, not copied, on success"
    assert not page._open_btn.isHidden()
    # In-place mode drives the same plate-map dashboard through the
    # subprocess-backed run_zipper's progress_fn -> "writing" then
    # _zip_well_folders's own "done" report, not just the new-destination
    # mode's ThreadPoolExecutor path.
    assert page.dashboard._wells == ["A01", "B02"]
    assert page.dashboard._well_stage == {"A01": "done", "B02": "done"}
    ctrl.shutdown()


def test_queue_spec_round_trips_filename_schema_and_separator(qapp, tmp_path):
    """Regression guard: collect_spec()/show_job() used to leave out
    filename_schema/filename_sep entirely -- a queued Import Loose job
    always converted with the page's default schema/separator regardless of
    what the user had actually typed when they queued it."""
    from datasets.import_loose.controller import ImportLooseController

    src = tmp_path / "loose"
    _write_loose(src, ["A01"])
    ctrl = ImportLooseController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()
    page._dest_field.set_path(str(tmp_path / "dest"))
    page.set_filename_schema("plate:marker:well:site:tp")
    page.set_filename_sep("-")

    spec = ctrl.collect_spec()
    assert spec["filename_schema"] == "plate:marker:well:site:tp"
    assert spec["filename_sep"] == "-"

    page.set_filename_schema("")
    page.set_filename_sep("")
    ctrl.show_job(spec)
    assert page.filename_schema() == "plate:marker:well:site:tp"
    assert page.filename_sep() == "-"
    ctrl.shutdown()


def test_in_place_success_reports_the_launched_source_not_a_since_changed_field(qapp, tmp_path):
    """Regression guard: the source PathField used to stay enabled for the
    whole run (unlike Subset's/Validate's locked fields), and
    _on_succeeded used to read self.page.source_path() (the live field)
    for in-place mode's "produced" root instead of what this run actually
    converted -- so a source change mid-run would make jobFinished's third
    argument (what "Open in Analyze" opens, and what a chained queue
    successor uses) name a folder this run never touched."""
    from datasets.import_loose.controller import ImportLooseController

    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    _write_loose(root_a, ["A01"])
    _write_loose(root_b, ["B02"])

    ctrl = ImportLooseController(on_open_in_analyze=lambda _p: None)
    ctrl._confirm_in_place = lambda: True  # bypass the modal QMessageBox
    page = ctrl.page
    page._src_field.set_path(str(root_a))
    page._on_source_changed()
    page._in_place_cb.setChecked(True)
    page._refresh_run_enabled()

    page.runRequested.emit()
    # Locked means the Browse button, not the whole field (UX audit P2-4) —
    # doesn't change what this test is actually about: the field's *value*
    # can still be driven programmatically below regardless of lock state,
    # which is exactly the path _on_succeeded must not trust.
    assert page._src_field._browse_btn.isEnabled() is False

    # The field changes after the run is launched but before it finishes.
    page._src_field.set_path(str(root_b))
    page._on_source_changed()
    _run_worker_to_completion(ctrl)
    assert page._src_field._browse_btn.isEnabled() is True

    assert ctrl._last_result[2] == str(root_a)
    ctrl.shutdown()


def test_request_stop_terminates_tracked_subprocess(tmp_path):
    """Regression guard: ImportLooseController.stop() used to call only
    QThread.requestInterruption() -- a no-op while run() is blocked inside
    convert_in_place's WellPlateZipper.py subprocess, since nothing on that
    path ever polls the interruption flag. request_stop() must also reach
    the tracked subprocess directly."""
    import subprocess
    import time

    from datasets.import_loose.worker import ImportLooseWorker

    worker = ImportLooseWorker(
        str(tmp_path), "", filename_schema="experiment:channel:well:fov:timepoint",
        filename_sep="_", in_place=True,
    )
    proc = subprocess.Popen(["sleep", "30"])
    worker._track_proc(proc)
    worker.request_stop()

    for _ in range(50):
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    assert proc.poll() is not None, "tracked subprocess should have been terminated"
    proc.wait(timeout=5)


def test_no_match_reports_failure_not_crash(qapp, tmp_path):
    from datasets.import_loose.controller import ImportLooseController

    src = tmp_path / "loose"
    src.mkdir()
    (src / "readme.txt").write_text("hi")
    ctrl = ImportLooseController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    page._src_field.set_path(str(src))
    page._on_source_changed()
    page._dest_field.set_path(str(tmp_path / "dest"))
    page._refresh_run_enabled()

    seen = []
    ctrl._on_failed = lambda msg: (seen.append(msg), page.append_log(f"✗ {msg}"))
    page.runRequested.emit()
    _run_worker_to_completion(ctrl)
    assert seen and "No files matched" in seen[0]
    ctrl.shutdown()


# ── the second UX review: saying which thing went wrong, and refusing early ──

def _page(qapp):
    from datasets.import_loose.view import ImportLoosePage

    page = ImportLoosePage()
    page.show()
    for _ in range(4):
        qapp.processEvents()
    return page


def test_an_empty_result_says_which_of_the_three_reasons_it_is(qapp, tmp_path):
    """One sentence — "No files matched the schema." — used to cover a folder
    whose images are one level down, a folder with no images at all, and a
    genuinely wrong schema. Two of those are not about the schema, and the
    page sent the user off to rebuild one that was never the problem."""
    nested = tmp_path / "nested"
    _write_loose(nested / "condition_a", ["A01"])
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "notes.txt").write_text("x")
    wrong = tmp_path / "wrong"
    _write_loose(wrong, ["A01"])

    page = _page(qapp)

    page._src_field.set_path(str(nested))
    page._on_source_changed()
    nested_text = page._readout.text()

    page._src_field.set_path(str(empty))
    page._on_source_changed()
    empty_text = page._readout.text()

    page._src_field.set_path(str(wrong))
    page._on_source_changed()
    page.set_filename_sep("-")                     # real files, wrong separator
    wrong_text = page._readout.text()

    assert "subfolder" in nested_text.lower()
    assert "1" in nested_text, "it should say how many are down there"
    assert "no .tif" in empty_text.lower()
    assert "schema" in wrong_text.lower()
    assert len({nested_text, empty_text, wrong_text}) == 3, (
        f"three causes, three messages: {nested_text!r} / {empty_text!r} / {wrong_text!r}")


def test_the_run_is_refused_when_the_scan_found_nothing(qapp, tmp_path):
    """The page has already scanned and knows the answer is zero. Offering the
    run spends the user's click to tell them what the readout above the button
    already says — and the failure it produces replaces any previous result."""
    src = tmp_path / "loose"
    _write_loose(src, ["A01"])
    page = _page(qapp)
    page._dest_field.set_path(str(tmp_path / "dest"))
    page._src_field.set_path(str(src))
    page._on_source_changed()
    assert page._run_btn.isEnabled()

    page.set_filename_sep("-")                     # nothing matches now
    assert not page._run_btn.isEnabled()
    assert not page._queue_btn.isEnabled(), "queueing a doomed run only defers it"
    assert "schema" in page._readout.text().lower()

    page.set_filename_sep("_")
    assert page._run_btn.isEnabled()


def test_the_destination_may_not_be_the_source(qapp, tmp_path):
    """It used to be allowed, and left the folder half loose images and half
    dataset with the originals undeleted — neither what the new-destination
    mode promises nor what Group in place does."""
    src = tmp_path / "loose"
    _write_loose(src, ["A01"])
    page = _page(qapp)
    page._dest_field.set_path(str(src))
    page._src_field.set_path(str(src))
    page._on_source_changed()

    assert not page._run_btn.isEnabled()
    assert "different folder" in page._dest_field._hint.text().lower()

    page._dest_field.set_path(str(tmp_path / "elsewhere"))
    page._refresh_run_enabled()
    assert page._run_btn.isEnabled()


def test_arming_in_place_takes_the_destination_error_with_the_field(qapp, tmp_path):
    """A red "choose an empty folder" under a greyed-out box, beside a live Run
    button that does not use the destination at all."""
    from tests.fixtures.dataset_builder import build_dataset

    src = tmp_path / "loose"
    _write_loose(src, ["A01"])
    existing = tmp_path / "existing"
    build_dataset(existing, wells=["A01"], timepoints=["0h"])

    page = _page(qapp)
    page._src_field.set_path(str(src))
    page._on_source_changed()
    page._dest_field.set_path(str(existing))
    page._refresh_run_enabled()
    assert page._dest_field._hint.isVisibleTo(page)

    page.set_in_place(True)
    assert not page._dest_field._hint.isVisibleTo(page)
    assert page._run_btn.isEnabled()

    page.set_in_place(False)
    assert page._dest_field._hint.isVisibleTo(page)


def test_one_scan_per_input_change_not_two(qapp, tmp_path, monkeypatch):
    """The readout and the plate preview ask the same question of the same
    folder with the same arguments. Scanning twice walks a whole plate twice on
    the GUI thread, on fields that update per keystroke."""
    import datasets.import_loose.view as view_mod

    src = tmp_path / "loose"
    _write_loose(src, ["A01", "B02"])
    page = _page(qapp)
    page._src_field.set_path(str(src))
    page._on_source_changed()

    calls = []
    real = view_mod.scan_folder
    monkeypatch.setattr(view_mod, "scan_folder",
                        lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    page.set_filename_sep("-")
    assert len(calls) == 1, f"{len(calls)} scans for one change"


def test_after_an_in_place_run_the_page_does_not_contradict_itself(qapp, tmp_path):
    """The folder now holds archives, so a re-scan reports that nothing matches
    the schema — directly above this run's own green "Grouped loose TIFs …".
    And Run stayed enabled: pressing it traded the result, including the Open
    in Analyze handoff, for a red failure."""
    src = tmp_path / "loose"
    _write_loose(src, ["A01"])
    page = _page(qapp)
    page._src_field.set_path(str(src))
    page._on_source_changed()
    page.set_in_place(True)
    assert page._run_btn.isEnabled()

    page.mark_in_place_done()

    assert not page._run_btn.isEnabled()
    assert not page._queue_btn.isEnabled()
    text = page._readout.text().lower()
    assert "archive" in text
    assert "no " not in text.split("archive")[0], f"still reporting a failure: {text!r}"


def test_picking_a_new_source_clears_the_in_place_verdict(qapp, tmp_path):
    src = tmp_path / "loose"
    _write_loose(src, ["A01"])
    other = tmp_path / "other"
    _write_loose(other, ["B02"])
    page = _page(qapp)
    page._src_field.set_path(str(src))
    page._on_source_changed()
    page.set_in_place(True)
    page.mark_in_place_done()
    assert not page._run_btn.isEnabled()

    page._src_field.set_path(str(other))
    page._on_source_changed()
    assert page._run_btn.isEnabled()


def test_the_readout_names_files_that_did_not_match(qapp, tmp_path):
    """The count alone leaves the user guessing which files; the names are the
    fastest way to spot a wrong separator."""
    src = tmp_path / "loose"
    _write_loose(src, ["A01"])
    (src / "IMG_thumbnail.tif").write_bytes(b"img")
    page = _page(qapp)
    page._src_field.set_path(str(src))
    page._on_source_changed()
    assert "IMG_thumbnail.tif" in page._readout.text()


def test_a_total_mismatch_is_not_also_called_reassuring(qapp, tmp_path):
    """"would be left alone" reads as fine when nothing matched at all, and the
    summary has already said so."""
    src = tmp_path / "loose"
    _write_loose(src, ["A01"])
    page = _page(qapp)
    page._src_field.set_path(str(src))
    page._on_source_changed()
    page.set_filename_sep("-")
    assert "left alone" not in page._readout.text().lower()


def test_the_confirmation_states_what_it_will_touch(qapp, tmp_path):
    """A dialog for an irreversible delete, built from a scan taken seconds
    earlier. Not driven through QMessageBox — a modal blocks forever offscreen
    (tests/conftest.py::_no_modal_dialogs); this checks the text the controller
    composes it from."""
    src = tmp_path / "loose"
    _write_loose(src, ["A01", "B02"])
    (src / "IMG_thumbnail.tif").write_bytes(b"img")
    page = _page(qapp)
    page._src_field.set_path(str(src))
    page._on_source_changed()

    counts = page.confirm_counts()
    assert "2" in counts, f"no well count in {counts!r}"
    assert "don't match" in page.unmatched_note()


def test_the_confirmation_states_no_counts_it_has_not_measured(qapp, tmp_path):
    page = _page(qapp)
    assert page.confirm_counts() == "This will group the loose TIFs "
    assert page.unmatched_note() == ""
