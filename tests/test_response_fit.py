"""Fitting a dose–response curve, and refusing to.

The rule this file is built around is §7's: **a number the data does not
support must never be printed as though it were measured.** A four-parameter
logistic will happily return an EC50 for a flat line, for two points, and for a
series that never plateaued — it is an optimiser, and it returns wherever it
stopped. Every one of those is a refusal here, with a reason of its own, and the
legend prints an em dash rather than the optimiser's last iterate.

The curves are generated from the model itself, so the answer is known exactly
and a fit that comes back wrong is wrong rather than merely different.
"""

from __future__ import annotations

import math

import pytest

from review.features.response import response_compute as rc

pytest.importorskip("scipy")


def _truth(x, *, bottom=5.0, top=95.0, ec50=1.0, hill=1.0):
    """A point on a 4PL curve, so the tests know what the answer is."""
    return bottom + (top - bottom) / (1 + 10 ** ((math.log10(ec50) - math.log10(x)) * hill))


DOSES = [0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0]


def _series(**kwargs):
    return DOSES, [_truth(x, **kwargs) for x in DOSES]


# ── it recovers a curve it was given ────────────────────────────────────────

def test_it_recovers_the_ec50_it_was_built_from():
    xs, ys = _series(ec50=2.5)
    fit = rc.fit_response(xs, ys, model=rc.FIT_4PL)
    assert fit.ok, fit.reason
    assert fit.ec50 == pytest.approx(2.5, rel=1e-3)


def test_it_recovers_the_plateaus_and_the_slope():
    xs, ys = _series(bottom=5.0, top=95.0, hill=1.7)
    fit = rc.fit_response(xs, ys, model=rc.FIT_4PL)
    assert fit.bottom == pytest.approx(5.0, rel=1e-2)
    assert fit.top == pytest.approx(95.0, rel=1e-2)
    assert fit.hill == pytest.approx(1.7, rel=1e-2)


def test_the_hill_model_pins_the_bottom_at_zero():
    """Not merely 'starts near zero' — it is removed from the fit."""
    xs, ys = _series(bottom=0.0, ec50=1.0)
    fit = rc.fit_response(xs, ys, model=rc.FIT_HILL)
    assert fit.ok, fit.reason
    assert fit.bottom == 0.0
    assert fit.ec50 == pytest.approx(1.0, rel=1e-3)


def test_the_hill_model_will_not_be_talked_out_of_its_zero():
    """A caller passing a bottom is overridden, not silently obeyed.

    The model *is* "bottom is zero"; a Hill fit with a bottom of 40 is a 4PL
    wearing the wrong name, and a legend saying Hill would be lying about what
    was fitted.
    """
    xs, ys = _series(bottom=0.0)
    fit = rc.fit_response(xs, ys, model=rc.FIT_HILL, fixed_bottom=40.0)
    assert fit.bottom == 0.0


def test_a_pinned_plateau_is_honoured_exactly():
    xs, ys = _series(bottom=5.0, top=95.0)
    fit = rc.fit_response(xs, ys, model=rc.FIT_4PL, fixed_top=100.0)
    assert fit.top == 100.0, "the pinned top was refitted"


def test_a_descending_curve_fits_too():
    """An inhibition series has a negative slope; it is not an error."""
    xs = DOSES
    ys = [_truth(x, bottom=95.0, top=5.0, ec50=1.0) for x in xs]
    fit = rc.fit_response(xs, ys, model=rc.FIT_4PL)
    assert fit.ok, fit.reason
    assert fit.ec50 == pytest.approx(1.0, rel=1e-3)


# ── it fits where the axis is drawn ─────────────────────────────────────────

def test_the_fit_is_in_log_space_not_linear():
    """Decade-spaced doses fitted in linear x weight the top dose almost alone.

    The witness is a low EC50 with a wide dose range: in linear x the residual
    at 30 is nine hundred times the residual at 0.01 for the same proportional
    error, so a linear fitter lands its EC50 far above the truth. Recovering
    0.03 to a tenth of a percent is only possible in log space.
    """
    xs, ys = _series(ec50=0.03)
    fit = rc.fit_response(xs, ys, model=rc.FIT_4PL)
    assert fit.ec50 == pytest.approx(0.03, rel=1e-3)


def test_a_zero_dose_is_kept_out_of_the_fit():
    """A vehicle control at x=0 has no log; letting it in would pull the bottom."""
    xs, ys = _series()
    fit = rc.fit_response([0.0, *xs], [0.0, *ys], model=rc.FIT_4PL)
    assert fit.n_points == len(xs), "the zero dose reached the fitter"
    assert fit.ec50 == pytest.approx(1.0, rel=1e-3)


# ── the interval ────────────────────────────────────────────────────────────

