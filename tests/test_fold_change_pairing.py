"""Resolving a per-sample control mapping, and saying what came of each member.

The property under test throughout: a member that failed to pair must never be
indistinguishable from one that paired. Four failure modes are decidable before
any aggregation runs, they are different things to tell a user, and a blank
``fold_change_mean`` says none of them — so each is a named outcome and each
gets a test that it is reached.

The fixtures deliberately carry the mess the last round's did not: a replicate
set and a loaded well sharing the token ``A01``, a control naming a group whose
wells are all unloaded, an entry for a member that is not plotted, and unequal
membership between sets.
"""
from __future__ import annotations

import pytest

from review.domain.control_ref import ControlRef
from review.domain.fold_change_pairing import (
    CONTROL_FIRST,
    CONTROL_LAST,
    ControlOutcome,
    MemberControl,
    PerMemberControls,
    auto_pair,
    build_per_member_controls,
    control_denominators,
    shortfall_text,
    summary_text,
)
from review.domain.fold_change_state import FoldChangeState
from review.domain.plot_members import member_wells, plotted_member_keys

from tests.conftest import MockApp, make_agg


_SET = ControlRef.for_replicate_set

_SETS = [
    {"name": "CTRL", "wells": ["A01", "A02"], "hidden": False},
    {"name": "DRUG", "wells": ["B01", "B02"], "hidden": False},
    {"name": "CTRL2", "wells": ["C01"], "hidden": False},
    {"name": "DRUG2", "wells": ["D01", "D02"], "hidden": False},
]


@pytest.fixture
def ctx():
    app = MockApp(
        per_well_pts={
            w: [make_agg(0.0, 100.0)]
            for w in ("A01", "A02", "B01", "B02", "C01", "D01", "D02")
        },
        selections=_SETS,
    )
    return app._ctx


def _build(ctx, mapping, *, scope="bar", **kw):
    return build_per_member_controls(
        ctx, plotted_member_keys(ctx, scope), mapping,
        member_wells=member_wells(ctx, scope), **kw)


# ── the four decidable outcomes ──────────────────────────────────────────────

def test_a_member_paired_with_another_is_ok(ctx):
    got = _build(ctx, {"DRUG": ControlRef.for_replicate_set("CTRL")})
    rec = got.get("DRUG")
    assert rec.outcome is ControlOutcome.OK
    assert rec.wells == ("A01", "A02")
    assert rec.resolved is True


def test_a_member_paired_with_itself_is_self(ctx):
    got = _build(ctx, {"CTRL": ControlRef.for_replicate_set("CTRL")})
    assert got.get("CTRL").outcome is ControlOutcome.SELF
    assert got.get("CTRL").resolved is True


def test_a_member_nobody_mapped_is_unmapped(ctx):
    got = _build(ctx, {"DRUG": ControlRef.for_replicate_set("CTRL")})
    assert got.get("CTRL2").outcome is ControlOutcome.UNMAPPED
    assert got.get("CTRL2").resolved is False


def test_a_control_that_resolves_to_nothing_is_named_as_such(ctx):
    """Not the same as unmapped, and the difference is what the user needs:
    somebody did pick a control, and it is gone."""
    got = _build(ctx, {"DRUG": ControlRef.for_replicate_set("DELETED")})
    rec = got.get("DRUG")
    assert rec.outcome is ControlOutcome.CONTROL_UNRESOLVED
    assert rec.control == ControlRef.for_replicate_set("DELETED")
    assert rec.wells == ()


def test_a_group_whose_wells_are_all_unloaded_is_unresolved_not_ok():
    """It has a name and no data — the shape that used to produce a bar
    silently divided by nothing."""
    app = MockApp(
        per_well_pts={"B01": [make_agg(0.0, 100.0)]},
        selections=[{"name": "DRUG", "wells": ["B01"], "hidden": False},
                    {"name": "GONE", "wells": ["Z98", "Z99"], "hidden": False}],
    )
    got = _build(app._ctx, {"DRUG": ControlRef.for_replicate_set("GONE")})
    assert got.get("DRUG").outcome is ControlOutcome.CONTROL_UNRESOLVED


def test_the_timepoint_dependent_outcome_is_not_decided_here(ctx):
    """``CONTROL_NO_SAMPLE`` is a function of (member, control, timepoint), so
    only a collector can stamp it. Nothing here may invent it."""
    got = _build(ctx, {"DRUG": ControlRef.for_replicate_set("CTRL"),
                       "CTRL": ControlRef.for_replicate_set("DELETED")})
    assert ControlOutcome.CONTROL_NO_SAMPLE not in got.counts()


