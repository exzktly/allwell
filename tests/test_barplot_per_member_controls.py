"""Four bars, four different controls — and what happens when one of them fails.

This is the feature the branch exists for: a plate carrying several conditions,
each with its own paired control, plotted on one fold-change axis. Everything
here asserts the **collected bar** rather than a helper feeding it, because the
last round's regression was invisible to a suite that only ever asked the
resolver (CLAUDE.md §7.4 #1).

Two fixes ride along and are pinned here because both were silent:

* one missing denominator used to NaN **every** bar, since the six control
  values were hoisted once per timepoint and shared. A control that is gone
  should cost its own members and nothing else;
* the force-NaN flag only ever tested the *mean* denominator, so a control with
  a mean but no fraction series left the Fraction panel drawing a **raw** value
  under a fold-change label. Each panel drops on its own denominator now.
"""
from __future__ import annotations

import math

import pytest

from well_viewer.domain.control_ref import ControlRef
from well_viewer.domain.fold_change_pairing import ControlOutcome
from well_viewer.features.barplot.collect import collect_bar_items

from tests.conftest import MockApp, make_agg


#: Deliberately unequal per-well values, so a mean is not a round number and a
#: bar divided by the wrong control cannot land on the right answer by luck.
_VALUES = {
    "A01": 100.0, "A02": 120.0,     # CTRL  → mean 110
    "B01": 210.0, "B02": 230.0,     # DRUG  → mean 220
    "C01": 50.0,                    # CTRL2 → mean 50
    "D01": 150.0, "D02": 170.0,     # DRUG2 → mean 160
}
_SETS = [
    {"name": "CTRL", "wells": ["A01", "A02"], "hidden": False},
    {"name": "DRUG", "wells": ["B01", "B02"], "hidden": False},
    {"name": "CTRL2", "wells": ["C01"], "hidden": False},
    {"name": "DRUG2", "wells": ["D01", "D02"], "hidden": False},
]
_SET = ControlRef.for_replicate_set


@pytest.fixture
def ctx():
    app = MockApp(
        per_well_pts={w: [make_agg(0.0, v)] for w, v in _VALUES.items()},
        selections=_SETS,
    )
    c = app._ctx
    c.state.plot.fc_vs_control_on = True
    c.state.plot.fc_control_mode = "per_member"
    return c


def _bars(ctx, mapping, *, tp=0.0, render=None):
    ctx.state.plot.fc_control_map = dict(mapping)
    if render is not None:
        ctx.state.plot.fc_control_render = render
    _use_groups, items, _band = collect_bar_items(ctx, tp)
    return {it.key: it for it in items}


# ── the feature ──────────────────────────────────────────────────────────────

def test_each_member_is_divided_by_its_own_control(ctx):
    bars = _bars(ctx, {"CTRL": _SET("CTRL"), "DRUG": _SET("CTRL"),
                       "CTRL2": _SET("CTRL2"), "DRUG2": _SET("CTRL2")})
    assert bars["DRUG"].mean == pytest.approx(220.0 / 110.0)
    assert bars["DRUG2"].mean == pytest.approx(160.0 / 50.0)
    assert bars["DRUG"].mean != pytest.approx(bars["DRUG2"].mean), (
        "both ratios computed against one control would be the giveaway"
    )


def test_a_member_carries_the_control_it_was_divided_by(ctx):
    """The x-tick's second line, the CSV column and the annotations read this
    rather than each re-deriving the decision."""
    bars = _bars(ctx, {"DRUG": _SET("CTRL"), "DRUG2": _SET("CTRL2")})
    assert bars["DRUG"].fc_control == _SET("CTRL")
    assert bars["DRUG2"].fc_control == _SET("CTRL2")


def test_a_member_paired_with_itself_lands_on_one(ctx):
    bars = _bars(ctx, {"CTRL": _SET("CTRL")})
    assert bars["CTRL"].mean == pytest.approx(1.0)
    assert bars["CTRL"].fc_outcome == ControlOutcome.SELF


def test_a_self_normalized_member_keeps_an_error_bar(ctx):
    """A zero error bar would claim infinite precision. Propagating the
    calibrator's own error is the ΔΔCt convention."""
    bars = _bars(ctx, {"CTRL": _SET("CTRL")})
    assert bars["CTRL"].spread > 0.0


