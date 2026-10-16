"""The bar chart's beeswarm and violin columns draw a stated sample above their cap (tracker F3d).

Each per-cell column used to concatenate every well's whole frame and draw every
above-threshold cell. ``barplot/_pooled.column_cells`` now reads a column's wells
narrowly and, above ``BAR_CELL_MAX_POINTS``, pools them and draws a sample, while
the column's fraction, mean and median stay every cell's. These tests hold that
against the renderers' previous computation — every well's frame concatenated,
then filtered — on a real ``AppState`` and real Parquet caches, and on the
artists the renderers draw.

The fixture's three wells differ in scale and are pooled into one column, so a
sample's mean and median land visibly away from the whole column's.
"""

from __future__ import annotations

import matplotlib
import numpy as np
import pandas as pd
import pytest

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

from review.data import data_loading as dl  # noqa: E402
from review.domain.fold_change import CellScaleSpec, apply_cell_scale  # noqa: E402
from review.features.barplot import _pooled  # noqa: E402
from review.features.barplot import renderer  # noqa: E402

pytest.importorskip("pyarrow.parquet")

WELLS = {"A01": 1500, "A02": 900, "A03": 1200}
VAL = "gfp_mean_intensity"
THRESHOLD = 5.0
GROUP = {"pooled": list(WELLS)}


def _write(tmp_path, well, n, seed):
    rng = np.random.default_rng(seed)
    path = tmp_path / f"{well}.csv"
    pd.DataFrame({
        "well": [well] * n, "fov": ["1"] * n,
        "timepoint": ["6h"] * n, "timepoint_hours": [6.0] * n,
        "filename": [f"{well}.tif"] * n, "nucleus_id": [str(i) for i in range(n)],
        "area_px": [100.0] * n, VAL: rng.lognormal(1.5 + seed, 0.8, n), "m0": rng.normal(size=n),
    }).to_csv(path, index=False)
    return path


@pytest.fixture
def ctx(tmp_path):
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
    for i, (well, n) in enumerate(WELLS.items()):
        path = _write(tmp_path, well, n, i)
        state.dataset.well_paths[well] = path
        dl.read_well_frame(path)
        state.dataset.summaries[well] = well_summary(path)
    state.channel.active_val_col = VAL
    state.channel.active_channel = "gfp"
    state.channel.active_metric = "mean_intensity"
    store: dict = {}
    c = ViewerContext(state=state, bus=EventBus(), widgets=WidgetRegistry(None, store=store),
                      runtime=AppRuntime(None, store=store))
    state.repo = WellRepository(c)
    return c


def _whole(ctx):
    """The renderers' previous computation: every frame concatenated, then filtered."""
    df = pd.concat([dl.get_rows(ctx, w) for w in WELLS], ignore_index=True)
    ctx.state.dataset.cache.clear()
    mask = dl.df_included_mask(df).to_numpy(copy=True)
    mask &= np.isfinite(_pooled._timepoint_hours(df))
    v = dl.resolve_value_series(df, VAL, {}).to_numpy()
    mask &= np.isfinite(v)
    v_sel = v[mask]
    return int(v_sel.size), v_sel[v_sel > THRESHOLD]


SPEC = CellScaleSpec(control_mean=2.5, op_ctrl="ratio", t0_mean=None, op_t0="ratio",
                     order="ctrl_first", missing=False)


def test_a_column_under_the_cap_is_every_cell_in_order(ctx, monkeypatch):
    monkeypatch.setattr(_pooled, "BAR_CELL_MAX_POINTS", 10_000)
    n_total, above = _whole(ctx)
    cells = _pooled.column_cells(ctx, "pooled", GROUP, 6.0, THRESHOLD)
    assert cells.pool is None and not cells.sampled
    assert (cells.n_total, cells.n_above) == (n_total, above.size)
    assert cells.vals.tolist() == above.tolist()


