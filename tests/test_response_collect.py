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


def _ctx(*, wells, variable=None, assignments=None, selections=(), means=None,
         selected=(), variables=None, group_by="", response_state=None):
    """A ctx with the slices the collector reads, and a stubbed stats call.

    *selected* is the wells lit on the plate. It defaults to none because most
    of this file is about grouped plates — but it defaulting to none for every
    test is how the ungrouped plate went unmeasured until a user reported it,
    so the tests below that pass it are load-bearing rather than incidental.

    *variables* takes a whole tuple where *variable* takes one, for a plate that
    varies more than the dose. *group_by* and *response_state* are what the
    member seam reads: a real ``ResponseState`` rather than a namespace, so a
    field the tab actually keeps cannot be missing from the stub and pass.
    """
    declared = tuple(variables) if variables is not None else (
        (variable,) if variable else ())
    vset = sv.VariableSet(variables=declared,
                          assignments=dict(assignments or {}))
    slices = {"response": response_state}
    return SimpleNamespace(
        state=SimpleNamespace(
            feature_slice=lambda name: slices.get(name),
            dataset=SimpleNamespace(well_paths={w: f"/{w}.csv" for w in wells}),
            selection=SimpleNamespace(selections=list(selections),
                                      selected_wells=set(selected),
                                      variables=vset),
            plot=SimpleNamespace(group_by_variable=group_by,
                                 bar_pool_replicates=True,
                                 use_sem=True, line_order_rsets=[],
                                 line_order_wells=[],
                                 fc_vs_control_on=False, fc_control=None,
                                 fc_vs_t0_on=False, fc_control_mode="single",
                                 fc_control_map={}, fc_control_render="self",
                                 fc_baseline_tp="", fc_op_ctrl="ratio",
                                 fc_op_t0="ratio", fc_order="ctrl_first"),
            channel=SimpleNamespace(active_val_col="gfp_mean_intensity",
                                    active_channel="gfp", fluor_channels=()),
        ),
        runtime=SimpleNamespace(barplot=SimpleNamespace(
            current_keys=lambda: [s.get("name") for s in selections])),
    )


@pytest.fixture
def stub_stats(monkeypatch):
    """``compute_rep_stats`` and the ON cutoff, so the curve's shape is the test's."""
    means = {}

    def _stats(ctx, rset, target_t, threshold, use_sem, **kw):
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


def test_a_curve_is_fitted_in_log_space_whatever_the_axis_is_drawn_in():
    """Decade-spaced doses fitted in linear x weight the top dose almost
    exclusively — the residual at 10 µM is a hundred times the one at 0.1 µM
    for the same proportional error, so the fitter ignores the half of the curve
    that decides the EC50. ``fit_response`` therefore fits in log10 always.

    This replaces a test that asserted ``fit_space(log_x=True) == "log10"``.
    That function had no application caller, its rule (fit linearly when the
    axis is linear) was one ``fit_response`` never followed, and the assertion
    could not fail for any implementation returning those two strings — §7.8
    face two, guarding a decision nothing made. The behaviour is measured here
    instead: recovering a decade-spaced EC50 to a fraction of a percent is
    something only a log-space fit does.
    """
    ec50 = 0.03
    xs = [0.001, 0.01, 0.1, 1.0, 10.0, 30.0]
    ys = [100.0 / (1.0 + (x / ec50) ** -1.0) for x in xs]

    fit = rcompute.fit_response(xs, ys, model="4pl")

    assert fit.ok, fit.reason
    assert fit.ec50 == pytest.approx(ec50, rel=0.01), (
        "a linear-space fit cannot recover a decade-spaced EC50 this closely"
    )


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


# ── the empty state ─────────────────────────────────────────────────────────
#
# Bare axes are the same answer to six different mistakes, and they read as a
# broken tab rather than as a plate that has not been filled in yet. Each cause
# below has its own fix, so each has its own sentence naming the place that fix
# is made — and each is asserted to be *distinct*, because a single generic
# "nothing to plot" would pass a test that only checked a sentence was drawn.

def _reason(data, **kwargs):
    return rc.empty_reason(data, **kwargs)


def test_no_variable_chosen_says_to_choose_one():
    assert "under X" in _reason(rc.ResponseData(), variable_chosen=False)


def test_no_timepoint_ticked_says_so_rather_than_blaming_the_variables():
    """Every group has a dose; the user simply turned the timepoints off."""
    data = rc.ResponseData(variable_name="Dose", placed=3)
    assert "Timepoints" in _reason(data, timepoints_chosen=False)


def test_placed_groups_with_no_measurement_blame_the_timepoint():
    """Three groups have a dose and none was imaged then — not a fill problem.

    This is the case that has no other witness: ``omitted`` and ``non_numeric``
    are both empty, so without ``placed`` it is indistinguishable from a plate
    with no groups at all, whose fix is somewhere else entirely.
    """
    reason = _reason(rc.ResponseData(variable_name="Dose", placed=3))
    assert "measured" in reason and "timepoint" in reason
    assert "Variables" not in reason, "it blamed the fill for a timepoint gap"


def test_the_collector_records_how_many_groups_it_placed(stub_stats):
    """Driven through ``collect_response``, because ``placed`` is a *collector*
    field and a test that constructs ``ResponseData(placed=3)`` by hand proves
    only that ``empty_reason`` can read one.

    Both groups here have a perfectly good dose and neither was measured at the
    requested timepoint. Without the count the collector returns exactly what an
    empty plate returns, and the tab sends the user to fix a fill that is fine.
    """
    ctx = _ctx(wells=["A01", "A02"],
               variable=_dose(("1", 1.0), ("10", 10.0)),
               assignments={"dose": {"A01": "d0", "A02": "d1"}},
               selections=_groups(("low", ["A01"]), ("high", ["A02"])))
    stub_stats[("low", 48.0)] = None
    stub_stats[("high", 48.0)] = None

    data = rc.collect_response(ctx, "dose", [48.0])

    assert data.curves[0].points == ()
    assert data.placed == 2, "the placed groups were not counted"
    reason = _reason(data)
    assert "measured" in reason and "Variables" not in reason, reason


def test_a_plate_with_nothing_filled_places_nothing(stub_stats):
    """The other side of the count: it is not simply "number of groups"."""
    ctx = _ctx(wells=["A01"], variable=_dose(("1", 1.0)),
               assignments={"dose": {}},
               selections=_groups(("a", ["A01"])))
    data = rc.collect_response(ctx, "dose", [24.0])
    assert data.placed == 0


def test_no_groups_at_all_says_to_make_some():
    reason = _reason(rc.ResponseData(variable_name="Dose"))
    assert "No groups to plot" in reason


