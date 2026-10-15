"""The time-gap field is wired to the merge, driven through the real page.

The planner is covered in ``test_concatenate_gap.py``. These tests type into
the field the way a user does and read what ends up in the merged dataset, so
a field that is built, shown, and connected to nothing fails them: asserting
only that the field exists would pass against exactly that.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from tests._datasets_worker import run_worker_to_completion
from tests.fixtures.dataset_builder import build_dataset
from tests.test_concatenate_gap import _record


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _page(tmp_path, tps1, tps2, *, records=False):
    from datasets.concatenate.controller import ConcatenateController

    ds1, ds2, dest = tmp_path / "ds1", tmp_path / "ds2", tmp_path / "dest"
    build_dataset(ds1, wells=["A01"], timepoints=tps1)
    build_dataset(ds2, wells=["A01"], timepoints=tps2)
    if records:
        _record(ds1, {"0h": "2026-03-01T09:00:00+00:00", "24h": "2026-03-02T09:00:00+00:00"})
        _record(ds2, {"0h": "2026-03-03T15:00:00+00:00"})
    ctrl = ConcatenateController(on_open_in_review=lambda _p: None)
    page = ctrl.page
    page._ds1_field.set_path(str(ds1))
    page._ds2_field.set_path(str(ds2))
    page._dest_field.set_path(str(dest))
    for field in (page._ds1_field, page._ds2_field, page._dest_field):
        field.changed.emit()
    return ctrl, page, dest


def _merged_timepoints(dest):
    return json.loads((dest / "out" / "pipeline_info.json").read_text())["available_timepoints"]


def test_a_single_timepoint_dataset_needs_a_typed_gap_and_the_run_uses_it(qapp, tmp_path):
    ctrl, page, dest = _page(tmp_path, ["0h"], ["0h"])
    assert page._gap_edit.text() == ""
    assert page._run_btn.isEnabled() is False            # no interval, no record: required
    assert "enter the time gap" in page._gap_hint.text()

    page._gap_edit.setText("24h")
    assert page._run_btn.isEnabled() is True
    assert page._gap_labels.text().endswith("24h")

    page.runRequested.emit()
    run_worker_to_completion(ctrl)
    assert _merged_timepoints(dest) == ["0h", "24h"]     # not the field being ignored
    ctrl.shutdown()


def test_the_prefilled_interval_is_shown_and_typing_over_it_changes_the_merge(qapp, tmp_path):
    ctrl, page, dest = _page(tmp_path, ["0h", "24h"], ["0h"])
    assert page._gap_edit.text() == "24h"                # dataset 1's own interval
    assert page.gap_text_if_entered() == ""              # untouched → "use the default"
    assert page._gap_labels.text().endswith("48h")

    page._gap_edit.setText("6h")
    assert page.gap_text_if_entered() == "6h"
    assert page._gap_labels.text().endswith("30h")       # 24 + 6, where the default says 48

    page.runRequested.emit()
    run_worker_to_completion(ctrl)
    assert _merged_timepoints(dest) == ["0h", "24h", "30h"]
    ctrl.shutdown()


def test_records_prefill_the_field_and_a_different_entry_warns(qapp, tmp_path):
    ctrl, page, _dest = _page(tmp_path, ["0h", "24h"], ["0h"], records=True)
    assert page._gap_edit.text() == "30h"                # recorded, not the 24h interval
    assert page._gap_hint.objectName() == "Muted"

    page._gap_edit.setText("36h")
    assert page._gap_hint.objectName() == "Warn"
    assert "36h" in page._gap_hint.text() and "30h" in page._gap_hint.text()
    assert page._run_btn.isEnabled() is True             # a warning, not a refusal
    ctrl.shutdown()


@pytest.mark.parametrize("text", ["abc", "-3h", "0"])
def test_an_unreadable_gap_disables_the_run_and_says_why(qapp, tmp_path, text):
    ctrl, page, _dest = _page(tmp_path, ["0h", "24h"], ["0h"])
    page._gap_edit.setText(text)
    assert page._run_btn.isEnabled() is False
    assert page._gap_hint.objectName() == "Error"
    assert page._gap_hint.text()
    ctrl.shutdown()


def test_the_queued_spec_carries_only_a_gap_the_user_entered(qapp, tmp_path):
    ctrl, page, _dest = _page(tmp_path, ["0h", "24h"], ["0h"])
    assert ctrl.collect_spec()["gap"] == ""
    page._gap_edit.setText("6h")
    spec = ctrl.collect_spec()
    assert spec["gap"] == "6h"

    page._gap_edit.setText("")
    ctrl.show_job(spec)
    assert page._gap_edit.text() == "6h"
    assert ctrl.collect_spec()["gap"] == "6h"
    ctrl.shutdown()
