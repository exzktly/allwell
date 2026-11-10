"""Every plot drawn from one Cell Gating size class: the Plotting band's **Population** (``QCParams.population``,
``review.data.population_gate``, ``review.core.population_band``).

Asked for: with cells split into two named size classes (*T cells* and *DCs*), every plottable metric — the count,
the mean, the fraction above threshold, the fold change — must be available per class. The class is chosen once, in
the band, and applied as an inclusion layer (``Population_Pass``) at the chokepoint every aggregate reads
(``df_included_mask``), so no plot can draw it and another ignore it.

The mini dataset's areas are ``200 + 10·cell`` for cells 1–6. The rule puts the **larger** cells in the first class
and the smaller in the second, so a layer that confused class order with row or id order would keep the wrong
cells; two cells (230, 240 px) are in neither class and must be in neither population.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from review.domain.qc_params import QCParams
from review.domain.size_classes import SizeClassRule

RULE = SizeClassRule.of(("Large", 245, None), ("Small", None, 225))
LARGE, SMALL = 0, 1


# ── the parameter ─────────────────────────────────────────────────────────────


def test_the_choice_is_saved_and_restored_with_the_qc_parameters():
    params = QCParams(size_classes=RULE, population=SMALL)
    back = QCParams.from_dict(params.to_dict())
    assert back.population == SMALL and back.population_rule() == (RULE, SMALL)
    assert back.population_label() == "Small"
    assert QCParams.from_dict({**params.to_dict(), "population": "junk"}).population is None
    assert QCParams.from_dict({**params.to_dict(), "population": True}).population is None


def test_a_choice_restricts_nothing_without_a_complete_rule_or_a_class_of_that_index():
    assert QCParams(size_classes=SizeClassRule.of(("Large", 245, None)), population=LARGE).population_rule() is None
    assert QCParams(size_classes=RULE, population=5).population_rule() is None
    assert QCParams(size_classes=RULE).population_rule() is None
    assert QCParams(size_classes=RULE).population_label() == ""


# ── the layer ─────────────────────────────────────────────────────────────────


def test_the_layer_keeps_exactly_the_chosen_class_and_drops_itself_when_lifted():
    from review.data.population_gate import POPULATION_COLUMN, _apply

    df = pd.DataFrame({"area_px": [210.0, 230.0, 250.0, np.nan]})
    _apply(df, (RULE, SMALL))
    assert df[POPULATION_COLUMN].tolist() == [1, 0, 0, 0]          # a blank area cannot be shown to be Small
    _apply(df, (RULE, LARGE))
    assert df[POPULATION_COLUMN].tolist() == [0, 0, 1, 0]
    _apply(df, None)
    assert POPULATION_COLUMN not in df.columns


def test_a_table_with_no_area_column_has_no_cell_in_any_class():
    from review.data.population_gate import POPULATION_COLUMN, _apply

    df = pd.DataFrame({"gfp_mean_intensity": [1.0, 2.0]})
    _apply(df, (RULE, SMALL))
    assert df[POPULATION_COLUMN].tolist() == [0, 0]


# ── through the real band control, in the app's order ─────────────────────────


@pytest.fixture
def shell(qtbot, tmp_path):
    pytest.importorskip("PySide6")
    from tests._minimal_shell import load_dataset, minimal_shell
    from tests.fixtures.mini_dataset import build_mini_dataset

    root = build_mini_dataset(tmp_path / "mini")
    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, root)
    ctx.state.selection.selected_wells = {"A01"}
    return ctx


_HELD: list = []


def _band(ctx, qtbot):
    from widgets.control_band import ControlBand

    from review.core.population_band import build_population_controls, refresh

    band = ControlBand()
    qtbot.addWidget(band)
    build_population_controls(ctx, band)
    band.show()
    refresh(ctx, "Line Graphs")
    _HELD.append(band)             # a parentless band is deleted with its last Python reference
    return ctx.widgets.get("_plotting_population_cb"), ctx.widgets.get("_plotting_population_grp")


def _kept_ids(ctx) -> list:
    from review.data.data_loading import df_included_mask, get_rows

    df = get_rows(ctx, "A01")
    kept = df[df_included_mask(df).to_numpy()]
    return sorted(set(kept["nucleus_id"].astype(int)))


def _n_total(ctx) -> list:
    """Each timepoint's cell count as every aggregate takes it (``AggPoint[5]``, n_total)."""
    from review.domain.stats_compute import aggregate_well

    points = aggregate_well(ctx, "A01", -1.0, False, "gfp_mean_intensity")
    return [int(p[5]) for p in points]


