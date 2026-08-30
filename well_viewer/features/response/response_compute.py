"""The arithmetic behind a response curve — axis choices and the off-scale control.

Qt-free and ctx-free: every function here takes numbers and returns numbers, so
the decisions that are easy to get quietly wrong — whether an axis is
logarithmic, where a zero dose sits on one, what an EC50 means — are settled
where a test can drive them with a list.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace as _replace

#: A log x axis is offered by default once the positive values span this many
#: decades. Two is the point at which a linear axis stops being readable: a
#: 0.1–10 µM series on a linear axis puts three quarters of its points in the
#: leftmost tenth, which is the shape that makes people reach for log in the
#: first place.
LOG_X_DECADE_THRESHOLD = 2.0

#: How far below the lowest positive value the zero control is drawn, in
#: decades. One, so the gap reads as "off the scale" rather than as a measured
#: point that happens to be small.
ZERO_OFFSET_DECADES = 1.0


def spans_enough_decades(values, threshold: float = LOG_X_DECADE_THRESHOLD) -> bool:
    """Whether the positive members of *values* span *threshold* decades.

    Zero and negative values are ignored rather than treated as an error: a dose
    series legitimately contains a zero, and it is the *positive* spread that
    decides whether a log axis helps.
    """
    positives = [float(v) for v in values
                 if isinstance(v, (int, float)) and not isinstance(v, bool)
                 and float(v) > 0 and math.isfinite(float(v))]
    if len(positives) < 2:
        return False
    return math.log10(max(positives)) - math.log10(min(positives)) >= threshold


def zero_position(values, offset_decades: float = ZERO_OFFSET_DECADES) -> float | None:
    """Where a zero-valued point sits on a log x axis, or ``None``.

    **A decade below the lowest positive value, behind an axis break.** The two
    alternatives are both wrong and both common: dropping the point is
    matplotlib's default and a silent substitution — the vehicle control simply
    vanishes from a figure that still looks complete — while squeezing it onto
    the axis at some small positive number is a lie about the concentration it
    was given.

    ``None`` when there is no positive value to be below, which is a series that
    cannot be drawn on a log axis at all.
    """
    positives = [float(v) for v in values
                 if isinstance(v, (int, float)) and not isinstance(v, bool)
                 and float(v) > 0 and math.isfinite(float(v))]
    if not positives:
        return None
    return 10.0 ** (math.log10(min(positives)) - float(offset_decades))


def fit_space(log_x: bool) -> str:
    """``"log10"`` or ``"linear"`` — the space a curve is fitted in.

    **Fit where the axis is drawn.** Decade-spaced doses fitted in linear x
    weight the top dose almost exclusively: the residual at 10 µM is a hundred
    times the residual at 0.1 µM for the same proportional error, so the fitter
    is effectively ignoring the bottom of the curve — which is the half that
    decides the EC50.
    """
    return "log10" if log_x else "linear"


# ── curve fitting ───────────────────────────────────────────────────────────
#
# Fitted in log10(x), for the reason ``fit_space`` gives: decade-spaced doses
# fitted in linear x weight the top dose almost exclusively, so the fitter is
# effectively ignoring the half of the curve that decides the EC50.

#: The models offered. ``HILL`` is the four-parameter curve with its bottom
#: pinned at zero — the right model when the assay's zero is a real zero and
#: letting the bottom float would buy a better residual by inventing a floor
#: the experiment says is not there.
FIT_NONE = "none"
FIT_4PL = "4pl"
FIT_HILL = "hill"
FIT_MODELS = (FIT_NONE, FIT_4PL, FIT_HILL)

#: Human names for the popover, in offer order.
FIT_LABELS = {
    FIT_NONE: "None",
    FIT_4PL: "4PL (four-parameter logistic)",
    FIT_HILL: "Hill (bottom = 0)",
}

#: How close to a bound a parameter may sit before the fit is called
#: bound-limited, as a fraction of the bound's own span. The optimiser stops
#: exactly on a bound, so this is about floating-point slack, not judgement.
_BOUND_TOLERANCE = 1e-6

#: How far outside the tested doses ``log_ec50`` is allowed to wander before the
#: fit is refused. Two decades: an EC50 a hundredfold outside the range anyone
#: dosed is not a measurement, it is the fitter extrapolating a plateau it never
#: saw.
_LOG_EC50_SLACK = 2.0

#: How small a fitted top-to-bottom span counts as no response at all, as a
#: fraction of the measurements' own spread. One percent: below that the curve
#: is a horizontal line and ``log_ec50`` is unconstrained, so the optimiser
#: returns the initial guess and it prints as a confident dose.
_FLAT_FRACTION = 0.01


@dataclass(frozen=True)
class FitResult:
    """One curve's fit, or the named reason there isn't one.

    ``ok`` false is never a silent absence: :attr:`reason` always says which of
    the refusals it was, because "you have too few doses", "the curve has not
    plateaued" and "the optimiser did not converge" have three different fixes
    and only the first is about the experiment's design.
    """

    model: str = FIT_NONE
    ok: bool = False
    reason: str = ""
    bottom: float | None = None
    top: float | None = None
    log_ec50: float | None = None
    hill: float | None = None
    #: The EC50 in the variable's own units — ``10 ** log_ec50``, so it reads
    #: as a dose rather than as an exponent.
    ec50: float | None = None
    #: The confidence interval, **asymmetric in linear space**: it is symmetric
    #: about ``log_ec50`` and exponentiating makes the upper arm longer, which
    #: is the truth about a dose estimated on a log axis. Averaging the two arms
    #: into a ± would report a lower bound the fit does not support.
    ec50_ci: tuple[float, float] | None = None
    #: Why there is no interval, when :attr:`ec50_ci` is ``None`` on a fit that
    #: otherwise succeeded. Carried rather than guessed at the call site: "you
    #: have as many doses as parameters" and "the covariance came back
    #: singular" are different statements about the data, and a legend that
    #: printed the first for the second would name a cause nobody checked.
    ci_reason: str = ""
    n_points: int = 0
    n_distinct_x: int = 0


def _four_pl(log_x, bottom, top, log_ec50, hill):
    """The four-parameter logistic, in log10(x)."""
    import numpy as np
    return bottom + (top - bottom) / (1.0 + np.power(10.0, (log_ec50 - log_x) * hill))


def curve_y(fit: FitResult, xs):
    """The fitted y at each x in *xs*, or ``None`` when there is no fit.

    ``None`` rather than a flat line at zero: a fit that did not converge must
    draw *nothing*, and a caller handed an array of zeros would draw a line
    across the floor of the axes that looks like a measured result.
    """
    import numpy as np
    if not fit.ok:
        return None
    xs = np.asarray([float(x) for x in xs], dtype=float)
    with np.errstate(divide="ignore"):
        log_x = np.log10(xs)
    return _four_pl(log_x, fit.bottom, fit.top, fit.log_ec50, fit.hill)


def fit_response(xs, ys, *, model: str, fixed_top: float | None = None,
                 fixed_bottom: float | None = None) -> FitResult:
    """Fit *ys* against *xs* in log10 space, or refuse and say why.

    Only positive *xs* are fitted: a zero dose has no place on a log axis, and
    the renderer draws it off-scale behind a break for exactly that reason.
    Feeding it to the fitter as some small positive number would let a vehicle
    control pull the bottom of the curve.

    *fixed_top* / *fixed_bottom* pin a plateau the experiment already knows —
    a normalised assay whose top is 100, a background-subtracted one whose
    bottom is 0. A pinned parameter is removed from the fit rather than bounded
    to a narrow range, so it costs a degree of freedom rather than consuming
    one.

    Every refusal returns ``ok=False`` with a :attr:`FitResult.reason`. There is
    no path here that returns a number it does not stand behind.
    """
    import numpy as np

    if model not in (FIT_4PL, FIT_HILL):
        return FitResult(model=FIT_NONE)

    pairs = [(float(x), float(y)) for x, y in zip(xs, ys)
             if float(x) > 0 and np.isfinite(float(x)) and np.isfinite(float(y))]
    if model == FIT_HILL:
        # The model *is* "bottom is zero", so it is pinned here rather than
        # offered as a bound the optimiser could drift off.
        fixed_bottom = 0.0

    log_x = np.array([np.log10(x) for x, _ in pairs])
    y = np.array([v for _, v in pairs])
    distinct = sorted({round(v, 12) for v in log_x})
    free = [name for name, fixed in (("bottom", fixed_bottom), ("top", fixed_top))
            if fixed is None] + ["log_ec50", "hill"]
    base = FitResult(model=model, n_points=len(pairs), n_distinct_x=len(distinct))

    if len(distinct) < len(free):
        return _replace(base, reason=(
            f"{len(distinct)} distinct dose(s), and this model needs "
            f"{len(free)} to be determined at all."))

    y_span = float(np.ptp(y)) or 1.0
    lo_log, hi_log = float(distinct[0]), float(distinct[-1])
    bounds_lo, bounds_hi, guess = [], [], []
    if fixed_bottom is None:
        bounds_lo.append(float(y.min()) - y_span)
        bounds_hi.append(float(y.max()) + y_span)
        guess.append(float(y.min()))
    if fixed_top is None:
        bounds_lo.append(float(y.min()) - y_span)
        bounds_hi.append(float(y.max()) + y_span)
        guess.append(float(y.max()))
    bounds_lo += [lo_log - _LOG_EC50_SLACK, -10.0]
    bounds_hi += [hi_log + _LOG_EC50_SLACK, 10.0]
    guess += [(lo_log + hi_log) / 2.0, 1.0]

    def _model(lx, *free_values):
        values = dict(zip(free, free_values))
        return _four_pl(
            lx,
            fixed_bottom if fixed_bottom is not None else values["bottom"],
            fixed_top if fixed_top is not None else values["top"],
            values["log_ec50"], values["hill"])

    import warnings

    from scipy.optimize import OptimizeWarning, curve_fit
    try:
        with warnings.catch_warnings():
            # "Covariance of the parameters could not be estimated" is a
            # condition this function reports itself, through ``ci_reason``.
            # Left to escape it reaches the app's log as a stack of scipy
            # internals every time someone fits a flat series, which trains
            # people to ignore the log.
            warnings.simplefilter("ignore", OptimizeWarning)
            popt, pcov = curve_fit(_model, log_x, y, p0=guess,
                                   bounds=(bounds_lo, bounds_hi), maxfev=20000)
    except (RuntimeError, ValueError, TypeError) as exc:
        return _replace(base, reason=f"the fit did not converge ({exc.__class__.__name__}).")

    values = dict(zip(free, (float(v) for v in popt)))
    bottom = fixed_bottom if fixed_bottom is not None else values["bottom"]
    top = fixed_top if fixed_top is not None else values["top"]
    if abs(top - bottom) <= _FLAT_FRACTION * max(y_span, abs(top), abs(bottom), 1e-12):
        # Checked before the bounds are: a flat series fits *perfectly* with
        # top equal to bottom, at which point ``log_ec50`` is whatever the
        # initial guess was and the optimiser never moved it. That number then
        # prints in the legend as a dose, which is the worst kind of wrong —
        # a confident answer to a question the data did not ask.
        return _replace(base, reason=(
            "the response is flat: no dose changed the measurement, so there "
            "is no half-maximal one."))
    at_bound = [
        name for name, value, lo, hi in zip(free, popt, bounds_lo, bounds_hi)
        if min(abs(value - lo), abs(value - hi)) <= _BOUND_TOLERANCE * max(abs(hi - lo), 1.0)
    ]
    if at_bound:
        # §7: an optimum sitting on a bound is not an estimate, it is the
        # optimiser saying "further, and I cannot tell you how much further".
        # Reporting the bound as the answer would be a number nobody measured.
        return _replace(base, reason=(
            f"{', '.join(at_bound)} ran to the edge of its range — the doses "
            f"tested do not determine it."))

    log_ec50 = values["log_ec50"]
    if not (lo_log <= log_ec50 <= hi_log):
        return _replace(base, reason=(
            "the curve has not plateaued: the fitted EC50 lies outside the "
            "doses tested."))

    interval, ci_reason = _ec50_interval(log_ec50, pcov, free, len(pairs))
    return _replace(
        base, ok=True, bottom=bottom, top=top,
        log_ec50=log_ec50, hill=values["hill"],
        ec50=float(10.0 ** log_ec50),
        ec50_ci=interval, ci_reason=ci_reason)


def _ec50_interval(log_ec50: float, pcov, free, n_points: int):
    """``(interval, why_not)`` — the 95% interval, or ``None`` and the reason.

    ``None`` rather than a zero-width interval: an exactly-determined fit — as
    many points as free parameters — has no residual to estimate an error from,
    and printing ``(3.2–3.2)`` would claim a precision the data cannot support.

    The reason comes back with it because the two ways to get here are
    different facts about the experiment. Too few points is a design problem
    the user can fix by dosing more. A covariance that came back singular or
    infinite means the parameters are not separable at these doses, which more
    of the same doses will not fix.
    """
    import numpy as np

    dof = n_points - len(free)
    if dof <= 0:
        return None, "no CI: as many points as parameters"
    if pcov is None:
        return None, "no CI: the fit reported no covariance"
    index = free.index("log_ec50")
    variance = float(np.asarray(pcov)[index][index])
    if not np.isfinite(variance) or variance <= 0:
        return None, "no CI: the parameters are not separable at these doses"
    from scipy.stats import t as t_dist
    half = float(t_dist.ppf(0.975, dof)) * float(np.sqrt(variance))
    return (float(10.0 ** (log_ec50 - half)),
            float(10.0 ** (log_ec50 + half))), ""


def ec50_text(fit: FitResult, unit: str = "") -> str:
    """The legend's EC50 clause — a number with its interval, or an em dash.

    **The dash is never a small number.** A fit that did not converge, or whose
    optimum sat on a bound, has no EC50; printing the optimiser's last iterate
    there would put a dose in the legend that nothing measured and nothing
    supports, which is exactly the substitution §7 forbids.
    """
    if not fit.ok or fit.ec50 is None:
        return "EC50 — (no fit)"
    suffix = f" {unit}" if unit else ""
    head = f"EC50 {fit.ec50:.3g}{suffix}"
    if fit.ec50_ci is None:
        return f"{head} ({fit.ci_reason})" if fit.ci_reason else head
    low, high = fit.ec50_ci
    return f"{head} ({low:.3g}–{high:.3g})"
