"""Which lattice frame a nanowell well's timepoints share, decided from the nanowells each one detected.

Every timepoint of a well is anchored on its own evidence — a hand-set anchor, the central square, the fitted array
centre, or a plate-rigid prediction — and a nanowell's address ``(q, r)`` is counted from that origin. Review joins
a well's timepoints by address, so a timepoint whose origin landed a site off names every nanowell after its
neighbour. The nanowells a timepoint detected sit at the same physical sites at every timepoint, so the lattice step
that best overlays one timepoint's detected addresses on another's says how far apart their origins are.

This module holds that one rule, used by the run before it measures (``nanowell_run._settle_steps``) and by
Datasets → Validate's repair of a dataset analysed before the run applied it (``datasets.validate.nanowell_relabel``),
which has only the tables the run wrote. Qt-free and image-free: it sees addresses and how each timepoint was
anchored, nothing else.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: How many lattice steps along each axis two timepoints' nanowells are searched over for the overlay that matches them
#: best. An array centre two sites off was once reported as one step off, because only one step was searched.
STEP_REACH = 3

#: Which timepoint's origin a well's timepoints are put in the frame of when their nanowells sit a lattice step apart:
#: the best-ranked source among them, lower first. A hand-set anchor is a person's decision; the central square is
#: the array's own mark; the array's centre is a fitted circle that a hexagonal rim leaves loose by a fraction of a
#: pitch (up to 0.27 on a small rendered array, 0.47 on a real one); a plate-rigid prediction is carried from other
#: wells. A source not named here ranks last.
ORIGIN_RANK = {"manual": 0, "fiducial": 1, "array_centre": 2, "plate_rigid": 3}

#: The verdicts of :func:`settle_frames`.
AGREE, SHIFT, TIE, KEPT_OUTRANKED = "agree", "shift", "tie", "kept_outranked"


#: How many more of a timepoint's detected addresses a lattice step must match than step ``(0, 0)`` does before the
#: timepoint is read as a step off — the same floor ``datasets.core.nanowell_rim.MIN_CRESCENT`` puts on the roster's
#: own step test. A dense reference that missed one nanowell (debris, a bubble) near the open side of a sparser
#: timepoint let a shifted overlay win by that one site, and the run then renamed every nanowell of the timepoint
#: after its neighbour (or, on a tie, left the well unaddressed). A step off moves a whole edge row of the array out of
#: the overlay, many sites, never one or two.
MIN_STEP_GAIN = 3


def best_step(mine, others) -> tuple[int, int]:
    """The lattice step ``(dq, dr)`` that moves the addresses *mine* onto *others* best: ``(0, 0)`` unless some step
    within :data:`STEP_REACH` overlaps them more than they already do by at least :data:`MIN_STEP_GAIN` sites and by
    half of the addresses step ``(0, 0)`` leaves unmatched — a step that explains the mismatch, not noise in it."""
    mine, others = set(mine), set(others)
    steps = [(dq, dr) for dq in range(-STEP_REACH, STEP_REACH + 1) for dr in range(-STEP_REACH, STEP_REACH + 1)]
    overlap = {s: len({(q + s[0], r + s[1]) for q, r in mine} & others) for s in steps}
    best = max(steps, key=lambda s: (overlap[s], s == (0, 0)))
    gain, unmatched = overlap[best] - overlap[(0, 0)], len(mine) - overlap[(0, 0)]
    return best if best != (0, 0) and gain >= max(MIN_STEP_GAIN, unmatched / 2) else (0, 0)


def rank_of(method) -> int:
    """How far down :data:`ORIGIN_RANK` an origin method sits; an unknown or missing one ranks last."""
    return ORIGIN_RANK.get(method, len(ORIGIN_RANK))


@dataclass(frozen=True)
class FrameDecision:
    """What :func:`settle_frames` decided for a well's anchored timepoints.

    *verdict* is :data:`AGREE` (one frame: nothing to move), :data:`SHIFT` (*shifts* moves each listed timepoint's
    addresses onto *reference*'s frame: address ``(q, r)`` becomes ``(q + dq, r + dr)``), :data:`TIE` (two frames
    rank alike: which is right cannot be told, *frames* names them), or :data:`KEPT_OUTRANKED` (an earlier run's kept
    frame does not win against a new timepoint: the well must be analysed again with every timepoint in view).
    *onto* is each timepoint's step onto the frame it was measured against."""
    verdict: str
    shifts: dict = field(default_factory=dict)
    reference: str | None = None
    onto: dict = field(default_factory=dict)
    frames: tuple = ()