# ── self is decided by wells, not by name ────────────────────────────────────

def test_self_is_decided_by_resolved_wells_not_by_name():
    """A set named ``A01`` and the well ``A01`` are different controls, and a
    member paired with the other one is not paired with itself."""
    app = MockApp(
        per_well_pts={"A01": [make_agg(0.0, 100.0)],
                      "B01": [make_agg(0.0, 200.0)]},
        selections=[{"name": "A01", "wells": ["B01"], "hidden": False}],
    )
    ctxx = app._ctx
    got = build_per_member_controls(
        ctxx, ["A01"], {"A01": ControlRef.for_well("A01")},
        member_wells={"A01": ["B01"]})
    assert got.get("A01").outcome is ControlOutcome.OK, (
        "the member is the set (wells B01); the control is the well A01"
    )


def test_two_names_for_the_same_wells_are_self():
    """A one-well group and that well are the same denominator, so a member
    pointed at either of them is normalizing against itself."""
    app = MockApp(
        per_well_pts={"C01": [make_agg(0.0, 100.0)]},
        selections=[{"name": "CTRL2", "wells": ["C01"], "hidden": False}],
    )
    got = build_per_member_controls(
        app._ctx, ["CTRL2"], {"CTRL2": ControlRef.for_well("C01")},
        member_wells={"CTRL2": ["C01"]})
    assert got.get("CTRL2").outcome is ControlOutcome.SELF


def test_a_member_with_no_wells_is_never_self():
    """Otherwise "no wells" would compare equal to "no wells" and a member
    with nothing loaded would report a clean self-normalization."""
    app = MockApp(per_well_pts={"A01": [make_agg(0.0, 1.0)]}, selections=[])
    got = build_per_member_controls(
        app._ctx, ["Z99"], {"Z99": ControlRef.for_well("A01")},
        member_wells={"Z99": []})
    assert got.get("Z99").outcome is ControlOutcome.OK


# ── inheritance ──────────────────────────────────────────────────────────────

def test_a_well_inherits_its_sets_control_when_pooling_is_off(ctx):
    """The key space flips to well tokens; a mapping written against the sets
    must not be orphaned by a toggle that has nothing to do with fold change."""
    ctx.state.plot.bar_pool_replicates = False
    got = _build(ctx, {"DRUG": ControlRef.for_replicate_set("CTRL")},
                 member_rsets={"B01": "DRUG", "B02": "DRUG"})
    for well in ("B01", "B02"):
        rec = got.get(well)
        assert rec.outcome is ControlOutcome.OK
        assert rec.inherited_from == "DRUG"
        assert rec.wells == ("A01", "A02")


def test_an_explicit_entry_beats_the_inherited_one(ctx):
    ctx.state.plot.bar_pool_replicates = False
    got = _build(
        ctx,
        {"DRUG": ControlRef.for_replicate_set("CTRL"),
         "B01": ControlRef.for_replicate_set("CTRL2")},
        member_rsets={"B01": "DRUG", "B02": "DRUG"},
    )
    assert got.get("B01").wells == ("C01",)
    assert got.get("B01").inherited_from == ""
    assert got.get("B02").wells == ("A01", "A02")


def test_inheritance_is_one_hop(ctx):
    """An inherited control is not itself looked up for further inheritance;
    without that, an edit to one group could silently change another."""
    ctx.state.plot.bar_pool_replicates = False
    got = _build(
        ctx,
        # DRUG points at CTRL, and CTRL points at CTRL2. B01 inherits DRUG's
        # control, which is CTRL — not CTRL2.
        {"DRUG": ControlRef.for_replicate_set("CTRL"),
         "CTRL": ControlRef.for_replicate_set("CTRL2")},
        member_rsets={"B01": "DRUG"},
    )
    assert got.get("B01").wells == ("A01", "A02")


def test_no_owner_means_no_inheritance(ctx):
    ctx.state.plot.bar_pool_replicates = False
    got = _build(ctx, {"DRUG": ControlRef.for_replicate_set("CTRL")})
    assert got.get("B01").outcome is ControlOutcome.UNMAPPED


# ── retained entries ─────────────────────────────────────────────────────────

