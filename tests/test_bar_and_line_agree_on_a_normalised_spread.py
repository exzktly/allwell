"""Bar Plots and Line Graphs draw the same spread for the same member, statistic and timepoint.

Raw values always agreed. Under fold change two did not, both on the Line
side, both in ``domain/fold_change``:

* **Subtract control, a member with no spread of its own.** A replicate set of
  one well has an undefined spread (NaN). ``_subtract_pt`` folded it to 0.0
  and returned the control's spread alone, so the curve drew ±4.52 around a
  point the Bar tab draws with no whisker.
* **Ratio to t0, the fraction and Events panels.** The mean step folds the
  baseline's own spread into every point; ``_field_baseline`` handed the
  fraction and Events steps a baseline spread of 0.0, so the fraction of a set
  whose t0 fraction was 0.08 ± 0.04 read ±3.40 on the curve and ±12.59 on the
  bar.

The first half of this file checks the arithmetic against hand-computed
quadrature; the second drives both tabs' real collectors over one synthetic
plate and holds every panel's value and spread equal.
"""
from __future__ import annotations

import math
import sys

import numpy as np
import pandas as pd
import pytest

from review.domain import fold_change as fc

# AggPoint: (t, mean, spread, frac, n_above, n_total, frac_spread, n_pf_mean, n_pf_spread, n_spread, n_total_x)
T0 = (0.0, 20.0, 2.0, 0.08, 14.0, 180, 0.04, 5.0, 1.0, 7.0, 42)
T24 = (24.0, 30.0, 6.0, 0.80, 144.0, 180, 0.27, 48.0, 12.0, 36.0, 432)


def _quad_ratio(value, spread, base, base_spread):
    return (value / base) * math.sqrt((spread / value) ** 2 + (base_spread / base) ** 2)


# ── the arithmetic ──────────────────────────────────────────────────────────

def test_subtracting_a_control_leaves_an_undefined_spread_undefined():
    solo = (24.0, 60.0, float("nan"), 1.0, 180.0, 180, float("nan"))
    out = fc.normalize_pts([solo], control_stats={24.0: (27.2, 4.5)}, op_ctrl="subtract")[0]
    assert out[1] == pytest.approx(32.8)
    assert math.isnan(out[2]), f"the control's spread was reported as the member's own: {out[2]}"
    # ...which is what the bar path answers for the same inputs.
    bar = fc.scale_bar_value(60.0, float("nan"), control_mean=27.2, control_spread=4.5, op_ctrl="subtract")
    assert bar[0] == pytest.approx(32.8) and math.isnan(bar[1])


def test_subtracting_a_control_still_adds_a_measured_spread_in_quadrature():
    out = fc.normalize_pts([T24], control_stats={24.0: (27.2, 4.5)}, op_ctrl="subtract")[0]
    assert (out[1], out[2]) == (pytest.approx(2.8), pytest.approx(math.hypot(6.0, 4.5)))


@pytest.mark.parametrize("n_per_fov, n_val, n_spread", [(False, 4, 9), (True, 7, 8)])
def test_ratio_to_t0_folds_the_baseline_s_spread_into_the_fraction_and_events(n_per_fov, n_val, n_spread):
    out = fc.normalize_pts([T0, T24], use_t0=True, baseline_tp=0.0, normalize_frac_n=True, n_per_fov=n_per_fov)[1]
    # Mean: the step that always propagated; the reference for the other two.
    assert (out[1], out[2]) == (pytest.approx(1.5), pytest.approx(_quad_ratio(30.0, 6.0, 20.0, 2.0)))
    expected_frac = _quad_ratio(0.80, 0.27, 0.08, 0.04)
    assert expected_frac == pytest.approx(6.0324, abs=1e-4)            # 3.375 with the baseline treated as exact
    assert (out[3], out[6]) == (pytest.approx(10.0), pytest.approx(expected_frac))
    assert out[n_val] == pytest.approx(T24[n_val] / T0[n_val])
    assert out[n_spread] == pytest.approx(_quad_ratio(T24[n_val], T24[n_spread], T0[n_val], T0[n_spread]))
    # The bar path's answer for the fraction, from the same four numbers.
    assert fc.scale_bar_value(0.80, 0.27, t0_mean=0.08, t0_spread=0.04) == (
        pytest.approx(10.0), pytest.approx(expected_frac))


