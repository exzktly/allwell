"""Two labelled size classes, counted per nanowell over the cells Cell Gating keeps (``review.domain.size_classes``,
``review.features.nanowell.size_class_compute``).

Asked for: telling T cells from dendritic cells by size alone, with no marker imaged — two area ranges the user
labels, each nanowell's cells counted per range, and their ratio. The fixtures are built so the wrong answers differ
from the right ones (CLAUDE.md §7.8): a gated-out cell sits inside a class's range, so counting every row rather than
the kept ones gives a different count; another well's rows share the table; a cell sits exactly on a bound, so an
exclusive comparison drops it; and a cell sits in the gap between the ranges, so forcing it into a class changes a
count while dropping it silently changes the unclassified count.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from review.domain.qc_params import QCParams
from review.domain.size_classes import UNCLASSIFIED, SizeClassRule, classify, clipped_by_gate, diameter_text
from review.features.nanowell import cell_gate_compute as cg
from review.features.nanowell import size_class_compute as scc

RULE = SizeClassRule.of(("T cells", 80, 250), ("DCs", 350, 1200))


# ── the rule ──────────────────────────────────────────────────────────────────

def test_bounds_are_inclusive_and_a_cell_in_the_gap_is_in_neither_class():
    areas = [79, 80, 250, 251, 349, 350, 1200, 1201, np.nan]
    assert classify(areas, RULE).tolist() == [UNCLASSIFIED, 0, 0, UNCLASSIFIED, UNCLASSIFIED, 1, 1, UNCLASSIFIED,
                                              UNCLASSIFIED]


def test_float_noise_from_a_micron_area_does_not_push_a_cell_off_its_bound():
    # A nanowell run writes area_px as area_um2 / pixel_um**2: 250 px can read back as 250.00000000000003.
    pixel = 0.325
    area = (250 * pixel * pixel) / pixel ** 2
    assert classify([area, 250 + 1e-9], RULE).tolist() == [0, 0]


def test_an_open_bound_takes_everything_on_that_side():
    rule = SizeClassRule.of(("small", None, 100), ("large", 300, None))
    assert classify([1, 100, 200, 300, 1e6], rule).tolist() == [0, 0, UNCLASSIFIED, 1, 1]


@pytest.mark.parametrize("rows, said", [
    ((), "No size classes are set."),
    ((("T cells", 80, 250), ("", 350, 1200)), "Class 2 needs a label."),
    ((("T cells", 80, 250), ("DCs", None, None)), "DCs needs a minimum or a maximum area."),
    ((("T cells", 300, 250), ("DCs", 350, 1200)), "T cells's minimum (300 px) is above its maximum (250 px)."),
    ((("T cells", 80, 250), ("t CELLS", 350, 1200)), "The two classes need different labels."),
])
def test_an_incomplete_rule_says_what_is_missing_and_classifies_nothing(rows, said):
    rule = SizeClassRule.of(*rows)
    assert rule.problem() == said
    assert not rule.active() and rule.key() == ()
    assert classify([100, 400], rule).tolist() == [UNCLASSIFIED, UNCLASSIFIED]


def test_ranges_that_share_a_value_are_refused_because_a_cell_could_be_counted_twice():
    touching = SizeClassRule.of(("a", 80, 250), ("b", 250, 400))
    assert "overlap" in touching.problem()
    assert SizeClassRule.of(("a", 80, 250), ("b", 251, 400)).active()


def test_the_user_s_labels_replace_the_placeholder_names():
    assert RULE.name("Size class 1 : Size class 2 ratio") == "T cells : DCs ratio"
    assert SizeClassRule().name("Size class 1 count") == "Size class 1 count"     # nothing to name yet
    assert RULE.text() == "T cells 80–250 px, DCs 350–1200 px"


def test_the_rule_survives_a_save_and_a_reload_with_the_other_qc_parameters():
    params = QCParams(enabled=True, min_area=20.0, size_classes=RULE)
    again = QCParams.from_dict(params.to_dict())
    assert again.size_classes == RULE and again.min_area == 20.0
    assert QCParams.from_dict(QCParams().to_dict()).size_classes == SizeClassRule()
    assert QCParams().to_dict()["size_classes"] == []
    assert QCParams().with_updates(size_classes=RULE.to_list()).size_classes == RULE


def test_the_diameter_readout_is_the_equivalent_circle_at_the_run_s_pixel_size():
    # 250 px at 0.5 µm/px: area 62.5 µm², diameter 2·sqrt(62.5/π) = 8.92 µm.
    assert diameter_text(RULE.classes[0], 0.5) == f"≈ {2 * math.sqrt(80 / math.pi) * 0.5:.1f}–8.9 µm"
    assert diameter_text(RULE.classes[0], None) == ""


def test_a_debris_bound_inside_a_class_s_range_is_said():
    assert clipped_by_gate(RULE, 100.0, None).startswith("The debris gate keeps areas 100–— px, which cuts into "
                                                         "T cells")
    assert clipped_by_gate(RULE, 50.0, 2000.0) == ""


# ── counted per nanowell ──────────────────────────────────────────────────────

def _cells(rows):
    """Per-cell rows ``(well, timepoint, q, r, area_px)``."""
    return pd.DataFrame(rows, columns=["well", "timepoint", "nw_q", "nw_r", "area_px"])


CELLS = _cells([
    ("A01", "t1", 0, 0, 100), ("A01", "t1", 0, 0, 250), ("A01", "t1", 0, 0, 400),   # 2 T, 1 DC
    ("A01", "t1", 0, 0, 300),                                                        # in the gap
    ("A01", "t1", 0, 0, 120),                                                        # gated out (index 4)
    ("A01", "t1", 1, 0, 90),                                                         # 1 T, no DC
    ("A01", "t1", None, None, 100),                                                  # in no nanowell
    ("B01", "t1", 0, 0, 100),                                                        # another well's row
])
KEPT = np.array([True, True, True, True, False, True, True, True])


def _sites():
    """The site-metrics columns :func:`scc.apply` reads, A01 at t1: (0,0) the run counted 5 cells (one gated out
    below), (1,0) 1 cell, (2,0) empty (0 cells), (3,0) occluded (no count)."""
    return pd.DataFrame({"well": ["A01"] * 4, "timepoint": ["t1"] * 4, "nw_q": [0.0, 1.0, 2.0, 3.0],
                         "nw_r": [0.0] * 4, "n": [5.0, 1.0, 0.0, np.nan]})


def _applied(rule=RULE, unread=()):
    frame = _sites()
    gate = cg.tally([("A01", CELLS, KEPT)], unread=unread, sizes=rule)
    cg.apply(frame, gate, [])
    scc.apply(frame, gate)
    return frame


def test_each_nanowell_counts_its_kept_cells_per_class_and_the_rest_as_unclassified():
    frame = _applied()
    assert frame["n"].tolist()[:3] == [4.0, 1.0, 0.0]                 # (0,0) lost its gated-out cell
    assert frame["size_1"].tolist()[:3] == [2.0, 1.0, 0.0]           # not 3: the gated-out 120 px cell is out
    assert frame["size_2"].tolist()[:3] == [1.0, 0.0, 0.0]
    assert frame["size_unclassified"].tolist()[:3] == [1.0, 0.0, 0.0]  # the 300 px cell in the gap
    assert frame["size_ratio"].iloc[0] == 2.0
    assert frame["size_1_share"].iloc[0] == pytest.approx(2 / 3)


def test_an_empty_second_class_leaves_the_ratio_undefined_never_zero_or_infinite():
    frame = _applied()
    assert np.isnan(frame["size_ratio"].iloc[1])                       # 1 T cell, no DC
    assert frame["size_1_share"].iloc[1] == 1.0
    reasons = scc.exclusion_reason(frame, "size_ratio", pd.Series("", index=frame.index))
    assert reasons.iloc[1] == scc.SECOND_EMPTY


def test_an_empty_nanowell_counts_zero_but_has_no_ratio_or_share():
    frame = _applied()
    assert (frame["size_1"].iloc[2], frame["size_2"].iloc[2], frame["size_unclassified"].iloc[2]) == (0, 0, 0)
    assert np.isnan(frame["size_ratio"].iloc[2]) and np.isnan(frame["size_1_share"].iloc[2])
    share = scc.exclusion_reason(frame, "size_1_share", pd.Series("", index=frame.index))
    assert share.iloc[2] == scc.NONE_CLASSIFIED


def test_a_nanowell_with_no_count_has_no_classes():
    frame = _applied()
    assert frame.loc[3, list(scc.COLUMNS)].isna().all()


def test_with_no_rule_every_size_metric_is_undefined_and_says_why():
    frame = _applied(rule=SizeClassRule())
    assert frame[list(scc.COLUMNS)].isna().all().all()
    why = scc.exclusion_reason(frame, "size_1", pd.Series("", index=frame.index))
    assert why.tolist()[:3] == [scc.NOT_SET] * 3


def test_a_well_whose_cells_were_not_read_keeps_its_count_but_gets_no_classes():
    frame = _sites()
    gate = cg.tally([], unread=["A01"], sizes=RULE)
    scc.apply(frame, gate)
    assert frame["size_1"].isna().all()
    why = scc.exclusion_reason(frame, "size_1", pd.Series("", index=frame.index))
    assert why.iloc[0] == scc.NOT_READ


def test_classes_adding_up_to_more_than_the_count_are_refused_not_written_as_a_negative():
    frame = _sites()
    frame.loc[0, "n"] = 1.0                                           # the run counted 1; the table holds 4 kept
    gate = cg.tally([("A01", CELLS, KEPT)], sizes=RULE)
    scc.apply(frame, gate)
    assert frame.loc[0, list(scc.COLUMNS)].isna().all()
    why = scc.exclusion_reason(frame, "size_unclassified", pd.Series("", index=frame.index))
    assert why.iloc[0] == scc.MISMATCH


def test_every_reason_the_classes_give_has_a_figure_label():
    from review.features.nanowell.distribution_compute import reason_label

    for reason in scc.REASONS:
        assert reason_label(reason)


def test_the_note_names_the_classes_and_counts_the_cells_in_neither():
    frame = _applied()
    said = scc.note(frame, RULE, "size_ratio")
    assert said.startswith("Size classes (Cell Gating, cells it keeps): T cells 80–250 px, DCs 350–1200 px.")
    assert "Of 5 cell-timepoints counted here, 1 (20%) fall in neither class" in said
    assert scc.note(frame, RULE, "n") == ""


# ── what the page's metrics were counted for ──────────────────────────────────

def test_changing_only_the_size_classes_makes_the_page_s_counts_stale():
    """The page and the plot tools' memo reuse a tally while ``gate_version`` is unchanged. The per-class counts
    are an output of the tally, so the rule must be in that key in its own right — not only through the population
    version a QC edit happens to move as well."""
    from types import SimpleNamespace

    from review.features.nanowell import controller as nwc

    params = {"now": QCParams(size_classes=RULE)}
    ctx = SimpleNamespace(state=SimpleNamespace(stats_cache=SimpleNamespace(population_version=7)),
                          features=SimpleNamespace(query=lambda name, _ctx, default=None:
                                                   params["now"] if name == "qc.get_params" else default))
    before = nwc.gate_version(ctx)
    params["now"] = QCParams(size_classes=SizeClassRule.of(("T cells", 80, 260), ("DCs", 350, 1200)))
    assert nwc.gate_version(ctx) != before
    params["now"] = QCParams(size_classes=RULE)
    assert nwc.gate_version(ctx) == before
    assert nwc.size_rule(ctx) == RULE


# ── each well's value is the mean over its nanowells ─────────────────────────

def _well_rows():
    """Three nanowells of A01 at t1 with 0, 0 and 3 cells in the first class: mean 1, median 0."""
    return pd.DataFrame({"well": ["A01"] * 3, "timepoint": ["t1"] * 3, "time_h": [0.0] * 3, "tp_order": [0] * 3,
                         "nw_q": [0.0, 1.0, 2.0], "nw_r": [0.0] * 3, "state": ["measured"] * 3, "n": [1.0, 2.0, 3.0],
                         "size_1": [0.0, 0.0, 3.0], scc.REASON_COLUMN: [""] * 3})


def test_a_well_s_size_value_is_the_mean_of_its_nanowells_and_says_so():
    from review.features.nanowell.summary_compute import well_summary

    size = well_summary(_well_rows(), "size_1").iloc[0]
    assert (size["median"], size["statistic"]) == (1.0, "mean")          # the median would be 0
    count = well_summary(_well_rows(), "n").iloc[0]
    assert (count["median"], count["statistic"]) == (2.0, "median")      # other metrics keep the median


def test_the_plot_tools_take_the_mean_for_a_size_metric():
    from review.domain.observation import NANOWELLS, ObservationMetric, ObservationTable, well_medians

    frame = _well_rows().assign(timepoint_hours=0.0)
    specs = (ObservationMetric("size_1", "T cells count", "value", well_statistic="mean"),
             ObservationMetric("n", "Cell count", "value"))
    table = ObservationTable(NANOWELLS, frame, specs)
    assert well_medians(table, "size_1", ["A01"])["median"].tolist() == [1.0]
    assert well_medians(table, "n", ["A01"])["median"].tolist() == [2.0]


def test_the_plot_tools_offer_the_size_metrics_by_the_user_s_labels_with_a_mean_per_well():
    from review.features.nanowell.observation_compute import _metric_specs

    specs = {m.key: m for m in _metric_specs(["GFP"], sizes=RULE)}
    assert specs["size_ratio"].label == "T cells : DCs ratio"
    assert specs["size_1"].well_statistic == "mean" and specs["n"].well_statistic == "median"


def test_the_statistics_view_compares_groups_on_each_well_s_mean():
    from types import SimpleNamespace

    from review.features.nanowell import statistics_compute as stc

    swarm = SimpleNamespace(well="A01", values=np.array([0.0, 0.0, 3.0]), n_roster=3, excluded={})
    model = SimpleNamespace(swarms=lambda: [swarm], column="size_1")
    sample = stc.samples_from_model(model, {"g": ["A01"]}, stc.WELLS)[0]
    assert sample.values.tolist() == [1.0]
    assert stc.unit_sentence(stc.WELLS, "mean").startswith("Values: one mean per well")


def test_a_subset_condition_on_a_size_metric_is_written_with_the_user_s_label():
    from review.features.nanowell import controller as nwc
    from review.features.nanowell import subset_compute as ss

    condition = ss.Condition("size_1", "ge", 2.0)
    assert "Size class 1 count" in condition.text()                    # the condition itself is generic
    assert nwc._named(ss.Subset((condition,)).label(), RULE).startswith("Subset: T cells count")
    assert nwc._named("", RULE) == "" and nwc._named("Size class 1", None) == "Size class 1"


# ── shaded on Cell Gating's cell-area curve ───────────────────────────────────

def _cell_gating_ctx(rule):
    from types import SimpleNamespace

    from matplotlib.figure import Figure

    runtime = SimpleNamespace(cell_gating=SimpleNamespace(
        figure=Figure(), canvas=SimpleNamespace(draw_idle=lambda: None, setMinimumHeight=lambda _h: None), axes_stack=[], cell_areas=[60.0, 100.0, 200.0, 400.0, 900.0],
        fluor_data={}, ratio_data={}))
    state = SimpleNamespace(qc=SimpleNamespace(params=QCParams(size_classes=rule)),
                            cell_gating=SimpleNamespace(fluor_gates={}, thresh_frac_on={}),
                            channel=SimpleNamespace(seg_channel_token=""), dataset=SimpleNamespace(pipeline_info={}))
    return SimpleNamespace(runtime=runtime, state=state)


def test_each_class_is_shaded_and_labelled_on_the_cell_area_curve():
    pytest.importorskip("matplotlib")
    from review.features.home.cell_gating.controller import cell_gating_replot_cdfs

    ctx = _cell_gating_ctx(RULE)
    cell_gating_replot_cdfs(ctx)
    ax = ctx.runtime.cell_gating.figure.axes[0]
    spans = {p.get_gid(): p for p in ax.patches if str(p.get_gid()).startswith("cg-size-class-")}
    assert set(spans) == {"cg-size-class-0", "cg-size-class-1"}
    first = spans["cg-size-class-0"].get_window_extent().transformed(ax.transData.inverted())
    assert (round(first.x0, 6), round(first.x1, 6)) == (80.0, 250.0)
    labels = [t.get_text() for t in ax.texts if str(t.get_gid()).startswith("cg-size-class-label-")]
    assert labels == ["T cells", "DCs"]

    blank = _cell_gating_ctx(SizeClassRule())
    cell_gating_replot_cdfs(blank)
    assert not [p for p in blank.runtime.cell_gating.figure.axes[0].patches
                if str(p.get_gid()).startswith("cg-size-class-")]
