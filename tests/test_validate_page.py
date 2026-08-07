"""Offscreen controller/page tests for the Validate & Repair Dataset feature.

Same pattern as ``tests/test_ome_import_page.py``: build the controller,
drive the page's private widgets directly, assert on the run gate and the
findings rendered. Real filesystem datasets via
``tests.fixtures.dataset_builder``, no mocking of the compute layer.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.fixtures.dataset_builder import build_dataset  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _run_worker_to_completion(ctrl) -> None:
    """Block until the controller's just-started QThread worker finishes,
    then pump the event loop so its queued cross-thread signals (logLine,
    succeeded/failed, finished) actually reach the connected slots.

    Polling ``isRunning()`` is not safe here: a scan over a tiny tmp_path
    dataset can finish in well under a millisecond, so ``isRunning()`` can
    read ``False`` because the thread hasn't started yet OR because it has
    already finished — the two are indistinguishable from that alone.
    """
    worker = ctrl._worker
    assert worker is not None
    worker.wait(5000)
    for _ in range(10):
        QApplication.processEvents()


def test_apply_repairs_tooltip_explains_why_when_findings_cant_be_repaired(qapp, tmp_path):
    """Regression guard (UX audit P2-7): a greyed "Apply repairs" button
    sitting under a findings list that isn't empty reads as broken -- "it
    found problems, why won't it fix them?" -- with nothing on the page
    saying this dataset's findings just aren't the metadata-only kind
    apply.py knows how to repair (report.repairable is False even though
    findings is non-empty, e.g. a genuine missing-FOV finding with correct
    availability lists already)."""
    from datasets.validate.controller import ValidateController
    from datasets.validate.validate_compute import DatasetReport, Finding

    ctrl = ValidateController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page

    unrepairable = DatasetReport(
        root=tmp_path,
        findings=(Finding(severity="error", code="missing_fov", message="A01 missing 2 FOVs"),),
    )
    assert not unrepairable.repairable
    page.show_report(unrepairable)
    assert not page._repair_btn.isEnabled()
    assert page._repair_btn.toolTip() == "These findings can't be repaired automatically."

    clean = DatasetReport(root=tmp_path)
    page.show_report(clean)
    assert page._repair_btn.toolTip() == ""
    ctrl.shutdown()


def test_findings_list_is_absent_until_a_check_produces_something(qapp, tmp_path):
    """The findings list carries a layout stretch and a panel border, so an
    empty one is not a small widget — it drew a page-height bordered box
    holding a single line of placeholder text. That line told the user to pick
    a dataset folder and stayed exactly as it was after they picked one: the
    largest thing on the page, describing a step already completed. It is now
    hidden until there is a real finding, failure, or note to put in it."""
    from datasets.validate.controller import ValidateController

    ctrl = ValidateController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    assert page._findings_list.count() == 0
    assert page._findings_list.isHidden()

    # Still absent once a valid folder is picked — that was the inaccurate
    # part: the box went on asking for the folder it had already been given.
    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    page.set_dataset_path(str(tmp_path))
    assert page._findings_list.count() == 0
    assert page._findings_list.isHidden()
    ctrl.shutdown()


def test_a_report_reveals_the_findings_list(qapp, tmp_path):
    """The other half of hiding it: a check with something to say must bring
    the list back, including the clean-dataset "No issues found." row."""
    from datasets.validate.controller import ValidateController

    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    ctrl = ValidateController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    page.set_dataset_path(str(tmp_path))
    page._run_btn.click()
    _run_worker_to_completion(ctrl)

    assert not page._findings_list.isHidden()
    assert page._findings_list.count() >= 1
    ctrl.shutdown()


def test_queue_button_is_second_in_the_run_row(qapp):
    """Regression guard: +Queue used to sit third, after Apply repairs, the
    only Datasets tool whose secondary action came before the queue button —
    every other tool's run row is Run, then +Queue, then anything tool-
    specific. Buried third, +Queue also used to fall past the point where
    Apply repairs' 240px-wide ToolTip made a narrow window need a horizontal
    scroll to reach it."""
    from datasets.validate.controller import ValidateController

    ctrl = ValidateController()
    page = ctrl.page
    btn_row = page._run_btn.parentWidget()
    widgets = [btn_row.layout().itemAt(i).widget() for i in range(btn_row.layout().count())]
    widgets = [w for w in widgets if w is not None]
    assert widgets.index(page._queue_btn) == 1
    assert widgets.index(page._run_btn) == 0
    assert widgets.index(page._repair_btn) > widgets.index(page._queue_btn)


def test_dataset_field_flags_a_folder_missing_in_and_out(qapp, tmp_path):
    """Regression guard (UX audit P1-10): Validate's dataset-folder PathField
    was the one dataset picker in the whole Datasets pane with no validator
    at all -- pointing it at a folder with no in/out just left the Check
    button silently disabled, with none of the inline "Needs an 'in' and an
    'out' folder" hint every other dataset-folder field gives."""
    from datasets.validate.controller import ValidateController

    not_a_dataset = tmp_path / "plain_folder"
    not_a_dataset.mkdir()
    ctrl = ValidateController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page

    page._dataset_field.set_path(str(not_a_dataset))
    page._on_dataset_changed()
    assert not page._run_btn.isEnabled()
    assert page._dataset_field.message() == "Needs an 'in' and an 'out' folder."
    ctrl.shutdown()


