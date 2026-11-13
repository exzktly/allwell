"""What the QC view's *Resolve* walk says about each problem: which class of problem a timepoint is in, where on its
montage the fix is made, and the three sentences the guide card shows while it is open — what is wrong, exactly what
to do and where, and what happens next.

A **problem class** is one QC state that needs a look (``qc_compute``). The walk steps through one class at a time,
because each class has one remedy: a timepoint *not addressed*, *contradicted* or *inconclusive* is fixed by a click
on the central square (an anchor, which the run puts before anything it found itself); a *partial* one needs no fix —
its left-out nanowells already stay out of every join — only a look at which nanowells those are; *not located* cannot
be changed by an anchor at all; *anchor waiting* needs a re-run, not a click.

**Where** (:func:`hint_for`) is the best evidence the run and the identity check left of where the central square is,
in the well frame anchors are set in:

1. where the reference timepoint's central square correlates on this montage (the identity check's registration,
   ``square.found_um`` in the well's identity sidecar) — only when that registration was trusted;
2. the square the run itself picked (``fiducial.position_um`` in the grid record), accepted or not;
3. the array's rim-fitted centre (``array_boundary.centre_um``), the square's usual place;
4. nothing: the view is centred on the whole-well montage, as the walk always opened it.

Each hint names its source, because a ring on the montage is a claim, and a reader who knows it is the run's rejected
guess clicks differently from one who knows it is where the reference's square matched.

Qt-free.
"""

from __future__ import annotations

from dataclasses import dataclass

from review.features.nanowell import qc_compute as qc

#: The Array view's zoom about a hint, times *Fit*: close enough to tell the square from a round nanowell, wide
#: enough to see a few rings round it if the hint is a site or two off.
HINT_ZOOM = 6.0


@dataclass(frozen=True)
class ProblemClass:
    """One class of problem: the QC state, its name on the picker, and how a walk opens it."""

    state: str
    label: str
    #: The view a timepoint of this class opens on: ``"array"`` (its montage) or ``"identity"`` (compared with its
    #: reference, where the nanowells left out are drawn as such).
    view: str
    #: Whether it opens with *Set anchor* on, so a click on the square anchors it and the walk moves on.
    armed: bool


#: Every class a walk can resolve, worst first. The labels lead with the legend's word for the state, so the picker
#: and the matrix name a class the same way.
CLASSES = {
    qc.NOT_ADDRESSED: ProblemClass(qc.NOT_ADDRESSED, "not addressed — no central square", "array", True),
    qc.CONTRADICTED: ProblemClass(qc.CONTRADICTED, "contradicted — wrong square", "array", True),
    qc.INCONCLUSIVE: ProblemClass(qc.INCONCLUSIVE, "inconclusive — square unconfirmed", "array", True),
    qc.PARTIAL: ProblemClass(qc.PARTIAL, "partial — some nanowells left out", "identity", False),
    qc.NOT_LOCATED: ProblemClass(qc.NOT_LOCATED, "not located — no array found", "array", False),
    qc.ANCHOR_WAITING: ProblemClass(qc.ANCHOR_WAITING, "anchor waiting — needs a re-run", "array", False),
}
#: The classes the band's *Problem* menu offers: every class that takes a look per timepoint. *Anchor waiting* is
#: left out (its remedy is one button for the whole plate) but a click on such a cell still walks it.
PICKABLE = (qc.NOT_ADDRESSED, qc.CONTRADICTED, qc.INCONCLUSIVE, qc.PARTIAL, qc.NOT_LOCATED)
#: The views a walk can show its timepoints on: choosing any other view ends it.
WALK_VIEWS = ("array", "identity")


def class_of(state: str) -> ProblemClass | None:
    return CLASSES.get(state)


def armed_for(item) -> bool:
    """Whether *item* opens with *Set anchor* on: its class is armed and an anchor can change it
    (``qc_compute.anchor_can_fix``). A contradicted or inconclusive timepoint whose doubt is not its square opens
    unarmed, and its card says why: a click there would only anchor the square the run already used."""
    cls = class_of(item.state)
    return bool(cls is not None and cls.armed and qc.anchor_can_fix(item))


def choices(matrix) -> list[tuple[str, int]]:
    """``(state, count)`` for every pickable class, worst first, with how many timepoints of it are on the plate."""
    counts = matrix.counts if matrix is not None else {}
    return [(s, counts.get(s, 0)) for s in PICKABLE]


def chosen(matrix, states) -> str | None:
    """The one class *states* (``NanowellState.anchor_queue_states``) picks: the state itself when it names one, and
    otherwise — the walk's old several-state default — the worst of them with anything on the plate."""
    states = tuple(states or ())
    if len(states) == 1:
        return states[0]
    counts = matrix.counts if matrix is not None else {}
    ordered = [s for s in qc.QC_STATES if s in states]
    return next((s for s in ordered if counts.get(s)), ordered[0] if ordered else None)


def walk_states(matrix, states) -> tuple:
    """The class :func:`chosen` picks, as the one-state tuple ``qc_compute``'s queue functions take; ``()`` for none."""
    state = chosen(matrix, states)
    return (state,) if state else ()