def test_nothing_filled_names_the_variables_tab():
    data = rc.ResponseData(variable_name="Dose",
                           omitted=[("a", rc.NO_VALUE), ("b", rc.NO_VALUE)])
    reason = _reason(data)
    assert "No group has a Dose value" in reason
    assert "Home → Variables" in reason


def test_wells_that_disagree_get_their_own_sentence():
    data = rc.ResponseData(variable_name="Dose",
                           omitted=[("a", rc.MIXED_VALUE)])
    assert "disagree" in _reason(data)


def test_values_that_are_not_numbers_get_their_own_sentence():
    data = rc.ResponseData(variable_name="Dose", non_numeric=["vehicle", "DMSO"])
    reason = _reason(data)
    assert "not a number" in reason or "is a number" in reason
    assert "off-scale" in reason


def test_a_mixture_of_causes_points_at_the_caption():
    data = rc.ResponseData(variable_name="Dose",
                           omitted=[("a", rc.NO_VALUE), ("b", rc.MIXED_VALUE)])
    assert "caption" in _reason(data)


def test_every_cause_reads_differently():
    """A generic sentence would satisfy each test above on its own."""
    causes = [
        _reason(rc.ResponseData(), variable_chosen=False),
        _reason(rc.ResponseData(variable_name="Dose", placed=3),
                timepoints_chosen=False),
        _reason(rc.ResponseData(variable_name="Dose", placed=3)),
        _reason(rc.ResponseData(variable_name="Dose")),
        _reason(rc.ResponseData(variable_name="Dose",
                                omitted=[("a", rc.NO_VALUE)])),
        _reason(rc.ResponseData(variable_name="Dose",
                                omitted=[("a", rc.MIXED_VALUE)])),
        _reason(rc.ResponseData(variable_name="Dose", non_numeric=["vehicle"])),
        _reason(rc.ResponseData(variable_name="Dose",
                                omitted=[("a", rc.NO_VALUE),
                                         ("b", rc.MIXED_VALUE)])),
    ]
    assert all(causes), "a cause produced no sentence at all"
    assert len(set(causes)) == len(causes), causes


def test_a_figure_with_points_has_no_empty_state():
    assert _reason(_curve((1.0, 5.0))) == ""


def test_the_reason_is_drawn_in_place_of_the_axes():
    """Not beside them: axis furniture around a sentence reads as a failed draw."""
    from well_viewer.features.response.renderer import draw_response

    data = rc.ResponseData(variable_name="Dose",
                           omitted=[("Ctrl", rc.NO_VALUE)])
    ax = _axes()
    draw_response(None, ax, data)

    texts = [t.get_text() for t in ax.texts]
    assert any("No group has a Dose value" in s for s in texts), texts
    assert list(ax.get_xticks()) == [], "the empty state kept its ticks"
    assert not any(s.get_visible() for s in ax.spines.values()), \
        "the empty state kept its frame"


def test_the_empty_state_still_lists_which_groups_were_dropped():
    """The reason names the fix; the caption names the members. Both, not one."""
    from well_viewer.features.response.renderer import draw_response

    data = rc.ResponseData(variable_name="Dose",
                           omitted=[("Ctrl", rc.NO_VALUE)])
    ax = _axes()
    draw_response(None, ax, data)

    texts = " | ".join(t.get_text() for t in ax.texts)
    assert "Ctrl" in texts, texts


# ── the fixture's plot state matches the real one ───────────────────────────

def test_the_stub_plot_state_carries_every_field_the_collector_reads():
    """A stub missing a field is a test that passes on code the app cannot run.

    The collector normalises through the shared fold-change path, which reads
    ten ``fc_*`` fields off ``ctx.state.plot``. A ``SimpleNamespace`` without
    them raises ``AttributeError`` — that much these tests would catch — but the
    reverse is the danger: a *tolerant* read added to make the stub work would
    swallow the same absence in the app, and fold change would silently stop
    applying on a real plate. So the stub is required to match, and this is
    what notices when the real state grows a field.
    """
    from well_viewer.core.plot_state import PlotState

    real = PlotState()
    stub = _ctx(wells=["A01"]).state.plot
    missing = [name for name in vars(real)
               if name.startswith("fc_") and name != "fc_dirty_scopes"
               and not hasattr(stub, name)]
    assert not missing, (
        f"the collect fixture's plot stub lacks {missing} — add them there "
        f"rather than making the collector's read tolerant")


# ── fold change ─────────────────────────────────────────────────────────────

def test_vs_t0_is_declined_on_one_timepoint_and_says_so(stub_stats):
    """With one timepoint every point is its own baseline: a flat line at 1.0.

    Declining it silently would be worse than drawing the flat line, because
    the toggle the user can see is on.
    """
    ctx = _ctx(wells=["A01", "A02"],
               variable=_dose(("1", 1.0), ("10", 10.0)),
               assignments={"dose": {"A01": "d0", "A02": "d1"}},
               selections=_groups(("low", ["A01"]), ("high", ["A02"])))
    ctx.state.plot.fc_vs_t0_on = True

    data = rc.collect_response(ctx, "dose", [24.0])

    assert data.t0_declined, "the declined axis was not recorded"
    assert [p.y for p in data.curves[0].points] == [10.0, 10.0], (
        "vs t0 was applied anyway and flattened the curve")
    assert "vs t0" in rc.omission_caption(data)


def test_vs_t0_is_applied_once_two_timepoints_are_drawn(stub_stats):
    """Each member divided by its own earliest drawn point."""
    ctx = _ctx(wells=["A01", "A02"],
               variable=_dose(("1", 1.0), ("10", 10.0)),
               assignments={"dose": {"A01": "d0", "A02": "d1"}},
               selections=_groups(("low", ["A01"]), ("high", ["A02"])))
    ctx.state.plot.fc_vs_t0_on = True
    stub_stats[("low", 0.0)] = 5.0
    stub_stats[("low", 24.0)] = 15.0
    stub_stats[("high", 0.0)] = 4.0
    stub_stats[("high", 24.0)] = 8.0

    data = rc.collect_response(ctx, "dose", [0.0, 24.0])

    assert not data.t0_declined
    at_zero = {p.member: p.y for p in data.curves[0].points}
    at_24 = {p.member: p.y for p in data.curves[1].points}
    assert at_zero == {"low": 1.0, "high": 1.0}
    assert at_24["low"] == pytest.approx(3.0)
    assert at_24["high"] == pytest.approx(2.0)