def test_check_gate_requires_a_path(qapp, tmp_path):
    from datasets.validate.controller import ValidateController

    build_dataset(tmp_path, wells=["A01"], timepoints=["0h", "24h"])
    ctrl = ValidateController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    assert not page._run_btn.isEnabled()

    page._dataset_field.set_path(str(tmp_path))
    page._on_dataset_changed()
    assert page._run_btn.isEnabled()
    ctrl.shutdown()


def test_dashboard_reveals_on_a_valid_dataset_and_hides_without_one(qapp, tmp_path):
    """Regression guard: the status strip below the button row used to
    either stay hidden until Check was pressed (a real dataset already
    picked, and the page had every fact needed to answer "what would this
    check?" — the picked dataset's own wells — yet stayed hidden and showed
    nothing), or, before that, be visible from page load with an idle
    headline and an empty plate map (nothing to show at all yet). Neither is
    right: it must hide with no valid dataset picked, and reveal seeded with
    the dataset's real wells (a "Ready" preview, not a run) the moment one
    is. Uses isHidden() (a local, explicit hide/show flag) rather than
    isVisible(), since the page is never actually shown in this test —
    isVisible() would report False for the whole tree regardless of these
    setVisible() calls."""
    from datasets.validate.controller import ValidateController

    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    not_a_dataset = tmp_path.parent / "not_a_dataset"
    not_a_dataset.mkdir()
    ctrl = ValidateController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    assert page.dashboard.isHidden() is True

    page._dataset_field.set_path(str(tmp_path))
    page._on_dataset_changed()
    assert page.dashboard.isHidden() is False  # a real dataset -> a real preview
    assert page.dashboard._wells == ["A01"]
    assert "Ready" in page.dashboard._headline.text()

    page.checkRequested.emit()
    assert page.dashboard.isHidden() is False  # stays up while checking
    _run_worker_to_completion(ctrl)
    assert page.dashboard.isHidden() is False  # stays up to show the result

    page._dataset_field.set_path(str(not_a_dataset))
    page._on_dataset_changed()
    assert page.dashboard.isHidden() is True  # not a dataset -> nothing to preview
    ctrl.shutdown()


def test_check_reports_clean_dataset(qapp, tmp_path):
    from datasets.validate.controller import ValidateController

    build_dataset(tmp_path, wells=["A01", "B02"], timepoints=["0h", "24h"])
    ctrl = ValidateController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    page._dataset_field.set_path(str(tmp_path))
    page._on_dataset_changed()

    page.checkRequested.emit()
    _run_worker_to_completion(ctrl)

    assert page._findings_list.count() == 1
    assert "No issues" in page._findings_list.item(0).text()
    assert not page._repair_btn.isEnabled()
    ctrl.shutdown()


