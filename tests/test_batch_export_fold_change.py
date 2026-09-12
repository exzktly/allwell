"""Fold change on the two Batch Export paths, which the fast lane could not see.

Both defects here were invisible to several thousand green tests, for the same
reason: the batch render and collection paths are reached from `slow`-marked
tests, so nothing in the fast lane exercised them. These do, without building
the app.

**The blank curves.** ``_app_group_scope`` does not add a batch group to the
app's selections — it *replaces* them with that group's members. A control
naming a replicate set in another group is then not in the list ``resolve_control``
scans, so it resolves to nothing and every curve in the group draws blank. One
batch group per condition, with the control as its own group, is the normal
layout, so this is the ordinary case rather than an edge.

**The raw Events panel.** The batch bar collector normalized Mean and Fraction
and left Events alone. It was left alone because a measurement said the
numerator and the denominator were different statistics; the measurement came
from a mock that discarded the per-well counts the fixture had set. In
production ``aggregate_group`` hands a one-well list straight to
``aggregate_well``, and a replicate set reads ``compute_rep_n_above_stats`` on
both sides — the same function. The fixture is fixed in ``conftest``; these
assert the numbers by hand rather than by calling the helper the code calls.
"""
from __future__ import annotations

import math
import types

import pytest

from review.domain.batch_models import BarGroup, ReplicateSet
from review.domain.control_ref import ControlRef
from review.domain.fold_change import fold_change_state
from review.domain.selections_model import active_replicate_sets
from review.features.barplot.collect import (
    collect_bar_items,
    collect_bar_items_for_group,
)
from review.features.batch_export._line_series_runner import (
    collect_line_series_for_group,
)
from review.features.batch_export.base_panel import BatchExportPanel

from tests.conftest import MockApp, make_agg

_SET = ControlRef.for_replicate_set

#: Unequal per-well counts on purpose, so every replicate mean is fractional and
#: a truncation cannot pass as a rounding. CTRL events mean 5.5, DRUG 11.0 — a
#: ratio of exactly 2.0, which no other pairing in this fixture produces.
_WELLS = {
    "A01": (100.0, 4), "A02": (120.0, 7),      # CTRL: mean 110, events 5.5
    "B01": (210.0, 9), "B02": (230.0, 13),     # DRUG: mean 220, events 11.0
}
_SELECTIONS = [
    {"name": "CTRL", "wells": ["A01", "A02"], "hidden": False},
    {"name": "DRUG", "wells": ["B01", "B02"], "hidden": False},
]


@pytest.fixture
def ctx():
    app = MockApp(
        per_well_pts={w: [make_agg(0.0, v, n_above=n)]
                      for w, (v, n) in _WELLS.items()},
        selections=list(_SELECTIONS),
    )
    c = app._ctx
    c.state.plot.fc_vs_control_on = True
    c.state.plot.fc_control_mode = "per_member"
    c.state.plot.fc_control_map = {"DRUG": _SET("CTRL"), "CTRL": _SET("CTRL")}
    return c


def _panel(ctx, control=None, control_map=None):
    """A stand-in carrying the three attributes ``_app_group_scope`` reads.

    Building a real panel needs Qt and a window; the scope needs the context,
    the control and the mapping, so those are what it gets.

    ``_fold_change_control_refs`` comes off the real class rather than being
    written out here, because *which* controls the scope carries is now a
    decision a subclass can make — Response mode reads the live shared state
    where every other mode reads this panel's snapshot. A stub that answered it
    for itself would go on passing while the two modes disagreed.
    """
    stub = types.SimpleNamespace(
        _ctx=ctx, _fc_control=control, _fc_control_map=control_map or {})
    for name in ("_control_selections", "_fold_change_control_refs"):
        setattr(stub, name,
                types.MethodType(getattr(BatchExportPanel, name), stub))
    return stub


def _treat_group():
    return BarGroup(name="G", members=[ReplicateSet("DRUG", ["B01", "B02"])],
                    solo_wells=[])


def _both_group():
    return BarGroup(
        name="G",
        members=[ReplicateSet("CTRL", ["A01", "A02"]),
                 ReplicateSet("DRUG", ["B01", "B02"])],
        solo_wells=[])


# ── the group scope keeps a cross-group control reachable ────────────────────

def test_a_control_in_another_group_resolves(ctx):
    """The defect: `('','')` and every curve blank."""
    from review.domain.fold_change import resolve_control_wells

    panel = _panel(ctx, control=_SET("CTRL"))
    with BatchExportPanel._app_group_scope(panel, _treat_group()):
        assert resolve_control_wells(ctx, _SET("CTRL")) == ["A01", "A02"]


