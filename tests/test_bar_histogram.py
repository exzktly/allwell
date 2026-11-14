"""The Bar tab's Histogram mode, read back from the artists ``draw_histogram`` draws on real caches.

Two wells carry the shared core's fixture (``tests/test_column_histogram.py``): A = 0.5, 0.5, 0.5, 3.5 and
B = 1.0, 1.0, 1.2, 1.2, 2.6, 2.6, 3.0, 3.0, at 4 bins. Per-sample bins, scaling each column to its own peak, or counts
in place of probabilities each put different numbers on the bars, so asserting the bars' geometry asserts all three.
"""

from __future__ import annotations

import matplotlib
import numpy as np
import pandas as pd
import pytest

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

from review.data import data_loading as dl  # noqa: E402
from review.domain.fold_change import CellScaleSpec  # noqa: E402
from review.features.barplot import renderer  # noqa: E402
from review.features.barplot.renderer import HISTOGRAM_GID, HISTOGRAM_NOTE_GID  # noqa: E402

pytest.importorskip("pyarrow.parquet")

A = [0.5, 0.5, 0.5, 3.5]
B = [1.0, 1.0, 1.2, 1.2, 2.6, 2.6, 3.0, 3.0]


def _ctx(tmp_path, wells: dict, val: str):
    from review.core.app_runtime import AppRuntime
    from review.core.app_state import AppState
    from review.core.event_bus import EventBus
    from review.core.widget_registry import WidgetRegistry
    from review.data.well_repository import WellRepository
    from review.data.well_summary import well_summary
    from review.features.registry import attach_feature_state, build_feature_registry
    from review.viewer_context import ViewerContext

    build_feature_registry()
    state = AppState()
    attach_feature_state(state)
    state.dataset.data_dir = tmp_path
    for well, values in wells.items():
        n = len(values)
        path = tmp_path / f"{well}.csv"
        pd.DataFrame({
            "well": [well] * n, "fov": ["1"] * n, "timepoint": ["6h"] * n, "timepoint_hours": [6.0] * n,
            "filename": [f"{well}.tif"] * n, "nucleus_id": [str(i) for i in range(n)], "area_px": [100.0] * n,
            val: values,
        }).to_csv(path, index=False)
        state.dataset.well_paths[well] = path
        dl.read_well_frame(path)
        state.dataset.summaries[well] = well_summary(path)
    state.channel.active_val_col = val
    state.channel.active_channel = "gfp"
    state.channel.active_metric = "smfish_count" if val.endswith("smfish_count") else "mean_intensity"
    store: dict = {}
    c = ViewerContext(state=state, bus=EventBus(), widgets=WidgetRegistry(None, store=store),
                      runtime=AppRuntime(None, store=store))
    state.repo = WellRepository(c)
    return c


def _draw(ctx, wells, *, threshold=0.0, **kw):
    fig, (ax_mean, ax_frac) = plt.subplots(2, 1)
    renderer.draw_histogram(ctx, ax_mean, ax_frac, wells=list(wells), colors=["#4488cc", "#cc8844"],
                            xlabels=list(wells), target_t=6.0, tp_str="6", threshold=threshold, **kw)
    return fig, ax_mean


def _bars(ax):
    """Per column x: ``[(bottom, height, left, width), …]`` of the histogram's bars, bottom up."""
    out: dict = {}
    for p in ax.patches:
        if p.get_gid() != HISTOGRAM_GID:
            continue
        x = round(p.get_x() + p.get_width() / 2, 9)
        out.setdefault(x, []).append((p.get_y(), p.get_height(), p.get_x(), p.get_width()))
    return {x: [v for bar in sorted(bars) for v in bar] for x, bars in out.items()}


def _bottoms(ax):
    return sorted({round(p.get_y(), 9) for p in ax.patches if p.get_gid() == HISTOGRAM_GID})


def _note(ax):
    return [t.get_text() for t in ax.texts if t.get_gid() == HISTOGRAM_NOTE_GID]


def test_the_columns_share_edges_and_one_width_scale(tmp_path):
    ctx = _ctx(tmp_path, {"A01": A, "A02": B}, "gfp_mean_intensity")
    fig, ax = _draw(ctx, ["A01", "A02"], bins=4)
    try:
        bars = _bars(ax)
        assert sorted(bars) == [0.0, 1.0]
        # bar_w = min(0.4, 3/2) = 0.4; half-widths A [0.4, 0.1333], B [0.2667, 0.1333, 0.1333]
        # Each bar is (bottom, height, left, width), flattened bottom up.
        assert bars[0.0] == pytest.approx([0.5, 0.75, -0.4, 0.8, 2.75, 0.75, -0.4 / 3, 0.8 / 3])
        assert bars[1.0] == pytest.approx([0.5, 0.75, 1 - 0.8 / 3, 1.6 / 3, 2.0, 0.75, 1 - 0.4 / 3, 0.8 / 3,
                                           2.75, 0.75, 1 - 0.4 / 3, 0.8 / 3])
        assert _note(ax) == ["4 bins · widest bar = 75% of a column"]
        assert "distribution (histogram, 4 bins)" in ax.get_title()
    finally:
        plt.close(fig)