def test_headline_does_not_say_clean_when_a_warning_finding_exists(qapp, tmp_path):
    """Regression guard: the dashboard headline used to be built from
    report.is_clean, which ignores warning-severity findings -- a well with
    no measurements CSV at all but full image evidence for every intended
    (fov, timepoint) (a legitimate "segmented zero cells" well) has no
    missing pairs, so is_clean read True even though a "no_csv" warning
    finding existed and the plate painted that exact well red with a "!"
    marker. The headline must agree with what the findings list and the
    plate actually show: "Clean" only when there are zero findings."""
    import zipfile

    from datasets.validate.controller import ValidateController

    build_dataset(tmp_path, wells=["A01", "B02"], timepoints=["0h"])
    # B02: delete its CSV entirely but keep its out-zip images intact, so
    # csv_state == "missing" while (intent - done) stays empty via the
    # out-zip's own evidence -- exactly the "zero cells segmented" case.
    (tmp_path / "out" / "measurements_B02.csv").unlink()
    with zipfile.ZipFile(tmp_path / "out" / "B02_out.zip") as zf:
        assert zf.namelist()  # sanity: image evidence still there

    ctrl = ValidateController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    page._dataset_field.set_path(str(tmp_path))
    page._on_dataset_changed()

    page.checkRequested.emit()
    _run_worker_to_completion(ctrl)

    assert page._findings_list.count() >= 1
    assert "Clean" not in page.dashboard._headline.text()
    assert "1 finding" in page.dashboard._headline.text()
    ctrl.shutdown()


def test_check_reports_missing_fov_and_offers_completion(qapp, tmp_path):
    import csv
    import zipfile

    from datasets.validate.controller import ValidateController

    build_dataset(tmp_path, wells=["A01"], timepoints=["0h", "24h", "48h"])
    out_zip = tmp_path / "out" / "A01_out.zip"
    with zipfile.ZipFile(out_zip) as zf:
        members = zf.namelist()
    keep = [m for m in members if "_0h_" in m]
    with zipfile.ZipFile(out_zip, "w") as zf:
        for m in keep:
            zf.writestr(m, b"x")
    csv_path = tmp_path / "out" / "measurements_A01.csv"
    with csv_path.open() as f:
        rows = list(csv.DictReader(f))
    fieldnames = rows[0].keys()
    rows = [r for r in rows if r["timepoint"] == "0h"]
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    seen = []

    def _open(path, pipeline_info=None, *, incremental=False, force_wells=None):
        seen.append(path)

    ctrl = ValidateController(on_open_in_analyze=_open)
    page = ctrl.page
    page._dataset_field.set_path(str(tmp_path))
    page._on_dataset_changed()
    page.checkRequested.emit()
    _run_worker_to_completion(ctrl)

    assert page._findings_list.count() >= 1
    assert not page._complete_btn.isHidden()

    page.completeRunRequested.emit(str(tmp_path))
    assert seen == [str(tmp_path)]
    ctrl.shutdown()


def test_apply_repairs_rebuilds_availability(qapp, tmp_path):
    import json

    from datasets.validate.controller import ValidateController

    build_dataset(tmp_path, wells=["A01"], timepoints=["0h", "24h"])
    info_path = tmp_path / "out" / "pipeline_info.json"
    info = json.loads(info_path.read_text())
    info["available_timepoints"] = ["0h", "24h", "999h"]
    info_path.write_text(json.dumps(info))

    ctrl = ValidateController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    page._dataset_field.set_path(str(tmp_path))
    page._on_dataset_changed()
    page.checkRequested.emit()
    _run_worker_to_completion(ctrl)
    assert page._repair_btn.isEnabled()

    page.applyRepairsRequested.emit()
    _run_worker_to_completion(ctrl)

    new_info = json.loads(info_path.read_text())
    assert new_info["available_timepoints"] == ["0h", "24h"]
    ctrl.shutdown()