def test_a_normalised_figure_says_what_it_was_normalised_by(stub_stats):
    """A normalised value under a raw axis label is a number that does not mean
    what the axis says it means."""
    ctx = _ctx(wells=["A01", "A02"],
               variable=_dose(("1", 1.0), ("10", 10.0)),
               assignments={"dose": {"A01": "d0", "A02": "d1"}},
               selections=_groups(("low", ["A01"]), ("high", ["A02"])))
    ctx.state.plot.fc_vs_t0_on = True

    data = rc.collect_response(ctx, "dose", [0.0, 24.0])

    assert data.normalised, "the axis carried no fold-change suffix"


def test_raw_values_carry_no_suffix(stub_stats):
    ctx = _ctx(wells=["A01"], variable=_dose(("1", 1.0)),
               assignments={"dose": {"A01": "d0"}},
               selections=_groups(("a", ["A01"])))
    assert rc.collect_response(ctx, "dose", [24.0]).normalised == ""


# ── this tab has no un-pooled mode ──────────────────────────────────────────

def test_pooling_off_is_said_rather_than_obeyed(stub_stats):
    """A dose is a property of a condition; one point per well at the same x is
    a scatter of replicates rather than a curve.

    The Bar tab a click away *is* drawing one bar per well with the same switch
    off, so the two disagree — and neither would explain why.
    """
    ctx = _ctx(wells=["A01", "A02"], variable=_dose(("1", 1.0)),
               assignments={"dose": {"A01": "d0", "A02": "d0"}},
               selections=_groups(("both", ["A01", "A02"])))
    ctx.state.plot.bar_pool_replicates = False

    data = rc.collect_response(ctx, "dose", [24.0])

    assert len(data.curves[0].points) == 1, "it drew a point per well"
    assert data.curves[0].points[0].n == 2
    assert "pooled" in data.pooled_notice
    assert "pooled" in rc.omission_caption(data)


def test_pooling_on_says_nothing(stub_stats):
    ctx = _ctx(wells=["A01"], variable=_dose(("1", 1.0)),
               assignments={"dose": {"A01": "d0"}},
               selections=_groups(("a", ["A01"])))
    assert rc.collect_response(ctx, "dose", [24.0]).pooled_notice == ""


def test_the_fit_is_told_what_the_user_called_the_x_axis(stub_stats):
    """Driven through ``fit_curves``, because the collector is the only thing
    that knows the variable's name and a fitter told nothing calls it ``x``."""
    variable = sv.Variable(id="temp", name="Temperature", kind=sv.NUMBER,
                           unit="°C",
                           values=tuple(sv.VariableValue(f"t{i}", f"{v:g}", v)
                                        for i, v in enumerate((4.0, 25.0))))
    ctx = _ctx(wells=["A01", "A02"], variable=variable,
               assignments={"temp": {"A01": "t0", "A02": "t1"}},
               selections=_groups(("cold", ["A01"]), ("warm", ["A02"])))
    data = rc.collect_response(ctx, "temp", [24.0])

    fits = rc.fit_curves(data, rcompute.FIT_4PL)

    reason = fits[(24.0, "")].reason
    assert "Temperature" in reason, reason
    assert "dose" not in reason.lower(), reason


def test_the_caption_names_which_mistake_each_omission_was():
    """"No dose here" and "these wells disagree about the dose" are different
    mistakes with different fixes — one is a gap to fill, the other a
    contradiction to resolve — and the module's own roster comment says they are
    distinguished rather than pooled into "no value".

    The caption pooled them anyway: it read the members out of ``omitted`` and
    dropped the reason, so a member whose wells disagreed was captioned "no
    Dose". ``empty_reason`` branched correctly, but it only speaks when the
    figure is empty, while this is the sentence that reaches the axes and
    travels with a saved file.
    """
    from well_viewer.features.response import collect as rc

    data = rc.ResponseData(
        variable_name="Dose",
        omitted=[("grpA", rc.NO_VALUE), ("grpB", rc.MIXED_VALUE),
                 ("grpC", rc.MIXED_VALUE)],
    )

    caption = rc.omission_caption(data)

    assert "1 omitted: no Dose (grpA)" in caption
    assert "2 omitted: wells disagree about Dose (grpB, grpC)" in caption


# ── the shortfall counters, driven by the collector ─────────────────────────
#
# These were written by code no test reached: replacing either the append or the
# increment with `pass` left 131 response tests green. The caption tests build
# ``ResponseData`` by hand, which exercises the *formatter* and never the
# collector that fills it — §7.8's "ResponseData(placed=3) built by hand", the
# exact shape this repo has already been bitten by once.

def test_a_single_replicate_member_is_counted_as_having_no_spread(stub_stats, monkeypatch):
    """A member with one well has no spread to draw, and says which cause.

    The caption used to blame the control for every missing error bar — "the
    control had no spread to propagate" — on a plate that need have no control
    at all. ``compute_rep_stats`` returns NaN for a lone replicate and, with
    fold change off, the identity normaliser passes it straight through. Two
    causes with two different fixes (another replicate, or a control), so two
    counts rather than one sentence guessing between them.
    """
    def _stats(ctx, rset, target_t, threshold, use_sem, **kw):
        return (10.0, float("nan"), 0.5, 0.1)      # a mean, no spread

    import well_viewer.domain.stats_compute as sc
    monkeypatch.setattr(sc, "compute_rep_stats", _stats)

    ctx = _ctx(wells=["A01"],
               variable=_dose(("1", 1.0)),
               assignments={"dose": {"A01": "d0"}},
               selections=_groups(("solo", ["A01"])))

    data = rc.collect_response(ctx, "dose", [24.0])

    assert data.solo_spread == 1, "the lone replicate was not counted"
    assert data.undefined_spread == 0, "it was blamed on a control that does not exist"
    caption = rc.omission_caption(data)
    # "well", not "replicate": the caption names the unit because "add another
    # well" and "add another field" are different fixes, and the generic word
    # named neither. It is rendered from the same value the CSV's
    # ``spread_unit`` column carries (``well`` / ``fov``), so the sentence and
    # the file cannot drift apart.
    assert "a single well has no spread" in caption
    # The value the CSV's ``spread_unit`` column carries is the app-wide
    # vocabulary — ``well`` / ``fov``, from ``stats_compute.replicate_unit`` —
    # rather than a second pair of words. Concatenating a Response file with a
    # Heat Map one used to give a column reading FOV, fov, replicate and well.
    assert data.replicate_unit == "well"
    assert "control" not in caption


