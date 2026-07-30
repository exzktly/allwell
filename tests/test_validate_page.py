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


def test_dashboard_hidden_until_check_starts(qapp, tmp_path):
    """Regression guard: the status strip below the button row used to be
    visible from the moment the page loaded, showing an idle headline and
    an empty plate map before anything had happened — confusing, since it
    reads as an in-between state rather than "nothing has run yet". Uses
    isHidden() (a local, explicit hide/show flag) rather than isVisible(),
    since the page is never actually shown in this test — isVisible() would
    report False for the whole tree regardless of these setVisible() calls."""
    from datasets.validate.controller import ValidateController

    build_dataset(tmp_path, wells=["A01"], timepoints=["0h"])
    ctrl = ValidateController(on_open_in_analyze=lambda _p: None)
    page = ctrl.page
    assert page.dashboard.isHidden() is True

    page._dataset_field.set_path(str(tmp_path))
    page._on_dataset_changed()
    assert page.dashboard.isHidden() is True  # still nothing has run

    page.checkRequested.emit()
    assert page.dashboard.isHidden() is False  # shown the moment Check starts
    _run_worker_to_completion(ctrl)
    assert page.dashboard.isHidden() is False  # stays up to show the result

    page._dataset_field.set_path(str(tmp_path))
    page._on_dataset_changed()
    assert page.dashboard.isHidden() is True  # hidden again for the new selection
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
