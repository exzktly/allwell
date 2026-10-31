"""Read a lattice step off a nanowell timepoint's rim, from its sites table alone.

Every timepoint of a circular nanowell well whose run kept a roster is measured on that one roster, so its sites
table has a row for the same addresses whatever its origin, and the nanowells it detected cannot show a step by
their addresses: a timepoint anchored a site off still detects nanowells only at roster addresses. Its rim can. Such
a timepoint measured, in its own frame, the roster disk while the array sat at ``roster - s``, so a crescent of
wholly imaged roster sites on one side holds no nanowell.

The reading is not proof. A bubble or glare over one side of the rim leaves the same crescent, and the run writes it
the same way (``occluded``, nothing of the disk visible), so each caller weighs it against what the run itself said:
Validate's relabel (``datasets.validate.nanowell_relabel``) moves only a timepoint the run flagged
``roster_mismatch`` by it, and Concatenate's link (``datasets.concatenate.nanowell_link``) sets a dataset 2
timepoint aside for an incremental Analyze, which decides from the images.

Qt-free.
"""

from __future__ import annotations

from pipeline.nanowell_frames import STEP_REACH

#: A timepoint is judged a lattice step off only when that step explains at least this many more of its rim sites
#: than its own addresses do, and at least half the rim sites the step predicts: a nanowell missed at the rim, or a
#: bubble over it, moves the count by one or two, while a step off leaves a whole crescent (17 sites on a 265-site
#: rendered array).
MIN_CRESCENT = 3


def site_address(row) -> tuple[int, int] | None:
    """A sites-table row's ``(nw_q, nw_r)``; ``None`` when either is blank."""
    q, r = str(row.get("nw_q") or "").strip(), str(row.get("nw_r") or "").strip()
    if not q or not r:
        return None
    return int(float(q)), int(float(r))


def step_against_roster(rows, roster: set) -> tuple[int, int]:
    """The step ``s`` that best explains which of this timepoint's wholly imaged roster sites hold a nanowell.

    The step chosen is the one whose predicted nanowells (``a`` such that ``a + s`` is in the roster) disagree least
    with the detections, and it must beat ``(0, 0)`` by :data:`MIN_CRESCENT` and by half the crescent it predicts.
    ``(0, 0)`` when none does, or when no roster site was imaged whole."""
    imaged, detected = set(), set()
    for row in rows:
        address = site_address(row)
        try:
            whole = float(row.get("imaged_fraction") or "nan") >= 0.999
        except ValueError:
            whole = False
        if address in roster and whole:
            imaged.add(address)
            if str(row.get("detected")).strip().lower() == "true":
                detected.add(address)
    steps = [(dq, dr) for dq in range(-STEP_REACH, STEP_REACH + 1) for dr in range(-STEP_REACH, STEP_REACH + 1)]
    predicted = {st: {a for a in imaged if (a[0] + st[0], a[1] + st[1]) in roster} for st in steps}
    misses = {st: len(detected ^ predicted[st]) for st in steps}
    best = min(steps, key=lambda st: (misses[st], st != (0, 0)))
    crescent = len(imaged - predicted[best])
    gain = misses[(0, 0)] - misses[best]
    return best if best != (0, 0) and gain >= max(MIN_CRESCENT, crescent / 2) else (0, 0)