@pytest.mark.parametrize("spec", [None, SPEC], ids=["raw", "fold_change"])
def test_above_the_cap_the_column_states_every_cell(ctx, monkeypatch, spec):
    monkeypatch.setattr(_pooled, "BAR_CELL_MAX_POINTS", 400)
    n_total, above = _whole(ctx)
    cells = _pooled.column_cells(ctx, "pooled", GROUP, 6.0, THRESHOLD)
    try:
        assert cells.sampled and cells.vals.size == 400
        assert (cells.n_total, cells.n_above) == (n_total, above.size)
        assert set(cells.vals.tolist()) <= set(above.tolist()), "a drawn value is not one of the column's cells"
        scaled = apply_cell_scale(above, spec) if spec else above
        assert cells.mean(spec) == pytest.approx(float(scaled.mean()), rel=1e-12)
        assert cells.median(spec) == pytest.approx(float(np.median(scaled)), rel=1e-12)
        drawn = apply_cell_scale(cells.vals, spec) if spec else cells.vals
        assert abs(float(drawn.mean()) - float(scaled.mean())) > 1e-6, "the sample's mean is the whole's"
    finally:
        cells.close()


def _draw(ctx, name):
    fig, (ax_mean, ax_frac) = plt.subplots(2, 1)
    kw = {"bw": 0.5} if name == "draw_violin" else {}
    getattr(renderer, name)(ctx, ax_mean, ax_frac, wells=["pooled"], colors=["#4488cc"], xlabels=["pooled"],
                            target_t=6.0, tp_str="6", threshold=THRESHOLD, pool_groups=GROUP, **kw)
    return fig, ax_mean, ax_frac


def _notes(ax):
    return [t.get_text() for t in ax.texts if getattr(t, _pooled.SAMPLED_NOTE_TAG, False)]


def test_the_beeswarm_draws_the_sample_and_states_every_cells_mean_and_fraction(ctx, monkeypatch):
    monkeypatch.setattr(_pooled, "BAR_CELL_MAX_POINTS", 400)
    n_total, above = _whole(ctx)
    fig, ax_mean, ax_frac = _draw(ctx, "draw_beeswarm")
    try:
        points = [c for c in ax_mean.collections if getattr(c, "_aw_marker_collection", False)]
        assert [len(c.get_offsets()) for c in points] == [400]
        (mean_line,) = ax_mean.lines
        assert mean_line.get_ydata()[0] == pytest.approx(float(above.mean()), rel=1e-12)
        assert ax_frac.collections[0].get_offsets()[0][1] == above.size / n_total
        assert _notes(ax_mean) == ["Columns over 400 cells draw a sample of 400 (1 of 1); "
                                   "fractions, means and medians use every cell"]
    finally:
        plt.close(fig)


def test_the_violin_states_every_cells_median(ctx, monkeypatch):
    pytest.importorskip("scipy")
    monkeypatch.setattr(_pooled, "BAR_CELL_MAX_POINTS", 400)
    _n_total, above = _whole(ctx)
    fig, ax_mean, _ax_frac = _draw(ctx, "draw_violin")
    try:
        medians = {round(float(seg[0][1]), 9) for c in ax_mean.collections
                   if type(c).__name__ == "LineCollection" for seg in c.get_segments()}
        assert medians == {round(float(np.median(above)), 9)}
        assert _notes(ax_mean)
    finally:
        plt.close(fig)


def test_under_the_cap_nothing_is_noted(ctx, monkeypatch):
    monkeypatch.setattr(_pooled, "BAR_CELL_MAX_POINTS", 10_000)
    fig, ax_mean, _ax_frac = _draw(ctx, "draw_beeswarm")
    try:
        assert _notes(ax_mean) == []
    finally:
        plt.close(fig)


def test_the_columns_pools_are_released_after_drawing(ctx, monkeypatch):
    monkeypatch.setattr(_pooled, "BAR_CELL_MAX_POINTS", 400)
    made = []
    real = _pooled.CellPool

    def tracked(*a, **k):
        pool = real(*a, **k)
        made.append(pool)
        return pool

    monkeypatch.setattr(_pooled, "CellPool", tracked)
    fig, *_ = _draw(ctx, "draw_beeswarm")
    plt.close(fig)
    assert made and all(p._con is None and not p._parts for p in made)


def test_a_column_reads_its_wells_narrowly(ctx, monkeypatch):
    monkeypatch.setattr(_pooled, "BAR_CELL_MAX_POINTS", 400)
    cells = _pooled.column_cells(ctx, "pooled", GROUP, 6.0, THRESHOLD)
    cells.close()
    assert len(ctx.state.dataset.cache) == 0
