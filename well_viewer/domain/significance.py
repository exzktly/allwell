"""How a significance result is written down — the stars, the number, or both.

One module because two surfaces state the same result and must state it the
same way: the Statistics tab prints a text report of every comparison, and the
Bar plot draws a bracket over the pair of bars that were compared. Those two
had a private asterisk ladder each — ``_sig_marker`` in the Statistics
controller and ``_significance_marker`` in the bar renderer — identical today
and free to drift tomorrow, which is exactly the shape of a number that reads
one way on the panel and another way in the report beside it.

Pure: stdlib only, no Qt, no matplotlib, no app state. Everything here takes a
p-value and returns a string.

**A p-value is never printed as zero.** ``scipy`` returns a float, and a
sufficiently extreme test underflows the double to exactly ``0.0``. Formatting
that with ``%g`` yields ``"0"``, which claims the impossible — a probability of
zero — where the truth is that the number is smaller than a float can hold. It
is written ``p < 1e-300`` instead, which is both true and visibly a bound
rather than a measurement. That is CLAUDE.md §7's computed-as-zero rule applied
to the one quantity in the app where a printed zero is always a lie.
"""

from __future__ import annotations

import math

#: How the text over a significance bracket reads. ``"stars"`` is the asterisk
#: ladder alone (the original, and still the default); ``"p"`` is the p-value
#: itself; ``"both"`` prints the ladder with the number after it in brackets.
#: Ordered as the picker offers them.
SIGNIFICANCE_LABEL_MODES = ("stars", "p", "both")

#: Human labels for the modes, for a combo box. Keyed by the stored value, so
#: the wording can change without migrating anybody's saved view state.
SIGNIFICANCE_LABEL_MODE_NAMES = {
    "stars": "Stars (*)",
    "p": "p-value",
    "both": "Stars + p-value",
}

#: Below this, a p-value is printed as a bound rather than as a number. It is
#: the smallest magnitude the automatic format still renders with a significant
#: digit; anything under it has underflowed or is close enough to underflow
#: that the digits are no longer meaningful.
_P_FLOOR = 1e-300


def significance_stars(p: float) -> str:
    """Asterisk code for a p-value — ``***``, ``**``, ``*`` or ``ns``.

    Strict ``<`` at every threshold, so a p of exactly 0.05 is ``ns`` and one of
    exactly 0.01 is ``*``: the conventional reading of "significant at the 5%
    level" is *below* the level, not at it.

    A p that is not a finite number — ``None``, a NaN from a test that could not
    be computed — is ``ns`` rather than an exception, because this is called
    while drawing and a figure must not die on one unusable comparison. The
    caller that wants to know the difference reads :func:`format_p_value`, which
    returns an empty string for the same input.
    """
    try:
        pv = float(p)
    except (TypeError, ValueError):
        return "ns"
    if not math.isfinite(pv):
        return "ns"
    if pv < 0.001:
        return "***"
    if pv < 0.01:
        return "**"
    if pv < 0.05:
        return "*"
    return "ns"


def format_p_value(p: float) -> str:
    """The p-value itself, as it should be printed — ``""`` when there isn't one.

    Three significant figures through ``%.3g``, which is what a p-value is
    quoted to and which switches to an exponent by itself once the number leaves
    the range digits read well in: ``0.0312``, ``0.203``, ``3.24e-08``.

    An empty string for anything unprintable (``None``, NaN, an infinity) rather
    than a placeholder, so a caller can tell "no p-value" from a p-value that
    happens to be large. See the module docstring for why a zero comes back as
    ``< 1e-300`` and not as ``0`` — the returned text carries its own relation
    in that case, which is why :func:`significance_label` looks for it rather
    than always writing an ``=``.
    """
    try:
        pv = float(p)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(pv):
        return ""
    if pv < _P_FLOOR:
        return f"< {_P_FLOOR:g}"
    return f"{pv:.3g}"


def significance_label(p: float, mode: str = "stars", *,
                       adjusted: bool = False) -> str:
    """The text drawn over one significance bracket.

    *mode* is one of :data:`SIGNIFICANCE_LABEL_MODES`; anything else reads as
    ``"stars"``, so a saved view state written by a future version — or edited
    by hand — degrades to the original behaviour rather than to a blank bracket.

    *adjusted* names which p is being shown. The bar renderer draws the
    Benjamini-Hochberg adjusted p whenever the run has one, and ``p`` and
    ``p_BH`` are different claims about the same pair: with three or more groups
    the raw p is the one that overstates the result, so a figure printing an
    adjusted number under a bare ``p =`` would be quoting the stricter test
    under the looser test's name. The Statistics tab's report already writes
    ``p_BH``; this writes it the same way.

    A mode that asks for the number and has no number to print falls back to the
    stars alone — the bracket still says whether the comparison passed, which is
    the part that does not depend on the value being printable.
    """
    stars = significance_stars(p)
    if str(mode) not in ("p", "both"):
        return stars
    text = format_p_value(p)
    if not text:
        return stars
    name = "p_BH" if adjusted else "p"
    # ``<`` already carries its own relation, so it takes no ``=``: the floor
    # case reads ``p < 1e-300`` rather than ``p = <1e-300``.
    body = f"{name} {text}" if text.startswith("<") else f"{name} = {text}"
    if str(mode) == "p":
        return body
    return f"{stars} ({body})"