def test_an_entry_for_a_member_that_is_not_plotted_is_kept(ctx):
    """Retain-don't-drop, at the resolver as well as in storage."""
    got = _build(ctx, {"DRUG": ControlRef.for_replicate_set("CTRL"),
                       "NOT-PLOTTED": ControlRef.for_replicate_set("CTRL")})
    assert got.unused == (("NOT-PLOTTED", ControlRef.for_replicate_set("CTRL")),)
    assert "NOT-PLOTTED" not in got.order


def test_an_unused_entry_does_not_count_toward_the_summary(ctx):
    got = _build(ctx, {"NOT-PLOTTED": ControlRef.for_replicate_set("CTRL")})
    assert summary_text(got) == "Per-sample control (0/4)"


# ── distinct controls, which the axis label branches on ──────────────────────

def test_no_controls_is_no_distinct_controls(ctx):
    assert _build(ctx, {}).distinct_controls() == ()


def test_one_control_for_everything(ctx):
    """The case that must read exactly like today's single-control title,
    because it is mathematically identical to it."""
    got = _build(ctx, {k: ControlRef.for_replicate_set("CTRL")
                       for k in ("CTRL", "DRUG", "CTRL2", "DRUG2")})
    assert got.distinct_controls() == (ControlRef.for_replicate_set("CTRL"),)


def test_two_controls_are_both_named(ctx):
    got = _build(ctx, {"CTRL": ControlRef.for_replicate_set("CTRL"),
                       "DRUG": ControlRef.for_replicate_set("CTRL"),
                       "CTRL2": ControlRef.for_replicate_set("CTRL2"),
                       "DRUG2": ControlRef.for_replicate_set("CTRL2")})
    assert len(got.distinct_controls()) == 2


def test_distinctness_is_by_wells_not_by_name():
    """Two references that resolve to the same wells are one control, so an
    axis label does not claim two denominators where there is one."""
    app = MockApp(
        per_well_pts={"C01": [make_agg(0.0, 1.0)], "D01": [make_agg(0.0, 2.0)]},
        selections=[{"name": "CTRL2", "wells": ["C01"], "hidden": False}],
    )
    got = build_per_member_controls(
        app._ctx, ["X", "Y"],
        {"X": ControlRef.for_replicate_set("CTRL2"),
         "Y": ControlRef.for_well("C01")},
        member_wells={"X": ["D01"], "Y": ["D01"]})
    assert len(got.distinct_controls()) == 1


def test_an_unresolved_control_is_not_a_distinct_control(ctx):
    got = _build(ctx, {"DRUG": ControlRef.for_replicate_set("DELETED")})
    assert got.distinct_controls() == ()


# ── is_pure_control: both halves of the rule ─────────────────────────────────

def test_a_self_normalized_member_somebody_uses_is_a_pure_control(ctx):
    got = _build(ctx, {"CTRL": ControlRef.for_replicate_set("CTRL"),
                       "DRUG": ControlRef.for_replicate_set("CTRL")})
    assert got.is_pure_control("CTRL") is True


def test_a_self_normalized_member_nobody_uses_is_not(ctx):
    """It is an ordinary sample that happens to be divided by itself; hiding it
    would delete a measurement the user asked for."""
    got = _build(ctx, {"CTRL": ControlRef.for_replicate_set("CTRL")})
    assert got.is_pure_control("CTRL") is False


def test_a_member_used_as_a_control_but_normalized_elsewhere_is_not(ctx):
    """It carries its own normalization, so it is a sample too."""
    got = _build(ctx, {"CTRL": ControlRef.for_replicate_set("CTRL2"),
                       "DRUG": ControlRef.for_replicate_set("CTRL")})
    assert got.is_pure_control("CTRL") is False


def test_an_unmapped_member_is_not_a_pure_control(ctx):
    got = _build(ctx, {"DRUG": ControlRef.for_replicate_set("CTRL")})
    assert got.is_pure_control("CTRL") is False


def test_hidden_render_removes_only_the_pure_controls(ctx):
    got = _build(ctx, {"CTRL": ControlRef.for_replicate_set("CTRL"),
                       "DRUG": ControlRef.for_replicate_set("CTRL"),
                       "CTRL2": ControlRef.for_replicate_set("CTRL2")},
                 render="hidden")
    assert got.hidden_members() == ("CTRL",)


def test_self_render_hides_nothing(ctx):
    got = _build(ctx, {"CTRL": ControlRef.for_replicate_set("CTRL"),
                       "DRUG": ControlRef.for_replicate_set("CTRL")})
    assert got.hidden_members() == ()


def test_an_unknown_render_falls_back_to_self(ctx):
    assert _build(ctx, {}, render="raw").render == "self"


# ── the per-member denominator lookup the collectors share ───────────────────

