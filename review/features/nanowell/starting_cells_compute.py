"""The *Starting cells* condition's bounds: the presets the page offers, and how a pair of bounds is checked and written.

Asked for: "filter nanowells for purposes of inclusion … only compute statistics/display [nanowells] that start
with a minimum number of cells". That filter is now one kind of condition of the page's *Subset*
(:mod:`.subset_compute`), which does the filtering, the counting and the figure's sentence; this module keeps the
part that is about a count at the baseline alone — the presets, the bounds' rules, their text, and which occupancy
classes a pair of bounds can hold.

**What "starting" means.** A nanowell's count at the page's Baseline — the well's earliest timepoint, or the
nanowell's first measurable one — which the metrics table holds as ``n0``: the same number the occupancy colours
(0 / 1 / 2 / 3+) are drawn from, so the condition and the key always agree.

Qt-free.
"""

from __future__ import annotations

#: The presets the control offers: key, label, minimum, maximum (``None`` is no bound on that side).
PRESETS = (("any", "Any", None, None), ("occupied", "≥1 (occupied)", 1, None),
           ("single", "1 (single cell)", 1, 1), ("one_two", "1–2", 1, 2), ("two_plus", "≥2", 2, None))
#: The control's last entry: bounds typed by hand.
CUSTOM = "custom"
#: A drawn well with fewer nanowells in the filter than this is named on the figure: wells are the replicate unit,
#: so a well of four nanowells weighs as much in a group's statistic as one of three hundred.
FEW_BELOW = 10


def normalise(lo, hi) -> tuple[int | None, int | None]:
    """*lo* and *hi* as bounds the filter applies: whole numbers ≥ 0 or ``None``; a minimum of 0 is no minimum.
    Raises ``ValueError`` for anything else, or a minimum above the maximum."""
    out = []
    for value in (lo, hi):
        if value is None:
            out.append(None)
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"a starting-cells bound must be a whole number of cells, not {value!r}")
        out.append(value)
    lo, hi = out
    lo = None if lo == 0 else lo
    if lo is not None and hi is not None and lo > hi:
        raise ValueError(f"the minimum ({lo}) is above the maximum ({hi})")
    return lo, hi


def is_active(lo, hi) -> bool:
    return lo is not None or hi is not None


def preset_of(lo, hi) -> str:
    """The preset key the bounds are, or :data:`CUSTOM`."""
    return next((key for key, _label, a, b in PRESETS if (a, b) == (lo, hi)), CUSTOM)


def bounds_text(lo, hi) -> str:
    """The bounds as the figure writes them: ``1–2``, ``≥1``, ``1``, ``≤5``, or ``any``."""
    if lo is None and hi is None:
        return "any"
    if lo is None:
        return f"≤{hi}"
    if hi is None:
        return f"≥{lo}"
    return f"{lo}" if lo == hi else f"{lo}–{hi}"


def occupancy_in_filter(cls, lo, hi) -> bool:
    """Whether any nanowell of occupancy class *cls* (``"0"``, ``"1"``, ``"2"``, ``"3+"``; ``None`` for an unknown
    count) can be in the filter — for the key, which greys the classes the filter removes whole."""
    if not is_active(lo, hi):
        return True
    if cls is None:
        return False
    if cls == "3+":
        return hi is None or hi >= 3
    k = int(cls)
    return (lo is None or k >= lo) and (hi is None or k <= hi)