def test_subtract_puts_a_self_normalized_member_on_zero(ctx):
    ctx.state.plot.fc_op_ctrl = "subtract"
    bars = _bars(ctx, {"CTRL": _SET("CTRL"), "DRUG": _SET("CTRL")})
    assert bars["CTRL"].mean == pytest.approx(0.0)
    assert bars["DRUG"].mean == pytest.approx(220.0 - 110.0)


# ── partial failure costs only the members it belongs to ─────────────────────

def test_a_missing_control_drops_only_its_own_member(ctx):
    """The blast-radius regression: one bad denominator used to NaN all four."""
    bars = _bars(ctx, {"CTRL": _SET("CTRL"), "DRUG": _SET("CTRL"),
                       "CTRL2": _SET("CTRL2"), "DRUG2": _SET("DELETED")})
    assert math.isnan(bars["DRUG2"].mean)
    assert bars["DRUG"].mean == pytest.approx(2.0)
    assert bars["CTRL2"].mean == pytest.approx(1.0)


def test_an_unmapped_member_is_a_placeholder_not_a_raw_value(ctx):
    """Raw is the one outcome that must never happen: it is a number on a
    fold-change axis that is not a fold change, and nothing on screen says so."""
    bars = _bars(ctx, {"DRUG": _SET("CTRL")})
    assert math.isnan(bars["DRUG2"].mean)
    assert bars["DRUG2"].has_mean is False
    assert bars["DRUG2"].mean != pytest.approx(160.0)


def test_the_three_failures_are_told_apart(ctx):
    """All three produce the same empty bar; only the outcome distinguishes
    them, and the user needs a different sentence for each."""
    bars = _bars(ctx, {"DRUG": _SET("DELETED"), "DRUG2": _SET("CTRL")},
                 tp=99.0)
    assert bars["CTRL"].fc_outcome == ControlOutcome.UNMAPPED
    assert bars["DRUG"].fc_outcome == ControlOutcome.CONTROL_UNRESOLVED
    assert bars["DRUG2"].fc_outcome == ControlOutcome.CONTROL_NO_SAMPLE


def test_the_outcome_is_a_plain_value_not_a_repr(ctx):
    """It reaches a CSV cell and a figure annotation."""
    bars = _bars(ctx, {"DRUG": _SET("CTRL")})
    assert bars["DRUG"].fc_outcome == "ok"
    assert "ControlOutcome" not in str(bars["DRUG"].fc_outcome)


def test_the_control_axis_being_off_leaves_no_outcome(ctx):
    ctx.state.plot.fc_vs_control_on = False
    bars = _bars(ctx, {"DRUG": _SET("CTRL")})
    assert bars["DRUG"].fc_outcome == ""
    assert bars["DRUG"].fc_control is None
    assert bars["DRUG"].mean == pytest.approx(220.0)


# ── each panel drops on its own denominator ──────────────────────────────────

def test_a_control_with_no_data_at_the_timepoint_nans_every_panel(ctx):
    bars = _bars(ctx, {"DRUG": _SET("CTRL")}, tp=99.0)
    rec = bars["DRUG"]
    assert math.isnan(rec.mean) and math.isnan(rec.frac) and math.isnan(rec.n_above)


def test_the_fraction_panel_does_not_draw_a_raw_value_under_a_fc_label():
    """A control whose fraction is undefined while its mean is fine. The single
    force-NaN flag tested only the mean, so this panel kept its raw number."""
    app = MockApp(
        per_well_pts={
            # frac (index 3) is NaN for the control, finite for the sample.
            "A01": [make_agg(0.0, 100.0, frac=float("nan"))],
            "B01": [make_agg(0.0, 200.0, frac=0.5)],
        },
        selections=[{"name": "CTRL", "wells": ["A01"], "hidden": False},
                    {"name": "DRUG", "wells": ["B01"], "hidden": False}],
    )
    c = app._ctx
    c.state.plot.fc_vs_control_on = True
    c.state.plot.fc_control_mode = "per_member"
    bars = _bars(c, {"DRUG": _SET("CTRL")})
    assert math.isnan(bars["DRUG"].frac), (
        f"drew {bars['DRUG'].frac} — 0.5 is the raw fraction, so a raw value "
        "on a fold-change-labelled panel reads exactly like this"
    )
    assert bars["DRUG"].mean == pytest.approx(2.0), "the mean panel is fine"