def test_subtracting_t0_folds_the_baseline_s_spread_into_the_fraction():
    out = fc.normalize_pts([T0, T24], use_t0=True, baseline_tp=0.0, normalize_frac_n=True, op_t0="subtract")[1]
    assert (out[3], out[6]) == (pytest.approx(0.72), pytest.approx(math.hypot(0.27, 0.04)))


def test_a_point_with_no_events_spread_slot_is_not_given_one():
    """A plain per-well point is a 9-tuple: normalisation rescales fields, it does not create them."""
    out = fc.normalize_pts([T0[:9], T24[:9]], use_t0=True, baseline_tp=0.0, normalize_frac_n=True)[1]
    assert len(out) == 9 and out[4] == pytest.approx(144.0 / 14.0)


# ── both tabs, through their collectors ─────────────────────────────────────

THRESHOLD = 20.0
SETS = [{"name": "CTRL", "wells": ["A01", "A02", "A03"]}, {"name": "TRT", "wells": ["B01", "B02", "B03"]},
        {"name": "SOLO", "wells": ["C01"]}]


def _plate() -> dict[str, pd.DataFrame]:
    """Seven wells, three timepoints, three fields; the control straddles the threshold at t0."""
    rng = np.random.default_rng(3)
    wells = {}
    for well, level in {"A01": 10, "A02": 13, "A03": 16, "B01": 22, "B02": 25, "B03": 28, "C01": 30}.items():
        rows = []
        for tp in (0.0, 24.0, 48.0):
            for fov in (1, 2, 3):
                for v in rng.normal(level * (1 + tp / 24.0), 4.0, 60):
                    rows.append({"well": well, "fov": str(fov), "timepoint": f"{tp:g}h", "timepoint_hours": tp,
                                 "filename": f"{well}_f{fov}_t{tp:g}.tif", "nucleus_id": str(len(rows)),
                                 "area_px": 100.0, "gfp_mean_intensity": v})
        wells[well] = pd.DataFrame(rows)
    return wells


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    from review.core.app_runtime import AppRuntime
    from review.core.app_state import AppState
    from review.core.event_bus import EventBus
    from review.core.widget_registry import WidgetRegistry
    from review.data import data_loading as dl
    from review.data.well_repository import WellRepository
    from review.data.well_summary import well_summary
    from review.features.barplot.controller import ordered_bar_keys
    from review.features.registry import attach_feature_state, build_feature_registry
    from review.viewer_context import ViewerContext

    tmp = tmp_path_factory.mktemp("bar_line_plate")
    build_feature_registry()
    state = AppState()
    attach_feature_state(state)
    state.dataset.data_dir = tmp
    for well, df in _plate().items():
        path = tmp / f"{well}.csv"
        df.to_csv(path, index=False)
        state.dataset.well_paths[well] = path
        dl.read_well_frame(path)
        state.dataset.summaries[well] = well_summary(path)
    state.channel.active_val_col = "gfp_mean_intensity"
    state.channel.active_channel = "gfp"
    state.channel.active_metric = "mean_intensity"
    state.channel.fluor_channels = ["gfp"]
    state.channel.threshold = THRESHOLD
    state.cell_gating.thresh_frac_on["gfp"] = THRESHOLD
    state.plot.use_sem = False
    state.selection.selections = SETS
    store: dict = {}
    c = ViewerContext(state=state, bus=EventBus(), widgets=WidgetRegistry(None, store=store),
                      runtime=AppRuntime(None, store=store))
    state.repo = WellRepository(c)
    c.runtime.barplot.current_keys = lambda: ordered_bar_keys(c)
    return c


def _both_tabs(ctx, state, *, op_ctrl="ratio"):
    """``{member: (bar panels, line panels)}`` at 24 h, each ``(mean, spread, frac, spread, events, spread)``."""
    from review.features.barplot.collect import collect_bar_items
    from review.features.lineplot.controller import collect_line_series

    plot = ctx.state.plot
    plot.fc_vs_control_on, plot.fc_control, plot.fc_vs_t0_on = state.vs_control_on, state.control, state.vs_t0_on
    plot.fc_op_ctrl, plot.fc_op_t0, plot.fc_baseline_tp = op_ctrl, "ratio", "0"
    ctx.state.stats_cache.cache.clear()
    grouped, items, _label = collect_bar_items(ctx, 24.0, fc_state=state, fc_baseline_tp=0.0)
    assert grouped, "the bar tab is not drawing the replicate sets"
    series, _ = collect_line_series(ctx, threshold=THRESHOLD, use_sem=False, include_cdf=False,
                                    fc_state=state, fc_baseline_tp=0.0)
    line = {s.key: next(p for p in s.points if abs(p.t - 24.0) < 1e-6) for s in series}
    return {it.key: ((it.mean, it.spread, it.frac, it.frac_spread, it.n_above, it.n_above_spread),
                     (line[it.key].mean, line[it.key].spread, line[it.key].frac, line[it.key].frac_spread,
                      line[it.key].n_above, line[it.key].n_above_spread))
            for it in items}