def test_the_interval_is_asymmetric_in_linear_space():
    """Symmetric about log10(EC50), so the upper arm is the longer one.

    Reporting a single ± would claim a lower bound the fit does not support.
    """
    xs, ys = _series(ec50=1.0)
    noisy = [y + (2.0 if i % 2 else -2.0) for i, y in enumerate(ys)]
    fit = rc.fit_response(xs, noisy, model=rc.FIT_4PL)
    assert fit.ok and fit.ec50_ci is not None, fit.reason
    low, high = fit.ec50_ci
    assert low < fit.ec50 < high
    assert (high - fit.ec50) > (fit.ec50 - low), (low, fit.ec50, high)


def test_the_interval_brackets_the_true_value():
    xs, ys = _series(ec50=2.0)
    noisy = [y + (1.5 if i % 2 else -1.5) for i, y in enumerate(ys)]
    fit = rc.fit_response(xs, noisy, model=rc.FIT_4PL)
    low, high = fit.ec50_ci
    assert low <= 2.0 <= high, (low, high)


def test_an_exactly_determined_fit_reports_no_interval_and_says_which():
    """Four points and four parameters: no residual, so no error estimate.

    ``(3.2–3.2)`` would claim a precision the data cannot support, and the
    reason has to distinguish "dose more" from "these doses cannot separate
    the parameters" because only the first is fixable.
    """
    xs = [0.1, 1.0, 10.0, 100.0]
    ys = [_truth(x) for x in xs]
    fit = rc.fit_response(xs, ys, model=rc.FIT_4PL)
    assert fit.ok and fit.ec50_ci is None
    assert "as many points as parameters" in fit.ci_reason
    assert "no CI" in rc.ec50_text(fit)


def test_the_no_ci_reason_is_carried_not_guessed():
    """A singular covariance with plenty of points is not 'too few doses'.

    Three doses, two free parameters and a perfect fit: there is a degree of
    freedom, so the honest reason is that the parameters are not separable —
    the wording the earlier version printed here named a cause it had not
    checked.
    """
    xs = [0.1, 1.0, 10.0]
    ys = [_truth(x) for x in xs]
    fit = rc.fit_response(xs, ys, model=rc.FIT_4PL,
                          fixed_top=95.0, fixed_bottom=5.0)
    assert fit.ok, fit.reason
    assert fit.ec50_ci is None
    assert "not separable" in fit.ci_reason
    assert "too few" not in fit.ci_reason


# ── the refusals, each with its own reason ──────────────────────────────────

def test_a_flat_series_is_refused_rather_than_given_the_initial_guess():
    """The regression this whole file exists for.

    A horizontal line fits a 4PL perfectly with top equal to bottom, at which
    point ``log_ec50`` is unconstrained and the optimiser hands back the
    initial guess. Before this check that guess printed in the legend as
    ``EC50 0.548 µM`` — a confident dose, from eight measurements that showed
    no response at all.
    """
    fit = rc.fit_response(DOSES, [50.0] * len(DOSES), model=rc.FIT_4PL)
    assert not fit.ok
    assert "flat" in fit.reason
    assert fit.ec50 is None
    assert rc.ec50_text(fit) == "EC50 — (no fit)"


def test_too_few_doses_names_how_many_the_model_needs():
    fit = rc.fit_response([1.0, 10.0], [1.0, 9.0], model=rc.FIT_4PL)
    assert not fit.ok
    assert "2 distinct" in fit.reason and "needs 4" in fit.reason


def test_the_hill_model_needs_one_fewer_dose_than_the_4pl():
    """Pinning the bottom removes a parameter, so three doses are enough."""
    xs = [0.1, 1.0, 10.0]
    ys = [_truth(x, bottom=0.0) for x in xs]
    assert rc.fit_response(xs, ys, model=rc.FIT_HILL).ok
    assert not rc.fit_response(xs, ys, model=rc.FIT_4PL).ok


def test_replicate_doses_do_not_count_as_distinct():
    """Six points at two doses is still two doses' worth of information."""
    xs = [1.0, 1.0, 1.0, 10.0, 10.0, 10.0]
    ys = [_truth(x) for x in xs]
    fit = rc.fit_response(xs, ys, model=rc.FIT_4PL)
    assert not fit.ok and "2 distinct" in fit.reason
    assert fit.n_points == 6, "the points were counted as doses"


def test_a_series_that_never_plateaus_is_refused():
    """Every dose below the inflection: the top is not in the data."""
    xs = [0.001, 0.003, 0.01, 0.03, 0.1]
    fit = rc.fit_response(xs, [_truth(x) for x in xs], model=rc.FIT_4PL)
    assert not fit.ok
    assert "edge of its range" in fit.reason or "plateau" in fit.reason