def test_the_picker_is_hidden_until_the_rule_is_complete(shell, qtbot):
    from review.features.qc.controller import get_qc_params

    combo, group = _band(shell, qtbot)
    assert not group.isVisible()
    shell.state.qc.params = replace(get_qc_params(shell), size_classes=RULE)
    from review.core.population_band import refresh

    refresh(shell, "Line Graphs")
    assert group.isVisible()
    assert [combo.itemText(i) for i in range(combo.count())] == ["All cells", "Large only", "Small only"]


def test_choosing_a_class_restricts_every_count_and_the_footer_names_it(shell, qtbot):
    from matplotlib.figure import Figure

    from review.core.observation_band import FOOTER_GID, stamp_cells_footer
    from review.features.qc.controller import get_qc_params
    from tests._panel_jobs import apply_qc_params, wait_for_panel_jobs

    apply_qc_params(qtbot, shell, replace(get_qc_params(shell), size_classes=RULE))
    combo, _group = _band(shell, qtbot)
    assert _kept_ids(shell) == [1, 2, 3, 4, 5, 6] and _n_total(shell) == [6, 6, 6]

    combo.setCurrentIndex(2)                                   # "Small only", as a user picks it
    wait_for_panel_jobs(qtbot, shell)
    assert get_qc_params(shell).population == SMALL
    assert _kept_ids(shell) == [1, 2]
    assert _n_total(shell) == [2, 2, 2]

    fig = Figure()
    fig.add_subplot(111)
    line = stamp_cells_footer(shell, fig)
    assert line.startswith("Population: Small only (≤ 225 px; Large and cells in neither class left out)")
    assert [t.get_text() for t in fig.texts if t.get_gid() == FOOTER_GID] == [line]

    combo.setCurrentIndex(1)                                   # "Large only"
    wait_for_panel_jobs(qtbot, shell)
    assert _kept_ids(shell) == [5, 6]

    combo.setCurrentIndex(0)                                   # back to every cell
    wait_for_panel_jobs(qtbot, shell)
    assert _kept_ids(shell) == [1, 2, 3, 4, 5, 6]
    assert stamp_cells_footer(shell, fig) == ""


def test_moving_a_class_s_range_while_it_is_chosen_moves_the_cells_in_every_count(shell, qtbot):
    from review.features.qc.controller import get_qc_params, only_size_classes_differ
    from tests._panel_jobs import apply_qc_params

    apply_qc_params(qtbot, shell, replace(get_qc_params(shell), size_classes=RULE, population=SMALL))
    assert _kept_ids(shell) == [1, 2]
    wider = replace(get_qc_params(shell), size_classes=SizeClassRule.of(("Large", 245, None), ("Small", None, 235)))
    assert not only_size_classes_differ(wider, get_qc_params(shell))     # not the light path: cells move
    apply_qc_params(qtbot, shell, wider)
    assert _kept_ids(shell) == [1, 2, 3]


# ── each class's share, as a plottable per-cell value ────────────────────────


def _mean(ctx, col) -> list:
    """Each timepoint's mean of *col* over A01's kept cells, as every Value plot takes it (``AggPoint[1]``)."""
    from review.domain.stats_compute import aggregate_well

    return [round(float(p[1]), 6) for p in aggregate_well(ctx, "A01", -1.0, False, col)]