def test_a_point_normalisation_blanked_is_named_not_dropped(stub_stats, monkeypatch):
    """A curve that simply stops at its third dose looks like a plate nobody
    dosed higher. ``control_gaps`` is what stops that being silent — and nothing
    drove the collector into filling it.

    Reaching it takes fold change **on** with a control that resolves to
    nothing: the member is measured, so it survives the raw stage, and the
    normaliser is what blanks it. My first attempt at this test stubbed the raw
    stats to ``None`` instead, which blanks the point one stage earlier — before
    ``raw.append`` — so ``normalise`` saw an empty list and the counter was never
    reached. It passed, and a mutation deleting the append left it passing.
    """
    ctx = _ctx(wells=["A01", "A02"],
               variable=_dose(("1", 1.0), ("10", 10.0)),
               assignments={"dose": {"A01": "d0", "A02": "d1"}},
               selections=_groups(("lo", ["A01"]), ("hi", ["A02"])))
    ctx.state.plot.fc_vs_control_on = True
    from well_viewer.domain.control_ref import ControlRef

    ctx.state.plot.fc_control = ControlRef("set", "a-group-that-does-not-exist")

    data = rc.collect_response(ctx, "dose", [24.0])

    assert data.control_gaps, (
        "every point was blanked by an unresolvable control and none was named"
    )
    assert {m for m, _t in data.control_gaps} == {"lo", "hi"}
    assert "without a control" in rc.omission_caption(data)


def test_a_measured_plate_reports_no_shortfall_at_all(stub_stats):
    """The half that makes the two above mean something: a collector that
    incremented unconditionally would satisfy them and be wrong."""
    ctx = _ctx(wells=["A01", "A02"],
               variable=_dose(("1", 1.0), ("10", 10.0)),
               assignments={"dose": {"A01": "d0", "A02": "d1"}},
               selections=_groups(("lo", ["A01"]), ("hi", ["A02"])))

    data = rc.collect_response(ctx, "dose", [24.0])

    assert (data.solo_spread, data.undefined_spread, data.control_gaps) == (0, 0, [])
    assert rc.omission_caption(data) == ""


# ── a plate whose wells are lit but not grouped ─────────────────────────────
#
# The reported defect: a Number variable defined and filled, wells clicked on
# the plate, nothing drawn — under a sentence telling the user to select wells
# on the plate, which is what they had already done. The members came from
# ``plot_replicate_sets``, which is the user's *groups* alone, so a plate
# nobody had grouped had no members at all.
#
# The reasoning behind that had the annotation in the wrong place. A sample
# variable is assigned per **well** (``VariableSet.assignments`` maps a well
# token to a value id), so a lit well carries its own dose and there is nothing
# a group adds except pooled replicates.

def test_wells_lit_on_an_ungrouped_plate_each_get_a_point_at_their_own_dose(
        stub_stats):
    """The reported case. Members are the lit wells, keyed by their tokens."""
    ctx = _ctx(wells=["A01", "A02", "A03"],
               variable=_dose(("0.1", 0.1), ("1", 1.0), ("10", 10.0)),
               assignments={"dose": {"A01": "d0", "A02": "d1", "A03": "d2"}},
               selections=(),
               selected={"A01", "A02", "A03"})
    data = rc.collect_response(ctx, "dose", [24.0])

    assert [(p.member, p.x) for p in data.curves[0].points] == [
        ("A01", 0.1), ("A02", 1.0), ("A03", 10.0)]
    assert data.placed == 3
    assert rc.empty_reason(data) == ""


def test_a_lit_well_the_variable_never_reached_is_named_not_dropped(stub_stats):
    """The omission machinery has to work in the well key space too.

    Two of three wells dosed. The third must come back in ``omitted`` under
    its own token — a curve that quietly draws two of the three wells the user
    lit is the silent substitution this whole module exists to prevent.
    """
    ctx = _ctx(wells=["A01", "A02", "A03"],
               variable=_dose(("1", 1.0), ("10", 10.0)),
               assignments={"dose": {"A01": "d0", "A02": "d1"}},
               selections=(),
               selected={"A01", "A02", "A03"})
    data = rc.collect_response(ctx, "dose", [24.0])

    assert [p.member for p in data.curves[0].points] == ["A01", "A02"]
    assert data.omitted == [("A03", rc.NO_VALUE)]


def test_a_group_still_pools_its_wells_when_those_wells_are_also_lit(stub_stats):
    """The fallback must not shadow the groups, or it trades one bug for another.

    ``A01`` and ``A02`` are one group at one dose *and* both lit on the plate.
    The fixture puts them at the same dose so that the wrong answer — two
    points, one per well — is distinguishable from the right one, which is a
    single pooled point named for the group.
    """
    ctx = _ctx(wells=["A01", "A02"],
               variable=_dose(("1", 1.0),),
               assignments={"dose": {"A01": "d0", "A02": "d0"}},
               selections=_groups(("treated", ["A01", "A02"])),
               selected={"A01", "A02"})
    data = rc.collect_response(ctx, "dose", [24.0])

    assert [(p.member, p.n) for p in data.curves[0].points] == [("treated", 2)]


def test_an_ungrouped_plate_with_nothing_lit_still_says_so(stub_stats):
    """The empty state stays reachable — the fallback is not a fallback to all.

    Falling back to every loaded well would draw a plate nobody asked for, and
    would make the "select wells" sentence unreachable rather than accurate.
    """
    ctx = _ctx(wells=["A01", "A02"],
               variable=_dose(("1", 1.0), ("10", 10.0)),
               assignments={"dose": {"A01": "d0", "A02": "d1"}},
               selections=(), selected=())
    data = rc.collect_response(ctx, "dose", [24.0])

    assert not data.has_points() and data.placed == 0
    assert "select wells on the plate" in rc.empty_reason(data)


# ── several curves on one figure ────────────────────────────────────────────
#
# Without a series variable every member landed on one curve, sorted by x, so
# two cell lines each given a dose series were drawn as a single line doubling
# back at every dose — a zig-zag that reads as a curve, one legend entry, and
# with a fit on, one EC50 fitted across both conditions.
#
# The fixtures below give the two series *different shapes* (one responds, one
# is flat) and put them at the same doses. That is deliberate: with both series
# responding identically, a collector that ignored the split entirely would
# produce points a lenient assertion could not tell from the right answer.

