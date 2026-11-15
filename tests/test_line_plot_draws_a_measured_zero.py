"""A timepoint with no cell above the ON cutoff is drawn at zero on the Line Graphs tab.

At such a timepoint the mean is undefined (it is the average intensity *of the ON cells*, and there are none), the
fraction is 0.0 and the event count is 0. The last two are measurements. The tab used to key both on the mean: a
replicate set's point was built only when the set had a mean, so its Fraction and Events curves began at the first
timepoint with an ON cell, and a well's Events curve skipped the timepoint while its Fraction curve drew it.

The plate here has every cell below the cutoff at 0 h, so the right answer (a point at t = 0 worth 0) and the old one
(no point at t = 0) differ on every panel asserted. The values at 24 h are chosen so the per-well mean (2.0 events,
fraction 0.5) is not any single well's value. Collected through the tab's own collector and the batch exporter's, and
read back from the painted axes (CLAUDE.md §7.4 #1).
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6")

CUT = 50.0
#: gfp intensities per (well, timepoint): 0 h none above the cut; 24 h three of four, then one of four; 48 h all.
CELLS = {
    "A01": {"0h": [20, 21, 22, 23], "24h": [60, 61, 62, 20], "48h": [70, 71, 72, 73]},
    "A02": {"0h": [20, 21, 22, 23], "24h": [60, 20, 21, 22], "48h": [70, 71, 72, 73]},
}
HOURS = {"0h": 0.0, "24h": 24.0, "48h": 48.0}


@pytest.fixture
def ctx(qtbot, tmp_path):
    import pandas as pd

    from tests._minimal_shell import load_dataset, minimal_shell

    for well, by_tp in CELLS.items():
        rows = [{"well": well, "fov": "1", "timepoint": tp, "timepoint_hours": HOURS[tp], "channel": "dapi",
                 "nucleus_id": i + 1, "area_px": 200.0, "gfp_mean_intensity": float(v),
                 "dapi_mean_intensity": 300.0}
                for tp, values in by_tp.items() for i, v in enumerate(values)]
        pd.DataFrame(rows).to_csv(tmp_path / f"syn_{well}.csv", index=False)
    (tmp_path / "pipeline_info.json").write_text(json.dumps({
        "schema": "mini", "separator": "_", "fluor_tokens": ["gfp"], "nuclear_token": "dapi",
        "available_timepoints": list(HOURS), "fov_index": -1, "tp_index": 2}))
    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, tmp_path)
    ctx.state.channel.active_channel = "gfp"
    ctx.state.channel.active_val_col = "gfp_mean_intensity"
    return ctx


def _painted(series):
    """Paint one series and return ``{panel: (x, y)}`` of the line each time-series panel drew."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from review.features.lineplot.controller import LineAxes, _paint_series

    fig, axs = plt.subplots(1, 4)
    try:
        axes = LineAxes(*axs)
        _paint_series(axes, series, fov_active=False)
        out = {}
        for name, ax in (("mean", axes.mean), ("frac", axes.frac), ("n", axes.n)):
            assert len(ax.lines) == 1, name
            out[name] = ([float(x) for x in ax.lines[0].get_xdata()],
                         [float(y) for y in ax.lines[0].get_ydata()])
        return out
    finally:
        plt.close(fig)


def _tab_series(ctx):
    from review.features.lineplot.controller import collect_line_series

    series, _ = collect_line_series(ctx, threshold=CUT, use_sem=True, include_cdf=False)
    assert len(series) == 1
    return series[0]


def test_a_replicate_set_draws_zero_fraction_and_zero_events_where_no_cell_is_on(ctx):
    ctx.state.selection.selections = [{"name": "G1", "wells": ["A01", "A02"], "hidden": False}]
    ctx.state.selection.selected_wells = {"A01", "A02"}
    drawn = _painted(_tab_series(ctx))
    assert drawn["frac"] == ([0.0, 24.0, 48.0], [0.0, 0.5, 1.0])
    assert drawn["n"] == ([0.0, 24.0, 48.0], [0.0, 2.0, 4.0])
    # The mean of the ON cells is undefined at 0 h and stays undrawn there: not computed is not zero.
    assert drawn["mean"][0] == [24.0, 48.0]


def test_a_single_well_draws_zero_events_where_no_cell_is_on(ctx):
    ctx.state.selection.selections = []
    ctx.state.selection.selected_wells = {"A01"}
    drawn = _painted(_tab_series(ctx))
    assert drawn["frac"] == ([0.0, 24.0, 48.0], [0.0, 0.75, 1.0])
    assert drawn["n"] == ([0.0, 24.0, 48.0], [0.0, 3.0, 4.0])
    assert drawn["mean"][0] == [24.0, 48.0]


def test_the_batch_exporter_keeps_the_same_timepoint(ctx):
    from review.domain.batch_models import BarGroup, ReplicateSet
    from review.features.batch_export._line_series_runner import collect_line_series_for_group

    group = BarGroup("g", members=[ReplicateSet("G1", ["A01", "A02"])])
    series = collect_line_series_for_group(
        ctx, group, threshold=CUT, use_sem=True, val_col="gfp_mean_intensity", fluor_gates=None)
    assert len(series) == 1
    drawn = _painted(series[0])
    assert drawn["frac"] == ([0.0, 24.0, 48.0], [0.0, 0.5, 1.0])
    assert drawn["n"] == ([0.0, 24.0, 48.0], [0.0, 2.0, 4.0])


def test_a_count_that_was_not_computed_is_not_drawn_as_zero():
    """The Events panel judges the count: NaN (a fold change with no control) is left out, 0 is drawn."""
    from review.features.lineplot.controller import LinePoint, LineSeries, _events_points

    nan = float("nan")
    series = LineSeries(key="A01", display="A01", color="k", kind="well", wells=["A01"], points=[
        LinePoint(t=0.0, mean=nan, spread=0.0, frac=0.0, n_above=0.0, has_mean=False),
        LinePoint(t=24.0, mean=1.0, spread=0.0, frac=nan, n_above=nan, has_frac=False),
        LinePoint(t=48.0, mean=nan, spread=0.0, frac=nan, n_above=1.5, has_mean=False, has_frac=False),
    ])
    assert _events_points(series, fov_active=False) == [(0.0, 0.0, 0.0), (48.0, 1.5, 0.0)]