def test_each_class_s_share_is_a_channel_whose_well_mean_is_the_share(shell, qtbot):
    from review.data.metric_labels import value_column_label
    from review.domain.size_classes import share_column
    from review.features.qc.controller import get_qc_params
    from tests._panel_jobs import apply_qc_params

    apply_qc_params(qtbot, shell, replace(get_qc_params(shell), size_classes=RULE))
    large, small = share_column("Large"), share_column("Small")
    assert shell.state.channel.shape_features[-3:] == [large, small, "shape_class_ratio::Large::Small"]
    assert {"Large share", "Small share"} <= set(shell.state.channel.label_to_channel_key)
    assert value_column_label(small) == "Small share"
    assert _mean(shell, small) == [round(2 / 6, 6)] * 3          # cells 1–2 of six
    assert _mean(shell, large) == [round(2 / 6, 6)] * 3          # cells 5–6


def test_the_share_follows_a_range_edit_on_the_light_path_and_a_renamed_class_takes_its_column_with_it(shell, qtbot):
    from review.domain.size_classes import share_column
    from review.features.qc.controller import get_qc_params, only_size_classes_differ, set_size_classes
    from tests._panel_jobs import apply_qc_params

    apply_qc_params(qtbot, shell, replace(get_qc_params(shell), size_classes=RULE))
    wider = replace(get_qc_params(shell), size_classes=SizeClassRule.of(("Large", 245, None), ("Tiny", None, 235)))
    assert only_size_classes_differ(wider, get_qc_params(shell))      # no class chosen: the light path
    set_size_classes(shell, wider)
    assert _mean(shell, share_column("Tiny")) == [0.5] * 3            # cells 1–3 of six
    assert share_column("Small") not in shell.state.channel.shape_features
    from review.data.data_loading import get_rows

    assert share_column("Small") not in get_rows(shell, "A01").columns


# ── the cell views outside the Plotting stack name it too ────────────────────


def _chosen_small(shell, qtbot):
    from review.features.qc.controller import get_qc_params
    from tests._panel_jobs import apply_qc_params

    apply_qc_params(qtbot, shell, replace(get_qc_params(shell), size_classes=RULE, population=SMALL))


def _footer(fig) -> list:
    from review.core.observation_band import FOOTER_GID

    return [t.get_text() for t in fig.texts if t.get_gid() == FOOTER_GID]


def test_multivariate_and_spatial_figures_name_the_population(shell, qtbot):
    from matplotlib.figure import Figure

    from review.features.multivariate.renderer import render_multivariate
    from review.features.spatial.renderer import render_spatial

    _chosen_small(shell, qtbot)
    mv = Figure()
    shell.runtime.multivariate.fig = mv
    shell.state.feature_slice("multivariate").last_error = "nothing computed"
    render_multivariate(shell)
    assert [line.split(" (")[0] for line in _footer(mv)] == ["Population: Small only"]

    sp = Figure()
    shell.widgets.register("_sp_fig", sp)
    shell.state.feature_slice("spatial").last_error = "nothing computed"
    render_spatial(shell)
    assert [line.split(" (")[0] for line in _footer(sp)] == ["Population: Small only"]


def test_the_statistics_replicate_line_names_the_population(shell, qtbot):
    from PySide6.QtWidgets import QLabel

    from review.features.stats.controller import stats_refresh_fov_indicator

    label = shell.widgets.register("_stats_fov_indicator", QLabel())
    _HELD.append(label)
    stats_refresh_fov_indicator(shell)
    assert "Population" not in label.text()
    _chosen_small(shell, qtbot)
    stats_refresh_fov_indicator(shell)
    assert label.text().splitlines()[-1].startswith("Population: Small only")


# ── the ratio of the two classes, as a plottable value ────────────────────────

#: Large ≥ 235 px, Small ≤ 215 px. At 0h and 48h the cells are 210…260 px: 3 Large, 1 Small, ratio 3 (a mean of the
#: 1s and 0s would read 0.75). At 24h they are rewritten to 3 Small, 2 Large: ratio 2/3.
RATIO_RULE = SizeClassRule.of(("Large", 235, None), ("Small", None, 215))


def _with_24h_reclassed(shell):
    from review.data.data_loading import get_rows

    df = get_rows(shell, "A01")
    rows = df.index[df["timepoint"].astype(str) == "24h"]
    df.loc[rows, "area_px"] = [210.0, 210.0, 210.0, 230.0, 240.0, 250.0]