def _two_series_ctx(stub_stats, *, series_assignments=None):
    # Four doses, not three: a 4PL has four parameters and declines outright
    # with fewer distinct x values, so a three-dose fixture would test the
    # refusal rather than the per-series fit.
    means = {"H-lo": 10.0, "H-mid": 30.0, "H-hi": 75.0, "H-top": 92.0,
             "U-lo": 20.0, "U-mid": 22.0, "U-hi": 21.0, "U-top": 20.5}
    for (name, value) in means.items():
        stub_stats[(name, 24.0)] = value
    dose = _dose(("0.1", 0.1), ("1", 1.0), ("10", 10.0), ("100", 100.0))
    line = sv.Variable(
        id="cl", name="Cell line", kind=sv.TEXT, unit="",
        values=(sv.VariableValue("hela", "HeLa", None),
                sv.VariableValue("u2os", "U2OS", None)))
    cells = series_assignments if series_assignments is not None else {
        "A01": "hela", "A02": "hela", "A03": "hela", "A04": "hela",
        "B01": "u2os", "B02": "u2os", "B03": "u2os", "B04": "u2os"}
    vset = sv.VariableSet(
        variables=(dose, line),
        assignments={"dose": {"A01": "d0", "A02": "d1", "A03": "d2",
                              "A04": "d3", "B01": "d0", "B02": "d1",
                              "B03": "d2", "B04": "d3"},
                     "cl": cells})
    ctx = _ctx(wells=["A01", "A02", "A03", "A04", "B01", "B02", "B03", "B04"],
               selections=_groups(("H-lo", ["A01"]), ("H-mid", ["A02"]),
                                  ("H-hi", ["A03"]), ("H-top", ["A04"]),
                                  ("U-lo", ["B01"]), ("U-mid", ["B02"]),
                                  ("U-hi", ["B03"]), ("U-top", ["B04"])))
    ctx.state.selection.variables = vset
    return ctx


def test_without_a_series_every_member_is_still_one_curve(stub_stats):
    """The existing behaviour, pinned so the feature cannot change it."""
    ctx = _two_series_ctx(stub_stats)
    data = rc.collect_response(ctx, "dose", [24.0])
    assert len(data.curves) == 1
    assert len(data.curves[0].points) == 8
    assert data.series_name == ""


def test_a_series_variable_splits_the_members_into_one_curve_each(stub_stats):
    """The reported gap: two conditions, two curves, not one zig-zag."""
    ctx = _two_series_ctx(stub_stats)
    data = rc.collect_response(ctx, "dose", [24.0], series_id="cl")

    assert data.series_name == "Cell line"
    assert [c.key for c in data.curves] == [(24.0, "hela"), (24.0, "u2os")]
    assert [c.series_label for c in data.curves] == ["HeLa", "U2OS"]
    # The values, not the counts: a split that put the wrong members on each
    # curve produces two three-point curves just as happily.
    assert [(p.x, p.y) for p in data.curves[0].points] == [
        (0.1, 10.0), (1.0, 30.0), (10.0, 75.0), (100.0, 92.0)]
    assert [(p.x, p.y) for p in data.curves[1].points] == [
        (0.1, 20.0), (1.0, 22.0), (10.0, 21.0), (100.0, 20.5)]


def test_each_series_is_fitted_on_its_own(stub_stats):
    """One model, one EC50 per curve — which is why they are comparable.

    Keyed by ``(timepoint, series)``: a timepoint-keyed dict would hand the
    second series' fit to the first, drawing one curve with the other's fitted
    line and EC50.
    """
    ctx = _two_series_ctx(stub_stats)
    data = rc.collect_response(ctx, "dose", [24.0], series_id="cl")
    fits = rc.fit_curves(data, rcompute.FIT_4PL)

    assert set(fits) == {(24.0, "hela"), (24.0, "u2os")}
    assert {f.model for f in fits.values()} == {"4pl"}
    # Each fit's plateaus come from *its own* points — HeLa climbs 10 → 92,
    # U2OS sits flat around 21. Asserted as values rather than as "the two
    # differ", because a pooled fit filed under both keys also differs from
    # nothing and would satisfy any weaker check; these numbers can only come
    # from the curve each key names.
    hela, u2os = fits[(24.0, "hela")], fits[(24.0, "u2os")]
    assert hela.ok and u2os.ok
    assert hela.top == pytest.approx(92.0, rel=0.15)
    assert u2os.top == pytest.approx(21.0, rel=0.15)
    # The span, not the bottom: a 4PL puts its lower asymptote at or below the
    # lowest dose measured, so HeLa's bottom is ~6.9 rather than the 10.0 its
    # lowest point reads. The span is what separates a responder from a flat
    # line and is what a pooled fit across both series could not produce.
    assert hela.top - hela.bottom > 50.0
    assert u2os.top - u2os.bottom < 5.0


def test_the_series_follow_the_variables_own_value_order(stub_stats):
    """Not alphabetical, and not well order — the order the user arranged.

    U2OS is declared second, so it is second here even though its wells sort
    after HeLa's either way; the check that bites is the reversed variable
    below, where alphabetical and declared order disagree.
    """
    ctx = _two_series_ctx(stub_stats)
    vset = ctx.state.selection.variables
    flipped = sv.Variable(
        id="cl", name="Cell line", kind=sv.TEXT, unit="",
        values=(sv.VariableValue("u2os", "U2OS", None),
                sv.VariableValue("hela", "HeLa", None)))
    ctx.state.selection.variables = sv.VariableSet(
        variables=(vset.by_id("dose"), flipped), assignments=vset.assignments)

    data = rc.collect_response(ctx, "dose", [24.0], series_id="cl")
    assert [c.series_label for c in data.curves] == ["U2OS", "HeLa"]


def test_a_member_the_series_variable_never_reached_gets_its_own_curve(stub_stats):
    """Named, not dropped and not folded into a neighbour.

    A member with a dose but no cell line is real data. Dropping it would take
    a measured point off the figure over an annotation gap, and folding it into
    either series would assert it was that condition.
    """
    ctx = _two_series_ctx(stub_stats, series_assignments={
        "A01": "hela", "A02": "hela", "A03": "hela", "A04": "hela",
        "B01": "u2os"})
    data = rc.collect_response(ctx, "dose", [24.0], series_id="cl")

    labels = [c.series_label for c in data.curves]
    assert "no Cell line" in labels, labels
    stray = next(c for c in data.curves if c.series_label == "no Cell line")
    assert {p.member for p in stray.points} == {"U-mid", "U-hi", "U-top"}


def test_a_series_on_the_x_variable_is_declined(stub_stats):
    """One curve per dose, each one point long, is not a figure."""
    ctx = _two_series_ctx(stub_stats)
    data = rc.collect_response(ctx, "dose", [24.0], series_id="dose")
    assert len(data.curves) == 1 and data.series_name == ""


# ── the Spread: FOV toggle, which the tab built and never read ──────────────

def _fov_ctx(monkeypatch, *, well_stats, fov_stats):
    """A one-well plate whose two stats producers disagree.

    They must disagree, or the test cannot tell which one the collector called
    — a fixture where both return the same spread passes against the bug.
    """
    import well_viewer.domain.stats_compute as sc
    monkeypatch.setattr(sc, "compute_rep_stats",
                        lambda *a, **k: well_stats)
    monkeypatch.setattr(sc, "compute_rep_per_fov_stats",
                        lambda *a, **k: fov_stats)
    import well_viewer.core.channel_state_controller as csc
    monkeypatch.setattr(csc, "on_cutoff_for_column", lambda *a, **k: 0.0)
    return _ctx(wells=["A01"], variable=_dose(("1", 1.0)),
                assignments={"dose": {"A01": "d0"}},
                selections=_groups(("solo", ["A01"])))


