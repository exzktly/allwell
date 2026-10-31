"""Which nanowells were out of focus at a timepoint, judged from each nanowell's own rim.

The pipeline measures every fitted nanowell's rim sharpness (``pipeline.nanowell_sites.rim_focus``, the
``focus`` column of ``nanowells/<well>.csv``): the reciprocal of the 20-80 % rise width of the wall's edge, in
pixels, so higher is sharper. Every rim is the same strong edge whether or not the nanowell holds cells, so a
nanowell's rim says whether its own tile was in focus at that timepoint. A tile is focused on its own, which is
why the verdict is per nanowell and per timepoint rather than per well: one tile of a well can be soft while its
neighbours are sharp.

**The rule is relative, never an absolute cut.** A nanowell is *out of focus* at a timepoint when its rim is less
sharp than :attr:`RimFocusRule.fraction` of its well's sharp rims at that timepoint — the
:data:`REFERENCE_PERCENTILE` th percentile of every rim focus the well measured there. No calibration is needed,
because the reference is the same plate, objective and exposure. The per-cell focus cut on the QC tab is absolute
and was measured on nuclei; a rim is a different edge in a different image, so that number is not reused here.

Measured on rendered tiles (``tests/_nanowell_synth.render_tiles``, both formats, 0.02 and 0.05 noise): an image
with every rim sharp reads no lower than 0.89 of its 90th percentile, so the default 0.75 flags none of it; a rim
blurred by 2 px reads 0.80 of a sharp one, by 3 px 0.67, by 4 px 0.60. So the default flags what is about 2.5 px or
more blurred than the well's sharp tiles.

**What it cannot see**, said wherever it matters: a timepoint blurred *uniformly* has no sharp rims to compare
against and flags nothing; and a nanowell whose rim could not be fitted (``predicted``, often the most blurred ones)
or ran off the image has no focus and is never flagged — undefined is not soft. :func:`note` counts both.

A flagged row's state becomes :data:`~review.features.nanowell.collect.OUT_OF_FOCUS`, its recorded state kept in
``recorded_state``, so every figure on the page treats it as it treats an occluded nanowell: no count, confluence,
brightness or fluorescence there, no fold change from or to it, no growth rate through it, and the reason named.

Qt-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from review.features.nanowell.collect import MEASURABLE, OUT_OF_FOCUS

#: The default fraction of the well's sharp rims a nanowell's rim must reach (module docstring for the measurement).
DEFAULT_FRACTION = 0.75
#: The smallest and largest fractions the page accepts: below the low end almost nothing is flagged, and at 1 a
#: tenth of every timepoint's nanowells would be by construction.
MIN_FRACTION, MAX_FRACTION = 0.3, 0.95
#: Which percentile of a well's rims at a timepoint stands for "the sharp ones": high enough to be the in-focus
#: tiles when most of the array is soft, low enough not to be the one noisiest rim.
REFERENCE_PERCENTILE = 90.0
#: A well and timepoint with fewer rim focus values than this is not judged: there is no reference to judge against.
MIN_SITES = 10
#: The columns :func:`flag` adds.
COLUMNS = ("rim_focus_ref", "rim_focus_ok", "recorded_state")


@dataclass(frozen=True)
class RimFocusRule:
    """Whether nanowells are judged on their rim focus, and against what fraction of the well's sharp rims."""

    enabled: bool = True
    fraction: float = DEFAULT_FRACTION

    def active(self) -> bool:
        return bool(self.enabled) and isinstance(self.fraction, (int, float)) and math.isfinite(self.fraction)

    def key(self) -> tuple:
        """What the metrics were computed for: equal keys give equal verdicts."""
        return (True, float(self.fraction)) if self.active() else (False,)

    def text(self) -> str:
        """The rule in words, as a figure's note states it."""
        return (f"rim under {self.fraction:.0%} as sharp as the sharpest tenth of its well's rims at that timepoint"
                if self.active() else "off")


