"""Rescale the *labels* on a time axis, and nothing else.

The pipeline reads each image's timepoint out of its filename and writes the
parsed hours into every per-well CSV (``util.timepoint.parse_hours``). When the
acquisition cadence is not what the filename token says — a plate named
``T01 … T12`` but imaged every four hours — those numbers are wrong by a constant
factor, and so is every axis drawn from them. When the first image was taken
some way into the experiment — ``T01`` is two hours after the treatment, not at
it — they are wrong by a constant *shift* as well, and the two are independent:
a plate can need either, both, or neither.

This module holds the correction the Properties sidebar offers: an affine map,
``filename hours × factor + offset``, applied to what a time axis **prints**. The
plotted data, the timepoint menus, the Statistics tab, every cache key and every
exported CSV keep the filename's own hours. That is the whole point of doing it
here — a label pass cannot desynchronise a cache, resolve the wrong image, or put
two rows of one series on two different x-scales, because it never touches a
value anything else reads.

The cost of that safety is a deliberate, visible disagreement: a rescaled axis
says 16 where the Timepoint dropdown beside it still says 4. So a rescaled axis
is **always** marked — :func:`axis_label` is the only way these labels reach a
figure, and it appends the correction. An unmarked rescaled axis would be
indistinguishable from one drawn on real hours, which is the one outcome this
feature must not produce.

The factor and the offset travel together, in one :class:`TimeRescale` value, for
a reason worth stating: they were not always both here, and the natural way to
add the second one — a keyword argument defaulting to "no offset" — would have
let any of the twenty-odd call sites keep compiling while silently dropping half
the correction, printing an axis that is confidently wrong and marked as though
it were right. One value cannot be half-passed, and
``tests/test_time_rescale_is_swept.py`` holds the other half of that rule: no
module outside this one and its three owners reads the two numbers apart.

Formatting goes through ``data.image_identity.normalize_tp``, the canonical
timepoint key form, so a rescaled label reads the same way every other timepoint
in the app does.

Qt-free and feature-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from well_viewer.data.image_identity import normalize_tp

__all__ = [
    "DEFAULT_FACTOR",
    "DEFAULT_OFFSET",
    "OFF",
    "TimeRescale",
    "as_rescale",
    "axis_label",
    "coerce_factor",
    "coerce_offset",
    "describe",
    "displayed_interval",
    "factor_for_interval",
    "figure_timepoint_label",
    "figure_timepoints_label",
    "format_scaled",
    "is_active",
    "observed_interval",
    "rescale_for",
    "scaled",
]

#: No multiplication. A factor of exactly one is the "off" half of the map —
#: there is no separate enable flag, because a factor of one and a disabled
#: multiplier are the same axis and keeping two ways to express it invites them
#: to disagree.
DEFAULT_FACTOR = 1.0

#: No shift, for the same reason. Zero is a legitimate offset to *type* — it is
#: what the user returns to — and it is also the off state, which costs nothing
#: here because adding zero and adding nothing are the same axis.
DEFAULT_OFFSET = 0.0


def coerce_factor(value, fallback: float = DEFAULT_FACTOR) -> float:
    """Read a user-supplied factor, falling back when it cannot be one.

    Zero, negative and non-finite factors are rejected rather than clamped: each
    would collapse or mirror the axis, and a control that silently accepted one
    would leave the user looking at a plot that cannot be right with no
    indication of why. Returning *fallback* keeps the last good axis on screen
    so the caller can say what it ignored.
    """
    try:
        factor = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if not math.isfinite(factor) or factor <= 0.0:
        return float(fallback)
    return factor


def coerce_offset(value, fallback: float = DEFAULT_OFFSET) -> float:
    """Read a user-supplied offset, falling back when it cannot be one.

    A much wider door than :func:`coerce_factor`, and deliberately so. Negatives
    are ordinary — an offset of −2 is a plate whose ``T01`` sits two hours
    *before* the reference the user wants on the axis — and zero is the off
    state rather than a degenerate one. Only the values that are not numbers at
    all, or are infinite or NaN, are refused; each of those would put a tick at
    a place matplotlib cannot draw and take the label pass down with it.
    """
    try:
        offset = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if not math.isfinite(offset):
        return float(fallback)
    return offset


@dataclass(frozen=True)
class TimeRescale:
    """The whole correction a time axis prints with: ``× factor + offset``.

    One value rather than two arguments, because the two are read together at
    every one of the call sites and a site that read only one would draw an axis
    that is wrong *and* marked as correct. Frozen so a renderer cannot mutate
    the sidebar's setting by holding onto it, and coercing in ``__post_init__``
    so a value built straight from persisted or typed input is already safe to
    format with — there is no second, unchecked way in.
    """

    factor: float = DEFAULT_FACTOR
    offset: float = DEFAULT_OFFSET

    def __post_init__(self) -> None:
        object.__setattr__(self, "factor", coerce_factor(self.factor))
        object.__setattr__(self, "offset", coerce_offset(self.offset))


#: The identity map. Handy as a default and as the thing an inactive axis is
#: compared against; ``TimeRescale()`` builds an equal one, since it is frozen
#: and compares by value.
OFF = TimeRescale()


def as_rescale(value) -> TimeRescale:
    """Normalise what a caller passed into a :class:`TimeRescale`.

    A bare number is read as a factor with no offset. That is not a convenience
    for app code — which passes :func:`rescale_for` and is held to it by the
    sweep guard — but for the pure-logic tests and for the persisted stamp on a
    matplotlib axes, both of which predate the offset and describe a real,
    meaningful state: multiply, do not shift.
    """
    if isinstance(value, TimeRescale):
        return value
    return TimeRescale(factor=coerce_factor(value), offset=DEFAULT_OFFSET)


def rescale_for(plot_state) -> TimeRescale:
    """The correction currently in force, read off ``PlotState``.

    The single door between the sidebar's two stored numbers and every figure
    that prints them. Renderers call this rather than reaching for the fields,
    so adding a third term later is one edit here instead of twenty scattered
    ones — and so a renderer cannot pick up half of the current setting.

    Reads defensively because it is called from tick formatters and from tests
    that build a bare stand-in for the state object.
    """
    return TimeRescale(
        factor=getattr(plot_state, "time_scale", DEFAULT_FACTOR),
        offset=getattr(plot_state, "time_offset", DEFAULT_OFFSET),
    )


def observed_interval(timepoints) -> float | None:
    """The spacing between *timepoints*, or ``None`` when there is not one.

    The plate's own answer to "how far apart are these?", read off the hours the
    pipeline parsed out of the filenames. It is what makes the sidebar's
    "Interval per timepoint" row able to state a number that is true of the
    dataset instead of the multiplier's neutral ``1`` — a plate imaged at 0, 4,
    8, 12 has an interval of four hours, and a control that opens at one on that
    plate is not describing anything.

    ``None`` rather than a guess in the three cases where no single interval
    exists:

    * fewer than two timepoints, so there is no gap to measure;
    * a timepoint that is not a finite number, because dropping it and
      measuring across the hole reports a gap the plate does not have — a
      four-hourly plate with one unparseable point would come back as eight;
    * a gap of zero, which means duplicates rather than a cadence;
    * gaps that disagree — 0, 4, 8, 24 is a plate with a final overnight point,
      and calling that "every 4 hours" would state as measured a number that
      describes three of its four gaps. The caller falls back to showing the
      multiplier and says why, which is the honest reading of a plate that has
      no interval.

    The tolerance is relative, because the hours arrive from filename parsing
    and float arithmetic: 0, 3.9999999, 8 is a plate imaged every four hours,
    not an irregular one.
    """
    try:
        values = sorted(float(t) for t in (timepoints or []))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in values):
        return None
    if len(values) < 2:
        return None
    gaps = [b - a for a, b in zip(values, values[1:])]
    first = gaps[0]
    if first <= 0:
        return None
    if not all(math.isclose(g, first, rel_tol=1e-6, abs_tol=1e-9) for g in gaps):
        return None
    return first


def displayed_interval(timepoints, rescale) -> float | None:
    """What one step of the time axis *prints* as, or ``None`` with no interval.

    The measured spacing put through the correction currently in force, so the
    number the sidebar shows is the number a reader would get by subtracting two
    adjacent ticks. With the factor at its default this is the plate's own
    interval, which is the whole point: the control opens describing the data
    rather than describing the identity map.

    The offset does not appear, and must not: it shifts every label by the same
    amount, so it cancels between two adjacent ticks and changes no interval.
    """
    base = observed_interval(timepoints)
    if base is None:
        return None
    return base * as_rescale(rescale).factor


def factor_for_interval(timepoints, wanted, *,
                        fallback: float = DEFAULT_FACTOR) -> float:
    """The stored multiplier that makes the axis print *wanted* per timepoint.

    The inverse of :func:`displayed_interval`, and the reason the sidebar can
    take an interval from the user while ``PlotState`` goes on storing a factor:
    nothing downstream has to learn a second spelling of the correction, and a
    saved settings file written by an older build still means what it said.

    Falls back when the plate has no measurable interval to divide by, or when
    *wanted* is not a positive number — :func:`coerce_factor` rejects the same
    values for the same reason, and a control that silently accepted a zero
    would collapse the axis with nothing on screen saying why.
    """
    base = observed_interval(timepoints)
    if base is None:
        return coerce_factor(wanted, fallback)
    try:
        target = float(wanted)
    except (TypeError, ValueError):
        return float(fallback)
    if not math.isfinite(target) or target <= 0.0:
        return float(fallback)
    return coerce_factor(target / base, fallback)


def is_active(rescale) -> bool:
    """True when *rescale* actually changes the labels.

    Both halves count, and either alone is enough: an axis shifted by two hours
    disagrees with the timepoint menus exactly as loudly as one multiplied by
    four, and needs the marker exactly as much.

    Compared with a tolerance rather than ``!= 1.0`` / ``!= 0.0`` because the
    numbers arrive from text fields and round-trip through persistence;
    ``0.9999999`` is a user typing one, not a request to shift every label by a
    part in ten million.
    """
    rs = as_rescale(rescale)
    factor_on = not math.isclose(rs.factor, DEFAULT_FACTOR,
                                 rel_tol=1e-9, abs_tol=1e-12)
    offset_on = not math.isclose(rs.offset, DEFAULT_OFFSET,
                                 rel_tol=1e-9, abs_tol=1e-9)
    return factor_on or offset_on


def scaled(value, rescale) -> float:
    """The number a rescaled axis prints in place of *value*.

    The filename's own hours put through ``× factor + offset`` — so an axis of
    1, 2, 3 at a factor of four reads 4, 8, 12, and the same axis with an offset
    of −2 reads 2, 6, 10. Multiply first: the factor converts the filename's
    index into hours, and the offset is a shift *in hours*, so applying it
    before the multiplication would scale the user's own number out from under
    them.

    Non-numeric input comes back as ``nan`` rather than raising: this runs
    inside a tick formatter, where an exception takes the whole figure down.
    """
    rs = as_rescale(rescale)
    try:
        return float(value) * rs.factor + rs.offset
    except (TypeError, ValueError):
        return float("nan")


def format_scaled(value, rescale) -> str:
    """*value* rescaled and formatted the way every timepoint in the app is.

    Uses ``normalize_tp`` — the canonical key form — so a rescaled tick reads
    like an ordinary timepoint and not like a stray float.
    """
    out = scaled(value, rescale)
    if not math.isfinite(out):
        return ""
    return normalize_tp(out)


def describe(rescale) -> str:
    """The phrase a rescaled axis carries, or ``""`` when nothing changed.

    Written as the arithmetic it is — ``filename value × 4 + 2`` — so a reader
    can check the axis against the filenames without opening the sidebar. Each
    term appears only while it is doing something: a pure shift reads
    ``filename value + 2`` rather than ``filename value × 1 + 2``, which would
    invite the multiplier to be read as part of the correction.

    Empty for an inactive rescale so callers can append it unconditionally.
    """
    rs = as_rescale(rescale)
    if not is_active(rs):
        return ""
    parts = ["filename value"]
    if not math.isclose(rs.factor, DEFAULT_FACTOR, rel_tol=1e-9, abs_tol=1e-12):
        parts.append(f"× {normalize_tp(rs.factor)}")
    if not math.isclose(rs.offset, DEFAULT_OFFSET, rel_tol=1e-9, abs_tol=1e-9):
        # The sign is the operator, so the number beside it is never itself
        # signed — "− 2" rather than "+ -2", which reads as a typo.
        sign = "−" if rs.offset < 0 else "+"
        parts.append(f"{sign} {normalize_tp(abs(rs.offset))}")
    return " ".join(parts)


def figure_timepoint_label(value, rescale, *, unit: str = "h") -> str:
    """A timepoint named in a figure *title*, carrying its own marker.

    Returns the whole parenthetical body — ``"t=4 h"`` while the rescale is off,
    ``"t=4 h, filename value × 4"`` while it is on — so a caller writes
    ``f"… ({figure_timepoint_label(tp, rescale)})"`` and the unit stays beside
    the number instead of ending up outside the bracket.

    For figures with no time axis to hang a label on: the batch-export scatter
    plots, whose x and y are both metrics, so the title is the only place the
    timepoint appears. One call cannot produce a rescaled figure that reads like
    real hours.

    Figures that already carry the marker elsewhere — the Line panels on their
    axis label, the Bar panels via :func:`core.plot_style.mark_time_rescale` —
    keep using :func:`format_scaled` for their titles: two copies of the same
    sentence on one figure is noise.
    """
    text = format_scaled(value, rescale)
    if not text:
        return ""
    body = f"t={text}{' ' + unit if unit else ''}"
    marker = describe(rescale)
    return f"{body}, {marker}" if marker else body


def figure_timepoints_label(values, rescale, *, unit: str = "h") -> str:
    """Several timepoints named in a figure *title*, carrying one marker.

    The plural of :func:`figure_timepoint_label`, for a figure that draws a
    curve or a series **per** timepoint rather than picking one: ``"t=24 h"``
    for a single value and ``"t=[24, 48] h"`` for several, with the correction
    appended once — ``"t=[24, 48] h, filename value × 4"``. The bracket for the
    plural and the bare number for the singular follow
    :func:`data.metric_labels.timepoint_title`, because a bare list of one is a
    figure claiming to overlay something it does not.

    One marker, however many numbers: the correction is a property of the plate,
    not of each timepoint, and repeating it per value would read as several
    different corrections.

    Values that do not survive the rescale are dropped rather than printed
    empty, and a list that empties returns ``""`` — the same answer the caller
    gets for no timepoints at all, which is what a title clause has to be
    absent for rather than reading ``t=[] h``.
    """
    texts = [t for t in (format_scaled(v, rescale) for v in (values or ())) if t]
    if not texts:
        return ""
    if len(texts) == 1:
        body = f"t={texts[0]}"
    else:
        body = f"t=[{', '.join(texts)}]"
    if unit:
        body = f"{body} {unit}"
    marker = describe(rescale)
    return f"{body}, {marker}" if marker else body


def axis_label(base: str, rescale) -> str:
    """*base* with the rescale marker appended while a correction is in force.

    Every time axis builds its label through here. The marker is not decoration:
    the numbers on a rescaled axis disagree, on purpose, with the timepoint menus
    and with every exported CSV, and the label is the only place on the figure
    that says so. A copied or exported figure carries it too, because it is part
    of the label rather than an overlay.
    """
    marker = describe(rescale)
    if not marker:
        return base
    if base.endswith(")"):
        # "Time (hours)" reads better as "Time (hours, filename value × 4)"
        # than as "Time (hours) (filename value × 4)".
        return f"{base[:-1]}, {marker})"
    return f"{base} ({marker})"
