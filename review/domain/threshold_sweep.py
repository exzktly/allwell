"""Nucleus count against StarDist's probability threshold: the curve, not a number.

The Analyze form ships ``DEFAULT_PROB_THRESH_TEXT = "0.2"`` against the
pretrained model's own ~0.479, with a comment saying the model's threshold is
conservative for these plates and that an untouched run therefore detects "dim,
out-of-focus and marginal" nuclei. That constant decides what every untouched
run does, and the user has no evidence with which to move it.

**This module produces the evidence. It does not produce a recommendation, and
that is a decision with a measurement behind it.**

The first design here picked the midpoint of the widest plateau — the longest
run of consecutive threshold steps over which ``|Δ log count|`` stayed below a
tolerance. Implemented and run against a realistic monotone decay (400 nuclei,
sigmoidal fall-off, 48 steps over τ ∈ [0.05, 0.99], tolerance 0.05), that
criterion returns:

===========================  ==============  ============  ==================
derivative taken on          widest run      suggestion    counts over it
===========================  ==============  ============  ==================
``log``, nan-guarded         τ 0.050–0.350   **0.200**     400 → 352
``log1p``                    τ 0.050–0.350   **0.200**     400 → 352
raw counts                   τ 0.790–0.990   **0.890**     **0 → 0**
===========================  ==============  ============  ==================

Two degenerate answers, and which one you get is decided by how ``log(0)`` is
guarded — an implementation detail, not a property of the data. The zero tail
wins and the suggestion is *detect nothing*; or the low-τ shoulder wins and the
suggestion is 0.200, which is the constant this whole feature exists to let the
user argue with, reproduced to three decimal places for reasons having nothing
to do with their plate.

The structural reason: for a **monotone** curve there is no interior plateau
maximum, so "widest region of small derivative" always lands at an extreme,
while the informative feature — the knee — is where the derivative is
*largest*. Any rule of that family points away from the only interesting part
of the curve. ``pipeline/threshold_optimize.py`` reached the neighbouring
conclusion for the two-population case from the other direction and wrote it
down: *"Which point of a plateau should be reported is a domain question about
the assay, not one this module can decide."*

So: draw the curve, mark where the user currently is, and say plainly what was
and was not measured. A user looking at the plot can see whether their
threshold sits on a shoulder or on a cliff, which is better evidence than a
number they would have to trust.

numpy-free and Qt-free — plain stdlib over small sequences, because a sweep is
tens of points, not an array problem. No StarDist import: this module describes
a sweep, it does not run one (``services.frame_preview.sweep_frame`` does).
"""

from __future__ import annotations

from dataclasses import dataclass

#: The sweep's default span. Not 0 to 1: at τ=0 StarDist returns every candidate
#: polygon the network emitted, which on a noisy frame is tens of thousands of
#: objects and minutes of NMS, and at τ=1 nothing survives by construction.
#: Neither end is a threshold anyone would run, and both cost the most.
DEFAULT_LO = 0.05
DEFAULT_HI = 0.95

#: How many thresholds a sweep tries by default, and the bounds a caller may
#: ask for. Each point is a segmentation, so this is a time budget as much as a
#: resolution: the whole sweep must stay interruptible and finish while a user
#: is still watching it.
DEFAULT_STEPS = 19
MIN_STEPS = 3
MAX_STEPS = 61


def sweep_grid(lo: float = DEFAULT_LO, hi: float = DEFAULT_HI,
               steps: int = DEFAULT_STEPS) -> list[float]:
    """The thresholds a sweep will try, low to high, inclusive of both ends.

    Clamped rather than validated: a caller asking for two points or for two
    thousand has made a mistake this function can absorb, and a sweep that
    refuses to run is worse evidence than a coarse one.
    """
    lo = max(0.0, min(float(lo), 1.0))
    hi = max(0.0, min(float(hi), 1.0))
    if hi < lo:
        lo, hi = hi, lo
    n = max(MIN_STEPS, min(int(steps), MAX_STEPS))
    if hi == lo:
        return [round(lo, 6)]
    span = hi - lo
    return [round(lo + span * i / (n - 1), 6) for i in range(n)]