def test_the_ratio_channel_is_each_timepoint_s_count_of_one_class_over_the_other_and_takes_a_fold_change(shell, qtbot):
    from review.data.metric_labels import value_column_label
    from review.domain.fold_change import normalize_pts
    from review.domain.size_classes import class_ratio_column
    from review.domain.stats_compute import aggregate_well
    from review.features.qc.controller import get_qc_params
    from tests._panel_jobs import apply_qc_params

    _with_24h_reclassed(shell)
    apply_qc_params(qtbot, shell, replace(get_qc_params(shell), size_classes=RATIO_RULE))
    col = class_ratio_column(RATIO_RULE)
    assert col in shell.state.channel.shape_features
    assert value_column_label(col) == "Large : Small ratio"
    points = aggregate_well(shell, "A01", -1.0, False, col)
    assert [round(p[1], 6) for p in points] == [3.0, round(2 / 3, 6), 3.0]
    assert [(p[4], p[5]) for p in points] == [(3, 4), (2, 5), (3, 4)]     # Large, and both classes
    fc = normalize_pts(points, use_t0=True)
    assert [round(p[1], 6) for p in fc] == [1.0, round(2 / 9, 6), 1.0]


def test_the_ratio_pools_a_group_as_one_count_over_the_other_not_a_mean_of_well_ratios():
    from review.data.data_loading import _aggregate_arrays

    # Two wells pooled: 3:1 and 1:3 — a mean of the two ratios would be 1.67, the pooled count 4:4 is 1.
    v = np.array([1, 1, 1, 0, 1, 0, 0, 0], dtype=float)
    pts = _aggregate_arrays(np.zeros(8), v, None, -1.0, False, False, counts_ratio=True)
    assert pts[0][1] == 1.0


def test_the_ratio_s_spread_is_across_each_field_s_own_ratio():
    from review.data.data_loading import _ratio_timepoint

    v = np.array([1, 1, 0, 1, 0, 0, np.nan], dtype=float)       # field a: 2:1, field b: 1:2, one cell in neither
    f = np.array(["a", "a", "a", "b", "b", "b", "b"], dtype=object)
    point = _ratio_timepoint(0.0, v, f, use_sem=False, per_fov_spread=True, fov_trusted=True)
    assert point[1] == 1.0                                      # 3 : 3 over the well
    assert point[2] == pytest.approx(np.std([2.0, 0.5], ddof=1))
    assert (point[4], point[5]) == (3, 6)                       # the cell in neither class is in neither count
    alone = _ratio_timepoint(0.0, v, None, use_sem=False, per_fov_spread=False, fov_trusted=True)
    assert np.isnan(alone[2])                                   # a well's cells are not replicates of its ratio


def test_an_empty_second_class_gives_no_ratio_never_infinity():
    from review.domain.size_classes import class_ratio_of_counts

    assert np.isnan(class_ratio_of_counts([1.0, 1.0, np.nan]))
    assert class_ratio_of_counts([0.0]) == 0.0


def test_the_cached_aggregate_the_plots_read_and_the_statistics_replicates_give_the_same_ratio(shell, qtbot):
    from review.domain.size_classes import class_ratio_column
    from review.domain.stats_compute import aggregate_well_cached, group_replicate_stats
    from review.features.qc.controller import get_qc_params
    from tests._panel_jobs import apply_qc_params

    _with_24h_reclassed(shell)
    apply_qc_params(qtbot, shell, replace(get_qc_params(shell), size_classes=RATIO_RULE))
    col = class_ratio_column(RATIO_RULE)
    for _ in range(2):                                          # the second read is the population store's
        cached = aggregate_well_cached(shell, "A01", -1.0, False, col)
        assert [round(p[1], 6) for p in cached] == [3.0, round(2 / 3, 6), 3.0]
    reps = group_replicate_stats(shell, ["A01", "A02"], 24.0, use_fov=False, val_col=col, threshold=-1.0)
    assert [round(r.mean, 6) for r in reps] == [round(2 / 3, 6), 3.0]
    assert round(reps[0].mean_all, 6) == round(2 / 3, 6)        # the "mean of all cells" statistic is the ratio
    assert np.isnan(reps[0].median)                             # a ratio has no median above a cutoff


