"""Regression test for the scatter fluor-gate bypass on smFISH axes.

``collect_scatter_data`` used to apply ``(x > fluor_gate_x)`` and
``(y > fluor_gate_y)`` unconditionally. Those gates come from
``app._get_fluor_gate(channel)`` which returns a fluorescence-intensity
floor (typical 50–1500). When the axis is ``*_smfish_count`` (an integer
spot tally, not an intensity), the gate strips every cell — which is the
'no scatter points at all' symptom the user reported.

The fix detects the ``_smfish_count`` suffix on each axis and bypasses
the fluor-gate for that axis. This test pins that contract.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Optional

import numpy as np
import pandas as pd

from well_viewer.features.scatter.controller import collect_scatter_data


def _df(*, w3cy5_counts, w4gfp_counts, w3cy5_mfi=None):
    n = len(w3cy5_counts)
    cols = {
        "Included": [1] * n,
        "area_px": [200.0] * n,
        "timepoint_hours": [24.0] * n,
        "timepoint": ["24h"] * n,
        "filename": [f"img_{i}.tif" for i in range(n)],
        "nucleus_id": [str(i) for i in range(n)],
        "w3cy5_smfish_count": w3cy5_counts,
        "w4gfp_smfish_count": w4gfp_counts,
    }
    if w3cy5_mfi is not None:
        cols["w3cy5_mean_intensity"] = w3cy5_mfi
    return pd.DataFrame(cols)


def _make_app(label_to_df):
    """Minimal stub exposing only what ``collect_scatter_data`` reads on
    the no-rep-set / individual-wells path."""
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths=dict.fromkeys(label_to_df)),
        channel=SimpleNamespace(ratio_index={}),
        selection=SimpleNamespace(selected_wells=list(label_to_df)),
        plot=SimpleNamespace(line_order_wells=[], line_order_rsets=[]),
    )
    app._rep_sets_active = lambda: []
    app._get_rows = lambda label: label_to_df[label]
    app._rank_color_well = lambda label: "#000000"
    app._parse_rc = lambda label: (label, 0, 0)
    return app


def test_scatter_smfish_axis_bypasses_fluor_gate():
    df = _df(w3cy5_counts=[0, 3, 5], w4gfp_counts=[1, 2, 4])
    app = _make_app({"A01": df})
    out = collect_scatter_data(
        app,
        col_x="w3cy5_smfish_count",
        col_y="w4gfp_smfish_count",
        timepoints_h=[24.0],
        well_colors=["#000000"],
        fluor_gate_x=500.0,  # would strip every cell if applied
        fluor_gate_y=500.0,
    )
    assert "A01" in out
    assert sorted(out["A01"]["x"]) == [0.0, 3.0, 5.0]
    assert sorted(out["A01"]["y"]) == [1.0, 2.0, 4.0]


def test_scatter_intensity_axis_still_honours_fluor_gate():
    """Counter-test: when both axes are MFI columns, the fluor-gate must
    still apply. Otherwise the bypass would over-correct and break the
    intensity-scatter use case."""
    df = pd.DataFrame({
        "Included": [1, 1, 1, 1],
        "area_px": [200.0] * 4,
        "timepoint_hours": [24.0] * 4,
        "timepoint": ["24h"] * 4,
        "filename": [f"img_{i}.tif" for i in range(4)],
        "nucleus_id": [str(i) for i in range(4)],
        "w3cy5_mean_intensity": [10.0, 100.0, 1000.0, 2000.0],
        "w4gfp_mean_intensity": [10.0, 100.0, 1000.0, 2000.0],
    })
    app = _make_app({"A01": df})
    out = collect_scatter_data(
        app,
        col_x="w3cy5_mean_intensity",
        col_y="w4gfp_mean_intensity",
        timepoints_h=[24.0],
        well_colors=["#000000"],
        fluor_gate_x=500.0,
        fluor_gate_y=500.0,
    )
    # Only the 1000 / 2000 rows are above the 500 gate on both axes.
    assert sorted(out["A01"]["x"]) == [1000.0, 2000.0]


def test_scatter_mixed_axes_gates_only_intensity_axis():
    """X = smfish count (no gate), Y = mean intensity (gated). The y
    gate must still apply; the x gate must not."""
    df = _df(
        w3cy5_counts=[0, 3, 5, 7],
        w4gfp_counts=[0, 0, 0, 0],
        w3cy5_mfi=[10.0, 100.0, 1000.0, 2000.0],
    )
    app = _make_app({"A01": df})
    out = collect_scatter_data(
        app,
        col_x="w3cy5_smfish_count",
        col_y="w3cy5_mean_intensity",
        timepoints_h=[24.0],
        well_colors=["#000000"],
        fluor_gate_x=500.0,
        fluor_gate_y=500.0,
    )
    # Y-gate keeps only the cells with MFI > 500, regardless of count.
    paired = sorted(zip(out["A01"]["x"], out["A01"]["y"]))
    assert paired == [(5.0, 1000.0), (7.0, 2000.0)]
