"""Four traces, four different controls — and the two raw-fallback fixes.

The Line tab's failure mode is worse than the Bar tab's. Bar NaNs a value it
cannot normalize, which leaves a visible gap; Line handed ``normalize_pts`` an
empty control series, which skips the control axis without complaint, so the
tab drew **raw traces** under a "fold change vs …" axis label with ``miss_sink``
empty. Nothing on screen said so, and the on-tab CSV — which routes through the
resolver — disagreed with the figure.

Both the tab collector and the batch runner had that shape, and both are
asserted here on the collected series rather than on a helper.
"""
from __future__ import annotations

import math

import pytest

from well_viewer.domain.control_ref import ControlRef
from well_viewer.features.lineplot.controller import collect_line_series

from tests.conftest import MockApp, make_agg


_VALUES = {
    "A01": (100.0, 110.0), "A02": (120.0, 130.0),   # CTRL  → 110, 120
    "B01": (210.0, 230.0),                           # DRUG
    "C01": (50.0, 60.0),                             # CTRL2
    "D01": (150.0, 170.0),                           # DRUG2
}
_SETS = [
    {"name": "CTRL", "wells": ["A01", "A02"], "hidden": False},
    {"name": "DRUG", "wells": ["B01"], "hidden": False},
    {"name": "CTRL2", "wells": ["C01"], "hidden": False},
    {"name": "DRUG2", "wells": ["D01"], "hidden": False},
]
_SET = ControlRef.for_replicate_set


@pytest.fixture
def ctx():
    app = MockApp(
        per_well_pts={
            w: [make_agg(0.0, v0), make_agg(1.0, v1)]
            for w, (v0, v1) in _VALUES.items()
        },
        selections=_SETS,
    )
    c = app._ctx
    c.state.plot.fc_vs_control_on = True
    c.state.plot.fc_control_mode = "per_member"
    return c


def _series(ctx, mapping, *, render=None):
    ctx.state.plot.fc_control_map = dict(mapping)
    if render is not None:
        ctx.state.plot.fc_control_render = render
    series, _msg = collect_line_series(ctx, threshold=0.0, use_sem=True)
    return {s.key: s for s in series}


def _means(series):
    return [p.mean for p in series.points]


# ── the feature ──────────────────────────────────────────────────────────────

def test_each_trace_is_divided_by_its_own_control(ctx):
    got = _series(ctx, {"DRUG": _SET("CTRL"), "DRUG2": _SET("CTRL2"),
                        "CTRL": _SET("CTRL"), "CTRL2": _SET("CTRL2")})
    assert _means(got["DRUG"]) == pytest.approx([210.0 / 110.0, 230.0 / 120.0])
    assert _means(got["DRUG2"]) == pytest.approx([150.0 / 50.0, 170.0 / 60.0])


def test_a_self_normalized_trace_is_flat_at_one(ctx):
    got = _series(ctx, {"CTRL": _SET("CTRL")})
    assert _means(got["CTRL"]) == pytest.approx([1.0, 1.0])


def test_every_timepoint_uses_its_own_denominator(ctx):
    """The control's value moves with time, so a trace normalized against a
    single timepoint's value would be the giveaway."""
    got = _series(ctx, {"DRUG": _SET("CTRL")})
    assert _means(got["DRUG"])[0] != pytest.approx(_means(got["DRUG"])[1])


# ── the raw fallback ─────────────────────────────────────────────────────────

def test_an_unmapped_trace_is_blank_not_raw(ctx):
    got = _series(ctx, {"DRUG": _SET("CTRL")})
    drawn = _means(got["DRUG2"])
    assert all(math.isnan(m) for m in drawn), (
        f"drew {drawn} — the raw values are {[150.0, 170.0]}, so an "
        "un-normalized trace under a fold-change axis reads exactly like this"
    )
    assert all(not p.has_mean for p in got["DRUG2"].points)


def test_a_trace_whose_control_is_gone_is_blank_not_raw(ctx):
    got = _series(ctx, {"DRUG": _SET("DELETED"), "DRUG2": _SET("CTRL2")})
    assert all(math.isnan(m) for m in _means(got["DRUG"]))
    assert _means(got["DRUG2"]) == pytest.approx([3.0, 170.0 / 60.0])


def test_a_single_control_that_resolves_to_nothing_blanks_every_trace(ctx):
    """Pre-existing: the Bar tab NaNed these while the Line tab drew raw."""
    ctx.state.plot.fc_control_mode = "single"
    ctx.state.plot.fc_control = _SET("DELETED")
    series, _msg = collect_line_series(ctx, threshold=0.0, use_sem=True)
    for s in series:
        assert all(math.isnan(p.mean) for p in s.points), f"{s.key} drew raw"


def test_a_blank_point_keeps_its_timepoint(ctx):
    """Dropping the point would leave a gap the renderer bridges with a
    straight line between the neighbours, which reads as data."""
    got = _series(ctx, {"DRUG": _SET("CTRL")})
    assert [p.t for p in got["DRUG2"].points] == [0.0, 1.0]


def test_a_blank_point_blanks_every_panel(ctx):
    got = _series(ctx, {"DRUG": _SET("CTRL")})
    point = got["DRUG2"].points[0]
    assert math.isnan(point.mean)
    assert math.isnan(point.frac)
    assert math.isnan(point.n_above)


