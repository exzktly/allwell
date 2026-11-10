"""Each size class's count, and the ratio of the two, over the Nanowells page's Baseline (``size_class_compute.FC_OF``,
``series_compute.site_metrics``).

Asked for: "T cell fold change" on the Nanowells plots, and the ratio of the two classes with its fold change. Each is
computed beside *Count fold change*, from the same baseline row, so the class counts and the count fold change are one
family of numbers.

The fixture is the shared nanowell dataset (two timepoints, ``0h`` the baseline) with each counted cell given an area
that puts it in a class. The classes are assigned so the wrong answers differ (CLAUDE.md §7.8): the class fold
changes are not the count's (a nanowell whose count rises while one class falls), a class empty at baseline is
undefined rather than infinite or zero, and a ratio that goes from 1 to 3 is told from one that goes 1 to 0.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from review.domain.size_classes import SizeClassRule
from review.features.nanowell import cell_gate_compute as cg
from review.features.nanowell import size_class_compute as scc

RULE = SizeClassRule.of(("T cells", None, 100), ("DCs", 300, None))
T, DC = 50.0, 400.0

#: A01's cells per nanowell, (T cells, DCs) at 0h and 24h — totals match ``nanowell_dataset.COUNTS["A01"]``.
CLASSES = {
    (0, 0): {"0h": (1, 0), "24h": (3, 0)},     # T ×3; no DC at either: DC fold change and ratio undefined
    (1, 0): {"0h": (1, 1), "24h": (0, 2)},     # count flat (2 → 2) while T falls to 0 and DC doubles
    (0, 1): {"0h": (0, 0), "24h": (0, 0)},     # empty throughout
    (-1, 1): {"0h": (2, 2), "24h": (3, 1)},    # ratio 1 → 3
    (-1, 0): {"0h": (0, 0), "24h": (5, 0)},    # empty at baseline, five T cells later
}


@pytest.fixture
def metrics(tmp_path):
    from review.features.nanowell.collect import load_nanowell_data
    from review.features.nanowell.series_compute import site_metrics
    from tests.fixtures.nanowell_dataset import COUNTS, SITES, build_nanowell_dataset

    root = build_nanowell_dataset(tmp_path / "nw")
    data = load_nanowell_data(root / "out")
    rows = []
    for (q, r), by_tp in CLASSES.items():
        for tp, (t, dc) in by_tp.items():
            assert t + dc == COUNTS["A01"][tp][SITES.index((q, r))]
            rows += [("A01", tp, q, r, T)] * t + [("A01", tp, q, r, DC)] * dc
    cells = pd.DataFrame(rows, columns=["well", "timepoint", "nw_q", "nw_r", "area_px"])
    gate = cg.tally([("A01", cells, np.ones(len(cells), dtype=bool))], sizes=RULE)
    frame = site_metrics(data, "earliest", gate=gate)
    later = frame[(frame["well"] == "A01") & (frame["timepoint"] == "24h")].set_index(["nw_q", "nw_r"])
    later.attrs["whole"] = frame                   # the reasons read each nanowell's baseline row
    return later


def _at(metrics, column, site):
    return metrics.loc[site, column]


def test_each_class_s_fold_change_is_its_own_count_over_its_baseline_count(metrics):
    assert _at(metrics, "size_1_fc", (0, 0)) == 3.0
    assert _at(metrics, "size_1_fc", (1, 0)) == 0.0               # T fell to none: a measured zero
    assert _at(metrics, "size_2_fc", (1, 0)) == 2.0
    assert _at(metrics, "count_fc", (1, 0)) == 1.0                # while the count itself did not move
    assert _at(metrics, "size_1_fc", (-1, 1)) == 1.5
    assert _at(metrics, "size_2_fc", (-1, 1)) == 0.5


def test_the_ratio_s_fold_change_is_the_ratio_at_t_over_the_ratio_at_baseline(metrics):
    assert _at(metrics, "size_ratio", (-1, 1)) == 3.0
    assert _at(metrics, "size_ratio_fc", (-1, 1)) == 3.0           # 3 / 1
    assert _at(metrics, "size_ratio_fc", (1, 0)) == 0.0            # 0 T / 2 DC at 24h over 1 / 1: a fall to zero


def test_undefined_fold_changes_say_why(metrics):
    from review.features.nanowell.series_compute import exclusion_reason

    whole = metrics.attrs["whole"]

    def why(column, site):
        rows = whole[(whole["well"] == "A01") & (whole["timepoint"] == "24h")
                     & (whole["nw_q"] == site[0]) & (whole["nw_r"] == site[1])]
        return exclusion_reason(whole, column).loc[rows.index[0]]

    assert np.isnan(_at(metrics, "size_2_fc", (0, 0)))
    assert why("size_2_fc", (0, 0)) == "size class empty at baseline"
    assert np.isnan(_at(metrics, "size_1_fc", (-1, 0)))
    assert why("size_1_fc", (-1, 0)) == "size class empty at baseline"
    assert np.isnan(_at(metrics, "size_ratio_fc", (0, 0)))
    assert why("size_ratio_fc", (0, 0)) == scc.SECOND_EMPTY        # no DC at 24h: the ratio itself is undefined


def test_the_page_offers_them_by_the_user_s_labels_and_greys_nothing_it_needs():
    from review.features.nanowell.distribution_compute import BASELINE_METRICS, metric_label
    from review.features.nanowell.state import METRIC_KEYS
    from review.features.nanowell.summary_compute import well_statistic

    for column in scc.FC_COLUMNS:
        assert column in METRIC_KEYS and column in BASELINE_METRICS
        assert well_statistic(column) == "mean"
    assert metric_label("size_1_fc", sizes=RULE) == "T cells count fold change"
    assert metric_label("size_ratio_fc", sizes=RULE) == "T cells : DCs ratio fold change"


def test_every_new_reason_has_a_figure_label_and_a_tag():
    from review.features.nanowell._array_key import reason_tag
    from review.features.nanowell.distribution_compute import reason_label

    for reason in ("size class empty at baseline", "size ratio zero at baseline"):
        assert reason_tag(reason_label(reason))


# ── the Population switch reaches the Nanowells count ─────────────────────────


def test_the_population_switch_leaves_the_nanowell_metrics_of_every_cell_and_the_class_metrics_answer_per_class(
        tmp_path, qtbot):
    """An earlier answer said that with Population on T cells, *Count fold change* would become the T cells'. Measured
    here, that design refused every size metric: the count had lost the DCs while the class counts had not, so the
    classes "exceeded the count". The nanowell metrics now ignore the Population layer, and *T cells count fold
    change* is the per-class answer. The real dataset loaded in a minimal shell, the rule and the population applied
    the way the panel and the band apply them, the tally read the way the page reads it. At (-1, 1) every cell
    counts 4 → 4 (fold change 1) while the T cells go 2 → 3 (1.5), so the two answers differ."""
    from dataclasses import replace

    from review.features.nanowell.collect import load_nanowell_data
    from review.features.nanowell.controller import gate_tally
    from review.features.nanowell.series_compute import site_metrics
    from review.features.qc.controller import get_qc_params
    from tests._minimal_shell import load_dataset, minimal_shell
    from tests._panel_jobs import apply_qc_params
    from tests.fixtures.nanowell_dataset import SITES, build_nanowell_dataset

    root = build_nanowell_dataset(tmp_path / "nw")
    table = root / "out" / "measurements_A01.csv"
    cells = pd.read_csv(table, dtype={"timepoint": str})
    for (q, r), by_tp in CLASSES.items():
        for tp, (t, dc) in by_tp.items():
            rows = cells.index[(cells["timepoint"] == tp) & (cells["nw_q"] == q) & (cells["nw_r"] == r)]
            cells.loc[rows, "area_px"] = [T] * t + [DC] * dc
    cells.to_csv(table, index=False)
    assert SITES                                                    # the fixture's roster, as CLASSES is keyed

    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, root / "out")
    apply_qc_params(qtbot, ctx, replace(get_qc_params(ctx), size_classes=RULE, population=0))

    data = load_nanowell_data(root / "out")
    frame = site_metrics(data, "earliest", gate=gate_tally(ctx, data))
    at = frame[(frame["well"] == "A01") & (frame["timepoint"] == "24h")].set_index(["nw_q", "nw_r"])
    assert at.loc[(-1, 1), "n"] == 4.0                              # every kept cell, whatever the population
    assert at.loc[(-1, 1), "count_fc"] == 1.0
    assert (at.loc[(-1, 1), "size_1"], at.loc[(-1, 1), "size_2"]) == (3.0, 1.0)
    assert at.loc[(-1, 1), "size_1_fc"] == 1.5                      # T 2 → 3: the per-class answer
    assert at.loc[(1, 0), "size_1_fc"] == 0.0                       # T 1 → 0
