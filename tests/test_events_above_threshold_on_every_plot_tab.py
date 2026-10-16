"""Events above threshold can be plotted on every plotting tab that reduces a group.

Bar Plots and Line Graphs have always drawn an Events panel: the number of cells
clearing the ON cutoff, as the mean of each replicate's count with the spread
across replicates. The other three tabs that reduce a group to a number — the
aggregate Scatter (Wells view), Response Curves and Heat Map — could plot the
mean and the fraction but not the count. They now offer ``Events above
threshold`` beside ``Fraction above threshold``.

Each fixture here is built so the right number differs from every wrong one a
plausible implementation would produce: the count is not the fraction, not the
mean, not the cell total, and — for a replicate set — not the cross-well total
the CSV's ``events_total`` column carries. A test whose fixture let those
coincide would pass against any of them (CLAUDE.md §7.8 rule 2).
"""

from __future__ import annotations

import types
from types import SimpleNamespace

import pandas as pd
import pytest

from review.data.metric_labels import (
    EVENTS_LABEL,
    EVENTS_METRIC_KEY,
    FRACTION_LABEL,
    SYNTHETIC_METRIC_LABEL_TO_KEY,
)


# ── the shared vocabulary ────────────────────────────────────────────────────

def test_the_events_entry_resolves_to_the_mean_intensity_column():
    """The ON cutoff is defined on mean intensity, so the count is taken there.

    A resolver that pasted the key onto the channel would ask for
    ``gfp_events_above_threshold``, which no frame has — and draw nothing.
    """
    from review.core.channel_state_controller import col_for_scatter_axis

    ctx = SimpleNamespace(state=SimpleNamespace(
        channel=SimpleNamespace(label_to_channel_key={})))
    assert col_for_scatter_axis(ctx, "GFP", EVENTS_METRIC_KEY) == (
        "gfp_mean_intensity")
    assert SYNTHETIC_METRIC_LABEL_TO_KEY[EVENTS_LABEL] == EVENTS_METRIC_KEY


def test_a_property_combo_that_offers_the_fraction_offers_the_events_too(qapp):
    """The one combo builder both Response and the aggregate Scatter use."""
    from PySide6.QtWidgets import QComboBox

    from review.core.channel_state_controller import populate_metric_combo

    ctx = SimpleNamespace(state=SimpleNamespace(channel=SimpleNamespace(
        label_to_channel_key={}, active_metric="mean_intensity",
        smfish_channels=set())))
    with_counts, without = QComboBox(), QComboBox()
    populate_metric_combo(ctx, with_counts, channel_entry="GFP",
                          include_fraction=True)
    populate_metric_combo(ctx, without, channel_entry="GFP")

    items = [with_counts.itemText(i) for i in range(with_counts.count())]
    assert items[-2:] == [FRACTION_LABEL, EVENTS_LABEL], items
    # The band's shared combo does not ask for them: its consumers resolve
    # ``f"{channel}_{metric}"`` and could not read a synthetic key.
    plain = [without.itemText(i) for i in range(without.count())]
    assert EVENTS_LABEL not in plain and FRACTION_LABEL not in plain


# ── Scatter, Wells view ──────────────────────────────────────────────────────
#
# Through the production DataFrame path (``aggregate.well = None``), so the
# count is the aggregator's own ``n_above`` rather than a number this file
# supplied.

def _well_df(values, *, tp_hours=24.0):
    n = len(values)
    return pd.DataFrame({
        "Included": [1] * n,
        "area_px": [200.0] * n,
        "timepoint_hours": [tp_hours] * n,
        "timepoint": [f"{int(tp_hours):02d}h"] * n,
        "fov": ["1"] * n,
        "nucleus_id": [str(i) for i in range(n)],
        "gfp_mean_intensity": values,
    })


