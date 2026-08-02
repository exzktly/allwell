"""Rescale the *labels* on a time axis, and nothing else.

The pipeline reads each image's timepoint out of its filename and writes the
parsed hours into every per-well CSV (``util.timepoint.parse_hours``). When the
acquisition cadence is not what the filename token says — a plate named
``T01 … T12`` but imaged every four hours — those numbers are wrong by a constant
factor, and so is every axis drawn from them.

This module holds the correction the Properties sidebar offers: a multiplier
applied to what a time axis **prints**. The plotted data, the timepoint menus,
the Statistics tab, every cache key and every exported CSV keep the filename's
own hours. That is the whole point of doing it here — a label pass cannot
desynchronise a cache, resolve the wrong image, or put two rows of one series on
two different x-scales, because it never touches a value anything else reads.

The cost of that safety is a deliberate, visible disagreement: a rescaled axis
says 16 where the Timepoint dropdown beside it still says 4. So a rescaled axis
is **always** marked — :func:`axis_label` is the only way these labels reach a
figure, and it appends the factor. An unmarked rescaled axis would be
indistinguishable from one drawn on real hours, which is the one outcome this
feature must not produce.

Formatting goes through ``data.image_identity.normalize_tp``, the canonical
timepoint key form, so a rescaled label reads the same way every other timepoint
in the app does.

Qt-free and feature-free.
"""

from __future__ import annotations

import math

from well_viewer.data.image_identity import normalize_tp

__all__ = [
    "DEFAULT_FACTOR",
    "axis_label",
    "coerce_factor",
    "describe",
    "format_scaled",
    "is_active",
    "scaled",
]

#: No rescaling. A factor of exactly one is the "off" state — there is no
#: separate enable flag, because a factor of one and a disabled multiplier are
#: the same axis and keeping two ways to express it invites them to disagree.
DEFAULT_FACTOR = 1.0


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


def is_active(factor) -> bool:
    """True when *factor* actually changes the labels.

    Compared with a tolerance rather than ``!= 1.0`` because the factor arrives
    from a text field and round-trips through persistence; ``0.9999999`` is a
    user typing one, not a request to shift every label by a part in ten
    million.
    """
    factor = coerce_factor(factor)
    return not math.isclose(factor, DEFAULT_FACTOR, rel_tol=1e-9, abs_tol=1e-12)


def scaled(value, factor) -> float:
    """The number a rescaled axis prints in place of *value*.

    Multiplication of the filename's own hours — so an axis of 1, 2, 3 at a
    factor of four reads 4, 8, 12. Non-numeric input comes back as ``nan``
    rather than raising: this runs inside a tick formatter, where an exception
    takes the whole figure down.
    """
    try:
        return float(value) * coerce_factor(factor)
    except (TypeError, ValueError):
        return float("nan")


def format_scaled(value, factor) -> str:
    """*value* rescaled and formatted the way every timepoint in the app is.

    Uses ``normalize_tp`` — the canonical key form — so a rescaled tick reads
    like an ordinary timepoint and not like a stray float.
    """
    out = scaled(value, factor)
    if not math.isfinite(out):
        return ""
    return normalize_tp(out)


def describe(factor) -> str:
    """The phrase a rescaled axis carries, or ``""`` when nothing changed.

    Empty for an inactive factor so callers can append it unconditionally.
    """
    if not is_active(factor):
        return ""
    return f"filename value × {normalize_tp(coerce_factor(factor))}"


def axis_label(base: str, factor) -> str:
    """*base* with the rescale marker appended while a factor is in force.

    Every time axis builds its label through here. The marker is not decoration:
    the numbers on a rescaled axis disagree, on purpose, with the timepoint menus
    and with every exported CSV, and the label is the only place on the figure
    that says so. A copied or exported figure carries it too, because it is part
    of the label rather than an overlay.
    """
    marker = describe(factor)
    if not marker:
        return base
    if base.endswith(")"):
        # "Time (hours)" reads better as "Time (hours, filename value × 4)"
        # than as "Time (hours) (filename value × 4)".
        return f"{base[:-1]}, {marker})"
    return f"{base} ({marker})"