# ── the render toggle ────────────────────────────────────────────────────────

def test_hidden_removes_a_pure_control_from_the_plot(ctx):
    bars = _bars(ctx, {"CTRL": _SET("CTRL"), "DRUG": _SET("CTRL")},
                 render="hidden")
    assert "CTRL" not in bars
    assert "DRUG" in bars


def test_hidden_keeps_a_member_that_normalizes_against_something_else(ctx):
    """It carries its own normalization, so it is a sample, not chrome."""
    bars = _bars(ctx, {"CTRL": _SET("CTRL2"), "DRUG": _SET("CTRL")},
                 render="hidden")
    assert "CTRL" in bars


def test_self_render_keeps_everything(ctx):
    bars = _bars(ctx, {"CTRL": _SET("CTRL"), "DRUG": _SET("CTRL")},
                 render="self")
    assert set(bars) == {"CTRL", "DRUG", "CTRL2", "DRUG2"}


# ── per-well mode ────────────────────────────────────────────────────────────

def test_the_mapping_follows_the_key_space_when_pooling_is_off(ctx):
    ctx.state.plot.bar_pool_replicates = False
    bars = _bars(ctx, {"A01": ControlRef.for_well("A01"),
                       "B01": ControlRef.for_well("A01")})
    assert bars["B01"].mean == pytest.approx(210.0 / 100.0)
    assert bars["A01"].mean == pytest.approx(1.0)


def test_a_well_inherits_its_sets_control_when_pooling_is_off(ctx):
    """The mapping was written against the sets; a toggle with nothing to do
    with fold change must not orphan it."""
    ctx.state.plot.bar_pool_replicates = False
    bars = _bars(ctx, {"DRUG": _SET("CTRL")})
    assert bars["B01"].mean == pytest.approx(210.0 / 110.0)
    assert bars["B02"].mean == pytest.approx(230.0 / 110.0)


def test_an_explicit_per_well_entry_beats_the_inherited_one(ctx):
    ctx.state.plot.bar_pool_replicates = False
    bars = _bars(ctx, {"DRUG": _SET("CTRL"),
                       "B01": _SET("CTRL2")})
    assert bars["B01"].mean == pytest.approx(210.0 / 50.0)
    assert bars["B02"].mean == pytest.approx(230.0 / 110.0)


# ── single mode is unchanged, except where it was wrong ──────────────────────

def test_single_mode_still_divides_everything_by_one_control(ctx):
    ctx.state.plot.fc_control_mode = "single"
    ctx.state.plot.fc_control = _SET("CTRL")
    _ug, items, _b = collect_bar_items(ctx, 0.0)
    bars = {it.key: it for it in items}
    assert bars["DRUG"].mean == pytest.approx(220.0 / 110.0)
    assert bars["DRUG2"].mean == pytest.approx(160.0 / 110.0)
    assert all(b.fc_control == _SET("CTRL") for b in bars.values())


def test_set_all_to_one_control_matches_single_mode(ctx):
    """``Set all to DMSO`` is mathematically identical to today's single mode,
    and the axis label branch below depends on that staying true."""
    per_member = _bars(ctx, {k: _SET("CTRL")
                             for k in ("CTRL", "DRUG", "CTRL2", "DRUG2")})
    ctx.state.plot.fc_control_mode = "single"
    ctx.state.plot.fc_control = _SET("CTRL")
    _ug, items, _b = collect_bar_items(ctx, 0.0)
    single = {it.key: it for it in items}
    for key in single:
        assert per_member[key].mean == pytest.approx(single[key].mean)


def test_the_control_axis_on_with_nothing_named_is_a_placeholder_not_raw(ctx):
    """Restorable from a saved document, and it used to draw raw values under a
    fold-change axis: the force-NaN flag ANDed with the control, so an absent
    one made it falsy and ``_apply_axis`` returned its input unchanged."""
    ctx.state.plot.fc_control_mode = "single"
    ctx.state.plot.fc_control = None
    _ug, items, _b = collect_bar_items(ctx, 0.0)
    for it in items:
        assert math.isnan(it.mean), f"{it.key} drew {it.mean}"
        assert it.fc_outcome == ControlOutcome.CONTROL_UNRESOLVED