def _scatter_ctx(label_to_df, *, cutoff=50.0):
    cache = dict(label_to_df)
    state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths=dict.fromkeys(label_to_df),
                                well_labels={}, cache=cache),
        channel=SimpleNamespace(ratio_index={}, label_to_channel_key={},
                                fluor_channels=[], active_channel="gfp"),
        cell_gating=SimpleNamespace(
            fluor_gates={}, thresh_frac_on={"gfp": cutoff}, thresh_count_on={},
            use_scatter_thresh=False, scatter_gates=[], thresh_choices=None),
        selection=SimpleNamespace(selected_wells=list(label_to_df), selections=[]),
        plot=SimpleNamespace(use_sem=False, use_fov_replicates=False),
        repo=SimpleNamespace(rows_or_none=cache.get, rows=cache.get),
    )
    runtime = SimpleNamespace(aggregate=SimpleNamespace(well=None))
    return SimpleNamespace(state=state, runtime=runtime)


def test_the_wells_view_plots_the_count_above_the_cutoff():
    """A01: 3 of 4 cells clear 50 (fraction 0.75, mean 70). A02: 1 of 2 (0.5, 60).

    The x axis must read 3 and 1 — not 0.75/0.5, not 70/60, not 4/2.
    """
    from review.features.scatter.collect import collect_scatter_agg_data

    ctx = _scatter_ctx({"A01": _well_df([10.0, 60.0, 70.0, 80.0]),
                        "A02": _well_df([60.0, 5.0])})
    out = collect_scatter_agg_data(
        ctx, "Events On GFP", "Mean Fluorescence GFP (Mean Intensity)", [24.0],
        well_colors=["#000000", "#111111"])

    by_well = {k.split("_tp")[0]: v for k, v in out.items()}
    assert by_well["A01"]["x"] == [3.0]
    assert by_well["A02"]["x"] == [1.0]
    # The y axis on the same call is still the mean, so the parser did not
    # switch both axes to the count.
    assert by_well["A01"]["y"] == [pytest.approx(70.0)]


def test_a_well_with_no_events_is_plotted_at_zero_not_dropped():
    """Zero cells above the cutoff is a measurement (§7 of CLAUDE.md).

    The mean of the above-cutoff cells is undefined for such a well and drops
    its point — correctly, for the mean. The count is 0 and must be drawn.
    """
    from review.features.scatter.collect import collect_scatter_agg_data

    ctx = _scatter_ctx({"A01": _well_df([10.0, 20.0]),
                        "A02": _well_df([60.0, 70.0])})
    out = collect_scatter_agg_data(
        ctx, "Events On GFP", "Events On GFP", [24.0],
        well_colors=["#000000", "#111111"])

    by_well = {k.split("_tp")[0]: v for k, v in out.items()}
    assert set(by_well) == {"A01", "A02"}
    assert by_well["A01"]["x"] == [0.0] and by_well["A01"]["y"] == [0.0]
    assert by_well["A02"]["x"] == [2.0]


def test_the_scatter_property_round_trips_to_the_events_stat_string():
    """The combo label → legacy stat string → parser chain the tab draws through."""
    from review.features.scatter.controller import (
        SCATTER_AGG_METRIC_EXTRA,
        compose_scatter_agg_stat,
    )

    ctx = SimpleNamespace(state=SimpleNamespace(
        channel=SimpleNamespace(label_to_channel_key={})))
    assert compose_scatter_agg_stat(ctx, "gfp", EVENTS_LABEL) == "Events On GFP"
    assert SCATTER_AGG_METRIC_EXTRA[EVENTS_LABEL] == EVENTS_METRIC_KEY


# ── Heat Map ────────────────────────────────────────────────────────────────

_VAL = "gfp_mean_intensity"
_TP = 0.0
# (t, mean, spread, frac, n_above, n_total, …). The two wells' counts are 5 and
# 9: the replicate mean is 7, the cross-well total 14, the pooled fraction
# 14/100 and the averaged fraction 0.3 — every candidate a different number.
_POINTS = {
    "A01": [(_TP, 10.0, 0.0, 0.5, 5.0, 10, 0.0, 0.0, 0.0, 0.0)],
    "A02": [(_TP, 2.0, 0.0, 0.1, 9.0, 90, 0.0, 0.0, 0.0, 0.0)],
}


@pytest.fixture
def heat_ctx():
    return types.SimpleNamespace(
        state=types.SimpleNamespace(
            dataset=types.SimpleNamespace(well_paths={"A01": "a", "A02": "b"}),
            channel=types.SimpleNamespace(ratio_index={}, active_val_col=_VAL),
            plot=types.SimpleNamespace(use_fov_replicates=False, use_sem=False),
            heatmap=types.SimpleNamespace(repset_avg=True),
            cell_gating=types.SimpleNamespace(
                use_scatter_thresh=False, use_scatter_gates=False,
                selected_gate=None),
            repo=None,
        ),
        runtime=types.SimpleNamespace(aggregate=types.SimpleNamespace(
            well=lambda label, **_kw: _POINTS[label], group=None)),
    )


