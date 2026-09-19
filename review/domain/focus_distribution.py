"""Per-cell focus across a frame: the distribution, and where the cut falls.

The sibling of :mod:`review.domain.threshold_sweep`, for the other threshold
this application asks a user to choose. Both modules answer the same question
the same way — **draw the evidence, mark where the user is, recommend
nothing** — and both reached that answer by trying to do better and measuring
what happened.

For StarDist's probability threshold the measurement is in that module: every
plateau rule degenerates on a monotone curve and returns either the sweep's
zero tail or the constant the feature exists to argue with.

For focus the refusal has a different shape and is, if anything, sharper.
Otsu's method on the per-cell values — the natural choice, run on log₁₀ since
the population is multiplicative — was measured over seven populations built
from a realistic fixture, and two rows decide it. On a **clean** plate, with
nothing defocused, it returns a number and throws away **39.5 %** of good
cells. On a **uniformly defocused** plate it splits the bad population against
itself, returns a cut far below everything, and keeps **76 %** of the cells it
exists to catch — reporting almost nothing wrong about the worst plate in the
set. And there is no refusal to gate it on: Otsu's own separability spans
0.516 to 0.671 across all seven, with the clean plate scoring *higher* than
three genuinely mixed ones, so the statistic that would have to say "a cut
exists here" cannot tell the two degenerate cases from the useful ones.
``_Docs/Markdowns/INFERENCE_SUBSYSTEMS_TRACKER.md`` §D.2d carries the table.

So this module builds a distribution and a live consequence — at *your* cut,
this many cells stay and this many go — and states plainly what it will not
do. A user looking at where their cut falls can see whether it sits in a
valley or in the middle of one population, which is better evidence than a
number they would have to trust.

Qt-free and numpy-free: plain stdlib over a few thousand floats, because a
frame's cells are a list, not an array problem. Nothing here measures focus —
:func:`pipeline.field_quality.edge_focus` does, and the values arrive already
computed from the CSV column or from a live segmentation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: The refusal, stated once so the readout and any future caller quote the
#: same words rather than each composing their own.
NO_RECOMMENDATION = (
    "No threshold is suggested, and that is measured rather than cautious: "
    "the obvious automatic rule returns a cut for a plate with nothing wrong "
    "with it — discarding two fifths of its good cells — and on a uniformly "
    "defocused plate it keeps three quarters of the cells it exists to catch. "
    "Nothing distinguishes those cases from the useful ones, so the "
    "distribution is the answer."
)

#: Refusals for the two states in which there is nothing to draw.
NOTHING_MEASURED = (
    "No cell in this frame carried a measurable outline, so there is no "
    "distribution to draw. An outline needs contrast across it and enough "
    "pixels either side — which is also what severe defocus destroys, so this "
    "is itself a finding about the frame rather than an error."
)
NO_CELLS = (
    "No cells were segmented in this frame, so there is nothing to measure. "
    "Segment it first, or lower the detection threshold."
)


@dataclass(frozen=True)
class FocusDistribution:
    """One frame's per-cell focus values, and everything the readout may say.

    ``declined`` is the reason there is nothing to draw and is the only thing
    that makes an empty distribution legitimate — a computation that produced
    no points and gives no reason is the silent failure :file:`CLAUDE.md` §7
    exists to prevent.
    """

    #: The measurable values, ascending. Sorted at construction so the
    #: percentile helpers and the plot cannot disagree about the order.
    values: tuple[float, ...] = ()
    #: Cells whose outline could not be measured. Carried rather than dropped,
    #: because "forty cells, six unmeasurable" and "thirty-four cells" are
    #: different statements about a frame and only the first is true.
    unmeasurable: int = 0
    #: Where the user's cut currently sits, so the plot can mark it. ``None``
    #: when no cut has been chosen — which is the normal state, and not a
    #: reason to invent one.
    cut: float | None = None
    #: What was measured — well, channel, timepoint, field — so a saved figure
    #: says which frame it came from, exactly as the threshold curve does.
    subject: str = ""
    #: Why there is nothing to draw. Empty when there is something.
    declined: str = ""
    #: Things that are true of this curve and would mislead a reader who did
    #: not know them — drawn ON the figure rather than left to a tooltip,
    #: because the window has a Save button and these travel with the picture.
    #:
    #: Distinct from ``declined``: a caveat does not stop the curve being
    #: drawn. The measurement is real; what is qualified is what may be
    #: concluded from it. The case this exists for is a curve measured on a
    #: channel the cut does not apply to, which is a correct plot of the wrong
    #: population and the most dangerous thing this window can show.
    caveats: tuple[str, ...] = ()

    @property
    def measurable(self) -> int:
        return len(self.values)

    @property
    def total(self) -> int:
        return len(self.values) + self.unmeasurable

    @property
    def kept(self) -> int:
        """Cells at or above the cut. Every measurable cell without one."""
        if self.cut is None:
            return self.measurable
        return sum(1 for v in self.values if v >= self.cut)

    @property
    def dropped(self) -> int:
        """Cells below the cut — **not** including the unmeasurable ones.

        Those are kept, and folding them in here would be the substitution the
        whole layer is built to avoid: a reader would believe the gate removed
        everything soft, and be wrong about exactly the cells nobody could
        judge.
        """
        return self.measurable - self.kept

    def percentile(self, pct: float) -> float | None:
        """Linear-interpolated percentile of the measurable values.

        Written out rather than taken from numpy because this module is
        deliberately array-free, and because the interpolation rule matters:
        the plot's cumulative line and this number must agree, and two
        libraries' definitions of a percentile do not always.
        """
        if not self.values:
            return None
        if len(self.values) == 1:
            return self.values[0]
        pos = (len(self.values) - 1) * max(0.0, min(float(pct), 100.0)) / 100.0
        lo = math.floor(pos)
        hi = math.ceil(pos)
        if lo == hi:
            return self.values[lo]
        return self.values[lo] + (self.values[hi] - self.values[lo]) * (pos - lo)

    def describe(self) -> str:
        """The sentence under the plot: what was measured, and what a cut does.

        Never a recommendation; :data:`NO_RECOMMENDATION` is the separate line
        that says so, kept separate because this one changes as the user moves
        their cut and that one never does.
        """
        if self.declined:
            return self.declined
        if not self.values:
            return ""
        lo, hi = self.values[0], self.values[-1]
        med = self.percentile(50.0)
        head = (f"{self.measurable:,} of {self.total:,} cells measured, "
                f"{lo:.3g} to {hi:.3g}, median {med:.3g}.")
        if self.unmeasurable:
            noun = "cell" if self.unmeasurable == 1 else "cells"
            verb = "carries" if self.unmeasurable == 1 else "carry"
            head += (f" {self.unmeasurable:,} {noun} {verb} no value and "
                     f"{'is' if self.unmeasurable == 1 else 'are'} kept "
                     f"whatever the cut is.")
        if self.cut is None:
            return head + " No cut set."
        return (f"{head} At {self.cut:g}: {self.kept:,} kept, "
                f"{self.dropped:,} excluded.")


def distribution_from_values(values, *, cut: float | None = None,
                             subject: str = "",
                             declined: str = "",
                             caveats=()) -> FocusDistribution:
    """Build a :class:`FocusDistribution`, or decline with the reason.

    *values* is one frame's per-cell focus column, with ``None`` (or a NaN)
    for a cell whose outline could not be measured. Those are **counted**, not
    filtered away silently, for the reason the dataclass records.

    *declined* is the caller's own refusal — a frame that was never segmented,
    say — taken in preference to anything derived here, because the caller
    knows why it did not run and this module can only see that nothing
    arrived.

    *caveats* qualify a curve that WAS drawn. They are the caller's too: only
    it knows which channel and which corrections produced these pixels, and
    whether the dataset the cut would be applied to even carries the column.
    """
    measured: list[float] = []
    unmeasurable = 0
    for v in values or ():
        if v is None:
            unmeasurable += 1
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            unmeasurable += 1
            continue
        if f != f or math.isinf(f):      # NaN is how pandas spells a blank
            unmeasurable += 1
            continue
        measured.append(f)
    if declined:
        return FocusDistribution(unmeasurable=unmeasurable, cut=cut,
                                 subject=subject, declined=declined)
    if not measured and not unmeasurable:
        return FocusDistribution(cut=cut, subject=subject, declined=NO_CELLS)
    if not measured:
        return FocusDistribution(unmeasurable=unmeasurable, cut=cut,
                                 subject=subject, declined=NOTHING_MEASURED)
    # Carried only onto a curve that is actually drawn. A refusal already
    # occupies the whole figure and says its own reason; stacking a caveat
    # under it would qualify a plot that does not exist.
    return FocusDistribution(values=tuple(sorted(measured)),
                             unmeasurable=unmeasurable, cut=cut,
                             subject=subject,
                             caveats=tuple(c for c in (caveats or ()) if c))
