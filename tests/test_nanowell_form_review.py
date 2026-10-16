"""Behaviour of the Nanowells section that a reviewer found missing, each driven through the real controls (tracker A5).

Shares the fixtures of ``test_nanowell_form``.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

import analyze.nanowell_preview as anp  # noqa: E402
from pipeline.nanowell_preview import PreviewResult  # noqa: E402
from tests.test_nanowell_form import (  # noqa: E402,F401
    _plate, _use_nanowell_schema, refused, tab,
)


def _open(tab, root):
    _use_nanowell_schema(tab)
    tab._input_edit.setText(str(root))
    tab._nanowell_cb.setChecked(True)
    tab._nanowell_preview_btn.click()
    return tab._nanowell_preview_window


# ── segmentation is pinned while the mode is on ──────────────────────────────
def test_the_mode_sets_the_selector_to_label_free_locks_it_and_puts_the_choice_back(tab):
    stardist = tab._seg_method_cb.findData("stardist_seeded_watershed_cell")
    tab._seg_method_cb.setCurrentIndex(stardist)
    tab._nanowell_cb.setChecked(True)
    assert tab._seg_method_cb.currentData() == "cellpose_labelfree" and not tab._seg_method_cb.isEnabled()
    tab._nanowell_cb.setChecked(False)
    assert tab._seg_method_cb.currentIndex() == stardist and tab._seg_method_cb.isEnabled()


def test_a_build_with_no_label_free_method_cannot_switch_the_mode_on(qtbot, monkeypatch):
    import analyze.form
    from analyze.tab import AnalyzeTab

    monkeypatch.setattr(analyze.form, "load_remotes", lambda: {})
    monkeypatch.setattr(AnalyzeTab, "_nanowell_label_free_index", lambda self: -1)
    t = AnalyzeTab(parent=None)
    qtbot.addWidget(t)
    assert not t._nanowell_cb.isEnabled() and "label-free" in t._nanowell_status.text()


# ── restoring a dataset's settings ───────────────────────────────────────────
def test_restoring_a_dataset_while_the_mode_is_on_keeps_the_method_the_dataset_states(tab):
    """Roll-up review X7: the sidecar's method is restored before the nanowell section, whose switch-off put back
    the method from before the mode was turned on."""
    stardist = tab._seg_method_cb.findData("stardist_seeded_watershed_cell")
    tab._seg_method_cb.setCurrentIndex(stardist)
    tab._nanowell_cb.setChecked(True)
    tab._apply_pipeline_info({"execution_options": {"nanowell_format": None, "segmentation_method": "cellpose"}})
    assert (tab._seg_method_cb.currentData(), tab._nanowell_cb.isChecked()) == ("cellpose", False)
    tab._nanowell_cb.setChecked(True)
    tab._apply_pipeline_info({"execution_options": {"nanowell_format": "100um", "segmentation_method": "cellpose"}})
    assert (tab._seg_method_cb.currentData(), tab._seg_method_cb.isEnabled()) == ("cellpose_labelfree", False)
    tab._nanowell_cb.setChecked(False)
    assert tab._seg_method_cb.currentData() == "cellpose"


def test_a_dataset_that_states_a_format_replaces_the_previous_datasets_numbers(tab):
    tab._apply_pipeline_info({"execution_options": {"nanowell_format": "100um", "nanowell_margin_um": 7.5,
                                                    "nanowell_cell_diameter_um": 20}})
    tab._apply_pipeline_info({"execution_options": {"nanowell_format": "50um"}})
    opts = tab._collect_run_spec().opts
    assert opts["nanowell_format"] == "50um" and opts["nanowell_margin_um"] == "" == opts["nanowell_cell_diameter_um"]


def test_a_format_this_version_does_not_know_switches_the_mode_off_and_says_so(tab):
    tab._nanowell_cb.setChecked(True)
    tab._apply_pipeline_info({"execution_options": {"nanowell_format": "75um"}})
    assert not tab._nanowell_cb.isChecked() and "'75um'" in tab._nanowell_status.text()
    assert tab._seg_method_cb.isEnabled()


# ── one warning, once ────────────────────────────────────────────────────────
def test_the_same_overlap_warning_is_logged_once_however_often_the_run_is_validated(tab, refused, tmp_path, monkeypatch):
    root, _ = _plate(tmp_path, overlap=0.03)
    logged: list[str] = []
    monkeypatch.setattr(tab, "_log_line", lambda text, tag="INFO": logged.append(text))
    _use_nanowell_schema(tab)
    tab._input_edit.setText(str(root))
    tab._nanowell_cb.setChecked(True)
    assert tab._validate_run_request() is not None and tab._validate_run_request() is not None
    assert len(logged) == 1 and "on a seam" in logged[0]


# ── the Test detection window ────────────────────────────────────────────────
def test_the_window_follows_the_input_folder_when_it_is_opened_again(tab, tmp_path):
    root, _ = _plate(tmp_path / "a")
    window = _open(tab, root)
    assert [window._well_cb.itemText(i) for i in range(window._well_cb.count())] == ["A01"]
    window._well_cb.clear()                                     # as if it showed a folder that has since changed
    window._well_cb.addItem("Z99")
    tab._nanowell_preview_btn.click()
    assert tab._nanowell_preview_window is window
    assert [window._well_cb.itemText(i) for i in range(window._well_cb.count())] == ["A01"]
    assert window._tp_cb.currentText() == "0h"
    window.close()


def test_a_bad_field_after_the_window_opened_is_said_in_it_and_run_stays_usable(tab, tmp_path, monkeypatch):
    root, _ = _plate(tmp_path)
    window = _open(tab, root)
    tab._nanowell_margin_edit.setText("-3")
    started = []
    monkeypatch.setattr(anp._PreviewWorker, "start", lambda self: started.append(self))
    window.run()
    assert not started and "Margin must be 0 or more" in window._summary.text() and window._run_btn.isEnabled()
    window.close()


def test_a_montage_is_never_drawn_larger_than_the_pixmap_cap_and_fit_follows_the_window(tab, tmp_path, monkeypatch):
    import numpy as np

    root, _ = _plate(tmp_path)
    window = _open(tab, root)
    monkeypatch.setattr(anp, "MAX_PIXMAP_SIDE", 300)
    window._result = PreviewResult("ok", montage=np.zeros((400, 800), np.uint8), um_per_px=4.0, origin_um=(0.0, 0.0))
    window._zoom_cb.setCurrentIndex(window._zoom_cb.findText("400 %"))
    pixmap = window._montage_label.pixmap()
    assert pixmap.width() == 300 and pixmap.height() == 150       # 3200 px asked for, capped
    monkeypatch.setattr(anp, "MAX_PIXMAP_SIDE", 12000)
    window._zoom_cb.setCurrentIndex(0)                                                  # Fit
    window.resize(420, 500)
    QApplication.processEvents()
    before = window._montage_label.pixmap().width()
    window.resize(900, 700)
    QApplication.processEvents()
    assert window._montage_label.pixmap().width() != before
    window.close()


def test_a_running_preview_outlives_its_window(tab, qtbot, tmp_path, monkeypatch):
    """A QThread destroyed while running aborts the process; the worker is owned by the module until it finishes."""
    import pipeline.nanowell_preview as npv

    root, _ = _plate(tmp_path)
    window = _open(tab, root)
    monkeypatch.setattr(npv, "preview_timepoint", lambda *a, **k: (time.sleep(1.0), PreviewResult("no_lattice", "x"))[1])
    window.run()
    assert len(anp._RUNNING) == 1
    window.close()
    window.setParent(None)
    window.deleteLater()
    QApplication.sendPostedEvents(None, 0)
    qtbot.waitUntil(lambda: not anp._RUNNING, timeout=20000)


def test_a_site_list_without_addresses_is_summarised_as_a_reason_then_a_sentence():
    import numpy as np

    result = PreviewResult("origin_unresolved", "the fiducial disagrees with the array centre", well="A01", timepoint="0h",
                           montage=np.zeros((9, 9), np.uint8), um_per_px=4.0, pitch_um=172.0, angle_deg=7.0,
                           overlap_um=(200.0, 200.0), overlap_needed_um=120.0, tile_count=4)
    result.sites = [{"address": None, "state": "empty", "fused": False, "n_cells": None, "well_x_um": 1.0,
                     "well_y_um": 1.0, "radius_um": 50.0}]
    assert "Not addressed: the fiducial disagrees with the array centre. The sites found" in anp.summary_text(result)


def test_the_phase_channel_is_demanded_only_when_nothing_will_detect_it(tab):
    tab._nanowell_cb.setChecked(True)
    tab._set_fluor_rows(["GFP"], smfish=[], phase="")
    tab._auto_detect_cb.setChecked(True)
    assert tab._nanowell_validation_error(check_input=False) is None            # detection names the channels at run time
    tab._auto_detect_cb.setChecked(False)
    assert "Phase channel" in tab._nanowell_validation_error(check_input=False)


def test_test_detection_opens_with_a_remote_and_incremental_chosen(tab, tmp_path):
    """Roll-up review X30: Test detection runs on this computer, so the refusal of a remote incremental *run* does
    not stop it. The run itself is still refused (``test_nanowell_form_grid``)."""
    root, _ = _plate(tmp_path)
    tab._remote_cb.addItem("bench-box", userData="bench-box")
    tab._remote_cb.setCurrentIndex(tab._remote_cb.findData("bench-box"))
    tab._incremental_cb.setChecked(True)
    window = _open(tab, root)
    assert window is not None and window.isVisible()
