"""Analyze-tab UI grouping regressions (runs headless / offscreen Qt):

* the ``robust_mesh`` tile-ratio / sigma-clip fields are shown only for that
  method and round-trip into the run options;
* every remote-only parameter lives in one group that is shown only when a
  remote is selected, and switching back to Local resets its toggles.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_robust_mesh_param_fields_gated_and_collected(qapp):
    from analyze_tab import AnalyzeTab

    tab = AnalyzeTab()

    # Hidden for the radius-only / fft methods…
    tab._background_cb.setCurrentText("sliding_disk")
    assert all(w.isHidden() for w in tab._mesh_param_rows)
    tab._background_cb.setCurrentText("fft_bandpass")
    assert all(w.isHidden() for w in tab._mesh_param_rows)
    # …shown for robust_mesh, with the fft cutoffs hidden in turn.
    tab._background_cb.setCurrentText("robust_mesh")
    assert not any(w.isHidden() for w in tab._mesh_param_rows)
    assert all(w.isHidden() for w in tab._fft_small_rows)

    # Defaults round-trip into the run options as the documented 3.0 / 3.0.
    opts = tab._collect_run_options()
    assert opts["mesh_ratio"] == "3.0"
    assert opts["mesh_sigma"] == "3.0"
    # A changed value flows through unchanged.
    tab._mesh_ratio_edit.setText("5")
    tab._mesh_sigma_edit.setText("2")
    opts = tab._collect_run_options()
    assert opts["mesh_ratio"] == "5"
    assert opts["mesh_sigma"] == "2"


def test_remote_params_grouped_and_gated_on_selection(qapp):
    from analyze_tab import AnalyzeTab

    tab = AnalyzeTab()

    # The batch-size field is now inside the remote group (folded out of
    # Output Options) alongside the cleanup toggles.
    grp = tab._remote_params_widget
    for w in (tab._remote_batch_size_edit, tab._cleanup_remote_cb,
              tab._dry_run_cleanup_cb):
        assert grp.isAncestorOf(w)

    # Local: the whole group is hidden.
    assert tab._remote_cb.currentData() is None
    assert grp.isHidden()

    # Selecting a remote reveals the group; dry-run stays gated on auto-delete.
    tab._remote_cb.addItem("fakehost", userData="fakehost")
    tab._remote_cb.setCurrentText("fakehost")
    assert not grp.isHidden()
    assert not tab._dry_run_cleanup_cb.isEnabled()
    tab._cleanup_remote_cb.setChecked(True)
    assert tab._dry_run_cleanup_cb.isEnabled()

    # Back to Local: hidden again and the toggles reset so a stale value can't
    # be submitted for the next remote run.
    tab._remote_cb.setCurrentText("Local")
    assert grp.isHidden()
    assert not tab._cleanup_remote_cb.isChecked()
    assert not tab._dry_run_cleanup_cb.isChecked()