def test_the_carried_control_is_not_drawn(ctx):
    """It is carried hidden, so the group still plots only its own members."""
    panel = _panel(ctx, control=_SET("CTRL"))
    with BatchExportPanel._app_group_scope(panel, _treat_group()):
        drawn = active_replicate_sets(ctx.state.selection.selections,
                                      ctx.state.dataset.well_paths)
        assert [r.name for r in drawn] == ["DRUG"]


def test_a_per_sample_mapping_carries_every_control_it_names(ctx):
    from review.domain.fold_change import resolve_control_wells

    panel = _panel(ctx, control_map={"DRUG": _SET("CTRL")})
    with BatchExportPanel._app_group_scope(panel, _treat_group()):
        assert resolve_control_wells(ctx, _SET("CTRL")) == ["A01", "A02"]


def test_a_control_the_group_already_holds_is_not_duplicated(ctx):
    panel = _panel(ctx, control=_SET("DRUG"))
    with BatchExportPanel._app_group_scope(panel, _treat_group()):
        assert [s["name"] for s in ctx.state.selection.selections] == ["DRUG"]


def test_a_control_naming_nothing_loaded_is_left_unresolved(ctx):
    """No synthetic empty group standing in for one the user deleted — the
    outcome vocabulary is what reports that, and it needs it to stay missing."""
    from review.domain.fold_change import resolve_control_wells

    panel = _panel(ctx, control=_SET("DELETED"))
    with BatchExportPanel._app_group_scope(panel, _treat_group()):
        assert resolve_control_wells(ctx, _SET("DELETED")) == []
        assert "DELETED" not in [s["name"] for s in ctx.state.selection.selections]


def test_the_scope_restores_what_it_replaced(ctx):
    panel = _panel(ctx, control=_SET("CTRL"))
    before = list(ctx.state.selection.selections)
    with BatchExportPanel._app_group_scope(panel, _treat_group()):
        pass
    assert ctx.state.selection.selections == before


def test_the_curves_are_normalized_rather_than_blank(ctx):
    """The user-visible half, asserted on the collected series."""
    panel = _panel(ctx, control_map={"DRUG": _SET("CTRL")})
    ctx.state.plot.fc_control_map = {"DRUG": _SET("CTRL")}
    with BatchExportPanel._app_group_scope(panel, _treat_group()):
        series = collect_line_series_for_group(
            ctx, _treat_group(), threshold=0.0, use_sem=True,
            val_col=ctx.state.channel.active_val_col, fluor_gates=None,
            fc_state=fold_change_state(ctx))
    drawn = [p.mean for p in series[0].points]
    assert not any(math.isnan(m) for m in drawn), f"drew blank: {drawn}"
    assert drawn == pytest.approx([220.0 / 110.0])


# ── the batch bar Events panel ───────────────────────────────────────────────

def _batch_bar(ctx, group=None):
    items = collect_bar_items_for_group(
        ctx, group or _both_group(), target_t=0.0, threshold=0.0, use_sem=True,
        val_col=ctx.state.channel.active_val_col, fluor_gates=None,
        per_fov_spread=False, fc_state=fold_change_state(ctx))
    return {i.key: i for i in items}


def test_the_events_panel_is_normalized(ctx):
    """11.0 / 5.5, computed here rather than by the helper under test."""
    bars = _batch_bar(ctx)
    assert bars["DRUG"].n_above == pytest.approx(2.0)
    assert bars["CTRL"].n_above == pytest.approx(1.0)


def test_the_batch_events_match_the_tab(ctx):
    """Same data, same mapping, two collectors. The assertion that would have
    caught the raw panel."""
    _ug, tab_items, _b = collect_bar_items(ctx, 0.0)
    tab = {i.key: i.n_above for i in tab_items}
    batch = {k: b.n_above for k, b in _batch_bar(ctx).items()}
    assert batch == pytest.approx(tab)


def test_the_events_value_is_not_truncated(ctx):
    """A per-well mean is fractional. The raw value here is 11.0 against a
    control of 5.5; truncating either end gives 11/5 or 2.2, not 2.0."""
    bars = _batch_bar(ctx)
    assert bars["DRUG"].n_above != pytest.approx(11.0 / 5.0)
    assert bars["DRUG"].n_above != pytest.approx(2.2)


def test_the_cross_well_total_stays_raw(ctx):
    """It is the CSV's integer count and must not be scaled with the panel."""
    bars = _batch_bar(ctx)
    assert bars["CTRL"].n_above_total == 11    # 4 + 7
    assert bars["DRUG"].n_above_total == 22    # 9 + 13


def test_a_member_with_no_control_blanks_every_panel(ctx):
    ctx.state.plot.fc_control_map = {"DRUG": _SET("CTRL")}
    bars = _batch_bar(ctx)
    assert math.isnan(bars["CTRL"].mean)
    assert math.isnan(bars["CTRL"].n_above)
    assert bars["DRUG"].n_above == pytest.approx(2.0)


