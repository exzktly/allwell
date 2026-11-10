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
    assert shell.state.channel.shape_features[-2:] == [large, small]
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