def settle_frames(found: dict, methods: dict, kept_detected=(), kept_origins=None) -> FrameDecision:
    """Decide one frame for the timepoints of *found* (``timepoint -> set of detected addresses``, in time order),
    *methods* naming how each was anchored.

    The timepoints are grouped by the step their detected nanowells sit at against the timepoint that detected most;
    the frame kept is the one holding the best-ranked origin (:data:`ORIGIN_RANK`), then the most timepoints, then
    the earliest — so a square-anchored baseline is never moved onto later timepoints that all share one wrong array
    centre. Two frames alike on rank and size are a :data:`TIE`.

    *kept_detected* (an incremental run's earlier timepoints' detections) and *kept_origins* (``timepoint -> method``
    for them, in time order) make the kept timepoints a frame of their own, measured against directly: when it wins,
    a new timepoint off it is shifted onto it; when a new frame outranks or ties it, :data:`KEPT_OUTRANKED`."""
    timepoints = list(found)
    kept_detected, kept_origins = set(kept_detected or ()), dict(kept_origins or {})
    methods = {**kept_origins, **methods}
    if kept_detected:
        onto = {tp: best_step(found[tp], kept_detected) for tp in timepoints}
        order = list(kept_origins) + timepoints
    elif len(timepoints) > 1:
        pivot = max(timepoints, key=lambda tp: (len(found[tp]), -timepoints.index(tp)))
        onto = {tp: (0, 0) if tp == pivot else best_step(found[tp], found[pivot]) for tp in timepoints}
        order = timepoints
    else:
        return FrameDecision(AGREE, onto={tp: (0, 0) for tp in timepoints})
    return choose_frame(onto, methods, order, kept=list(kept_origins) if kept_detected else None)


def choose_frame(onto: dict, methods: dict, order, kept=None) -> FrameDecision:
    """Decide one frame from each timepoint's step *onto* a common reference (``timepoint -> (dq, dr)``), *methods*
    naming how each was anchored and *order* the timepoints in time. The frame kept is the one holding the
    best-ranked origin, then the most timepoints, then the earliest; two alike on rank and size are a :data:`TIE`.
    *kept*, when given, are an earlier run's timepoints, which sit at step ``(0, 0)``: a frame that is not theirs
    winning, or tying with theirs, is :data:`KEPT_OUTRANKED`."""
    order = list(order)
    frames: dict = {(0, 0): list(kept)} if kept is not None else {}
    for tp, step in onto.items():
        frames.setdefault(step, []).append(tp)
    if len(frames) <= 1:                 # one frame, or no timepoint to compare (none anchored): nothing to choose
        return FrameDecision(AGREE, onto=dict(onto))

    def rank(step):
        members = frames[step]
        return min((rank_of(methods.get(tp)) for tp in members), default=len(ORIGIN_RANK)), -len(members)

    ordered = sorted(frames, key=lambda step: (rank(step), min((order.index(t) for t in frames[step]), default=-1)))
    named = tuple(tuple(frames[step]) for step in ordered)
    tied = rank(ordered[0]) == rank(ordered[1])
    if kept is not None and (tied or ordered[0] != (0, 0)):
        return FrameDecision(KEPT_OUTRANKED, onto=dict(onto), frames=named)
    if tied:
        return FrameDecision(TIE, onto=dict(onto), frames=named)
    keep = ordered[0]
    shifts = {tp: (step[0] - keep[0], step[1] - keep[1]) for tp, step in onto.items() if step != keep}
    return FrameDecision(SHIFT, shifts=shifts, reference=frames[keep][0], onto=dict(onto), frames=named)
