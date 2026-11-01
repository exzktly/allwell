"""Cell-gating CDFs respect the crop region + QC debris gate (not the fluor
gate, which they're used to set), and the Export-Style panel targets the active
metric's axis by default.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd


def test_cell_gating_valid_rows_filters_crop_and_qc_not_fluor_gate():
    from review.features.home.cell_gating.gating_compute import _cell_gating_valid_rows
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
    from review.features.home.cell_gating.gating_compute import _cell_gating_valid_rows
    df = pd.DataFrame({"area_px": [1, 2, 3]})   # no QC_Pass / Crop_Pass
    assert len(_cell_gating_valid_rows(df)) == 3


def test_export_axis_opens_on_all():
    """The Properties "Axis #" field opens on "All", so an edit reaches every
    panel until an axis is picked (asked for: "make the axis selection in the
    properties sidebar select All instead of the first plot"). A stored
    single-axis target is not honoured: the panel used to open on axis 1 and
    save that, so a stored "1" says nothing about what anyone chose."""
    from types import SimpleNamespace
    from review.features.export.properties_view import ExportStyleSidebar
    fn = ExportStyleSidebar._open_on_all_axes

    for saved in ("All", "1", "3"):
        self_ = SimpleNamespace(_prefs={"axis_target": saved}, _axis_buckets={},
                                _axis_keys=("y_lim_min", "y_lim_max"))
        assert fn(self_) == "All"
        assert self_._prefs["axis_target"] == "All"


def test_opening_on_all_keeps_axis_one_s_limit_on_axis_one():
    """A figure saved while axis 1 was targeted has that axis's limit in the
    flat prefs, which the style pass applies to every *targeted* axis. Opening
    on "All" files it in axis 1's bucket first, so the figure draws exactly as
    before — axis 1 limited, axis 2 left alone — instead of the limit landing
    on every panel."""
    from types import SimpleNamespace

    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    from review.core import plot_style as PS
    from review.features.export.properties_view import ExportStyleSidebar

    prefs = {"axis_target": "1", "y_lim_min": "0", "y_lim_max": "5"}
    self_ = SimpleNamespace(_prefs=prefs, _axis_buckets={},
                            _axis_keys=("y_lim_min", "y_lim_max"))
    ExportStyleSidebar._open_on_all_axes(self_)

    fig = Figure()
    first, second = fig.add_subplot(2, 1, 1), fig.add_subplot(2, 1, 2)
    first.plot([0, 1], [0, 100])
    second.plot([0, 1], [0, 100])
    second_before = second.get_ylim()
    PS.apply_export_style_prefs(fig, prefs)
    assert first.get_ylim() == (0.0, 5.0), "axis 1 lost the limit it was saved with"
    assert second.get_ylim() == second_before, "axis 1's limit spread to axis 2"


def test_export_click_selects_axis():
    """Clicking a subplot syncs the Export-Style 'Axis #' to that axis; a click
    that misses every axis, or one made while the sidebar is hidden, no-ops so
    normal canvas interaction is untouched."""
    from types import SimpleNamespace
    from review.features.export.properties_view import ExportStyleSidebar
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