def _assert_same(found: dict) -> None:
    assert set(found) == {"CTRL", "TRT", "SOLO"}
    for member, (bar, line) in found.items():
        assert bar == pytest.approx(line, nan_ok=True), f"{member}: bar {bar} against line {line}"


def test_the_tabs_agree_on_raw_values(ctx):
    from review.domain.fold_change_state import FoldChangeState

    found = _both_tabs(ctx, FoldChangeState())
    _assert_same(found)
    assert math.isnan(found["SOLO"][0][1]) and found["CTRL"][0][1] == pytest.approx(4.5159, abs=1e-4)


def test_the_tabs_agree_after_subtracting_the_control(ctx):
    from review.domain.control_ref import ControlRef
    from review.domain.fold_change_state import FoldChangeState

    found = _both_tabs(ctx, FoldChangeState(vs_control_on=True, control=ControlRef.for_replicate_set("CTRL")),
                       op_ctrl="subtract")
    _assert_same(found)
    solo_line = found["SOLO"][1]
    assert solo_line[0] == pytest.approx(32.7875, abs=1e-4) and math.isnan(solo_line[1])   # was ±4.5159


def test_the_tabs_agree_on_the_ratio_to_t0(ctx):
    from review.domain.fold_change_state import FoldChangeState

    found = _both_tabs(ctx, FoldChangeState(vs_t0_on=True))
    _assert_same(found)
    ctrl_line, trt_line = found["CTRL"][1], found["TRT"][1]
    assert (ctrl_line[2], ctrl_line[3]) == (pytest.approx(10.0930, abs=1e-4), pytest.approx(12.5906, abs=1e-4))
    assert trt_line[3] == pytest.approx(0.1922, abs=1e-4)                                   # were ±3.4039 and ±0.0


# ── a baseline that cannot divide is undefined on every tab ─────────────────────────────────────────────────────────

def test_a_ratio_baseline_of_zero_is_undefined_and_not_taken_from_a_later_timepoint():
    """Full-app review row S5a. Fraction ON is 0 at t0 and 0.5, 1.0 after it: the ordinary start of an induction."""
    pts = [(0.0, 10.0, 1.0, 0.0, 0, 50, 0.0), (24.0, 30.0, 2.0, 0.5, 25, 50, 0.1), (48.0, 40.0, 2.0, 1.0, 50, 50, 0.0)]
    out = {p[0]: p for p in fc.normalize_pts(pts, use_t0=True, baseline_tp=0.0, op="ratio", normalize_frac_n=True)}
    assert out[24.0][1] == pytest.approx(3.0)                     # the mean has a baseline, and divides by it
    assert all(math.isnan(out[t][3]) for t in (0.0, 24.0, 48.0))  # the fraction has none: not 1.0 at 24 h, 2.0 at 48 h
    # ... and with no timepoint named, "t0" is still the earliest one, not the earliest that happens to divide.
    out = {p[0]: p for p in fc.normalize_pts(pts, use_t0=True, baseline_tp=None, op="ratio", normalize_frac_n=True)}
    assert all(math.isnan(out[t][3]) for t in (0.0, 24.0, 48.0))
    # Subtracting zero is defined, so that operation is untouched.
    out = {p[0]: p for p in fc.normalize_pts(pts, use_t0=True, baseline_tp=0.0, op="subtract", normalize_frac_n=True)}
    assert out[24.0][3] == pytest.approx(0.5)


def test_the_bar_s_resolvers_name_the_requested_timepoint_and_say_it_cannot_divide():
    pts = [(0.0, 10.0, 1.0, 0.0, 0, 50, 0.0), (24.0, 30.0, 2.0, 0.5, 25, 50, 0.1)]
    tp, value, _spread = fc.baseline_frac_from_points_resolved(pts, baseline_tp=0.0, op="ratio")
    assert tp == 0.0 and math.isnan(value)                        # was (24.0, 0.5): another timepoint's value
    tp, value, _spread = fc.baseline_n_from_points_resolved(pts, baseline_tp=0.0, op="ratio")
    assert tp == 0.0 and math.isnan(value)
    assert fc.baseline_from_points_resolved(pts, baseline_tp=0.0, op="ratio")[:2] == (0.0, 10.0)
    # A timepoint the member does not sample still resolves to its earliest measured point.
    assert fc.baseline_from_points_resolved(pts, baseline_tp=12.0, op="ratio")[:2] == (0.0, 10.0)
    # A member with no measured point has no baseline at all, which is a different answer from "cannot divide".
    assert fc.baseline_frac_from_points_resolved([], baseline_tp=0.0, op="ratio") == (None, None, 0.0)