def test_the_events_panel_drops_on_its_own_denominator():
    """A control with a mean but no Events series leaves Mean drawn and Events
    blank, rather than one flag taking all three down or none."""
    app = MockApp(
        per_well_pts={"A01": [make_agg(0.0, 100.0, n_above=4)],
                      "B01": [make_agg(0.0, 200.0, n_above=8)]},
        selections=[],
    )
    ctx = app._ctx
    ctx.state.plot.fc_vs_control_on = True
    ctx.state.plot.fc_control_mode = "per_member"
    ctx.state.plot.fc_control_map = {"B01": ControlRef.for_well("A01")}
    grp = BarGroup(name="G", members=[], solo_wells=["A01", "B01"])
    bars = _batch_bar(ctx, grp)
    assert bars["B01"].mean == pytest.approx(2.0)
    assert bars["B01"].n_above == pytest.approx(2.0), (
        "a solo well's numerator and denominator are the same AggPoint"
    )


def test_the_control_axis_being_off_leaves_events_raw(ctx):
    ctx.state.plot.fc_vs_control_on = False
    bars = _batch_bar(ctx)
    assert bars["DRUG"].n_above == pytest.approx(11.0)


# ── `_pt_n_above`, which the Events change made reachable ────────────────────

def test_a_replicate_point_keeps_its_fractional_mean_and_its_spread():
    """Index 4 is a per-well *mean* on a collector-built point, and index 9 is
    its cross-well spread. Rounding one and zeroing the other was harmless only
    while nothing but well points reached this."""
    from review.domain.fold_change import _pt_n_above

    rset_point = (0.0, 100.0, 0.0, 0.5, 7.5, 0, 0.0, 0.0, 0.0, 1.25, 15)
    assert _pt_n_above(rset_point) == (7.5, 1.25)


def test_a_well_point_still_reports_no_spread():
    """An ``aggregate_well`` point is a 9-tuple with a true count and no index
    9, so the length check preserves it without a flag."""
    from review.domain.fold_change import _pt_n_above

    well_point = (0.0, 100.0, 0.0, 0.5, 7, 10, 0.0, 0.0, 0.0)
    assert _pt_n_above(well_point) == (7.0, 0.0)


def test_a_fractional_baseline_normalizes_to_exactly_one():
    """The truncation, at the size it would show: 7.5 / 7 is 1.071, not 1.0."""
    from review.domain.fold_change import baseline_n_from_points_resolved

    pt = (0.0, 100.0, 0.0, 0.5, 7.5, 0, 0.0, 0.0, 0.0, 0.0, 15)
    _tp, n, _spread = baseline_n_from_points_resolved([pt])
    assert n == pytest.approx(7.5)
    assert 7.5 / n == pytest.approx(1.0)


# ── the mock now matches production ──────────────────────────────────────────

def test_one_well_aggregates_to_that_well_verbatim():
    """``stats_compute.aggregate_group`` hands a one-well list to
    ``aggregate_well``. While the mock rebuilt the point instead, a solo well's
    numerator came from the mock's defaults and its denominator from the
    fixture, and the invented disagreement cost a panel."""
    app = MockApp(
        per_well_pts={"A01": [make_agg(0.0, 100.0, n_above=4, frac=0.25)]},
        selections=[],
    )
    got = app._aggregate_group(
        ["A01"], threshold=0.0, use_sem=True,
        val_col="gfp_mean_intensity", fluor_gates=None)
    assert got == app.per_well_pts["A01"]


def test_a_typed_well_control_is_not_captured_by_the_batch_scope():
    """The batch group scope registers each solo well as a single-well
    selection, so a bare label naming that well hits set-before-well precedence
    and takes a one-well set's denominator — which has no cross-well spread —
    where the tab takes the well's own within-well one. The propagated
    ``fold_change_<band>`` then differs between the two paths for the same
    member at the same timepoint.

    A control saved the way the app writes them today states its own kind, and
    ``resolve_control`` never guesses for one. That is the guarantee this pins;
    the legacy shape is recorded beside it so a change of precedence cannot
    quietly widen the divergence.
    """
    from review.domain.control_ref import KIND_WELL, ControlRef
    from review.domain.fold_change import resolve_control

    from tests.conftest import MockApp, make_agg

    app = MockApp(
        per_well_pts={"A01": [make_agg(0.0, 100.0)], "B01": [make_agg(0.0, 120.0)]},
        # What ``_app_group_scope`` installs for a group holding solo well B01.
        selections=[{"name": "B01", "wells": ["B01"], "hidden": False}],
    )
    ctx = getattr(app, "_ctx", app)

    assert resolve_control(ctx, ControlRef(KIND_WELL, "B01")) == ("well", "B01")
    assert resolve_control(ctx, "B01 (well)") == ("well", "B01")
    # The legacy bare label, and the precedence it was saved under.
    assert resolve_control(ctx, "B01") == ("set", "B01")