def test_the_control_axis_being_off_leaves_the_traces_raw(ctx):
    ctx.state.plot.fc_vs_control_on = False
    got = _series(ctx, {"DRUG": _SET("CTRL")})
    assert _means(got["DRUG"]) == pytest.approx([210.0, 230.0])


# ── the render toggle and per-well mode ──────────────────────────────────────

def test_hidden_removes_a_pure_control_trace(ctx):
    got = _series(ctx, {"CTRL": _SET("CTRL"), "DRUG": _SET("CTRL")},
                  render="hidden")
    assert "CTRL" not in got
    assert "DRUG" in got


def test_the_mapping_keys_on_well_tokens_with_no_set_active(ctx):
    """The Line tab's key space flips the same way the Bar tab's does."""
    ctx.state.selection.selections = []
    got = _series(ctx, {"B01": ControlRef.for_well("A01")})
    assert _means(got["B01"]) == pytest.approx([210.0 / 100.0, 230.0 / 110.0])
    assert all(math.isnan(m) for m in _means(got["C01"])), "unmapped"


# ── the batch runner draws the same figure off-screen ────────────────────────

def _group(ctx):
    from well_viewer.domain.batch_models import BarGroup, ReplicateSet
    return BarGroup(
        name="G",
        members=[ReplicateSet(s["name"], list(s["wells"])) for s in _SETS],
        solo_wells=[],
    )


def _batch(ctx, mapping):
    from well_viewer.domain.fold_change import fold_change_state
    from well_viewer.features.batch_export._line_series_runner import (
        collect_line_series_for_group,
    )
    ctx.state.plot.fc_control_map = dict(mapping)
    series = collect_line_series_for_group(
        ctx, _group(ctx), threshold=0.0, use_sem=True,
        val_col=ctx.state.channel.active_val_col, fluor_gates=None,
        fc_state=fold_change_state(ctx),
    )
    return {s.key: s for s in series}


def test_the_batch_runner_divides_each_member_by_its_own_control(ctx):
    got = _batch(ctx, {"DRUG": _SET("CTRL"), "DRUG2": _SET("CTRL2")})
    assert _means(got["DRUG"]) == pytest.approx([210.0 / 110.0, 230.0 / 120.0])
    assert _means(got["DRUG2"]) == pytest.approx([150.0 / 50.0, 170.0 / 60.0])


def test_the_batch_runner_blanks_an_unmapped_member(ctx):
    """Nobody is watching a batch export being drawn, so a raw value here is
    the least visible version of the same defect."""
    got = _batch(ctx, {"DRUG": _SET("CTRL")})
    assert all(math.isnan(m) for m in _means(got["DRUG2"]))


def test_the_batch_runner_matches_the_tab(ctx):
    """The exported figure and the on-screen one are the same plot."""
    mapping = {"DRUG": _SET("CTRL"), "DRUG2": _SET("CTRL2"),
               "CTRL": _SET("CTRL")}
    tab = _series(ctx, mapping)
    batch = _batch(ctx, mapping)
    for key in ("CTRL", "DRUG", "DRUG2"):
        assert _means(batch[key]) == pytest.approx(_means(tab[key])), key


# ── the per-cell overlay agrees with the bar under it ────────────────────────

def test_the_violin_overlay_uses_the_same_control_as_its_bar(ctx):
    """``build_cell_scaling`` is the overlay's own resolver and took one
    control. Left alone, the bars would normalize per member while the
    distribution drawn on top of them divided by a stale single control — two
    denominators on one axis, looking like one measurement."""
    from well_viewer.domain.fold_change import build_cell_scaling, fold_change_state

    ctx.state.plot.fc_control_map = {"DRUG": _SET("CTRL"),
                                     "DRUG2": _SET("CTRL2")}
    fc = fold_change_state(ctx)
    scale_for = build_cell_scaling(
        ctx, ["DRUG", "DRUG2", "CTRL"], fc_state=fc,
        threshold=0.0, val_col=ctx.state.channel.active_val_col,
        use_sem=True, per_fov_spread=False, fluor_gates=None,
        control_by_member={"DRUG": _SET("CTRL"), "DRUG2": _SET("CTRL2"),
                           "CTRL": None},
    )
    assert scale_for("DRUG", 0.0).control_mean == pytest.approx(110.0)
    assert scale_for("DRUG2", 0.0).control_mean == pytest.approx(50.0)


def test_an_unmapped_members_overlay_is_blank_rather_than_raw(ctx):
    from well_viewer.domain.fold_change import build_cell_scaling, fold_change_state

    ctx.state.plot.fc_control_map = {"DRUG": _SET("CTRL")}
    scale_for = build_cell_scaling(
        ctx, ["DRUG", "DRUG2"], fc_state=fold_change_state(ctx),
        threshold=0.0, val_col=ctx.state.channel.active_val_col,
        use_sem=True, per_fov_spread=False, fluor_gates=None,
        control_by_member={"DRUG": _SET("CTRL"), "DRUG2": None},
    )
    assert scale_for("DRUG2", 0.0).missing is True
    assert scale_for("DRUG", 0.0).missing is False