class _FakeCache:
    """A ``ControlSeriesCache``-shaped stub that records what it was asked."""

    def __init__(self, stats=None, frac=None, n=None):
        self._stats, self._frac, self._n = stats, frac, n
        self.asked: list = []

    def stats_at(self, ref, t):
        self.asked.append((ref, t))
        return self._stats

    def frac_at(self, ref, t):
        return self._frac

    def n_at(self, ref, t):
        return self._n


def test_the_axis_being_off_asks_the_cache_nothing(ctx):
    cache = _FakeCache(stats=(1.0, 0.0))
    denoms = control_denominators(ctx, cache, FoldChangeState(), [])
    assert denoms.at("DRUG", 0.0) == (None, "", None, None, None)
    assert cache.asked == []


def test_an_unresolved_member_asks_the_cache_nothing(ctx):
    """No point aggregating a denominator that cannot exist."""
    cache = _FakeCache(stats=(1.0, 0.0))
    fc = FoldChangeState(vs_control_on=True, control_mode="per_member",
                         control_map=(("DRUG", _SET("DELETED")),))
    denoms = control_denominators(
        ctx, cache, fc, ["DRUG"], scope_wells={"DRUG": ["B01", "B02"]})
    _ref, outcome, mean, _f, _n = denoms.at("DRUG", 0.0)
    assert outcome is ControlOutcome.CONTROL_UNRESOLVED
    assert mean is None
    assert cache.asked == []


def test_a_control_absent_at_the_timepoint_becomes_the_timepoint_outcome(ctx):
    cache = _FakeCache(stats=None)
    sink: set = set()
    fc = FoldChangeState(vs_control_on=True, control=_SET("CTRL"))
    denoms = control_denominators(ctx, cache, fc, [], miss_sink=sink)
    _ref, outcome, mean, _f, _n = denoms.at("DRUG", 7.0)
    assert outcome is ControlOutcome.CONTROL_NO_SAMPLE
    assert mean is None
    assert sink == {7.0}, "the renderer's status line reads this"


def test_a_single_control_naming_nothing_is_unresolved_not_absent(ctx):
    """Two different sentences for the user, and the same empty bar."""
    cache = _FakeCache(stats=(1.0, 0.0))
    fc = FoldChangeState(vs_control_on=True, control=_SET("DELETED"))
    denoms = control_denominators(ctx, cache, fc, [])
    assert denoms.at("DRUG", 0.0)[1] is ControlOutcome.CONTROL_UNRESOLVED


def test_each_panel_reports_its_own_denominator(ctx):
    """A control with a mean but no fraction series."""
    cache = _FakeCache(stats=(2.0, 0.1), frac=None, n=(5.0, 0.0))
    fc = FoldChangeState(vs_control_on=True, control=_SET("CTRL"))
    denoms = control_denominators(ctx, cache, fc, [])
    _ref, outcome, mean, frac, n = denoms.at("DRUG", 0.0)
    assert outcome is ControlOutcome.OK
    assert (mean, frac, n) == ((2.0, 0.1), None, (5.0, 0.0))


def test_hidden_members_are_empty_under_a_single_control(ctx):
    fc = FoldChangeState(vs_control_on=True, control=_SET("CTRL"))
    denoms = control_denominators(ctx, _FakeCache(), fc, [])
    assert denoms.hidden_members == frozenset()


# ── the auto-pair chunker ────────────────────────────────────────────────────

_REFS = {k: ControlRef.for_replicate_set(k)
         for k in ("CTRL", "DRUG", "CTRL2", "DRUG2")}


def test_chunks_of_two_pair_to_their_first():
    got = auto_pair(["CTRL", "DRUG", "CTRL2", "DRUG2"], _REFS, size=2)
    assert got == {
        "CTRL": _REFS["CTRL"], "DRUG": _REFS["CTRL"],
        "CTRL2": _REFS["CTRL2"], "DRUG2": _REFS["CTRL2"],
    }


def test_first_is_the_default_position():
    keys = ["CTRL", "DRUG", "CTRL2", "DRUG2"]
    assert auto_pair(keys, _REFS, size=2) \
        == auto_pair(keys, _REFS, size=2, position=CONTROL_FIRST)


def test_chunks_can_pair_to_their_last():
    got = auto_pair(["DRUG", "CTRL", "DRUG2", "CTRL2"], _REFS, size=2,
                    position=CONTROL_LAST)
    assert got["DRUG"] == _REFS["CTRL"]
    assert got["DRUG2"] == _REFS["CTRL2"]