def test_the_heat_map_offers_events_and_gates_it_on_a_cutoff():
    from review.domain.heatmap_metrics import (
        METRIC_EVENTS, METRIC_OPTIONS, METRICS_NEEDING_CUTOFF,
    )

    assert METRIC_EVENTS == EVENTS_LABEL
    assert METRIC_EVENTS in METRIC_OPTIONS
    # A Property with no ON cutoff of its own (Total / Max / Min / Std) has no
    # "above threshold" to count, exactly as for the fraction.
    assert METRIC_EVENTS in METRICS_NEEDING_CUTOFF


def test_a_heat_map_cell_is_the_mean_of_its_wells_counts(heat_ctx, monkeypatch):
    """Mean 7 ± SD √8 — the Bar tab's Events statistic for the same set."""
    from review.features.heatmap import controller as HM

    monkeypatch.setattr(HM, "_expand_repset_wells", lambda _c, w: list(w))
    value, spread = HM._cell_stat(heat_ctx, ["A01", "A02"], _TP,
                                  HM.METRIC_EVENTS, _VAL, 0.0, {})

    assert value == pytest.approx(7.0)
    assert spread == pytest.approx(8.0 ** 0.5)          # SD of (5, 9), ddof=1
    assert value != pytest.approx(14.0)                  # not the total


def test_a_single_well_heat_map_cell_shows_its_own_count(heat_ctx, monkeypatch):
    from review.features.heatmap import controller as HM

    monkeypatch.setattr(HM, "_expand_repset_wells", lambda _c, w: list(w))
    value, spread = HM._cell_stat(heat_ctx, ["A02"], _TP,
                                  HM.METRIC_EVENTS, _VAL, 0.0, {})
    assert value == pytest.approx(9.0)
    # One replicate: an undefined spread, never a zero one.
    assert spread != spread


def test_a_heat_map_count_prints_as_a_count():
    from review.features.heatmap import controller as HM

    assert HM._format_cell_value(310.0, HM.METRIC_EVENTS) == "310"
    # A replicate mean that is not whole keeps its fraction rather than being
    # rounded to a count nobody measured.
    assert HM._format_cell_value(7.5, HM.METRIC_EVENTS) == "7.5"


# ── Response Curves ─────────────────────────────────────────────────────────

def _response_ctx(monkeypatch, *, well_stats, fov_stats, n_stats):
    """One dosed well whose mean, fraction and count are all different numbers.

    Records the column the count was taken on, because this tab owns its own
    Property: a count read off the band's column would describe the wrong
    channel under the right axis label.
    """
    import review.core.channel_state_controller as csc
    import review.domain.stats_compute as sc
    from review.domain import sample_variables as sv

    calls = []

    def _n(ctx, rset, target_t, use_sem, threshold=None, *, val_col=None):
        calls.append(val_col)
        return n_stats

    monkeypatch.setattr(sc, "compute_rep_stats", lambda *a, **k: well_stats)
    monkeypatch.setattr(sc, "compute_rep_per_fov_stats", lambda *a, **k: fov_stats)
    monkeypatch.setattr(sc, "compute_rep_n_above_stats", _n)
    monkeypatch.setattr(csc, "on_cutoff_for_column", lambda *a, **k: 0.0)

    dose = sv.Variable(id="dose", name="Dose", kind=sv.NUMBER, unit="µM",
                       values=(sv.VariableValue("d0", "1", 1.0),))
    vset = sv.VariableSet(variables=(dose,), assignments={"dose": {"A01": "d0"}})
    ctx = SimpleNamespace(
        state=SimpleNamespace(
            feature_slice=lambda _name: None,
            dataset=SimpleNamespace(well_paths={"A01": "/A01.csv"}),
            selection=SimpleNamespace(
                selections=[{"id": "g0", "name": "solo", "wells": ["A01"]}],
                selected_wells=set(), variables=vset),
            plot=SimpleNamespace(
                group_by_variable="", bar_pool_replicates=True, use_sem=True,
                use_fov_replicates=False, line_order_rsets=[],
                line_order_wells=[], fc_vs_control_on=False, fc_control=None,
                fc_vs_t0_on=False, fc_control_mode="single", fc_control_map={},
                fc_control_render="self", fc_baseline_tp="", fc_op_ctrl="ratio",
                fc_op_t0="ratio", fc_order="ctrl_first"),
            channel=SimpleNamespace(active_val_col="gfp_mean_intensity",
                                    active_channel="gfp", fluor_channels=()),
        ),
        runtime=SimpleNamespace(barplot=SimpleNamespace(
            current_keys=lambda: ["solo"])),
    )
    return ctx, calls