def test_views_that_draw_each_cell_do_not_draw_the_ratio_and_the_distribution_says_why(shell, qtbot):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from review.domain.size_classes import class_ratio_column, share_column
    from review.features.distribution.controller import redraw_distribution
    from review.features.qc.controller import get_qc_params
    from review.features.spatial.collect import available_metric_columns
    from tests._panel_jobs import apply_qc_params

    apply_qc_params(qtbot, shell, replace(get_qc_params(shell), size_classes=RATIO_RULE))
    col = class_ratio_column(RATIO_RULE)
    metrics = available_metric_columns(shell)
    assert col not in metrics and share_column("Large") in metrics       # a share is a cell's 0/1; a ratio is not

    fig = Figure()
    shell.widgets.register("_distribution_fig", fig)
    shell.widgets.register("_distribution_canvas", FigureCanvasAgg(fig))
    shell.state.channel.active_val_col = col
    redraw_distribution(shell)
    said = " ".join(t.get_text() for ax in fig.axes for t in ax.texts)
    assert said.startswith("Large : Small ratio is one number per set of cells")


def test_under_a_one_class_population_the_class_channels_leave_the_list_and_a_plot_on_one_moves_off_it(shell, qtbot):
    """With one class left out of every aggregate a share reads 1 everywhere and the ratio has no denominator: every
    point would be NaN with nothing saying why. The channels are offered under All cells only, and a plot drawing
    one when the population is chosen moves to a fluorescence channel."""
    from review.core.channel_state_controller import set_active_channel
    from review.domain.size_classes import class_ratio_column, is_class_column
    from review.features.qc.controller import get_qc_params
    from tests._panel_jobs import apply_qc_params

    apply_qc_params(qtbot, shell, replace(get_qc_params(shell), size_classes=RATIO_RULE))
    col = class_ratio_column(RATIO_RULE)
    set_active_channel(shell, col)
    assert shell.state.channel.active_val_col == col
    apply_qc_params(qtbot, shell, replace(get_qc_params(shell), population=0))
    assert not any(is_class_column(c) for c in shell.state.channel.shape_features)
    assert not is_class_column(shell.state.channel.active_val_col)
    apply_qc_params(qtbot, shell, replace(get_qc_params(shell), population=None))
    assert col in shell.state.channel.shape_features                  # back under All cells


# ── the review's findings: every per-cell view declines the ratio; the pooled bar path counts it ─────────────────


def test_a_label_containing_the_ratio_s_separator_is_refused():
    from review.domain.size_classes import class_ratio_column, shape_column_label

    rule = SizeClassRule.of(("CD4::hi", 245, None), ("DC", None, 215))
    assert not rule.active() and "may not contain" in rule.problem()
    fine = SizeClassRule.of(("CD4 hi", 245, None), ("DC", None, 215))
    assert shape_column_label(class_ratio_column(fine)) == "CD4 hi : DC ratio"


def _ratio_shell(shell, qtbot):
    from review.core.channel_state_controller import set_active_channel
    from review.domain.size_classes import class_ratio_column
    from review.features.qc.controller import get_qc_params
    from tests._panel_jobs import apply_qc_params

    _with_24h_reclassed(shell)
    apply_qc_params(qtbot, shell, replace(get_qc_params(shell), size_classes=RATIO_RULE))
    col = class_ratio_column(RATIO_RULE)
    set_active_channel(shell, col)
    return col


def test_the_pooled_bar_counts_one_class_over_the_other_across_the_wells(shell, qtbot):
    """Bar Plots' Aggregate-FOV path pools every selected well's cells. At 24h A01 holds 2 Large : 3 Small and A02 3 : 1,
    so the pooled ratio is 5 / 4 — not the mean of the well ratios (1.83) nor the mean of the 1s and 0s (0.56)."""
    from review.features.barplot.collect import collect_bar_items_fov_pooled

    _ratio_shell(shell, qtbot)
    shell.state.selection.selected_wells = {"A01", "A02"}
    items, _label = collect_bar_items_fov_pooled(shell, [24.0], threshold=-1.0)
    assert len(items) == 1 and items[0].mean == pytest.approx(5 / 4)


