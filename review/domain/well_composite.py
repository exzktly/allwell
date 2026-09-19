"""One verdict per well: does a human need to look at this one?

The plate map on Home already marks wells whose *fields* the run flagged. That
covers a field which is anomalous against the rest of the plate, and it misses
the case this subsystem was built for: a well whose fields all look ordinary
while a large share of its individual cells are soft, because the softness is
a gradient *within* fields rather than a property of any whole one.

So the marker is driven by a composite over three independent reasons, each
computed from numbers already on disk — the per-cell focus column in the well
CSV and the run's own field-quality block — so nothing here decodes an image
or opens an output archive.

**Every reason is reported, not just the count.** A composite that shows only
a total is a number nobody can act on: "this well scores 2" tells a user
neither what to look at nor whether they agree. The verdict carries its
reasons and the plate's tooltip prints them.

**Where the three bounds come from**, since a threshold nobody can account for
is the thing this plan keeps refusing to ship:

* **A field the run flagged** — no bound of ours. That is the pipeline's own
  robust z against the rest of the plate, already absolute, already surfaced.
  One is enough, exactly as it is for the existing marker.
* **The well's median focus below the user's cut** — the *pooled* statistic,
  and the one with a measurement behind it. §D.2c: at a pooling of thirty
  cells the in-focus fifth percentile (0.1351) clears the one-pixel-defocus
  ninety-fifth (0.1164) outright, where the per-cell distributions at the same
  defocus overlap heavily. A well whose median sits below the cut is not
  ambiguous.
* **More than :data:`SOFT_FRACTION` of the well's cells below the cut** —
  a share, because a focus gradient leaves the median fine and takes a corner
  of every field. The number is anchored rather than picked: §D.2c measured
  that a cut placed where it keeps ninety-five per cent of in-focus cells
  drops five per cent of them *by construction*, so a well at or under five
  per cent soft is indistinguishable from a perfectly in-focus one at a
  well-placed cut. The bound is twice that, which is the smallest round number
  that cannot be the cut's own false-positive rate.

:data:`UNMEASURABLE_FRACTION` is the one bound with no measurement under it,
and it is a different kind of statement: it says the *evidence* is missing for
a large part of this well, not that the well is bad. It is deliberately much
looser than the soft bound, because some plates legitimately carry small
nuclei whose rings cannot be read, and a marker that fires on all of them says
nothing.

Qt-free and import-light: plain floats and dicts. This is
:mod:`review.domain`, so it may not import a pane, the GUI layers or
``core/``, and it takes its inputs as arguments rather than reaching for a
``ctx`` — which is also what lets a test hand it a well that cannot exist yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Share of a well's measurable cells that must read below the cut before the
#: well is surfaced. See the module docstring: twice the five per cent a
#: well-placed cut removes from a perfectly in-focus population by
#: construction (§D.2c), so it cannot be the cut's own false-positive rate.
SOFT_FRACTION = 0.10

#: Share of a well's cells that may carry no focus value at all before the
#: well is surfaced. Looser than :data:`SOFT_FRACTION` by a wide margin
#: because it is a statement about missing evidence rather than about focus,
#: and because a plate of genuinely small nuclei carries a standing share of
#: unreadable rings.
UNMEASURABLE_FRACTION = 0.25


@dataclass(frozen=True)
class WellVerdict:
    """Whether one well wants a human, and every reason it might.

    ``reasons`` is empty exactly when ``needs_review`` is false, which is the
    invariant that stops a marker appearing with nothing to explain it — the
    failure the plate's existing glyph was given a tooltip to avoid.
    """

    well: str
    #: Fields the run flagged, and what it flagged worst. Empty string when
    #: nothing was flagged, so the caller need not carry a second mapping.
    flagged_fields: int = 0
    flagged_note: str = ""
    #: Cells below the cut, cells with no value, and cells in total.
    soft: int = 0
    unmeasurable: int = 0
    total: int = 0
    #: The well's median focus over its measurable cells, or ``None``.
    median_focus: float | None = None
    #: The cut these were counted against, or ``None`` when none is set.
    cut: float | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def needs_review(self) -> bool:
        return bool(self.reasons)

    @property
    def measurable(self) -> int:
        return max(0, self.total - self.unmeasurable)

    def describe(self) -> str:
        """The tooltip sentence, or ``""`` when there is nothing to say.

        Every reason, in the order they were found, joined into one line. Not
        a count: a user reading "needs review" with no reason cannot tell
        whether they agree, and the number they would need is right here.
        """
        return "; ".join(self.reasons)


def score_well(well: str, focus_values, *, cut: float | None = None,
               flagged_fields: int = 0, flagged_note: str = "") -> WellVerdict:
    """The composite for one well.

    *focus_values* is that well's per-cell focus column as an iterable of
    floats and ``None``s — ``None`` for a cell whose outline could not be
    measured, which is a different fact from a cell measured and found soft
    and is counted as such throughout.

    *cut* is the user's threshold. Without one the focus reasons cannot be
    evaluated at all and are not: a well is then surfaced only by its flagged
    fields and by how much of it is unmeasurable, both of which stand on their
    own. Inventing a cut here is the thing §D.2d measured and refused.
    """
    # Materialised ONCE. The first version filtered the iterable and then
    # called ``len(list(...))`` on it again: handed a generator -- which this
    # function's own docstring accepts -- the second pass saw an exhausted one,
    # so ``total`` came back 0 and ``unmeasurable`` **negative**, and a well
    # that was half unmeasurable was silently not flagged. A silently wrong
    # number, in the module whose whole point is that missing evidence must be
    # visible.
    measured = list(focus_values or ())
    vals = [v for v in measured if v is not None]
    total = len(measured)
    unmeasurable = total - len(vals)
    median = _median(vals) if vals else None
    soft = sum(1 for v in vals if cut is not None and v < cut)

    reasons: list[str] = []
    if flagged_fields:
        noun = "field" if flagged_fields == 1 else "fields"
        reasons.append(flagged_note or
                       f"{flagged_fields} {noun} flagged by the run")
    if cut is not None and vals and soft / len(vals) > SOFT_FRACTION:
        # **One rule, two sentences.** These were two branches, and the first
        # was strictly dominated by the second: ``median < cut`` means more
        # than half the cells are below it, which always exceeds a
        # SOFT_FRACTION of 10 %, so the share rule fired on every well the
        # median rule did. The pooled statistic §D.2c cites as the median
        # rule's justification was therefore never what surfaced a well.
        #
        # The share is what decides; the median only changes how it is said,
        # because "the whole well is soft" and "a third of it is" send a
        # reader to different places and the number alone does not distinguish
        # them.
        share = soft / len(vals)
        if median is not None and median < cut:
            reasons.append(f"the whole well is soft — median focus "
                           f"{median:.3g}, below {cut:g} "
                           f"({soft:,} of {len(vals):,} cells, {share:.0%})")
        else:
            reasons.append(f"{soft:,} of {len(vals):,} cells below {cut:g} "
                           f"({share:.0%})")
    if total and unmeasurable / total > UNMEASURABLE_FRACTION:
        reasons.append(f"{unmeasurable:,} of {total:,} cells carry no focus "
                       f"value ({unmeasurable / total:.0%}) — their outlines "
                       f"could not be measured")
    return WellVerdict(well=well, flagged_fields=flagged_fields,
                       flagged_note=flagged_note, soft=soft,
                       unmeasurable=unmeasurable, total=total,
                       median_focus=median, cut=cut,
                       reasons=tuple(reasons))


def score_plate(per_well_focus: dict, *, cut: float | None = None,
                flagged: dict | None = None) -> dict:
    """``{well: WellVerdict}`` for every well in either input.

    *flagged* maps a well to ``(how many fields, the sentence saying which)``.
    Both, rather than the sentence alone, because reading the count back out
    of its own prose is a second derivation of a fact the layer that built the
    sentence already had — and the two would then be able to disagree about a
    well.

    A well that carries flagged fields but no cached rows still gets a
    verdict — it is exactly the well a user has not opened yet, and leaving it
    out would make the marker depend on what happens to be in memory.
    """
    flagged = flagged or {}
    wells = sorted(set(per_well_focus or {}) | set(flagged))
    out: dict[str, WellVerdict] = {}
    for well in wells:
        count, note = flagged.get(well) or (0, "")
        out[well] = score_well(
            well, (per_well_focus or {}).get(well) or (),
            cut=cut, flagged_fields=int(count), flagged_note=note)
    return out


def wells_needing_review(verdicts: dict) -> dict:
    """``{well: sentence}`` for the wells that want a human.

    The shape the plate map's marker and tooltip both take, so a glyph can
    never appear without its explanation and the two cannot disagree about
    which wells carry one.
    """
    return {w: v.describe() for w, v in (verdicts or {}).items()
            if v.needs_review}


def _median(values) -> float:
    ordered = sorted(float(v) for v in values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0