def test_the_response_curves_can_plot_events(monkeypatch):
    from review.features.response import collect as rc

    ctx, calls = _response_ctx(monkeypatch,
                               well_stats=(10.0, 1.0, 0.42, 0.03),
                               fov_stats=(10.0, 2.0, 0.42, 0.05, 6.0, 1.5),
                               n_stats=(17.0, 2.5))
    data = rc.collect_response(ctx, "dose", [24.0], y_metric=rc.Y_EVENTS,
                               val_col="rfp_mean_intensity")

    point = data.curves[0].points[0]
    assert (point.y, point.err) == (17.0, 2.5)
    assert data.y_metric == rc.Y_EVENTS
    # Counted on the tab's own column, not the band's ``gfp_mean_intensity``.
    assert calls == ["rfp_mean_intensity"]


def test_with_fov_spread_on_the_count_is_the_per_fov_one(monkeypatch):
    """The per-FOV producer already returns the count beside the mean."""
    from review.features.response import collect as rc

    ctx, calls = _response_ctx(monkeypatch,
                               well_stats=(10.0, 1.0, 0.42, 0.03),
                               fov_stats=(10.0, 2.0, 0.42, 0.05, 6.0, 1.5),
                               n_stats=(17.0, 2.5))
    ctx.state.plot.use_fov_replicates = True
    data = rc.collect_response(ctx, "dose", [24.0], y_metric=rc.Y_EVENTS)

    point = data.curves[0].points[0]
    assert (point.y, point.err) == (6.0, 1.5)
    assert calls == []


def test_an_uncounted_member_is_a_gap_and_a_zero_count_is_a_point(monkeypatch):
    from review.features.response import collect as rc

    ctx, _ = _response_ctx(monkeypatch,
                           well_stats=(10.0, 1.0, 0.42, 0.03),
                           fov_stats=(10.0, 2.0, 0.42, 0.05),
                           n_stats=(float("nan"), float("nan")))
    assert not rc.collect_response(ctx, "dose", [24.0],
                                   y_metric=rc.Y_EVENTS).has_points()

    ctx, _ = _response_ctx(monkeypatch,
                           well_stats=(10.0, 1.0, 0.42, 0.03),
                           fov_stats=(10.0, 2.0, 0.42, 0.05),
                           n_stats=(0.0, 0.0))
    data = rc.collect_response(ctx, "dose", [24.0], y_metric=rc.Y_EVENTS)
    assert data.curves[0].points[0].y == 0.0


def test_a_normalised_count_is_divided_by_the_controls_count():
    """The count rides in the Events slots (4 / 9) of the AggPoint.

    Fold change divides each slot by the control's series *for that quantity*.
    A count carried in the mean slot would be divided by the control's mean
    intensity — 17 / 100 instead of 17 / 4 — under a fold-change label.
    """
    from review.domain.fold_change import normalize_pts
    from review.features.response import collect as rc

    def _stats_for(*_a, **_k):
        return (100.0, 0.0, 0.5, 0.0)

    import review.domain.stats_compute as sc
    real = sc.compute_rep_n_above_stats
    try:
        sc.compute_rep_n_above_stats = lambda *a, **k: (17.0, 0.0)
        drawn, _err, point = rc._member_sample(
            None, _stats_for, None, 24.0, 0.0, True, "gfp_mean_intensity",
            rc.Y_EVENTS, False)
    finally:
        sc.compute_rep_n_above_stats = real
    assert drawn == 17.0

    (out,) = normalize_pts(
        [point],
        control_stats={24.0: (100.0, 0.0)},
        control_frac_stats={24.0: (0.5, 0.0)},
        control_n_stats={24.0: (4.0, 0.0)},
        normalize_frac_n=True, n_per_fov=False)
    assert out[4] == pytest.approx(17.0 / 4.0)