def test_one_well_with_fov_spread_gets_an_error_bar(monkeypatch):
    """The report: a single well, Spread: FOV on, and no whisker drawn.

    Across wells a one-well member has one replicate and no spread. Across
    FOVs it has as many replicates as it has fields, which is exactly what the
    toggle asks for — and the collector called the well producer regardless.
    """
    ctx = _fov_ctx(monkeypatch,
                   well_stats=(10.0, float("nan"), 0.5, float("nan")),
                   fov_stats=(10.0, 2.5, 0.5, 0.05))
    ctx.state.plot.use_fov_replicates = True

    data = rc.collect_response(ctx, "dose", [24.0])

    point = data.curves[0].points[0]
    assert point.err == 2.5, "the per-FOV spread never reached the point"
    assert data.solo_spread == 0
    assert "no error bar" not in rc.omission_caption(data)


def test_with_the_toggle_off_the_well_producer_is_still_used(monkeypatch):
    """The default is unchanged — the toggle is what selects the other one."""
    ctx = _fov_ctx(monkeypatch,
                   well_stats=(10.0, 1.25, 0.5, 0.02),
                   fov_stats=(10.0, 2.5, 0.5, 0.05))
    ctx.state.plot.use_fov_replicates = False

    data = rc.collect_response(ctx, "dose", [24.0])
    assert data.curves[0].points[0].err == 1.25


def test_a_missing_fov_spread_is_captioned_as_a_lone_fov(monkeypatch):
    """One FOV has no spread either, and the caption must say which unit.

    Blaming "a single replicate" with the FOV toggle on sends the user to add
    another well when what they need is another field.
    """
    ctx = _fov_ctx(monkeypatch,
                   well_stats=(10.0, 1.25, 0.5, 0.02),
                   fov_stats=(10.0, float("nan"), 0.5, float("nan")))
    ctx.state.plot.use_fov_replicates = True

    data = rc.collect_response(ctx, "dose", [24.0])
    caption = rc.omission_caption(data)
    assert data.solo_spread == 1
    assert "a single FOV has no spread" in caption, caption
    # The sentence says FOV; the file says fov. One stored value, rendered for
    # each audience, so the caption and the CSV cannot name different units.
    assert data.replicate_unit == "fov"


# ── fraction above threshold on y ───────────────────────────────────────────

def test_the_curves_can_plot_the_fraction_above_threshold(monkeypatch):
    """The third return value of the stats call, which the tab used to discard.

    The mean and the fraction are deliberately different numbers here, so a
    collector that ignored the metric and drew the mean cannot pass.
    """
    ctx = _fov_ctx(monkeypatch,
                   well_stats=(10.0, 1.0, 0.42, 0.03),
                   fov_stats=(10.0, 2.0, 0.42, 0.05))

    means = rc.collect_response(ctx, "dose", [24.0], y_metric=rc.Y_MEAN)
    fracs = rc.collect_response(ctx, "dose", [24.0], y_metric=rc.Y_FRACTION)

    assert means.curves[0].points[0].y == 10.0
    assert fracs.curves[0].points[0].y == 0.42
    assert fracs.curves[0].points[0].err == 0.03
    assert fracs.y_metric == rc.Y_FRACTION


def test_an_unknown_y_metric_falls_back_to_the_mean(monkeypatch):
    """A hand-edited saved plot must not draw an axis nobody can name."""
    ctx = _fov_ctx(monkeypatch,
                   well_stats=(10.0, 1.0, 0.42, 0.03),
                   fov_stats=(10.0, 2.0, 0.42, 0.05))
    data = rc.collect_response(ctx, "dose", [24.0], y_metric="nonsense")
    assert data.y_metric == rc.Y_MEAN
    assert data.curves[0].points[0].y == 10.0


def test_a_fraction_that_was_never_computed_is_a_gap_not_a_zero(monkeypatch):
    """§7: a fraction of 0.0 means "nothing above the cutoff" and is a result.

    A member with no fraction must leave a gap in the curve, not a point on the
    floor that reads as a treatment which killed everything.
    """
    ctx = _fov_ctx(monkeypatch,
                   well_stats=(10.0, 1.0, float("nan"), float("nan")),
                   fov_stats=(10.0, 2.0, float("nan"), float("nan")))
    data = rc.collect_response(ctx, "dose", [24.0], y_metric=rc.Y_FRACTION)
    assert not data.has_points(), "an uncomputed fraction was drawn as a point"


def test_a_fraction_of_zero_is_drawn_because_it_is_a_measurement(monkeypatch):
    """The other half of the same rule, and the one a NaN-vs-zero slip breaks."""
    ctx = _fov_ctx(monkeypatch,
                   well_stats=(10.0, 1.0, 0.0, 0.01),
                   fov_stats=(10.0, 2.0, 0.0, 0.01))
    data = rc.collect_response(ctx, "dose", [24.0], y_metric=rc.Y_FRACTION)
    assert data.curves[0].points[0].y == 0.0


# ── which shortfall it actually was ─────────────────────────────────────────

def test_a_per_fov_spread_that_is_undefined_blames_the_fov_not_a_control(
        monkeypatch):
    """The caption the FOV commit claimed to fix, on the path it enabled.

    A two-well member whose per-FOV reduction yields no spread. The old split
    asked ``n <= 1`` against the **well** count, so two wells meant "not a lone
    replicate" and the shortfall was blamed on a control — on a plate that has
    no control at all. The raw spread answers it directly in either mode.
    """
    import well_viewer.domain.stats_compute as sc
    import well_viewer.core.channel_state_controller as csc
    monkeypatch.setattr(csc, "on_cutoff_for_column", lambda *a, **k: 0.0)
    monkeypatch.setattr(sc, "compute_rep_stats",
                        lambda *a, **k: (10.0, 1.0, 0.5, 0.02))
    monkeypatch.setattr(sc, "compute_rep_per_fov_stats",
                        lambda *a, **k: (10.0, float("nan"), 0.5, float("nan")))

    ctx = _ctx(wells=["A01", "A02"], variable=_dose(("1", 1.0)),
               assignments={"dose": {"A01": "d0", "A02": "d0"}},
               selections=_groups(("pair", ["A01", "A02"])))
    ctx.state.plot.use_fov_replicates = True

    data = rc.collect_response(ctx, "dose", [24.0])
    caption = rc.omission_caption(data)

    assert data.solo_spread == 1 and data.undefined_spread == 0
    assert "a single FOV has no spread" in caption, caption
    # The sentence says FOV; the file says fov. One stored value, rendered for
    # each audience, so the caption and the CSV cannot name different units.
    assert data.replicate_unit == "fov"
    assert "control" not in caption, caption


