"""The arithmetic behind a response curve — axis choices and the off-scale control.

Qt-free and ctx-free: every function here takes numbers and returns numbers, so
the decisions that are easy to get quietly wrong — whether an axis is
logarithmic, where a zero dose sits on one, what an EC50 means — are settled
where a test can drive them with a list.
"""

from __future__ import annotations

import math

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