# ── where ─────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Hint:
    """Where on the montage the fix is made (well frame, µm), and what that place is, for the guide and the ring."""

    point_um: tuple[float, float] | None = None
    source: str = ""                       # "reference" | "run" | "centre" | ""
    words: str = ""


def _pair(value) -> tuple[float, float] | None:
    try:
        x, y = float(value[0]), float(value[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    return (x, y) if x == x and y == y else None


def hint_for(state: str, grid_block: dict | None, identity_entry: dict | None) -> Hint:
    """The best place to look for the central square at a timepoint in *state* (the module docstring's order).
    *grid_block* is the timepoint's block of ``<well>_grid.json``; *identity_entry* its entry in the well's identity
    sidecar (``timepoints[tp]``), or ``None``. Partial and anchor waiting have no square to find: no hint."""
    if state not in (qc.NOT_ADDRESSED, qc.CONTRADICTED, qc.INCONCLUSIVE, qc.NOT_LOCATED):
        return Hint()
    square = (identity_entry or {}).get("square") if isinstance(identity_entry, dict) else None
    if isinstance(square, dict) and square.get("ok"):
        point = _pair(square.get("found_um"))
        if point is not None:
            reference = str((identity_entry or {}).get("reference") or "the reference timepoint")
            return Hint(point, "reference", f"the ring is where {reference}'s central square matches this montage")
    block = grid_block if isinstance(grid_block, dict) else {}
    fid = block.get("fiducial") if isinstance(block.get("fiducial"), dict) else {}
    point = _pair(fid.get("position_um"))
    if point is not None:
        accepted = block.get("origin_method") == "fiducial"
        words = ("the ring is the square the run used" if accepted
                 else "the ring is the run's best guess, which it did not trust")
        return Hint(point, "run", words)
    boundary = block.get("array_boundary") if isinstance(block.get("array_boundary"), dict) else {}
    point = _pair(boundary.get("centre_um"))
    if point is not None:
        return Hint(point, "centre", "the ring is the array's fitted centre, where the square usually is")
    return Hint()


def focus_address(addresses) -> str:
    """The left-out nanowell to centre the Identity view on, as the *Nanowell* box writes it (``"q,r"``): the one
    nearest the middle of them all, so a tile left out is seen whole rather than from its corner. ``""`` for none."""
    points = sorted({(int(q), int(r)) for q, r in addresses or ()})
    if not points:
        return ""
    mq = sum(q for q, _r in points) / len(points)
    mr = sum(r for _q, r in points) / len(points)
    q, r = min(points, key=lambda a: ((a[0] - mq) ** 2 + (a[1] - mr) ** 2, a))
    return f"{q},{r}"


# ── what the guide says ───────────────────────────────────────────────────────
@dataclass(frozen=True)
class Guide:
    """The guide card's text for one timepoint: where the walk is, and the three things a reader needs."""

    kicker: str                            # "Resolving not addressed · 3 of 17"
    title: str                             # "B04 at 24 h"
    problem: str
    action: str
    then: str
    #: The card's own button for this class: ``"rerun"`` (*Re-run anchored wells…*), ``"exclude"`` (*Exclude well*),
    #: ``"next"`` (*Looks right — next*), or ``""``.
    button: str = ""


def _left(n_after: int, label: str) -> str:
    if n_after <= 0:
        return "This is the last one"
    return f"{n_after:,} more {label} timepoint{'s' if n_after != 1 else ''} after this one"


def _where(hint: Hint, fallback: str) -> str:
    return (hint.words[0].upper() + hint.words[1:] + ".") if hint.words else fallback


def guide_for(item, hint: Hint, *, index: int, total: int, armed: bool, left_out: int | None = None,
              hours: str = "") -> Guide:
    """The card for walk item *item* (``anchor_queue_compute.QueueItem``): number *index* (0-based) of *total* in
    its class, *armed* when *Set anchor* is on for it. *left_out* is how many nanowells a partial timepoint leaves
    out; *hours* how the timepoint is named in time (``"(24 h)"``)."""
    state = item.state if item.state in CLASSES else qc.NOT_ADDRESSED
    word = qc.WORDS[state]
    kicker = f"Resolving: {word} · {index + 1} of {total}"
    title = f"{item.well} at {item.timepoint}" + (f" {hours}" if hours else "")
    why = (item.reason or "").strip().rstrip(".")
    after = _left(total - index - 1, word)
    next_anchor = (f"The anchor is saved and the next one opens by itself. {after}. When the class is done, press "
                   "Re-run anchored wells… so the run re-addresses these timepoints from your anchors and checks them "
                   "again.")
    if state == qc.NOT_ADDRESSED:
        if item.refused:
            problem = ("The last run tried the anchor you set here and could not use it (it was not on a nanowell, or "
                       "it was off the array), so this timepoint still has no addresses.")
        else:
            problem = (f"The run could not find this well's central square{f' ({why})' if why else ''}, so no "
                       "nanowell here has an address and the timepoint is left out of every comparison over time.")
        action = ("Click the middle of the central square — the one square among the round nanowells. "
                  + _where(hint, "The view opens on the middle of the montage, where the square usually is; press "
                                 "Fit to see the whole well if it is not in view."))
        return Guide(kicker, title, problem, action if armed else _unarmed(action), next_anchor)
    if state in (qc.CONTRADICTED, qc.INCONCLUSIVE):
        if state == qc.CONTRADICTED:
            problem = (f"Its addresses name different nanowells than at the reference timepoint"
                       f"{f': {why}' if why else ''}. It is left out of every comparison over time until it is "
                       "re-addressed.")
        else:
            problem = (f"The identity check could not tell whether its addresses name the same nanowells as at the "
                       f"reference timepoint{f': {why}' if why else ''}. It is still used, unconfirmed.")
        if not qc.anchor_can_fix(item):
            cause = qc.NOT_THE_SQUARE_WORDS.get(qc.why_code(item.reason), "something other than the square")
            action = (f"Nothing to click: the doubt here is {cause}, not the central square, so an anchor — which "
                      "only says where the square is — cannot change it. Look, then press Skip (→).")
            then = ("A re-run with an anchor would address it from the same square and the check would find the same "
                    f"thing. {after}.")
            return Guide(kicker, title, problem, action, then)
        action = ("Click the middle of the true central square. " + _where(
            hint, "The view opens on the middle of the montage; press Fit to see the whole well if the square is not "
                  "in view.") + " If the ring is not on the square, click the square itself; if you cannot find it, "
                                "press Skip (→).")
        return Guide(kicker, title, problem, action if armed else _unarmed(action), next_anchor)
    if state == qc.PARTIAL:
        n = f"{left_out:,} nanowell{'s' if left_out != 1 else ''}" if left_out else "Some nanowells"
        causes = [words for code, words in _CAUSES.items() if code in why]
        problem = (f"{n} at this timepoint did not land on themselves at the reference"
                   f"{' (' + '; '.join(causes) + ')' if causes else ''}, so they are left out of comparisons over "
                   "time. The rest are used.")
        action = ("Nothing to click. The map colours each nanowell by status and is centred on the ones left out: "
                  "check they are the ones that moved (a whole tile left out was placed a lattice step off). Press "
                  "Looks right — next when you have seen them.")
        then = (f"No re-run is needed: the left-out nanowells already stay out of every join. {after}.")
        return Guide(kicker, title, problem, action, then, "next")
    if state == qc.NOT_LOCATED:
        problem = (f"The run could not place the nanowell array at this timepoint{f': {why}' if why else ''}. An "
                   "anchor cannot change that.")
        action = ("Look at the montage. If the well holds no array, is empty or is out of focus, press Exclude well "
                  "to leave it out of this page's figures; otherwise press Skip (→).")
        then = (f"A re-run changes this only with different nanowell settings (diameter, plate format). {after}.")
        return Guide(kicker, title, problem, action, then, "exclude")
    problem = "An anchor is set here and no run has used it yet."
    action = "Nothing to click: the crosshair is the anchor. Press Re-run anchored wells… to use it."
    then = ("The re-run addresses this timepoint from the anchor and checks it again; the QC view then shows its new "
            f"state. {after}.")
    return Guide(kicker, title, problem, action, then, "rerun")


#: The identity check's reasons for leaving a nanowell out (``pipeline.nanowell_identity._excluded``), in words.
_CAUSES = {"wrong_name": "named as another nanowell", "neighbour_of_wrong": "the nanowell another one was named as",
           "misregistered_tile": "in a tile placed a lattice step off"}


def hours_suffix(label: str, hours) -> str:
    """``"(24 h)"`` after a timepoint *label*, or ``""`` when there are no hours or the label already says them."""
    if not isinstance(hours, (int, float)) or hours != hours:
        return ""
    digits = "".join(ch for ch in str(label) if ch.isdigit() or ch == ".")
    return "" if digits and digits == f"{hours:g}" else f"({hours:g} h)"


def _unarmed(action: str) -> str:
    return action + " (Set anchor is off here — the figure says why; turn it on to set one.)"


def finished(state: str | None, *, anchored: int, walked: int, skipped: int, looked: int = 0, matrix=None) -> str:
    """What the QC view says when a walk of *state* ends: what it did, and what to do next — a re-run when anything
    was anchored, and otherwise the next class with anything on the plate. *looked* is how many timepoints the walk
    went past (a class with nothing to set is looked at, not anchored)."""
    word = qc.WORDS.get(state or "", "problem")
    cls = CLASSES.get(state or "")
    if cls is not None and not cls.armed:
        text = f"Resolve finished: looked at {looked:,} of {walked:,} {word} timepoint(s)."
    else:
        text = f"Resolve finished: {anchored:,} of {walked:,} {word} timepoint(s) anchored"
        text += f", {skipped:,} skipped." if skipped else "."
    if anchored:
        return text + " Next: press Re-run anchored wells… so the run addresses them from your anchors."
    rest = [(s, n) for s, n in choices(matrix) if n and s != state]
    if rest:
        s, n = rest[0]
        return text + f" Next: choose {qc.WORDS[s]} ({n:,}) under Problem and press Resolve."
    return text
