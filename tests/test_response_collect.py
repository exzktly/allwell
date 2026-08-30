"""Response curves: what gets a point, what gets named, and what never gets invented.

The rule this file exists for is the one §7 states: **degraded output must never
be indistinguishable from good output.** A plate of eight groups drawn as five
looks exactly like a plate of five, and matplotlib's default for a point with no
x is to draw nothing at all. So every member that cannot be placed comes back
named, with which of the three reasons it was, and the caller puts that on the
axes rather than only in a log.

Pure lane: the collector takes a ctx and returns dataclasses, and the axis
decisions take lists of numbers.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from well_viewer.domain import sample_variables as sv
from well_viewer.features.response import collect as rc
from well_viewer.features.response import response_compute as rcompute


def _dose(*pairs):
    """A Number variable from ``(label, number)`` pairs."""
    return sv.Variable(
        id="dose", name="Dose", kind=sv.NUMBER, unit="µM",
        values=tuple(sv.VariableValue(f"d{i}", lab, num)
                     for i, (lab, num) in enumerate(pairs)))


def _ctx(*, wells, variable=None, assignments=None, selections=(), means=None):
    """A ctx with the slices the collector reads, and a stubbed stats call."""
    vset = sv.VariableSet(variables=(variable,) if variable else (),
                          assignments=dict(assignments or {}))
    return SimpleNamespace(
        state=SimpleNamespace(
            dataset=SimpleNamespace(well_paths={w: f"/{w}.csv" for w in wells}),
            selection=SimpleNamespace(selections=list(selections),
                                      selected_wells=set(), variables=vset),
            plot=SimpleNamespace(group_by_variable="", bar_pool_replicates=True,
                                 use_sem=True, line_order_rsets=[],
                                 line_order_wells=[]),
            channel=SimpleNamespace(active_val_col="gfp_mean_intensity",
                                    active_channel="gfp"),
        ),
        runtime=SimpleNamespace(barplot=SimpleNamespace(
            current_keys=lambda: [s.get("name") for s in selections])),
    )


@pytest.fixture
def stub_stats(monkeypatch):
    """``compute_rep_stats`` and the ON cutoff, so the curve's shape is the test's."""
    means = {}

    def _stats(ctx, rset, target_t, threshold, use_sem):
        value = means.get((rset.name, target_t), 10.0)
        return (None, None, None, None) if value is None else (value, 1.0, 0.5, 0.1)

    import well_viewer.domain.stats_compute as sc
    import well_viewer.core.channel_state_controller as csc
    monkeypatch.setattr(sc, "compute_rep_stats", _stats)
    monkeypatch.setattr(csc, "on_cutoff_for_column", lambda *a, **k: 0.0)
    return means


def _groups(*pairs):
    return [{"id": f"g{i}", "name": n, "wells": w}
            for i, (n, w) in enumerate(pairs)]


# ── a member gets a point only when its wells agree ─────────────────────────

def test_each_group_becomes_one_point_at_its_own_dose(stub_stats):
    ctx = _ctx(wells=["A01", "A02", "A03", "A04"],
               variable=_dose(("0.1", 0.1), ("1", 1.0), ("10", 10.0)),
               assignments={"dose": {"A01": "d0", "A02": "d1",
                                     "A03": "d2", "A04": "d2"}},
               selections=_groups(("low", ["A01"]), ("mid", ["A02"]),
                                  ("high", ["A03", "A04"])))
    data = rc.collect_response(ctx, "dose", [24.0])

    assert [p.x for p in data.curves[0].points] == [0.1, 1.0, 10.0]
    assert [p.member for p in data.curves[0].points] == ["low", "mid", "high"]
    assert data.omitted == [] and data.non_numeric == []


def test_the_points_come_out_in_ascending_x_whatever_order_the_groups_are_in(stub_stats):
    """A line drawn through points in group order zig-zags across the axis."""
    ctx = _ctx(wells=["A01", "A02"],
               variable=_dose(("10", 10.0), ("1", 1.0)),
               assignments={"dose": {"A01": "d0", "A02": "d1"}},
               selections=_groups(("first", ["A01"]), ("second", ["A02"])))
    data = rc.collect_response(ctx, "dose", [24.0])
    assert [p.x for p in data.curves[0].points] == [1.0, 10.0]


def test_a_group_whose_wells_hold_two_doses_is_named_not_averaged(stub_stats):
    """Averaging them would place the group at a dose nothing was given."""
    ctx = _ctx(wells=["A01", "A02"],
               variable=_dose(("1", 1.0), ("10", 10.0)),
               assignments={"dose": {"A01": "d0", "A02": "d1"}},
               selections=_groups(("mixed", ["A01", "A02"])))
    data = rc.collect_response(ctx, "dose", [24.0])

    assert data.curves[0].points == ()
    assert data.omitted == [("mixed", rc.MIXED_VALUE)]


def test_a_group_holding_one_unassigned_well_is_named_not_placed(stub_stats):
    ctx = _ctx(wells=["A01", "A02"], variable=_dose(("1", 1.0)),
               assignments={"dose": {"A01": "d0"}},
               selections=_groups(("short", ["A01", "A02"])))
    data = rc.collect_response(ctx, "dose", [24.0])

    assert data.curves[0].points == ()
    assert data.omitted == [("short", rc.NO_VALUE)]


def test_the_two_reasons_stay_apart(stub_stats):
    """"You filled these inconsistently" and "you have not filled these" have
    different fixes, so a caption that pooled them would send the user to the
    wrong one."""
    ctx = _ctx(wells=["A01", "A02", "A03"],
               variable=_dose(("1", 1.0), ("10", 10.0)),
               assignments={"dose": {"A01": "d0", "A02": "d1"}},
               selections=_groups(("mixed", ["A01", "A02"]),
                                  ("short", ["A03"])))
    data = rc.collect_response(ctx, "dose", [24.0])
    assert dict(data.omitted) == {"mixed": rc.MIXED_VALUE, "short": rc.NO_VALUE}


def test_a_value_that_is_not_a_number_is_kept_apart_from_the_omissions(stub_stats):
    """A vehicle row is the user's deliberate entry, not a gap. Lumping it in
    with the omissions reads as an error report about a plate that is fine."""
    ctx = _ctx(wells=["A01", "A02"],
               variable=_dose(("vehicle", None), ("10", 10.0)),
               assignments={"dose": {"A01": "d0", "A02": "d1"}},
               selections=_groups(("veh", ["A01"]), ("top", ["A02"])))
    data = rc.collect_response(ctx, "dose", [24.0])

    assert data.non_numeric == ["veh"]
    assert data.omitted == []
    assert [p.member for p in data.curves[0].points] == ["top"]


def test_a_timepoint_a_member_was_not_measured_at_leaves_a_gap_not_a_zero(
        stub_stats):
    """Zero is a measurement. A member that was not measured then is not one."""
    stub_stats[("late", 0.0)] = None
    ctx = _ctx(wells=["A01", "A02"], variable=_dose(("1", 1.0), ("10", 10.0)),
               assignments={"dose": {"A01": "d0", "A02": "d1"}},
               selections=_groups(("early", ["A01"]), ("late", ["A02"])))
    data = rc.collect_response(ctx, "dose", [0.0])

    assert [p.member for p in data.curves[0].points] == ["early"]


def test_one_curve_per_timepoint(stub_stats):
    ctx = _ctx(wells=["A01"], variable=_dose(("1", 1.0)),
               assignments={"dose": {"A01": "d0"}},
               selections=_groups(("a", ["A01"])))
    data = rc.collect_response(ctx, "dose", [0.0, 24.0])
    assert [c.timepoint for c in data.curves] == [0.0, 24.0]


def test_a_variable_that_does_not_exist_draws_nothing_and_claims_nothing(stub_stats):
    ctx = _ctx(wells=["A01"], selections=_groups(("a", ["A01"])))
    data = rc.collect_response(ctx, "nope", [24.0])
    assert data.curves == [] and data.omitted == [] and not data.has_points()


# ── the caption ─────────────────────────────────────────────────────────────

def test_the_caption_names_the_omitted_groups():
    """It goes on the axes, because a saved figure outlives the status line."""
    data = rc.ResponseData(variable_name="Dose",
                           omitted=[("Ctrl", rc.NO_VALUE),
                                    ("Blank", rc.NO_VALUE)])
    caption = rc.omission_caption(data)
    assert "2 omitted" in caption and "Ctrl" in caption and "Blank" in caption


def test_the_caption_counts_the_rest_rather_than_listing_forty_names():
    data = rc.ResponseData(variable_name="Dose",
                           omitted=[(f"g{i}", rc.NO_VALUE) for i in range(9)])
    assert "and 6 more" in rc.omission_caption(data)


def test_the_caption_keeps_off_scale_members_separate():
    data = rc.ResponseData(variable_name="Dose", non_numeric=["veh"])
    caption = rc.omission_caption(data)
    assert "off-scale" in caption and "omitted" not in caption


def test_a_complete_plate_carries_no_caption():
    assert rc.omission_caption(rc.ResponseData(variable_name="Dose")) == ""


# ── the axis decisions ──────────────────────────────────────────────────────

@pytest.mark.parametrize("values, expected", [
    ([0.1, 1.0, 10.0], True),            # two decades exactly
    ([1.0, 10.0], False),                # one
    ([0.001, 10.0], True),
    ([1.0], False),                      # a single point spans nothing
    ([], False),
    ([0.0, 0.0], False),
    ([0.0, 0.1, 10.0], True),            # the zero is ignored, the rest span two
    ([-1.0, 1.0], False),
])
def test_log_x_is_offered_once_the_doses_span_two_decades(values, expected):
    """A 0.1–10 µM series on a linear axis puts three quarters of its points in
    the leftmost tenth, which is the shape that makes people reach for log."""
    assert rcompute.spans_enough_decades(values) is expected


def test_the_zero_control_sits_one_decade_below_the_lowest_dose():
    """Dropping it is matplotlib's default and a silent substitution; squeezing
    it onto the axis is a lie about the concentration it was given."""
    assert rcompute.zero_position([0.1, 1.0, 10.0]) == pytest.approx(0.01)


def test_a_series_with_no_positive_dose_has_nowhere_to_put_a_zero():
    assert rcompute.zero_position([0.0]) is None
    assert rcompute.zero_position([]) is None


def test_a_curve_is_fitted_in_the_space_its_axis_is_drawn_in():
    """Decade-spaced doses fitted in linear x weight the top dose almost
    exclusively — the residual at 10 µM is a hundred times the one at 0.1 µM
    for the same proportional error, so the fitter ignores the half of the
    curve that decides the EC50."""
    assert rcompute.fit_space(log_x=True) == "log10"
    assert rcompute.fit_space(log_x=False) == "linear"


def test_a_boolean_is_not_a_dose():
    """``isinstance(True, int)`` is True in Python, which is how a stray flag
    becomes a concentration of 1."""
    assert rcompute.spans_enough_decades([True, True, True]) is False
    assert rcompute.zero_position([True]) is None


# ── the renderer, with nothing stubbed ──────────────────────────────────────
#
# ``test_response_tab.py`` monkeypatches ``apply_ax_style`` so its assertions
# are about the controller. That stub hid a real break for a while:
# ``draw_response`` called ``apply_ax_style(ax)`` while the function takes
# ``(ax, title, ylabel)``, so every real call raised ``TypeError`` and every
# test passed. §7.3 #1 — a harness that fails open will happily confirm
# anything. These call the renderer against the *real* helper.

def _axes():
    from matplotlib.figure import Figure

    return Figure().add_subplot(1, 1, 1)


def _curve(*xy):
    points = tuple(rc.ResponsePoint(f"m{i}", x, y, 0.5, 3, f"{x:g}")
                   for i, (x, y) in enumerate(xy))
    return rc.ResponseData(variable_name="Dose", unit="µM",
                           curves=[rc.ResponseCurve(24.0, points)])


def test_the_renderer_runs_against_the_real_style_helper():
    from well_viewer.features.response.renderer import draw_response

    ax = _axes()
    draw_response(None, ax, _curve((1.0, 5.0), (10.0, 9.0)),
                  title="Response vs Dose", ylabel="GFP mean")

    assert ax.has_data()
    assert ax.get_title() == "Response vs Dose"
    assert ax.get_ylabel() == "GFP mean"
    assert ax.get_xlabel() == "Dose (µM)"


def test_a_two_decade_series_opens_logarithmic_with_a_tick_per_dose():
    """matplotlib's log locator ticks the decades, which on 0.1/1/10 labels the
    points it happens to agree with and invents positions no well was given."""
    from well_viewer.features.response.renderer import draw_response

    ax = _axes()
    draw_response(None, ax, _curve((0.1, 2.0), (1.0, 5.0), (10.0, 9.0)))

    assert ax.get_xscale() == "log"
    assert [t.get_text() for t in ax.get_xticklabels()] == ["0.1", "1", "10"]


def test_a_zero_dose_is_drawn_off_scale_with_a_tick_reading_zero():
    from well_viewer.features.response.renderer import draw_response

    ax = _axes()
    draw_response(None, ax, _curve((0.0, 1.0), (0.1, 2.0), (1.0, 5.0),
                                   (10.0, 9.0)))

    labels = [t.get_text() for t in ax.get_xticklabels()]
    assert labels[0] == "0", f"the zero dose lost its tick: {labels}"
    assert ax.get_xlim()[0] < 0.01, "the off-scale column is outside the view"


def test_a_one_decade_series_stays_linear():
    from well_viewer.features.response.renderer import draw_response

    ax = _axes()
    draw_response(None, ax, _curve((1.0, 5.0), (5.0, 9.0)))
    assert ax.get_xscale() == "linear"


def test_the_caption_is_drawn_on_the_axes():
    """Not only on the status line: a saved figure outlives the session."""
    from well_viewer.features.response.renderer import draw_response

    data = _curve((1.0, 5.0))
    data.omitted = [("Ctrl", rc.NO_VALUE)]
    ax = _axes()
    draw_response(None, ax, data)

    texts = [t.get_text() for t in ax.texts]
    assert any("omitted" in s and "Ctrl" in s for s in texts), texts