def test_the_response_axis_and_title_name_the_count():
    from review.features.response import collect as rc
    from review.features.response.controller import (
        response_measurement_title,
        response_y_label,
    )

    data = rc.ResponseData(y_metric=rc.Y_EVENTS)
    assert response_y_label(data, channel="gfp", metric_label=EVENTS_LABEL) == (
        "GFP events above threshold (N)")
    title = response_measurement_title(
        data, channel="gfp", metric_label=EVENTS_LABEL,
        metric_key=EVENTS_METRIC_KEY, draws_fraction=False, use_sem=True)
    # Not "GFP Events above threshold (above threshold)", and not the fraction.
    assert title == "GFP Events above threshold ± SEM"


def test_the_tab_and_batch_export_agree_on_what_the_key_plots():
    from review.features.response import collect as rc

    assert rc.y_metric_for_key(EVENTS_METRIC_KEY) == rc.Y_EVENTS
    assert rc.y_metric_for_key("fraction_above_threshold") == rc.Y_FRACTION
    assert rc.y_metric_for_key("mean_intensity") == rc.Y_MEAN


@pytest.mark.parametrize("per_fov", [False, True])
def test_a_rep_set_controls_count_is_taken_on_the_tabs_own_column(
        monkeypatch, per_fov):
    """Fold change divides by the control's count, read on the *same* column.

    Response Curves owns its Property. The control-series builder handed its
    column to a solo-well control but not to a replicate-set one, which then
    read the band's column — so an RFP events curve was normalised by a GFP
    count. The member dispatch is stubbed to hand the rep-set branch straight
    through; what is measured is the column that reaches the producers.
    """
    from review.domain import fold_change as fc

    seen = []
    monkeypatch.setattr(fc, "compute_rep_n_above_stats",
                        lambda *a, val_col=None, **k: seen.append(val_col) or (4.0, 1.0))
    monkeypatch.setattr(fc, "compute_rep_per_fov_stats",
                        lambda *a, val_col=None, **k: seen.append(val_col)
                        or (1.0, 0.0, 0.5, 0.0, 4.0, 1.0))
    monkeypatch.setattr(fc, "_member_series",
                        lambda ctx, label, *, rset_stat, **k: {24.0: rset_stat(None, 24.0)})

    out = fc.member_n_series(None, "ctrl", threshold=0.0,
                             val_col="rfp_mean_intensity", use_sem=True,
                             per_fov_spread=per_fov, fluor_gates={})
    assert out == {24.0: (4.0, 1.0)}
    assert seen == ["rfp_mean_intensity"]


@pytest.mark.parametrize("builder", ["member_stats_series", "member_frac_series"])
def test_a_rep_set_controls_mean_and_fraction_use_the_tabs_column(monkeypatch,
                                                                 builder):
    """The same seam as the count's, for the other two quantities."""
    from review.domain import fold_change as fc

    seen = []

    def _stats(*a, val_col=None, **k):
        seen.append(val_col)
        return (1.0, 0.0, 0.5, 0.0)

    monkeypatch.setattr(fc, "compute_rep_stats", _stats)
    monkeypatch.setattr(fc, "_member_series",
                        lambda ctx, label, *, rset_stat, **k: {24.0: rset_stat(None, 24.0)})
    getattr(fc, builder)(None, "ctrl", threshold=0.0,
                         val_col="rfp_mean_intensity", use_sem=True,
                         per_fov_spread=False, fluor_gates={})
    assert seen == ["rfp_mean_intensity"]


# ── an undefined per-field count is not a cut ───────────────────────────────