@dataclass(frozen=True)
class SweepPoint:
    """One threshold and the nucleus count it produced."""

    threshold: float
    count: int


@dataclass(frozen=True)
class ThresholdCurve:
    """A completed (or declined) sweep, and everything the readout may say.

    ``declined`` is the reason there is nothing to draw, and it is the only
    thing that makes an empty curve legitimate — a sweep that produced no
    points and gives no reason is a silent failure, which is the shortfall
    :file:`CLAUDE.md` §7 exists to prevent.
    """

    points: tuple[SweepPoint, ...] = ()
    #: Where the user's current threshold sits, so the plot can mark it.
    #: ``None`` when the form leaves it to the model's own thresholds.json.
    current: float | None = None
    #: Set when the sweep ran on a centre crop rather than the whole frame.
    #: Stated in the readout wherever it is true — a sweep over part of a frame
    #: presented as a sweep over the frame is the substitution §7 forbids.
    crop_px: int = 0
    #: Why there is no curve. Empty when there is one.
    declined: str = ""

    @property
    def counts(self) -> tuple[int, ...]:
        return tuple(p.count for p in self.points)

    @property
    def thresholds(self) -> tuple[float, ...]:
        return tuple(p.threshold for p in self.points)

    def describe(self) -> str:
        """The sentence under the plot: what was measured, and what was not.

        Never a recommendation. The module docstring records the measurement
        behind that.
        """
        if self.declined:
            return self.declined
        if not self.points:
            # The default instance: no sweep has been asked for yet, so there
            # is nothing to say and nothing to refuse. Distinct from
            # ``curve_from_counts([], [])``, which is a sweep that ran and
            # produced nothing, and which declines with a reason. Guarded
            # because the field defaults make this state reachable, and a
            # readout that raises on first paint is a worse failure than one
            # that says nothing.
            return ""
        n = len(self.points)
        lo, hi = self.thresholds[0], self.thresholds[-1]
        head = (f"{n} thresholds from {lo:g} to {hi:g}: "
                f"{max(self.counts):,} nuclei down to {min(self.counts):,}.")
        if self.crop_px:
            head += (f" Measured on a {self.crop_px}×{self.crop_px} centre crop, "
                     f"not the whole frame.")
        return head


def curve_from_counts(thresholds, counts, *, current: float | None = None,
                      crop_px: int = 0) -> ThresholdCurve:
    """Build a :class:`ThresholdCurve`, or decline with the reason.

    Three refusals, each of which would otherwise be drawn as a flat or empty
    plot that looks like a measurement:

    * **nothing ran** — no thresholds, or the sweep was stopped before its
      first point;
    * **no nuclei anywhere** — the frame is blank, wrongly channelled, or the
      model found nothing at any cut, and a line along zero says none of that;
    * **the count never moves** — every threshold gives the same number, so the
      frame has nothing to say about where the cut should go. That is a real
      answer and it is not a curve.

    Declining is not the same as failing: the caller shows the sentence and the
    Playground keeps whatever it was displaying.
    """
    pairs = [
        SweepPoint(float(t), int(c))
        for t, c in zip(list(thresholds), list(counts), strict=False)
    ]
    if not pairs:
        return ThresholdCurve(
            declined="The sweep did not run, so there is nothing to plot.",
            current=current, crop_px=crop_px)
    counts_only = [p.count for p in pairs]
    if max(counts_only) <= 0:
        return ThresholdCurve(
            declined=("No nuclei were detected at any threshold in the sweep, "
                      "so there is no curve to read. Check the segmentation "
                      "channel and the frame before reading anything into it."),
            current=current, crop_px=crop_px)
    if min(counts_only) == max(counts_only):
        return ThresholdCurve(
            declined=(f"Every threshold in the sweep detected the same "
                      f"{counts_only[0]:,} nuclei, so this frame says nothing "
                      f"about where the cut should go."),
            current=current, crop_px=crop_px)
    return ThresholdCurve(tuple(pairs), current=current, crop_px=crop_px)