def test_dataset_field_locked_during_a_run(qapp, tmp_path):
    """Regression guard: the dataset PathField stayed enabled for the whole
    run, unlike Subset's source/destination fields -- a user could pick a
    different dataset while a scan was still in flight."""
    from datasets.validate.controller import ValidateController

    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    ctrl = ValidateController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    page._dataset_field.set_path(str(tmp_path))
    page._on_dataset_changed()

    page.checkRequested.emit()
    # Locked means the Browse button, not the whole field (UX audit P2-4) —
    # the path text is already read-only and stays at full contrast so a
    # user watching a long scan can still read what it's running against.
    assert page._dataset_field.isEnabled() is True
    assert page._dataset_field._browse_btn.isEnabled() is False
    _run_worker_to_completion(ctrl)
    assert page._dataset_field._browse_btn.isEnabled() is True
    ctrl.shutdown()


def test_last_result_reports_the_scanned_root_not_a_since_changed_field(qapp, tmp_path):
    """Regression guard: ValidateController._on_succeeded used to read
    self.page.dataset_path() (the live PathField) instead of the finished
    report's own root -- if the field changed between the scan starting and
    its succeeded signal being handled, jobFinished's dataset_root (what the
    job queue chains a successor job on) would name a folder the just-
    finished scan never actually ran against."""
    from datasets.validate.controller import ValidateController
    from datasets.validate.validate_compute import DatasetReport

    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    build_dataset(root_a, wells=["A01"], timepoints=["0h"])
    build_dataset(root_b, wells=["B02"], timepoints=["0h"])

    ctrl = ValidateController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    page._dataset_field.set_path(str(root_a))
    page._on_dataset_changed()

    # The field changes after the scan of root_a is under way but before its
    # succeeded signal is handled.
    page._dataset_field.set_path(str(root_b))
    ctrl._on_succeeded(DatasetReport(root=root_a), None)

    assert ctrl._last_result[2] == str(root_a)
    ctrl.shutdown()


def test_failed_check_clears_the_previous_runs_findings(qapp, tmp_path):
    """Regression guard: _on_failed used to leave the previous successful
    check's findings rendered under the new red "Failed" headline --
    _on_dataset_changed (fired by editing the field interactively) already
    cleared them, but set_dataset_path() (what QueueBridge.show_job uses to
    repopulate the page) never runs it, so a failed re-check reachable via
    the job queue kept showing stale findings from an unrelated dataset."""
    from datasets.validate.controller import ValidateController

    build_dataset(tmp_path, wells=["A01"], timepoints=["0h", "24h"])
    ctrl = ValidateController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    page._dataset_field.set_path(str(tmp_path))
    page._on_dataset_changed()
    page.checkRequested.emit()
    _run_worker_to_completion(ctrl)
    assert page._findings_list.count() >= 1

    # Repopulate via set_dataset_path (the show_job path), pointed at a
    # non-dataset folder, then run a check that fails.
    bogus = tmp_path / "not_a_dataset"
    bogus.mkdir()
    page.set_dataset_path(str(bogus))
    page.checkRequested.emit()
    _run_worker_to_completion(ctrl)

    assert "Failed" in page.dashboard._headline.text()
    # The stale findings from the unrelated dataset are gone, which is what
    # this test guards -- and what replaces them is the reason the check
    # failed. It used to be an empty list, which reads as "nothing happened"
    # directly under a red Failed headline; see
    # test_failed_check_shows_why_and_withdraws_the_completion_offer.
    assert page._findings_list.count() == 1
    assert "Check failed" in page._findings_list.item(0).text()
    ctrl.shutdown()


def test_start_from_queue_with_empty_spec_emits_job_finished_instead_of_stalling(qapp):
    """Regression guard: _run's "no dataset path" early return used to bail
    out before a worker was ever created and before jobFinished was ever
    emitted -- a queued job with an empty/corrupted "dataset" spec value
    (e.g. a hand-edited persisted queue file) would stall QueueBridge's
    _current forever, since nothing would ever signal the job as done."""
    from datasets.validate.controller import ValidateController

    ctrl = ValidateController(on_open_in_analyze=lambda _p: None)
    seen = []
    ctrl.jobFinished.connect(lambda ok, msg, path: seen.append((ok, msg, path)))
    ctrl.start_from_queue({})
    assert seen and seen[0][0] is False
    ctrl.shutdown()


