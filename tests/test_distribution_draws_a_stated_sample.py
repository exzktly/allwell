"""The Distribution tab draws its curves from a stated sample, and states every value (tracker F3c).

Above ``DIST_MAX_VALUES`` the tab pools a selection's values
(``distribution/_pooled.py``) and draws the KDE, the CDF and the violin body from
a sample — while the counts, the bin range, the histogram's bars and the violin's
median line come from every value. These tests hold that against the
whole-selection path on a real ``AppState`` and real Parquet caches:

* a cap that triggers pooling but holds every value draws exactly the
  whole-selection values;
* below it, the histogram's bars, the counts, the title and the median line are
  the whole selection's — measured on rendered artists, not on the collector's
  return value — while the drawn values are a proportional sample;
* the figure says so.

The fixture's wells differ in scale, so a sample's range, median and bar heights
all land somewhere other than the whole selection's.
"""

from __future__ import annotations

import math

import matplotlib
import numpy as np
import pandas as pd
import pytest

matplotlib.use("Agg")

from matplotlib.figure import Figure  # noqa: E402

from review.data import data_loading as dl  # noqa: E402
from review.domain.batch_models import ReplicateSet  # noqa: E402
from review.features.distribution import _pooled  # noqa: E402
from review.features.distribution import controller as C  # noqa: E402

pytest.importorskip("pyarrow.parquet")

WELLS = {"A01": 900, "A02": 300, "B01": 600}
VAL = "gfp_mean_intensity"