def test_an_undefined_per_field_count_is_not_reported_as_a_cut(monkeypatch):
    """Spread: FOV on a well whose FOV labels are unusable.

    ``group_replicate_stats`` keeps such a well's mean and fraction and NaNs
    its per-field count. The Events axis then has nothing to plot, and the
    empty message used to say every cell was cut by the ON cutoff — of a well
    three of whose four cells clear it — sending the user to lower a cutoff
    that was not the cause.
    """
    import review.domain.stats_compute as sc
    from review.features.scatter._messages import agg_empty_message
    from review.features.scatter.collect import collect_scatter_agg_data

    ctx = _scatter_ctx({"A01": _well_df([60.0, 70.0, 80.0, 10.0])})
    ctx.state.plot.use_fov_replicates = True
    monkeypatch.setattr(sc, "_fov_verdict", lambda ctx, lbl, df: SimpleNamespace(
        trusted=False, reason="schema mismatch"))

    frac = collect_scatter_agg_data(
        ctx, "Fraction On GFP", "Mean Fluorescence GFP", [24.0],
        well_colors=["#000000"])
    assert [v["x"] for v in frac.values()] == [[0.75]]

    sink = {}
    out = collect_scatter_agg_data(
        ctx, "Events On GFP", "Mean Fluorescence GFP", [24.0],
        well_colors=["#000000"], cut_sink=sink)
    assert out == {}
    statuses = []
    ctx.notify = SimpleNamespace(set_status=statuses.append)
    message = agg_empty_message(ctx, sink)
    assert "cut by the ON cutoff" not in message
    assert "FOV labels" in message and "Spread: FOV" in message
    # Nothing to lower, so nothing is pushed to the status line either.
    assert statuses == []


def test_a_real_cut_on_an_events_axis_still_says_so():
    """The other half: an undefined count must not swallow a genuine cut."""
    from review.features.scatter._messages import agg_empty_message
    from review.features.scatter.collect import collect_scatter_agg_data

    ctx = _scatter_ctx({"A01": _well_df([10.0, 20.0])})
    sink = {}
    # Fraction, not Events: a zero count is a point, so it is the fraction's
    # undefined-free path that reaches the sink with every cell below 50.
    collect_scatter_agg_data(ctx, "Mean Fluorescence GFP (Mean Intensity)",
                             "Mean Fluorescence GFP (Mean Intensity)", [24.0],
                             well_colors=["#000000"], cut_sink=sink)
    ctx.notify = SimpleNamespace(set_status=lambda _s: None)
    ctx.state.scatter = SimpleNamespace(cut_status_posted=False)
    assert "cut by the ON cutoff" in agg_empty_message(ctx, sink)


# ── the heat map's colour range follows the replicate unit ──────────────────

def _fov_df(per_fov_counts, *, tp_hours=24.0):
    rows = []
    for fov, n in per_fov_counts.items():
        rows += [{"Included": 1, "area_px": 200.0, "timepoint_hours": tp_hours,
                  "timepoint": f"{int(tp_hours):02d}h", "fov": str(fov),
                  "nucleus_id": f"{fov}-{i}", "gfp_mean_intensity": 60.0}
                 for i in range(n)]
    return pd.DataFrame(rows)


def test_the_heat_map_colour_range_follows_the_per_fov_toggle():
    """30 and 60 per well are 10 and 20 per field; the Auto range must move.

    The range cache ignored the toggle, so after switching it every cell sat
    below the previous unit's floor and the map drew in one colour.
    """
    from review.features.heatmap import controller as HM

    dfs = {"A01": _fov_df({1: 10, 2: 10, 3: 10}),
           "A02": _fov_df({1: 20, 2: 20, 3: 20})}
    ctx = _scatter_ctx(dfs)
    ctx.state.channel.active_val_col = "gfp_mean_intensity"
    ctx.state.cell_gating.use_scatter_gates = False
    ctx.state.cell_gating.selected_gate = None
    ctx.state.heatmap = SimpleNamespace(repset_avg=False, tp_values=[24.0],
                                        log_scale=False)
    ctx.runtime.aggregate.group = None
    ctx.runtime.heatmap = SimpleNamespace(global_range_cache=None)
    layout = SimpleNamespace(name="L", rows=1, cols=2,
                             cells={(0, 0): ["A01"], (0, 1): ["A02"]})

    def _range():
        return HM._compute_global_range(ctx, layout, HM.METRIC_EVENTS,
                                        "gfp_mean_intensity", 50.0, {}, {})

    assert _range() == (30.0, 60.0)
    ctx.state.plot.use_fov_replicates = True
    assert _range() == (10.0, 20.0)
