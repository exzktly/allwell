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

from PySide6.QtWidgets import QApplication, QLabel


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_robust_mesh_param_fields_gated_and_collected(qapp):
    from analyze.tab import AnalyzeTab

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
    from analyze.tab import AnalyzeTab

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

    # Selecting a remote reveals the group. Auto-delete is ON by default, so
    # dry-run — which is gated on it — is available immediately.
    tab._remote_cb.addItem("fakehost", userData="fakehost")
    tab._remote_cb.setCurrentText("fakehost")
    assert not grp.isHidden()
    assert tab._cleanup_remote_cb.isChecked(), "auto-delete defaults to on"
    assert tab._dry_run_cleanup_cb.isEnabled()

    # Unchecking it gates dry-run off again.
    tab._cleanup_remote_cb.setChecked(False)
    assert not tab._dry_run_cleanup_cb.isEnabled()

    # Back to Local: hidden again, and the toggles keep exactly what the user
    # set them to.
    #
    # This used to assert the opposite — that both returned to their defaults —
    # and that reset was the defect (audit U3), not the contract. It ran against
    # a widget hidden at the time and was not undone on the way back, so a user
    # who ticked "Dry-run cleanup" to see what auto-delete would remove, glanced
    # at Local and came back had the tick silently dropped and the next run
    # deleted the remote's per-run output and cached input for real. The mirror
    # case, the one below, re-armed an auto-delete they had turned off. Nothing
    # reads either value on a local run — PipelineRunner passes both only down
    # the remote branch — so there was never anything to protect the next remote
    # run from. tests/test_analyze_cleanup_toggles_are_not_rewritten.py drives
    # the whole round trip.
    tab._remote_cb.setCurrentText("Local")
    assert grp.isHidden()
    assert not tab._cleanup_remote_cb.isChecked(), (
        "an auto-delete the user turned off was re-armed by a look at Local")
    tab._remote_cb.setCurrentText("fakehost")
    assert not tab._cleanup_remote_cb.isChecked()
    assert not tab._dry_run_cleanup_cb.isEnabled()


def test_fluor_radius_info_icon_precedes_disable_checkbox(qapp):
    """The ⓘ documents the radius entry and sits before the Disable checkbox.

    Trailing the checkbox put the marker on the tail of "Disable" under
    QMacStyle's checkbox metrics. Placing it beside the entry it explains keeps
    it clear of the checkbox regardless of platform style. Assert the layout
    order (a number that holds on any style) rather than the rendered pixels.
    """
    from analyze.tab import AnalyzeTab

    tab = AnalyzeTab()
    tab._background_cb.setCurrentText("robust_mesh")  # reveals the radius rows

    row = tab._no_tophat_fluor_cb.parentWidget()
    lay = row.layout()
    order = [lay.itemAt(i).widget() for i in range(lay.count())]
    entry_i = order.index(tab._tophat_radius_fluor_edit)
    disable_i = order.index(tab._no_tophat_fluor_cb)
    info_i = next(i for i, w in enumerate(order)
                  if isinstance(w, QLabel) and w.text() == "ⓘ")

    # entry → ⓘ → Disable: the marker never lands after the checkbox text.
    assert entry_i < info_i < disable_i
