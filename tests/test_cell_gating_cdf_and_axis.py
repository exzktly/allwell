"""Cell-gating CDFs respect the crop region + QC debris gate (not the fluor
gate, which they're used to set), and the Export-Style panel targets the active
metric's axis by default.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd


def test_cell_gating_valid_rows_filters_crop_and_qc_not_fluor_gate():
    from well_viewer.features.home.cell_gating.gating_compute import _cell_gating_valid_rows
    df = pd.DataFrame({
        "area_px": [10, 20, 30, 40],
        "Included": [1, 1, 0, 0],   # fluor gate / manual — MUST be ignored
        "QC_Pass": [1, 1, 1, 0],
        "Crop_Pass": [1, 0, 1, 1],
    })
    out = _cell_gating_valid_rows(df)
    # Keep rows passing QC AND Crop (10, 30); the Included column is not applied
    # (row 30 has Included == 0 but still shows, row 20 is dropped by Crop).
    assert sorted(out["area_px"].tolist()) == [10, 30]


def test_cell_gating_valid_rows_absent_columns_pass_through():
    from well_viewer.features.home.cell_gating.gating_compute import _cell_gating_valid_rows
    df = pd.DataFrame({"area_px": [1, 2, 3]})   # no QC_Pass / Crop_Pass
    assert len(_cell_gating_valid_rows(df)) == 3


def test_export_axis_default_targets_first_subplot():
    """The active metric drives the first subplot, so the Export-Style 'Axis #'
    defaults to it (not 'All'); a saved explicit choice is respected."""
    from types import SimpleNamespace
    from well_viewer.features.export.sidebar_view import ExportStyleSidebar
    fn = ExportStyleSidebar._default_axis_target

    default_self = SimpleNamespace(_prefs={"axis_target": "All"})
    assert fn(default_self) == "1"
    explicit_self = SimpleNamespace(_prefs={"axis_target": "3"})
    assert fn(explicit_self) == "3"


def test_export_click_selects_axis():
    """Clicking a subplot syncs the Export-Style 'Axis #' to that axis; a click
    that misses every axis, or one made while the sidebar is hidden, no-ops so
    normal canvas interaction is untouched."""
    from types import SimpleNamespace
    from well_viewer.features.export.sidebar_view import ExportStyleSidebar
    fn = ExportStyleSidebar._on_canvas_axis_pick

    class FakeCombo:
        def __init__(self):
            self._t = "1"
        def currentText(self):
            return self._t
        def setCurrentText(self, t):
            self._t = str(t)

    ax0, ax1, ax2 = object(), object(), object()
    flashed = []
    s = SimpleNamespace(
        _fig=SimpleNamespace(axes=[ax0, ax1, ax2]),
        _axis_cb=FakeCombo(),
        isVisible=lambda: True,
        _flash_axis=lambda ax: flashed.append(ax),
    )
    fn(s, SimpleNamespace(inaxes=ax1))
    assert s._axis_cb.currentText() == "2"
    assert flashed == [ax1]
    fn(s, SimpleNamespace(inaxes=None))          # miss → no change
    assert s._axis_cb.currentText() == "2"
    s.isVisible = lambda: False                   # hidden → no change
    fn(s, SimpleNamespace(inaxes=ax0))
    assert s._axis_cb.currentText() == "2"
