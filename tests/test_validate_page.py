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


def test_findings_list_shows_an_empty_state_hint_before_any_check(qapp):
    """Regression guard (UX audit P2-1): the findings list was a large,
    entirely blank box until Check ran — its own empty-container problem,
    the same class of "reads as broken" bug the dashboard hide/reveal fix
    addressed. The hint text lives inside the list itself as a non-
    selectable item, not as a separate caption elsewhere on the page."""
    from datasets.validate.controller import ValidateController

    ctrl = ValidateController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    assert page._findings_list.count() == 1
    item = page._findings_list.item(0)
    assert "Pick a dataset folder and press Check." == item.text()
    from PySide6.QtCore import Qt
    assert not (item.flags() & Qt.ItemIsSelectable)
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
    btn_row = page._check_btn.parentWidget()
    widgets = [btn_row.layout().itemAt(i).widget() for i in range(btn_row.layout().count())]
    widgets = [w for w in widgets if w is not None]
    assert widgets.index(page._queue_btn) == 1
    assert widgets.index(page._check_btn) == 0
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
    assert not page._check_btn.isEnabled()
    assert page._dataset_field.message() == "Needs an 'in' and an 'out' folder."
    ctrl.shutdown()


def test_check_gate_requires_a_path(qapp, tmp_path):
    from datasets.validate.controller import ValidateController

    build_dataset(tmp_path, wells=["A01"], timepoints=["0h", "24h"])
    ctrl = ValidateController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    assert not page._check_btn.isEnabled()

    page._dataset_field.set_path(str(tmp_path))
    page._on_dataset_changed()
    assert page._check_btn.isEnabled()
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
    ctrl = ValidateController(on_open_in_analyze=seen.append)
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
    assert page._dataset_field.isEnabled() is False
    _run_worker_to_completion(ctrl)
    assert page._dataset_field.isEnabled() is True
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
    # clear_findings() shows the empty-state hint inside the list (P1-2),
    # not literally zero items -- the stale findings from the unrelated
    # dataset are still gone, which is what this test actually guards.
    assert page._findings_list.count() == 1
    assert "Pick a dataset folder" in page._findings_list.item(0).text()
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