def test_the_line_graph_has_a_ratio_line_and_its_cdf_panel_says_why_it_has_no_curve(shell, qtbot):
    from review.data.data_loading import _all_fluor_values_filtered
    from review.domain.size_classes import class_ratio_not_per_cell
    from review.features.lineplot.controller import _empty_cdf_msg, collect_line_series

    col = _ratio_shell(shell, qtbot)
    series, _ = collect_line_series(shell, threshold=-1.0, use_sem=False, include_cdf=True,
                                    all_fluor_values_filtered=_all_fluor_values_filtered)
    assert series and all(np.asarray(s.cdf_vals).size == 0 for s in series)
    assert _empty_cdf_msg(shell) == class_ratio_not_per_cell(col)


def test_the_batch_line_figure_s_cdf_panel_says_why_too():
    from review.domain.size_classes import class_ratio_column, class_ratio_not_per_cell
    from review.features.batch_export.base_panel import _class_ratio_cdf_msg

    col = class_ratio_column(RATIO_RULE)
    assert _class_ratio_cdf_msg(col) == class_ratio_not_per_cell(col)
    assert _class_ratio_cdf_msg("gfp_mean_intensity") is None


@pytest.mark.parametrize("violin, swarm, drawn", [(True, False, True), (False, True, True), (False, False, False)])
def test_violin_and_beeswarm_say_the_ratio_has_no_cells_and_all_points_keeps_the_bars(violin, swarm, drawn):
    from types import SimpleNamespace

    from matplotlib.figure import Figure

    from review.domain.size_classes import class_ratio_column
    from review.features.barplot.renderer import _class_ratio_has_no_cells

    ax = Figure().add_subplot(111)
    ctx = SimpleNamespace(state=SimpleNamespace(channel=SimpleNamespace(active_val_col=class_ratio_column(RATIO_RULE)),
                                                bar=SimpleNamespace(violin=violin, swarm=swarm)))
    assert _class_ratio_has_no_cells(ctx, ax) is drawn
    assert bool(ax.texts) is drawn


def test_a_ks_test_on_the_ratio_is_not_run(shell, qtbot):
    from review.features.stats.controller import compute_pairwise

    col = _ratio_shell(shell, qtbot)
    groups = [SimpleNamespace(name="a", wells=["A01"]), SimpleNamespace(name="b", wells=["A02"])]
    assert compute_pairwise(shell, groups=groups, target_t=24.0, val_col=col, statistic="mean", threshold=-1.0,
                            test="KS (two-sample)", use_fov=False) == []


def test_the_per_cell_scatter_does_not_offer_the_ratio_as_an_axis(shell, qtbot, monkeypatch):
    """Drives the real ``update_scatter_menus`` up to the moment it has filled the two axis lists, then stops it: the
    timepoint menus after them need the Scatter tab's own widgets, which this test does not build."""
    from PySide6.QtWidgets import QComboBox

    from review.features.scatter import controller as sc

    class _Filled(Exception):
        pass

    def _stop(*_a, **_k):
        raise _Filled

    col = _ratio_shell(shell, qtbot)
    seen = []
    monkeypatch.setattr("review.core.ui_helpers.set_combo_values_sectioned",
                        lambda _cb, sections: seen.append([x for _h, xs in sections for x in xs]))
    monkeypatch.setattr(sc, "refresh_scatter_metric_for_axis", lambda *_a: None)
    monkeypatch.setattr(sc, "_set_combo_values", _stop)                # the first call after the axis lists
    for name in ("_scatter_ch_x_var", "_scatter_ch_y_var"):
        combo = QComboBox()
        _HELD.append(combo)
        shell.widgets.register(name, combo)
    with pytest.raises(_Filled):
        sc.update_scatter_menus(shell)
    assert len(seen) == 2
    labels = seen[0]
    assert "Large share" in labels and "Large : Small ratio" not in labels
    assert col not in labels