def test_clear_log_button_clears_the_log_not_the_findings(qapp, tmp_path):
    """Regression guard: the "Clear" button used to sit in the "Findings"
    header while actually clearing the raw log underneath it -- pressing it
    while looking at a findings list did nothing visible whenever the log
    panel (hidden by default) wasn't shown, reading as broken. It's now
    labelled "Clear log" and lives under the log's own "Raw log" header, and
    it must still only ever touch the log, never the findings list."""
    from datasets.validate.controller import ValidateController

    root = tmp_path / "ds"
    build_dataset(root, wells=["A01"], timepoints=["0h"])
    ctrl = ValidateController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    page._dataset_field.set_path(str(root))
    page._on_dataset_changed()
    page._raw_log_cb.setChecked(True)

    page.checkRequested.emit()
    _run_worker_to_completion(ctrl)

    assert page._status.toPlainText() != ""
    findings_count_before = page._findings_list.count()
    assert findings_count_before > 0

    page._clear_log_btn.click()

    assert page._status.toPlainText() == ""
    assert page._findings_list.count() == findings_count_before
    ctrl.shutdown()


def test_findings_and_log_panels_have_their_own_distinct_headers(qapp):
    """The findings panel and the raw-log panel must each say what they are
    -- previously only "Findings" was labelled at all, and the raw log
    (revealed by its own checkbox) had no header of its own, just an hrule,
    which is what made the mislabelled "Clear" button plausible in the
    first place."""
    from datasets.validate.controller import ValidateController

    ctrl = ValidateController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    assert page._log_hdr.isHidden() is True  # not shown until requested
    page._raw_log_cb.setChecked(True)
    assert page._log_hdr.isHidden() is False
    from PySide6.QtWidgets import QLabel
    log_header_text = [c.text() for c in page._log_hdr.findChildren(QLabel)]
    assert any("Raw log" in t for t in log_header_text)
    ctrl.shutdown()


def test_show_job_populates_the_page_instead_of_leaving_it_inert(qapp, tmp_path):
    """Regression guard: `QueueBridge.show_job` is the only caller of
    `set_dataset_path`, and `PathField.set_path` deliberately doesn't emit
    `changed` (only an interactive Browse pick does). Selecting a queued
    Validate job therefore filled in a perfectly valid dataset folder and left
    the page inert -- "▶ Check" stayed disabled, so pressing it did nothing at
    all, above a plate map that was never painted."""
    from datasets.validate.controller import ValidateController

    build_dataset(tmp_path, wells=["A01", "B02"], timepoints=["0h"])
    ctrl = ValidateController()
    page = ctrl.page

    ctrl.show_job({"dataset": str(tmp_path)})

    assert page._run_btn.isEnabled()
    assert page._queue_btn.isEnabled()
    # isVisibleTo, not isVisible: the page itself is never shown in an
    # offscreen test, which would make every child read as hidden.
    assert page.dashboard.isVisibleTo(page)
    assert page.dashboard._wells == ["A01", "B02"]
    assert "2 well(s)" in page.dashboard._headline.text()


def test_plate_map_stays_painted_while_the_scan_runs(qapp, tmp_path):
    """Regression guard: `_run` reset the dashboard, which clears the plate's
    enabled wells, and nothing repainted it until the whole scan landed. On a
    real plate that is the entire duration of the run -- every well's input
    archive opened, every measurements CSV read -- so pressing Check blanked
    the very plate map the ready-state preview had just painted and left it
    empty with no updates of any kind."""
    from datasets.validate.controller import ValidateController

    build_dataset(tmp_path, wells=["A01", "B02", "C03"], timepoints=["0h"])
    ctrl = ValidateController()
    page = ctrl.page
    page.set_dataset_path(str(tmp_path))

    # _run seeds the plate synchronously, before the worker thread is started,
    # so this reads the state the user sees for the scan's whole duration --
    # no event pumping, and therefore nothing the worker has done yet.
    ctrl._run(apply_repair=False)
    assert page.dashboard._wells == ["A01", "B02", "C03"]
    assert page.dashboard._headline.text() == "Scanning…"

    _run_worker_to_completion(ctrl)
    assert page.dashboard._wells == ["A01", "B02", "C03"]
    assert set(page.dashboard._well_state.values()) == {"complete"}


