"""What the figure, the CSV and the status line say about per-sample controls.

A plot whose bars were divided by four different denominators and whose axis
names one of them is a figure that misstates itself, and a CSV whose
``fold_change_control`` column repeats one value down every row misstates the
same thing to whatever reads it next. Neither is visible from the numbers:
every bar has a value, every cell is filled, and both are wrong about *why*.

So the three reporting surfaces are pinned here — the axis suffix, the row's
own control and outcome, and the coalesced status line.
"""
from __future__ import annotations

import pytest

from review.domain.control_ref import ControlRef
from review.domain.fold_change import (
    control_axis_phrase,
    fold_change_suffix,
    fold_change_suffix_for,
)
from review.domain.fold_change_pairing import (
    ControlOutcome,
    build_per_member_controls,
    shortfall_from_items,
)
from review.features.barplot.collect import collect_bar_items

from tests.conftest import MockApp, make_agg


_SET = ControlRef.for_replicate_set
_SETS = [
    {"name": "CTRL", "wells": ["A01"], "hidden": False},
    {"name": "DRUG", "wells": ["B01"], "hidden": False},
    {"name": "CTRL2", "wells": ["C01"], "hidden": False},
    {"name": "DRUG2", "wells": ["D01"], "hidden": False},
]


@pytest.fixture
def ctx():
    app = MockApp(
        per_well_pts={w: [make_agg(0.0, v)] for w, v in
                      (("A01", 100.0), ("B01", 200.0),
                       ("C01", 50.0), ("D01", 150.0))},
        selections=_SETS,
    )
    c = app._ctx
    c.state.plot.fc_vs_control_on = True
    c.state.plot.fc_control_mode = "per_member"
    return c


def _controls(ctx, mapping):
    from review.domain.plot_members import member_wells, plotted_member_keys
    return build_per_member_controls(
        ctx, plotted_member_keys(ctx, "bar"), mapping,
        member_wells=member_wells(ctx, "bar"))


# ── the axis label ───────────────────────────────────────────────────────────

def test_one_control_reads_exactly_as_the_single_control_title(ctx):
    """``Set all to DMSO`` is mathematically identical to single-control mode,
    so it has to read identically too."""
    controls = _controls(ctx, {k: _SET("CTRL")
                               for k in ("CTRL", "DRUG", "CTRL2", "DRUG2")})
    assert control_axis_phrase(controls=controls) == control_axis_phrase("CTRL")


def test_two_controls_are_both_named(ctx):
    controls = _controls(ctx, {"CTRL": _SET("CTRL"), "DRUG": _SET("CTRL"),
                               "CTRL2": _SET("CTRL2"), "DRUG2": _SET("CTRL2")})
    assert control_axis_phrase(controls=controls) == "vs CTRL / CTRL2"


def test_three_or_more_collapse(ctx):
    controls = _controls(ctx, {"CTRL": _SET("CTRL"), "DRUG": _SET("CTRL2"),
                               "CTRL2": _SET("DRUG"), "DRUG2": _SET("DRUG2")})
    assert control_axis_phrase(controls=controls) == "vs own control"


def test_nothing_resolved_says_so_rather_than_claiming_a_control(ctx):
    """"vs control" here would describe a normalization that did not happen."""
    controls = _controls(ctx, {"DRUG": _SET("DELETED")})
    assert control_axis_phrase(controls=controls) == "vs own control (none resolved)"


def test_the_suffix_keeps_its_positional_signature():
    """``base_panel`` calls it positionally, so ``controls`` is keyword-only."""
    got = fold_change_suffix(True, True, "DMSO", "ratio", "subtract", "t=0h")
    assert got == "  (fold change vs DMSO (ratio), vs t=0h (subtraction))"


def test_the_suffix_names_the_operator_per_axis(ctx):
    controls = _controls(ctx, {"CTRL": _SET("CTRL"), "DRUG": _SET("CTRL2")})
    got = fold_change_suffix(True, False, "", "subtract", controls=controls)
    assert got == "  (fold change vs CTRL / CTRL2 (subtraction))"


def test_the_live_suffix_counts_the_tabs_own_members(ctx):
    ctx.state.plot.fc_control_map = {"CTRL": _SET("CTRL"), "DRUG": _SET("CTRL"),
                                     "CTRL2": _SET("CTRL2"),
                                     "DRUG2": _SET("CTRL2")}
    assert "vs CTRL / CTRL2" in fold_change_suffix_for(ctx, "bar")


def test_a_caller_that_names_no_scope_gets_the_single_control_reading(ctx):
    """It cannot know which member list to count, so it must not guess."""
    ctx.state.plot.fc_control_map = {"CTRL": _SET("CTRL"), "DRUG": _SET("CTRL2")}
    ctx.state.plot.fc_control = _SET("CTRL")
    assert "vs CTRL (" in fold_change_suffix_for(ctx)


def test_the_suffix_is_empty_when_nothing_is_normalized(ctx):
    ctx.state.plot.fc_vs_control_on = False
    assert fold_change_suffix_for(ctx, "bar") == ""


# ── the status line ──────────────────────────────────────────────────────────

def test_the_status_line_coalesces_by_reason(ctx):
    ctx.state.plot.fc_control_map = {"CTRL": _SET("CTRL"),
                                     "DRUG": _SET("DELETED"),
                                     "CTRL2": _SET("ALSO-GONE")}
    _ug, items, _b = collect_bar_items(ctx, 0.0)
    text = shortfall_from_items(items)
    assert "1 with no control" in text
    assert "2 whose control is missing" in text