def _write(tmp_path, well, n, seed):
    rng = np.random.default_rng(seed)
    path = tmp_path / f"{well}.csv"
    pd.DataFrame({
        "well": [well] * n, "fov": ["1"] * n, "timepoint": ["0h"] * n,
        "timepoint_hours": [0.0] * n, "filename": [f"{well}.tif"] * n,
        "nucleus_id": [str(i) for i in range(n)],
        "area_px": np.where(np.arange(n) % 13 == 0, np.nan, 100.0),
        VAL: rng.lognormal(2 + seed, 0.7, n), "m0": rng.normal(size=n),
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
    state.selection.selected_wells = set(WELLS)
    state.channel.active_val_col = VAL
    store: dict = {}
    c = ViewerContext(state=state, bus=EventBus(), widgets=WidgetRegistry(None, store=store),
                      runtime=AppRuntime(None, store=store))
    state.repo = WellRepository(c)
    return c


@pytest.fixture(params=["wells", "groups"])
def mode(request, ctx, monkeypatch):
    if request.param == "groups":
        sets = [ReplicateSet("1", ["A02", "A01"]), ReplicateSet("2", ["B01"])]
        ctx.state.selection.selections = []
        monkeypatch.setattr(dl, "plot_replicate_sets", lambda c: list(sets), raising=False)
        import review.domain.plot_members as pm
        monkeypatch.setattr(pm, "plot_replicate_sets", lambda c: list(sets))
    return request.param


def _whole(ctx):
    """The tab's own whole-selection path, as ``redraw_distribution`` runs it."""
    ctx.state.dataset.cache.clear()
    groups = []
    for name, color, df in dl.iter_plot_groups(ctx, fallback_to_all=False):
        vals = dl._all_fluor_values_filtered(df, val_col=VAL, fluor_gates={}, ratios={}, tp_filter=None)
        if vals.size:
            groups.append((C._display_label_for(ctx, name), color, vals))
    ctx.state.dataset.cache.clear()
    return groups


def _pooled_groups(ctx, cap, monkeypatch):
    monkeypatch.setattr(_pooled, "DIST_MAX_VALUES", cap)
    ctx.state.dataset.cache.clear()
    return _pooled.collect_distribution(ctx, val_col=VAL, fluor_gates={}, ratios={}, tp_filter=None,
                                        display_label=C._display_label_for)


class _RenderCtx:
    """What the renderers read off ``ctx`` for a histogram with no threshold line."""

    def __init__(self, ctx):
        self.state = ctx.state
        self.widgets = ctx.widgets


def _render(ctx, groups, mode_name, exact):
    ax = Figure().add_subplot()
    C._render_overlay(_RenderCtx(ctx), ax, groups, mode_name, 20, False, float("nan"), exact=exact)
    return ax


def _bars(ax):
    return [round(p.get_height(), 12) for p in ax.patches]


def test_a_cap_that_holds_every_value_draws_exactly_the_whole_selection(ctx, mode, monkeypatch):
    whole = _whole(ctx)
    n = sum(v.size for _, _, v in whole)
    assert n < sum(WELLS.values()), "every row is included; the cap would not sit between them"
    groups, exact, pool = _pooled_groups(ctx, n, monkeypatch)
    try:
        assert [(lbl, col) for lbl, col, _ in groups] == [(lbl, col) for lbl, col, _ in whole]
        for (_, _, got), (_, _, want) in zip(groups, whole):
            assert got.tolist() == want.tolist()
        assert [e.n for e in exact] == [v.size for _, _, v in whole]
    finally:
        pool.close()


@pytest.mark.parametrize("mode_name", ["Histogram", "Histogram + KDE"])
def test_below_the_cap_the_bars_counts_and_title_are_the_whole_selections(ctx, mode, monkeypatch, mode_name):
    whole = _whole(ctx)
    groups, exact, pool = _pooled_groups(ctx, 300, monkeypatch)
    try:
        assert sum(v.size for _, _, v in groups) == 300, "the drawn values are not a sample of the cap"
        drawn, full = _render(ctx, groups, mode_name, exact), _render(ctx, whole, mode_name, None)
        assert _bars(drawn) == _bars(full)
        assert drawn.get_title() == full.get_title()
        assert [t.get_text() for t in drawn.get_legend().get_texts()] == \
               [t.get_text() for t in full.get_legend().get_texts()]
        sample_only = _render(ctx, groups, mode_name, None)
        assert _bars(sample_only) != _bars(full), "the sample alone gives the same bars; the test measures nothing"
    finally:
        pool.close()


def test_below_the_cap_the_violin_median_is_every_values(ctx, mode, monkeypatch):
    whole = _whole(ctx)
    groups, exact, pool = _pooled_groups(ctx, 300, monkeypatch)
    try:
        ax = _render(ctx, groups, "Violin (per group)", exact)
        medians = [seg[0][1] for seg in ax.collections[-1].get_segments()]
        assert medians == [float(np.median(v)) for _, _, v in whole]
        sample_medians = [float(np.median(v)) for _, _, v in groups]
        assert sample_medians != medians, "the sample's median is the whole's; the test measures nothing"
    finally:
        pool.close()


def test_the_cdf_states_every_value_in_its_legend(ctx, monkeypatch):
    whole = _whole(ctx)
    groups, exact, pool = _pooled_groups(ctx, 300, monkeypatch)
    try:
        ax = _render(ctx, groups, "CDF", exact)
        assert [t.get_text() for t in ax.get_legend().get_texts()] == \
               [f"{lbl} (n={v.size:,})" for lbl, _, v in whole]
    finally:
        pool.close()


def test_the_note_says_how_many_values_were_drawn(ctx, monkeypatch):
    whole = _whole(ctx)
    total = sum(v.size for _, _, v in whole)
    groups, exact, pool = _pooled_groups(ctx, 300, monkeypatch)
    try:
        assert _pooled.sampled_note(groups, exact) == (
            f"Curves drawn from a sample of 300 of {total:,} values; counts, bins and medians use every value")
        assert _pooled.sampled_note(whole, None) == ""
    finally:
        pool.close()


def test_a_selection_under_the_cap_is_not_pooled(ctx):
    assert _pooled.collect_distribution(ctx, val_col=VAL, fluor_gates={}, ratios={}, tp_filter=None,
                                        display_label=C._display_label_for) is None


def test_the_pooled_path_reads_narrowly(ctx, monkeypatch):
    _groups, _exact, pool = _pooled_groups(ctx, 300, monkeypatch)
    pool.close()
    assert len(ctx.state.dataset.cache) == 0


def test_the_ranges_are_every_values(ctx, monkeypatch):
    whole = _whole(ctx)
    _groups, exact, pool = _pooled_groups(ctx, 300, monkeypatch)
    try:
        allv = np.concatenate([v for _, _, v in whole])
        assert _pooled.exact_range(exact) == (float(allv.min()), float(allv.max()))
        assert not math.isnan(exact[0].median)
    finally:
        pool.close()


def test_below_the_cap_the_violin_extrema_are_every_values(ctx, mode, monkeypatch):
    whole = _whole(ctx)
    groups, exact, pool = _pooled_groups(ctx, 300, monkeypatch)
    try:
        ax = _render(ctx, groups, "Violin (per group)", exact)
        lines = [c for c in ax.collections if type(c).__name__ == "LineCollection"]
        # The caps and the median are horizontal marks, one per group; the bar
        # is vertical. Each horizontal mark must be every value's, on its own.
        horizontal = {tuple(round(float(seg[0][1]), 9) for seg in c.get_segments()) for c in lines
                      if all(seg[0][1] == seg[1][1] for seg in c.get_segments())}
        r = lambda f: tuple(round(float(f(v)), 9) for _, _, v in whole)  # noqa: E731
        assert horizontal == {r(np.min), r(np.max), r(np.median)}
        sample_extrema = {round(float(f(v)), 9) for _, _, v in groups for f in (np.min, np.max)}
        whole_extrema = {round(float(f(v)), 9) for _, _, v in whole for f in (np.min, np.max)}
        assert sample_extrema != whole_extrema, "the sample spans the whole range; the test measures nothing"
    finally:
        pool.close()


def test_a_failed_collection_releases_its_pool(ctx, monkeypatch):
    made, real = [], _pooled.CellPool

    def tracked(*a, **k):
        made.append(real(*a, **k))
        return made[-1]

    def failing(*a, **k):
        raise OSError("disk went away")

    monkeypatch.setattr(_pooled, "CellPool", tracked)
    monkeypatch.setattr(_pooled, "narrow_rows", failing)
    with pytest.raises(OSError):
        _pooled_groups(ctx, 300, monkeypatch)
    assert made and all(p._con is None and not p._parts for p in made)


def test_a_heavy_tails_every_value_extrema_stay_inside_the_axes(ctx, monkeypatch):
    """The caps are moved after the violin is drawn, and moving a segment does not
    rescale the axes: a true maximum far above the sample's must still be in view."""
    import dataclasses

    groups, exact, pool = _pooled_groups(ctx, 300, monkeypatch)
    try:
        span = max(float(np.max(v)) for *_, v in groups) - min(float(np.min(v)) for *_, v in groups)
        far = [dataclasses.replace(e, hi=float(np.max(v)) + 20 * span, lo=float(np.min(v)) - 20 * span)
               for e, (*_, v) in zip(exact, groups)]
        ax = _render(ctx, groups, "Violin (per group)", far)
        lo, hi = ax.get_ylim()
        assert lo <= min(e.lo for e in far) and max(e.hi for e in far) <= hi, (lo, hi)
    finally:
        pool.close()