def test_an_ec50_outside_the_doses_tested_is_refused_when_the_plateaus_are_pinned():
    """With both plateaus pinned nothing can run to a bound, so the plateau
    check is the one that has to catch it.

    The doses here sit entirely on the bottom arm of a curve whose midpoint is
    a thousandfold above them; a fitted EC50 there is extrapolation, not
    measurement.
    """
    xs = [1e-4, 3e-4, 1e-3, 3e-3]
    ys = [_truth(x, bottom=5.0, top=95.0, ec50=1.0) for x in xs]
    fit = rc.fit_response(xs, ys, model=rc.FIT_4PL,
                          fixed_top=95.0, fixed_bottom=5.0)
    assert not fit.ok, rc.ec50_text(fit)
    assert "plateau" in fit.reason or "edge of its range" in fit.reason


def test_every_refusal_reads_differently():
    """A single "could not fit" would satisfy each test above on its own."""
    reasons = [
        rc.fit_response(DOSES, [50.0] * len(DOSES), model=rc.FIT_4PL).reason,
        rc.fit_response([1.0, 10.0], [1.0, 9.0], model=rc.FIT_4PL).reason,
        rc.fit_response([0.001, 0.003, 0.01, 0.03, 0.1],
                        [_truth(x) for x in (0.001, 0.003, 0.01, 0.03, 0.1)],
                        model=rc.FIT_4PL).reason,
    ]
    assert all(reasons), "a refusal came back with no reason"
    assert len(set(reasons)) == len(reasons), reasons


def test_no_model_is_not_a_refusal():
    """``FIT_NONE`` means the user has not asked, which is not a failure."""
    fit = rc.fit_response(DOSES, [_truth(x) for x in DOSES], model=rc.FIT_NONE)
    assert not fit.ok and fit.reason == ""


# ── what a refusal draws ────────────────────────────────────────────────────

def test_a_refused_fit_yields_no_curve_to_draw():
    """``None``, not an array of zeros: a line across the floor of the axes
    reads as a measured result."""
    fit = rc.fit_response(DOSES, [50.0] * len(DOSES), model=rc.FIT_4PL)
    assert rc.curve_y(fit, [1.0, 2.0]) is None


def test_the_fitted_curve_passes_through_its_own_points():
    xs, ys = _series(ec50=1.0)
    fit = rc.fit_response(xs, ys, model=rc.FIT_4PL)
    drawn = rc.curve_y(fit, xs)
    assert drawn is not None
    for expected, actual in zip(ys, drawn):
        assert actual == pytest.approx(expected, rel=1e-3)


def test_the_legend_clause_carries_the_unit_and_the_interval():
    xs, ys = _series(ec50=1.0)
    noisy = [y + (2.0 if i % 2 else -2.0) for i, y in enumerate(ys)]
    text = rc.ec50_text(rc.fit_response(xs, noisy, model=rc.FIT_4PL), "µM")
    assert text.startswith("EC50 ") and "µM" in text and "–" in text


# ── the refusals name the user's own variable ───────────────────────────────
#
# This is not a dose-response tab. It plots a measurement against any number
# someone varied — seeding density, MOI, pre-treatment time, temperature — and
# a caption telling a user who varied temperature that "no dose changed the
# measurement" is describing a plot they did not make. The plan's own naming
# rule: no control says "dose" or "concentration" unless the user typed it.

@pytest.mark.parametrize("x_name", ["Dose", "Seeding density", "Temperature"])
def test_a_flat_series_names_the_variable_the_user_varied(x_name):
    reason = rc.fit_response(DOSES, [50.0] * len(DOSES), model=rc.FIT_4PL,
                             x_name=x_name).reason
    assert x_name in reason, reason
    assert "dose" not in reason.lower() or x_name.lower() == "dose", reason


def test_every_refusal_carries_the_variables_name():
    """All four, not the one that happened to be checked."""
    name = "Temperature"
    below = [0.001, 0.003, 0.01, 0.03, 0.1]
    reasons = [
        rc.fit_response(DOSES, [50.0] * len(DOSES), model=rc.FIT_4PL,
                        x_name=name).reason,
        rc.fit_response([1.0, 10.0], [1.0, 9.0], model=rc.FIT_4PL,
                        x_name=name).reason,
        rc.fit_response(below, [_truth(x) for x in below], model=rc.FIT_4PL,
                        x_name=name).reason,
    ]
    for reason in reasons:
        assert name in reason, reason
        assert "dose" not in reason.lower(), reason


def test_the_no_ci_clause_names_it_too():
    xs = [0.1, 1.0, 10.0]
    fit = rc.fit_response(xs, [_truth(x) for x in xs], model=rc.FIT_4PL,
                          fixed_top=95.0, fixed_bottom=5.0, x_name="MOI")
    assert "MOI" in fit.ci_reason, fit.ci_reason
    assert "dose" not in fit.ci_reason.lower()


def test_the_default_is_neutral_rather_than_a_dose():
    """A caller with no name says ``x``, not ``dose``."""
    reason = rc.fit_response([1.0, 10.0], [1.0, 9.0], model=rc.FIT_4PL).reason
    assert "dose" not in reason.lower(), reason