def test_the_status_line_names_the_timepoint_when_that_is_the_problem(ctx):
    ctx.state.plot.fc_control_map = {"DRUG": _SET("CTRL")}
    misses: set = set()
    _ug, items, _b = collect_bar_items(ctx, 99.0, miss_sink=misses)
    text = shortfall_from_items(items, timepoints=misses)
    assert "no data at t=99" in text


def test_a_complete_mapping_says_nothing(ctx):
    ctx.state.plot.fc_control_map = {k: _SET("CTRL")
                                     for k in ("CTRL", "DRUG", "CTRL2", "DRUG2")}
    _ug, items, _b = collect_bar_items(ctx, 0.0)
    assert shortfall_from_items(items) == ""


def test_the_control_axis_being_off_says_nothing(ctx):
    ctx.state.plot.fc_vs_control_on = False
    _ug, items, _b = collect_bar_items(ctx, 0.0)
    assert shortfall_from_items(items) == ""


# ── the CSV row ──────────────────────────────────────────────────────────────

def test_each_row_carries_its_own_control():
    from review.features.export.service import (
        attach_bar_fold_change_columns,
        bar_fold_change_fieldnames,
    )

    rows = []
    for control, outcome in (("DMSO", "ok"), ("Vehicle", "ok")):
        row: dict = {}
        attach_bar_fold_change_columns(
            row, fc_mean=2.0, fc_spread=0.1, fc_has=True, band_lbl="SEM",
            vs_control=True, vs_t0=False, control_label=control,
            outcome=outcome)
        rows.append(row)
    assert [r["fold_change_control"] for r in rows] == ["DMSO", "Vehicle"]
    assert "fold_change_outcome" in bar_fold_change_fieldnames("SEM")


def test_the_outcome_column_tells_the_three_failures_apart():
    """All three leave ``fold_change_mean`` empty. A reader with only that
    column cannot tell a normalization that failed from one never asked for."""
    from review.features.export.service import attach_bar_fold_change_columns

    seen = []
    for outcome in (ControlOutcome.UNMAPPED, ControlOutcome.CONTROL_UNRESOLVED,
                    ControlOutcome.CONTROL_NO_SAMPLE):
        row: dict = {}
        attach_bar_fold_change_columns(
            row, fc_mean=float("nan"), fc_spread=0.0, fc_has=False,
            band_lbl="SEM", vs_control=True, vs_t0=False,
            control_label="", outcome=outcome)
        assert row["fold_change_mean"] == ""
        seen.append(row["fold_change_outcome"])
    assert seen == ["unmapped", "control_unresolved", "control_no_sample"]
    assert not any("ControlOutcome" in s for s in seen), "a leaked repr"


def test_the_control_cell_never_carries_the_combo_suffix():
    """It was part of the stored value once, and it leaked into exactly here."""
    from review.features.export.service import _control_cell

    assert _control_cell(ControlRef.for_well("A01")) == "A01"
    assert _control_cell(None) == ""


def test_the_columns_are_blank_when_the_control_axis_is_off():
    from review.features.export.service import attach_bar_fold_change_columns

    row: dict = {}
    attach_bar_fold_change_columns(
        row, fc_mean=2.0, fc_spread=0.1, fc_has=True, band_lbl="SEM",
        vs_control=False, vs_t0=True, control_label="DMSO", outcome="ok")
    assert row["fold_change_control"] == ""
    assert row["fold_change_outcome"] == ""


def test_the_batch_row_writer_matches_the_on_tab_one():
    """Two writers answering the same question. Converting one and not the
    other lands the half-sweep in the artifact users analyse."""
    from review.features.batch_export.base_panel import _attach_fc_row
    from review.features.export.service import attach_bar_fold_change_columns

    batch: dict = {}
    _attach_fc_row(batch, (0.0, 2.0, 0.1), "SEM", True, False, "DMSO", "ok")
    tab: dict = {}
    attach_bar_fold_change_columns(
        tab, fc_mean=2.0, fc_spread=0.1, fc_has=True, band_lbl="SEM",
        vs_control=True, vs_t0=False, control_label="DMSO", outcome="ok")
    assert set(batch) == set(tab)
    assert batch["fold_change_control"] == tab["fold_change_control"]
    assert batch["fold_change_outcome"] == tab["fold_change_outcome"]


# ── the saved-plot summary ───────────────────────────────────────────────────

def test_a_saved_summary_names_one_control():
    from review.features.saved_plots.collect import _fc_summary

    assert _fc_summary({"vs_control_on": True, "control_mode": "per_member",
                        "control_map": {"A": {"kind": "set", "name": "DMSO"},
                                        "B": {"kind": "set", "name": "DMSO"}},
                        "op_ctrl": "ratio"}) == "vs DMSO (ratio)"


def test_a_saved_summary_counts_rather_than_claiming_a_resolution():
    """It has no ``ctx``, so it cannot know what resolves — and the document
    may have been saved against a dataset this session never loaded."""
    from review.features.saved_plots.collect import _fc_summary

    got = _fc_summary({
        "vs_control_on": True, "control_mode": "per_member", "op_ctrl": "ratio",
        "control_map": {c: {"kind": "set", "name": c} for c in "ABCD"},
    })
    assert got == "vs own control (×4) (ratio)"


def test_a_saved_summary_of_a_single_control_is_unchanged():
    from review.features.saved_plots.collect import _fc_summary

    assert _fc_summary({"vs_control_on": True, "control_label": "DMSO",
                        "op_ctrl": "ratio"}) == "vs DMSO (ratio)"


def test_a_saved_summary_of_an_empty_mapping_says_so():
    from review.features.saved_plots.collect import _fc_summary

    assert _fc_summary({"vs_control_on": True, "control_mode": "per_member",
                        "control_map": {}, "op_ctrl": "ratio"}) \
        == "vs own control (none set) (ratio)"