class _ZeroControl:
    """A denominator whose control resolves fine and reads 0 at every t."""

    def series_for(self, _member):
        return "ctl", "ok", {24.0: (0.0, 0.0)}, {24.0: (0.0, 0.0)}, {}


def test_a_control_that_reads_zero_says_so_rather_than_blaming_a_gap(
        stub_stats, monkeypatch):
    """The commonest control in a fraction experiment, and the worst caption.

    A control with nothing above the cutoff has a fraction of 0.0. Dividing by
    it is undefined, so the points blank — and the figure used to report "1
    without a control", sending the user to fix a control assignment that is
    already correct. The two have opposite fixes, so they are counted apart.
    """
    ctx = _ctx(wells=["A01"], variable=_dose(("1", 1.0)),
               assignments={"dose": {"A01": "d0"}},
               selections=_groups(("treated", ["A01"])))

    def _blanking(points, _name):
        return [(p[0], float("nan"), 0.0, float("nan"), *p[4:]) for p in points]

    _blanking.denominators = _ZeroControl()
    _blanking.op_ctrl = "ratio"
    monkeypatch.setattr(rc, "_member_normaliser",
                        lambda *a, **k: _blanking)

    data = rc.collect_response(ctx, "dose", [24.0], y_metric=rc.Y_FRACTION)
    caption = rc.omission_caption(data)

    assert data.zero_control == [("treated", 24.0)], data.zero_control
    assert data.control_gaps == [], data.control_gaps
    assert "the control reads 0 there" in caption, caption
    assert "without a control" not in caption, caption


def test_a_control_that_never_resolved_is_still_a_gap(stub_stats, monkeypatch):
    """The other half — the distinction is only worth drawing if both fire."""
    ctx = _ctx(wells=["A01"], variable=_dose(("1", 1.0)),
               assignments={"dose": {"A01": "d0"}},
               selections=_groups(("treated", ["A01"])))

    class _Unresolved:
        def series_for(self, _member):
            return None, None, {}, {}, {}

    def _blanking(points, _name):
        return [(p[0], float("nan"), 0.0, float("nan"), *p[4:]) for p in points]

    _blanking.denominators = _Unresolved()
    _blanking.op_ctrl = "ratio"
    monkeypatch.setattr(rc, "_member_normaliser", lambda *a, **k: _blanking)

    data = rc.collect_response(ctx, "dose", [24.0], y_metric=rc.Y_FRACTION)
    assert data.control_gaps == [("treated", 24.0)]
    assert data.zero_control == []


def test_subtracting_a_zero_control_is_not_reported_as_a_problem(
        stub_stats, monkeypatch):
    """Subtracting zero is well defined — only a ratio is undefined by it."""
    ctx = _ctx(wells=["A01"], variable=_dose(("1", 1.0)),
               assignments={"dose": {"A01": "d0"}},
               selections=_groups(("treated", ["A01"])))

    def _blanking(points, _name):
        return [(p[0], float("nan"), 0.0, float("nan"), *p[4:]) for p in points]

    _blanking.denominators = _ZeroControl()
    _blanking.op_ctrl = "subtract"
    monkeypatch.setattr(rc, "_member_normaliser", lambda *a, **k: _blanking)

    data = rc.collect_response(ctx, "dose", [24.0], y_metric=rc.Y_FRACTION)
    assert data.zero_control == []
    assert data.control_gaps == [("treated", 24.0)]


# ── the tab-local column actually reaches the producers ─────────────────────
#
# The whole `val_col` seam was unguarded: a reviewer reverted `collect_response`
# to read the shared `active_val_col` and dropped `val_col=` from the stats
# call, and every test in this file and the tab's still passed. The stubs took
# `**kw` and threw it away, which is a stub agreeing with the caller about a
# value neither of them looks at.

def test_the_tab_local_column_is_what_the_stats_producer_reads(monkeypatch):
    """Captured from the call, not inferred from the state it was built from."""
    import well_viewer.domain.stats_compute as sc
    import well_viewer.core.channel_state_controller as csc

    seen = {}

    def _stats(ctx, rset, target_t, threshold, use_sem, **kw):
        seen["val_col"] = kw.get("val_col")
        return (10.0, 1.0, 0.5, 0.05)

    monkeypatch.setattr(sc, "compute_rep_stats", _stats)
    monkeypatch.setattr(csc, "on_cutoff_for_column", lambda *a, **k: 0.0)
    ctx = _ctx(wells=["A01"], variable=_dose(("1", 1.0)),
               assignments={"dose": {"A01": "d0"}},
               selections=_groups(("solo", ["A01"])))

    rc.collect_response(ctx, "dose", [24.0], val_col="rfp_total_intensity")
    assert seen["val_col"] == "rfp_total_intensity", (
        "the tab-local column never reached compute_rep_stats")


def test_the_per_fov_producer_gets_the_same_column(monkeypatch):
    """A seam on one producer and not the other reverts on the FOV toggle."""
    import well_viewer.domain.stats_compute as sc
    import well_viewer.core.channel_state_controller as csc

    seen = {}
    monkeypatch.setattr(sc, "compute_rep_stats",
                        lambda *a, **k: (10.0, 1.0, 0.5, 0.05))

    def _fov(ctx, rset, target_t, threshold, use_sem, **kw):
        seen["val_col"] = kw.get("val_col")
        return (10.0, 2.0, 0.5, 0.05)

    monkeypatch.setattr(sc, "compute_rep_per_fov_stats", _fov)
    monkeypatch.setattr(csc, "on_cutoff_for_column", lambda *a, **k: 0.0)
    ctx = _ctx(wells=["A01"], variable=_dose(("1", 1.0)),
               assignments={"dose": {"A01": "d0"}},
               selections=_groups(("solo", ["A01"])))
    ctx.state.plot.use_fov_replicates = True

    rc.collect_response(ctx, "dose", [24.0], val_col="rfp_total_intensity")
    assert seen["val_col"] == "rfp_total_intensity"