def test_the_tabs_agree_that_a_set_with_no_on_cells_at_t0_has_no_fraction_ratio(ctx, monkeypatch):
    """Through both tabs' real collectors: with the cut above every control cell at t0, the replicate-set bar read
    NaN while the curve read 1.0 at 24 h (normalised against 24 h, under an axis labelled "vs t0")."""
    from review.domain.fold_change_state import FoldChangeState

    high = 40.0                                                    # above every CTRL cell at t0, not at 24 h
    monkeypatch.setattr(ctx.state.channel, "threshold", high)
    monkeypatch.setitem(ctx.state.cell_gating.thresh_frac_on, "gfp", high)
    monkeypatch.setattr(sys.modules[__name__], "THRESHOLD", high)
    try:
        found = _both_tabs(ctx, FoldChangeState(vs_t0_on=True))
    finally:
        ctx.state.stats_cache.cache.clear()
    bar, line = found["CTRL"]
    assert math.isnan(bar[2]) and math.isnan(line[2]), (bar, line)  # fraction: undefined on both
    assert math.isnan(bar[4]) and math.isnan(line[4]), (bar, line)  # events: undefined on both
    assert bar[0] == pytest.approx(line[0]) and math.isfinite(bar[0])   # the mean still has its baseline


# ── the Bar tab and Batch Export's bar figure draw the same numbers ─────────────────────────────────────────────────

def _batch_bars(ctx, state, *, op_ctrl="ratio"):
    """Batch Export's bar items for the same three sets at 24 h, keyed like the tab's."""
    from review.domain.batch_models import BarGroup
    from review.domain.plot_members import plot_replicate_sets_loaded
    from review.features.barplot.collect import collect_bar_items_for_group

    plot = ctx.state.plot
    plot.fc_vs_control_on, plot.fc_control, plot.fc_vs_t0_on = state.vs_control_on, state.control, state.vs_t0_on
    plot.fc_op_ctrl, plot.fc_op_t0, plot.fc_baseline_tp = op_ctrl, "ratio", "0"
    ctx.state.stats_cache.cache.clear()
    group = BarGroup("all", members=list(plot_replicate_sets_loaded(ctx)))
    items = collect_bar_items_for_group(
        ctx, group, 24.0, val_col="gfp_mean_intensity", threshold=THRESHOLD, use_sem=False,
        per_fov_spread=False, fc_state=state, fc_baseline_tp=0.0)
    return {it.display: (it.mean, it.spread, it.frac, it.frac_spread, it.n_above, it.n_above_spread) for it in items}


@pytest.mark.parametrize("case", ["raw", "subtract control", "ratio to t0"])
def test_batch_export_s_bars_are_the_bar_tab_s(ctx, case):
    """The review left this open: one probe printed 1.00 on the tab against 1.91 in Batch Export for one set under
    ratio-to-t0, having set the two up through different state objects. Here both read the same plot state."""
    from review.domain.control_ref import ControlRef
    from review.domain.fold_change_state import FoldChangeState

    state, op = {
        "raw": (FoldChangeState(), "ratio"),
        "subtract control": (FoldChangeState(vs_control_on=True, control=ControlRef.for_replicate_set("CTRL")),
                             "subtract"),
        "ratio to t0": (FoldChangeState(vs_t0_on=True), "ratio"),
    }[case]
    tab = {member: bar for member, (bar, _line) in _both_tabs(ctx, state, op_ctrl=op).items()}
    batch = _batch_bars(ctx, state, op_ctrl=op)
    assert set(batch) == set(tab) == {"CTRL", "TRT", "SOLO"}
    for member in tab:
        assert batch[member] == pytest.approx(tab[member], nan_ok=True), (member, tab[member], batch[member])
    if case == "ratio to t0":
        assert tab["TRT"][0] == pytest.approx(1.9138, abs=1e-4)     # the number the probe printed for Batch Export