def test_the_bin_count_defaults_to_the_tab_s_setting(tmp_path):
    ctx = _ctx(tmp_path, {"A01": A, "A02": B}, "gfp_mean_intensity")
    ctx.state.bar.hist_bins = 6
    fig, ax = _draw(ctx, ["A01", "A02"])
    try:
        assert _note(ax)[0].startswith("6 bins ·")
    finally:
        plt.close(fig)


def test_grey_colour_mode_reaches_the_bars(tmp_path):
    from review.domain.bar_colors import mono_bar_color

    ctx = _ctx(tmp_path, {"A01": A, "A02": B}, "gfp_mean_intensity")
    ctx.state.plot.bar_color_mode = "grey"
    fig, ax = _draw(ctx, ["A01", "A02"], bins=4)
    try:
        faces = {matplotlib.colors.to_hex(p.get_facecolor()) for p in ax.patches if p.get_gid() == HISTOGRAM_GID}
        assert faces == {matplotlib.colors.to_hex(mono_bar_color(ctx.state.plot))}
    finally:
        plt.close(fig)


def test_a_count_metric_bins_on_whole_numbers(tmp_path):
    ctx = _ctx(tmp_path, {"A01": [0, 0, 1, 1, 1, 2], "A02": [2, 3, 3, 4, 7]}, "gfp_smfish_count")
    fig, ax = _draw(ctx, ["A01", "A02"], bins=4, threshold=-1.0)
    try:
        assert _bottoms(ax) == [-0.5, 1.5, 3.5, 5.5]              # continuous bins would start at 0, 1.75, 3.5, 5.25
    finally:
        plt.close(fig)


def test_a_fold_change_bins_the_scaled_values_and_is_not_a_count(tmp_path):
    ctx = _ctx(tmp_path, {"A01": [0, 0, 1, 1, 1, 2], "A02": [2, 3, 3, 4, 7]}, "gfp_smfish_count")
    half = CellScaleSpec(control_mean=2.0, op_ctrl="ratio", t0_mean=None, op_t0="ratio", order="ctrl_first",
                         missing=False)
    fig, ax = _draw(ctx, ["A01", "A02"], bins=4, threshold=-1.0, cell_scale=lambda _w, _t: half)
    try:
        assert _bottoms(ax) == pytest.approx(list(np.linspace(0, 3.5, 5)[:-1]))     # 0 … 3.5 split evenly, halved
    finally:
        plt.close(fig)


def test_a_column_too_small_for_a_histogram_is_drawn_as_its_values(tmp_path):
    ctx = _ctx(tmp_path, {"A01": A, "A02": [2.0, 2.0]}, "gfp_mean_intensity")
    fig, ax = _draw(ctx, ["A01", "A02"], bins=4)
    try:
        assert sorted(_bars(ax)) == [0.0]
        assert any(tuple(c.get_offsets()[0]) == (1.0, 2.0) for c in ax.collections if len(c.get_offsets()))
        assert _note(ax) == ["4 bins · widest bar = 75% of a column"]    # the scale is A's alone
    finally:
        plt.close(fig)


def test_every_per_cell_site_names_the_histogram(tmp_path):
    from review.features.barplot import _per_cell_mode as pcm
    from review.features.barplot.cell_scope_compute import cell_scope

    ctx = _ctx(tmp_path, {"A01": A}, "gfp_mean_intensity")
    pcm.set_flags(ctx.state.bar, pcm.HISTOGRAM)
    assert pcm.per_cell_mode(ctx.state.bar) == "histogram"
    assert renderer._per_cell_mode_name(ctx) == "Display ▾ Show: Histogram"
    scope = cell_scope(ctx, timepoints=(6.0,))
    assert "histogram overlay is drawing them" in str(scope)


def _dots(ax, x):
    """Every dot drawn at column *x*, sorted by value."""
    return sorted(float(y) for c in ax.collections for (cx, y) in c.get_offsets() if abs(cx - x) < 1e-9)


def test_a_column_of_two_different_values_draws_both_not_only_the_first(tmp_path):
    """A column too small for a histogram drew ``vals[0]`` alone, so of 2.0 and 3.0 the 3.0 was never on the figure;
    the test above used two equal values, which the first-value dot also passes."""
    ctx = _ctx(tmp_path, {"A01": A, "A02": [2.0, 3.0]}, "gfp_mean_intensity")
    fig, ax = _draw(ctx, ["A01", "A02"], bins=4)
    try:
        assert _dots(ax, 1.0) == [2.0, 3.0]
    finally:
        plt.close(fig)


def test_a_violin_column_of_identical_values_is_drawn_as_its_dots(tmp_path):
    """A violin of four equal values is a KDE of a singular sample, which raised ``LinAlgError`` out of the whole Bar
    figure; the column is drawn as its four dots and the other column keeps its violin."""
    ctx = _ctx(tmp_path, {"A01": B, "A02": [2.0, 2.0, 2.0, 2.0]}, "gfp_mean_intensity")
    fig, (ax_mean, ax_frac) = plt.subplots(2, 1)
    try:
        renderer.draw_violin(ctx, ax_mean, ax_frac, wells=["A01", "A02"], colors=["#4488cc", "#cc8844"],
                             xlabels=["A01", "A02"], target_t=6.0, tp_str="6", threshold=0.0)
        assert _dots(ax_mean, 1.0) == [2.0, 2.0, 2.0, 2.0]
        assert len(ax_mean.collections) >= 2                    # A01's violin fill and A02's dots
    finally:
        plt.close(fig)