def clamp_fraction(value) -> float | None:
    """*value* as a fraction the page accepts, or ``None`` when it is not a number in range."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value) if MIN_FRACTION <= value <= MAX_FRACTION else None


def flag(frame: pd.DataFrame, rule: RimFocusRule | None) -> None:
    """Mark the rows of *frame* (``series_compute._addressed_grid``) whose rim was out of focus, in place.

    Adds ``rim_focus_ref`` (the well's reference at that timepoint; NaN where it has fewer than :data:`MIN_SITES`
    rims), ``rim_focus_ok`` (False only on a flagged row) and ``recorded_state`` (the state the run recorded), and
    turns a flagged row's ``state`` into :data:`OUT_OF_FOCUS` with the numbers in its ``reason``. Only a row in a
    measurable state is flagged: an occluded or clipped nanowell already has no number to remove. With *rule*
    ``None`` or off, nothing is flagged and the columns are still added."""
    frame["recorded_state"] = frame["state"]
    focus = pd.to_numeric(frame["focus"], errors="coerce") if "focus" in frame else pd.Series(np.nan, index=frame.index)
    focus = focus.where(np.isfinite(focus) & (focus > 0))
    keys = [frame["well"], frame["timepoint"]]
    enough = focus.groupby(keys).transform("count") >= MIN_SITES
    reference = focus.groupby(keys).transform(lambda v: np.nanpercentile(v, REFERENCE_PERCENTILE)
                                              if v.notna().any() else np.nan)
    frame["rim_focus_ref"] = reference.where(enough)
    frame["rim_focus_ok"] = True
    if rule is None or not rule.active() or not len(frame):
        return
    soft = (frame["state"].isin(MEASURABLE) & focus.notna() & frame["rim_focus_ref"].notna()
            & (focus < rule.fraction * frame["rim_focus_ref"])).to_numpy(dtype=bool)
    if not soft.any():
        return
    frame.loc[soft, "rim_focus_ok"] = False
    frame.loc[soft, "state"] = OUT_OF_FOCUS
    frame.loc[soft, "reason"] = [
        f"rim focus {f:.3g} under {rule.fraction:.0%} of its well's sharp rims ({ref:.3g}) at this timepoint"
        for f, ref in zip(focus[soft], frame.loc[soft, "rim_focus_ref"])]


@dataclass(frozen=True)
class FocusTally:
    """What :func:`flag` did over the rows a figure draws."""

    flagged: int = 0                # site-timepoints out of focus
    nanowells: int = 0              # distinct nanowells out of focus at one timepoint or more
    unjudged: int = 0               # measurable site-timepoints with no rim focus (predicted, off the image)
    unreferenced: int = 0           # measurable site-timepoints whose well had too few rims at that timepoint
    recorded: bool = True           # whether the run recorded rim focus at all


def tally(metrics: pd.DataFrame | None, wells=None) -> FocusTally:
    """Count, over *wells* (every well when ``None``), what the rim focus rule flagged and what it could not judge."""
    if metrics is None or not len(metrics) or "rim_focus_ok" not in metrics:
        return FocusTally()
    rows = metrics if wells is None else metrics[metrics["well"].isin(list(wells))]
    if not len(rows):
        return FocusTally()
    focus = pd.to_numeric(rows["focus"], errors="coerce") if "focus" in rows else pd.Series(np.nan, index=rows.index)
    soft = rows["rim_focus_ok"].eq(False)
    measurable = rows["recorded_state"].isin(MEASURABLE)
    return FocusTally(flagged=int(soft.sum()),
                      nanowells=int(rows.loc[soft, ["well", "nw_q", "nw_r"]].drop_duplicates().shape[0]),
                      unjudged=int((measurable & focus.isna()).sum()),
                      unreferenced=int((measurable & focus.notna() & rows["rim_focus_ref"].isna()).sum()),
                      recorded=bool(focus.notna().any()))


def note(metrics: pd.DataFrame | None, rule: RimFocusRule | None, wells=None) -> str:
    """The figure's sentence about rim focus: what was left out and what could not be judged; ``""`` when the
    rule is off and nothing needs saying."""
    if rule is None or not rule.active() or metrics is None or not len(metrics):
        return ""
    t = tally(metrics, wells)
    if not t.recorded:
        return "Rim focus was not recorded in this run, so no nanowell was checked for focus."
    parts = []
    if t.flagged:
        parts.append(f"{t.flagged:,} nanowell-timepoint{'s' if t.flagged != 1 else ''} "
                     f"({t.nanowells:,} nanowell{'s' if t.nanowells != 1 else ''}) left out as out of focus "
                     f"({rule.text()}): no counts, confluence or fluorescence there.")
    if t.unjudged:
        parts.append(f"{t.unjudged:,} with no rim focus (rim not fitted) were kept unchecked.")
    if t.unreferenced:
        parts.append(f"{t.unreferenced:,} in a well with under {MIN_SITES} rims at that timepoint were kept "
                     "unchecked.")
    return " ".join(parts)