def test_the_on_cutoff_is_resolved_for_the_tab_s_own_column(monkeypatch):
    """Not for the shared channel — that is a fraction against another cutoff.

    ``on_cutoff_for_column`` skips its own column split when handed a
    ``channel=``, so passing the shared ``active_channel`` while the column was
    tab-local read the wrong channel's ThreshFracOn: with the tab on RFP and
    the band on GFP, every fraction was "cells above RFP intensity, judged by
    GFP's cutoff". A plausible curve and a wrong number.
    """
    import well_viewer.domain.stats_compute as sc
    import well_viewer.core.channel_state_controller as csc

    seen = {}

    def _cutoff(ctx, col, channel=None, **kw):
        seen["col"], seen["channel"] = col, channel
        return 0.0

    monkeypatch.setattr(csc, "on_cutoff_for_column", _cutoff)
    monkeypatch.setattr(sc, "compute_rep_stats",
                        lambda *a, **k: (10.0, 1.0, 0.5, 0.05))
    ctx = _ctx(wells=["A01"], variable=_dose(("1", 1.0)),
               assignments={"dose": {"A01": "d0"}},
               selections=_groups(("solo", ["A01"])))
    ctx.state.channel.active_channel = "gfp"

    rc.collect_response(ctx, "dose", [24.0], val_col="rfp_mean_intensity")
    assert seen["col"] == "rfp_mean_intensity"
    assert seen["channel"] is None, (
        f"the cutoff was pinned to the shared channel {seen['channel']!r} "
        "instead of being derived from this tab's column")


# ── Group by, which is the second layer of split ────────────────────────────

def _two_factor_plate():
    """A dose series run on two cell lines: the plate Group by is for.

    Every combination is filled, and the doses are assigned **against** the
    cell-line order — A01/B01 low, A02/B02 high — so a collector that pooled by
    cell line alone and a collector that crosses do not produce the same points
    by coincidence.
    """
    dose = _dose(("1", 1.0), ("10", 10.0))
    line = sv.Variable(
        id="cl", name="Cell line", kind=sv.TEXT, unit="",
        values=(sv.VariableValue("hela", "HeLa", None),
                sv.VariableValue("u2os", "U2OS", None)))
    assignments = {
        "dose": {"A01": "d0", "A02": "d1", "B01": "d0", "B02": "d1"},
        "cl": {"A01": "hela", "A02": "hela", "B01": "u2os", "B02": "u2os"},
    }
    return dose, line, assignments


def _response_slice(**kwargs):
    from well_viewer.features.response.state import ResponseState

    return ResponseState(**kwargs)


def test_group_by_a_second_variable_draws_a_curve_per_value(stub_stats):
    """The reported defect: Group by anything but the axis emptied the figure.

    Grouping pools every well holding a value, so *Group by: Cell line* handed
    this tab one member per cell line carrying **both** doses — and a member
    with two doses has no position on a dose axis, so every one of them was
    refused as "wells disagree about Dose" and the figure came back blank under
    a sentence blaming the user's fill.

    The value of each point is asserted, not merely that curves exist: the stub
    keys its means on the member name, so a crossing that produced the wrong
    members would draw the default rather than these numbers.
    """
    dose, line, assignments = _two_factor_plate()
    # Keyed on the member names the crossing produces — the axis label first,
    # then the grouping's, which is the order the ids are given in.
    stub_stats[("1 µM · HeLa", 24.0)] = 5.0
    stub_stats[("10 µM · HeLa", 24.0)] = 50.0
    stub_stats[("1 µM · U2OS", 24.0)] = 7.0
    stub_stats[("10 µM · U2OS", 24.0)] = 70.0
    ctx = _ctx(wells=["A01", "A02", "B01", "B02"],
               variables=(dose, line), assignments=assignments,
               group_by="cl",
               response_state=_response_slice(x_variable_id="dose"))

    data = rc.collect_response(ctx, "dose", [24.0], ("cl",))

    assert data.omitted == [], (
        "a member was refused an x under a grouping that crosses the axis")
    assert [c.series_label for c in data.curves] == ["HeLa", "U2OS"]
    assert [[(p.x, p.y) for p in c.points] for c in data.curves] == [
        [(1.0, 5.0), (10.0, 50.0)], [(1.0, 7.0), (10.0, 70.0)]]
    # The member name says which cell of the plate it pooled, so the CSV and
    # the per-sample control editor can tell four members apart.
    assert [p.member for p in data.curves[0].points] == [
        "1 µM · HeLa", "10 µM · HeLa"]


def test_the_two_layers_cross_into_one_curve_each(stub_stats):
    """Group by and Series together: one curve per (outer, inner) value.

    The legend label carries both, in the order the layers were given, so a
    reader can tell four curves apart rather than seeing two pairs that share a
    name.
    """
    dose, line, assignments = _two_factor_plate()
    medium = sv.Variable(
        id="med", name="Medium", kind=sv.TEXT, unit="",
        values=(sv.VariableValue("dmem", "DMEM", None),
                sv.VariableValue("rpmi", "RPMI", None)))
    assignments = dict(assignments)
    assignments["dose"] = dict(assignments["dose"], C01="d0", C02="d1")
    assignments["cl"] = dict(assignments["cl"], C01="hela", C02="hela")
    assignments["med"] = {"A01": "dmem", "A02": "dmem", "B01": "dmem",
                          "B02": "dmem", "C01": "rpmi", "C02": "rpmi"}
    ctx = _ctx(wells=["A01", "A02", "B01", "B02", "C01", "C02"],
               variables=(dose, line, medium), assignments=assignments,
               group_by="cl",
               response_state=_response_slice(x_variable_id="dose",
                                              series_variable_ids=["med"]))

    data = rc.collect_response(ctx, "dose", [24.0], ("cl", "med"))

    assert [c.series_label for c in data.curves] == [
        "HeLa · DMEM", "HeLa · RPMI", "U2OS · DMEM"]
    assert data.series_name == "Cell line · Medium"
    # Every curve is a real dose series rather than a single doubled-back line.
    assert all(len(c.points) == 2 for c in data.curves)
    # U2OS was never run in RPMI, so there is no empty curve claiming it was.
    assert "U2OS · RPMI" not in [c.series_label for c in data.curves]


def test_a_grouping_the_axis_cannot_cross_falls_back_rather_than_emptying(stub_stats):
    """Grouping and axis filled on disjoint wells: draw the user's own groups.

    The same choice ``plot_replicate_sets`` makes for a grouping that groups
    nothing — an empty figure three clicks from where the fix is made explains
    nothing, and what was left out is reported on the figure itself.
    """
    dose, line, assignments = _two_factor_plate()
    assignments = {"dose": assignments["dose"], "cl": {}}
    ctx = _ctx(wells=["A01", "A02"], variables=(dose, line),
               assignments=assignments, group_by="cl",
               selections=_groups(("low", ["A01"]), ("high", ["A02"])),
               response_state=_response_slice(x_variable_id="dose"))

    data = rc.collect_response(ctx, "dose", [24.0], ("cl",))

    assert [p.member for p in data.curves[0].points] == ["low", "high"]