def test_a_trailing_partial_chunk_is_still_paired():
    """It is a real block of samples. Skipping it leaves a tail of unmapped
    members that reads as a bug rather than as a choice."""
    got = auto_pair(["CTRL", "DRUG", "CTRL2"], _REFS, size=2)
    assert got["CTRL2"] == _REFS["CTRL2"]


def test_a_chunk_of_one_pairs_that_member_with_itself():
    got = auto_pair(["CTRL", "DRUG"], _REFS, size=1)
    assert got == {"CTRL": _REFS["CTRL"], "DRUG": _REFS["DRUG"]}


def test_a_chunk_size_larger_than_the_plate_is_one_chunk():
    got = auto_pair(["CTRL", "DRUG"], _REFS, size=99)
    assert set(got.values()) == {_REFS["CTRL"]}


def test_a_chunk_whose_anchor_has_no_reference_is_skipped_not_guessed():
    got = auto_pair(["UNKNOWN", "DRUG", "CTRL2", "DRUG2"], _REFS, size=2)
    assert "DRUG" not in got
    assert got["DRUG2"] == _REFS["CTRL2"]


def test_a_nonsense_size_maps_nothing():
    assert auto_pair(["CTRL", "DRUG"], _REFS, size=0) == {}
    assert auto_pair([], _REFS, size=2) == {}


def test_the_chunker_addresses_each_member_by_its_own_kind():
    """A well token and a set name are different references, and the chunker
    must not turn one into the other."""
    refs = {"A01": ControlRef.for_well("A01"),
            "B01": ControlRef.for_well("B01")}
    got = auto_pair(["A01", "B01"], refs, size=2)
    assert got["B01"] == ControlRef.for_well("A01")


# ── what the user is told ────────────────────────────────────────────────────

def test_the_summary_counts_resolved_out_of_plotted(ctx):
    got = _build(ctx, {"CTRL": ControlRef.for_replicate_set("CTRL"),
                       "DRUG": ControlRef.for_replicate_set("CTRL")})
    assert summary_text(got) == "Per-sample control (2/4)"


def test_the_summary_does_not_call_a_broken_mapping_complete(ctx):
    """Four entries, one naming a deleted group — a count of entries would say
    4/4 and the figure would be missing a bar."""
    got = _build(ctx, {"CTRL": ControlRef.for_replicate_set("CTRL"),
                       "DRUG": ControlRef.for_replicate_set("CTRL"),
                       "CTRL2": ControlRef.for_replicate_set("CTRL"),
                       "DRUG2": ControlRef.for_replicate_set("DELETED")})
    assert summary_text(got) == "Per-sample control (3/4)"


def test_the_summary_says_so_when_nothing_is_plotted():
    app = MockApp(per_well_pts={}, selections=[])
    assert summary_text(_build(app._ctx, {})) == "Per-sample control (nothing plotted)"


def test_the_shortfall_coalesces_by_reason(ctx):
    got = _build(ctx, {"CTRL": ControlRef.for_replicate_set("CTRL"),
                       "DRUG": ControlRef.for_replicate_set("DELETED"),
                       "CTRL2": ControlRef.for_replicate_set("ALSO-GONE")})
    text = shortfall_text(got)
    assert "1 with no control" in text
    assert "2 whose control is missing" in text


def test_a_complete_mapping_has_nothing_to_report(ctx):
    got = _build(ctx, {k: ControlRef.for_replicate_set("CTRL")
                       for k in ("CTRL", "DRUG", "CTRL2", "DRUG2")})
    assert shortfall_text(got) == ""


def test_the_shortfall_reports_the_collectors_outcome_too():
    """``CONTROL_NO_SAMPLE`` is stamped by a collector, and the status line has
    to be able to say it."""
    rec = MemberControl("DRUG", ControlRef.for_replicate_set("CTRL"),
                        ("A01",), ControlOutcome.CONTROL_NO_SAMPLE)
    got = PerMemberControls(order=("DRUG",), by_member={"DRUG": rec})
    assert "no data at this timepoint" in shortfall_text(got)


# ── the default record ───────────────────────────────────────────────────────

def test_a_member_never_seen_reads_as_unmapped_rather_than_raising(ctx):
    """Every consumer asks by key; none of them should have to guard."""
    rec = _build(ctx, {}).get("NEVER-HEARD-OF-IT")
    assert rec.outcome is ControlOutcome.UNMAPPED
    assert rec.control is None
    assert rec.wells == ()