def test_check_streams_per_well_progress_and_logs_its_result(qapp, tmp_path):
    """A plain Check used to write exactly one log line ("Scanning dataset…")
    and never record that the scan had finished, let alone what it found --
    the completion line was gated on a repair result, which a Check never has.
    Per-well lines land alongside it as each well is scanned."""
    from datasets.validate.controller import ValidateController

    build_dataset(tmp_path, wells=["A01", "B02"], timepoints=["0h"])
    ctrl = ValidateController()
    page = ctrl.page
    page.set_dataset_path(str(tmp_path))

    page._run_btn.click()
    _run_worker_to_completion(ctrl)

    log = page._status.toPlainText()
    assert "Scanning dataset…" in log
    assert "A01: complete" in log
    assert "B02: complete" in log
    assert "✓ Clean — no issues found." in log


def test_live_well_paint_matches_the_finished_report(qapp, tmp_path):
    """The live per-well callback and the final report render through the same
    `well_health` classifier, so a partially-complete well is painted the same
    colour whichever of the two got there first."""
    from datasets.validate.controller import ValidateController
    from datasets.validate.validate_compute import WellReport

    ctrl = ValidateController()
    page = ctrl.page
    page.dashboard.set_wells(["A01"])

    ctrl._on_well_scanned(WellReport(
        well="A01", missing=(("F001", "24h"),), csv_state="ok",
        has_in_container=True, has_out_zip=True))

    assert page.dashboard._well_state["A01"] == "partial"
    assert page.dashboard._well_missing_count["A01"] == 1


def test_failed_check_shows_why_and_withdraws_the_completion_offer(qapp, tmp_path):
    """Regression guard for the pair of symptoms a failed re-check produced.

    The findings list reset to an empty state -- which says nothing
    happened -- while the "Complete this run
    in Analyze" button left over from the earlier successful check stayed
    visible and live. Clicking it then handed Analyze a dataset root whose
    sidecar had just proved unreadable, so no settings could travel with it and
    the user landed on an Analyze tab that looked untouched. The failure now
    replaces the findings, and the offer goes with the report it belonged to."""
    import csv
    import zipfile

    from datasets.validate.controller import ValidateController

    build_dataset(tmp_path, wells=["A01"], timepoints=["0h", "24h"])
    # Crash-truncate A01 so the first check offers completion.
    with zipfile.ZipFile(tmp_path / "out" / "A01_out.zip") as zf:
        keep = [m for m in zf.namelist() if "_0h" in m]
    with zipfile.ZipFile(tmp_path / "out" / "A01_out.zip", "w") as zf:
        for m in keep:
            zf.writestr(m, b"x")
    csv_path = tmp_path / "out" / "measurements_A01.csv"
    rows = [r for r in csv.DictReader(csv_path.open()) if r["timepoint"] == "0h"]
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    ctrl = ValidateController(on_open_in_analyze=lambda *a, **k: None)
    page = ctrl.page
    page.set_dataset_path(str(tmp_path))
    page._run_btn.click()
    _run_worker_to_completion(ctrl)
    assert page._complete_btn.isVisibleTo(page)
    assert page._findings_list.count() >= 1

    # Now make the sidecar unreadable and re-check on the same page.
    (tmp_path / "out" / "pipeline_info.json").write_text("{ not json")
    page._run_btn.click()
    _run_worker_to_completion(ctrl)

    shown = [page._findings_list.item(i).text()
             for i in range(page._findings_list.count())]
    assert any("Check failed" in text for text in shown), shown
    assert not page._findings_list.isHidden()
    assert not page._complete_btn.isVisibleTo(page)
    assert not page._repair_btn.isEnabled()
    ctrl.shutdown()
