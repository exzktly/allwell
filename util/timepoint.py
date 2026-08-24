"""Single source of truth for reading a timepoint token as hours.

A timepoint reaches the app as a string from three directions — a filename
field, a CSV column, and a combo box — and four places used to turn it into a
number: the pipeline, the pipeline's auto-threshold pass, the viewer, and the
remote runner. Three of the four implemented the same grammar; the fourth
implemented a looser one. They now all call this module.

The grammar, tried in order:

1. ``DDdHHhMMm`` with any component optional — ``"02d04h30m"`` → 52.5,
   ``"2d"`` → 48.0.
2. A standalone unit suffix — ``"48h"``, ``"90min"``, ``"2days"``.
3. A bare number, read as hours — ``"24"``, ``"1.5"``.
4. A prefixed ordinal, taking the *trailing* digit run — ``"T01"`` → 1.0,
   ``"day3"`` → 3.0.

Anything else is ``None``: an opaque label the caller should keep as-is rather
than guess at. That matters because :func:`well_viewer.data.image_identity.normalize_tp`
turns a parsed value into the canonical key ``f"{hours:g}"`` and passes an
unparsed one through unchanged — so a parser that guesses invents a key the
rest of the app will not recognise.

Deliberately strict about where digits may come from. A looser reading that
scavenged the first digit run anywhere in the token treated ``"5%"`` as 5 hours
and disagreed with this one about ``"a1b2"`` (1 versus 2) and ``"12.5.3"``
(12 versus 5.3). It only ever fed a sort, so nothing broke, but two answers to
"what hour is this token" is one too many.

A digit here means an **ASCII** digit. The ``\\d`` class is Unicode by default
and matches some six hundred decimal digits, and ``int()`` / ``float()`` convert
all of them, so ``"1h"``, ``"１h"`` and ``"١h"`` — three distinct filename tokens
naming three distinct files — all read as one hour and collapsed onto one
timepoint key. The patterns spell ``[0-9]`` and the bare-number branch is gated
on ``str.isascii``; a token whose digits come from anywhere else is opaque, and
an opaque label is kept as-is rather than guessed at.

Stdlib-only, so ``process_microscopy`` and ``services/`` can both import it
without breaching the no-``well_viewer`` contract (ARCHITECTURE §6.2) — the same
reason ``util.well_token`` and ``util.schema`` live here. Listed in
``services.remote_runner._REQUIRED_REMOTE_FILES`` so a remote run receives it.
"""

from __future__ import annotations

import re
from typing import Any

# All components optional, but ``parse_hours`` requires at least one to match —
# an empty match would otherwise accept every string.
_FULL_RE = re.compile(r"(?:([0-9]{1,4})d)?(?:([0-9]{1,2})h)?(?:([0-9]{1,2})m)?",
                      re.IGNORECASE)
# Spelled-out units are enumerated rather than matched with ``\w*``: a trailing
# wildcard made ``"2hpCh"`` parse as two hours.
_UNIT_RE = re.compile(
    r"([0-9]+(?:\.[0-9]+)?)\s*(h(?:ours?)?|d(?:ays?)?|m(?:in(?:utes?)?)?)",
    re.IGNORECASE,
)
_TRAILING_NUM_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)$")


def _token(value: Any) -> str:
    """Stringify *value* for parsing, treating only ``None`` as absent.

    The obvious ``str(value or "")`` is wrong here: ``0`` and ``0.0`` are falsy,
    so a numeric baseline timepoint — the first column of most time courses —
    stringified to ``""`` and read as no timepoint at all. Timepoints reach this
    module both as text (``"0"``, truthy) and as numbers off a CSV column or a
    parsed filename field (``0.0``, falsy), so the two spellings disagreed at
    exactly ``t=0`` and nowhere else.

    ``well_viewer.data.image_identity._raw_token`` is the same rule for the
    viewer's identity tokens. The duplication is the cost of this module's
    stdlib-only contract — the pipeline and ``services/`` import it and may not
    reach into ``well_viewer``.
    """
    return "" if value is None else str(value)


def parse_hours(value: Any) -> float | None:
    """Parse a timepoint token into fractional hours, or ``None``.

    ``None`` means "not a timepoint I can read" — an opaque label such as
    ``"treated_arm_2"``. Callers must keep those as-is rather than substitute a
    number; see the module docstring for why.

    Note that ``"nan"`` and ``"inf"`` parse, because ``float()`` accepts them.
    They are legitimately-parsed values, not failures, and a caller that needs
    to tell them apart from a real measurement should check ``math.isfinite``
    rather than relying on the return being ``None``.
    """
    s = _token(value).strip()
    if not s:
        return None

    m = _FULL_RE.fullmatch(s)
    if m and any(m.groups()):
        days = int(m.group(1) or 0)
        hours = int(m.group(2) or 0)
        mins = int(m.group(3) or 0)
        return days * 24.0 + hours + mins / 60.0

    m = _UNIT_RE.fullmatch(s)
    if m:
        val = float(m.group(1))
        unit = m.group(2)[0].lower()
        if unit == "h":
            return val
        if unit == "d":
            return val * 24.0
        if unit == "m":
            return val / 60.0

    # ``float`` reads Unicode decimal digits — ``float("１")`` is 1.0 — so the
    # ASCII digit classes above are not enough on their own: a bare
    # non-ASCII-digit token would still get a number out of this branch. Two
    # filename tokens that differ by which alphabet wrote their digits are two
    # different tokens naming two different files, and giving them one hours
    # value merges them.
    if s.isascii():
        try:
            return float(s)
        except ValueError:
            pass

    m = _TRAILING_NUM_RE.search(s)
    if m:
        return float(m.group(1))
    return None


def sort_key(value: Any) -> tuple[bool, float, str]:
    """Chronological sort key for a timepoint token.

    ``(unparseable, hours, token)`` — unparseable tokens sort last as a block
    and then alphabetically among themselves, so a dropdown built from mixed
    tokens is stable and puts the real timepoints first.

    Six call sites wrote this tuple out by hand before, which is how the
    ordering drifted between the tabs that show timepoints.

    The falsy-zero bug bit hardest here, because the fallback is not "no value"
    but *unparseable*: a numeric ``0`` stringified to ``""``, parsed as ``None``,
    and sorted into the opaque-label block at the end. The baseline timepoint —
    the one every time course starts at — came last in a list of numbers, after
    the twelve-hour column.
    """
    token = _token(value).strip()
    hours = parse_hours(token)
    return (hours is None, hours if hours is not None else 0.0, token)
