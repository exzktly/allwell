"""One verdict per well, and whether it surfaces the wells that matter.

The composite's whole job is to be **actionable**, and the two ways it can
fail are silent in opposite directions: a rule that fires on every well is a
marker nobody reads, and a rule that fires on none is a marker that is never
noticed missing. So every test here names the wrong implementation it excludes
and builds a fixture where the right and wrong answers differ — §7.8 #2, which
is the one that is easiest to skip and hardest to recover from.
"""
from __future__ import annotations

import pytest

from review.domain.well_composite import (
    SOFT_FRACTION,
    UNMEASURABLE_FRACTION,
    score_plate,
    score_well,
    wells_needing_review,
)


def _vals(n_sharp: int, n_soft: int = 0, n_none: int = 0,
          sharp: float = 0.30, soft: float = 0.02):
    return [sharp] * n_sharp + [soft] * n_soft + [None] * n_none


# ── a clean well says nothing ───────────────────────────────────────────────

def test_a_sharp_well_is_not_surfaced():
    """The most important negative. A rule that fires on every well is a
    marker nobody reads, and the fixture is deliberately at the boundary:
    one soft cell in fifty is 2 %, under the bound and under the 5 % a
    well-placed cut removes from a perfectly in-focus population anyway."""
    v = score_well("A01", _vals(49, 1), cut=0.10)
    assert v.needs_review is False
    assert v.reasons == ()
    assert v.describe() == ""


def test_a_well_with_no_cut_is_judged_only_on_what_stands_alone():
    """Without a threshold the focus reasons cannot be evaluated, and
    inventing one is exactly what §D.2d measured and refused. Excludes the
    implementation that substitutes a default and marks half the plate."""
    v = score_well("A01", _vals(0, 50), cut=None)
    assert v.needs_review is False
    assert v.soft == 0


# ── the three reasons, each on its own ──────────────────────────────────────

def test_a_flagged_field_surfaces_the_well_by_itself():
    v = score_well("A01", _vals(50), cut=0.10,
                   flagged_fields=1, flagged_note="1 field flagged — worst: "
                                                  "field 3, out of focus")
    assert v.needs_review is True
    assert "field 3" in v.describe()


def test_a_well_whose_median_is_below_the_cut_is_called_soft_outright():
    """The pooled statistic, and the strongest of the three: §D.2c measured
    that at a pooling of thirty cells the in-focus fifth percentile clears the
    one-pixel-defocus ninety-fifth outright, where the per-cell distributions
    overlap heavily at the same defocus."""
    v = score_well("A01", _vals(10, 40), cut=0.10)
    assert v.needs_review is True
    assert "the whole well is soft" in v.describe()
    assert "median focus 0.02" in v.describe()


def test_a_gradient_is_surfaced_although_the_median_is_fine():
    """**The case the whole subsystem exists for.** Thirty per cent of the
    cells are soft and the median is perfectly sharp, so every pooled
    statistic about this well is clean — which is what a focus gradient across
    a field looks like. Excludes a composite built on the median alone."""
    v = score_well("A01", _vals(35, 15), cut=0.10)
    assert v.median_focus == pytest.approx(0.30)
    assert v.needs_review is True
    assert "15 of 50 cells below 0.1 (30%)" in v.describe()


def test_a_soft_well_is_not_also_told_what_share_of_it_is_soft():
    """One finding, said once. Excludes the implementation that reports both,
    which reads as two problems where there is one."""
    v = score_well("A01", _vals(5, 45), cut=0.10)
    assert len(v.reasons) == 1
    assert "the whole well is soft" in v.reasons[0]


def test_a_well_mostly_unmeasurable_is_surfaced_as_missing_evidence():
    """A different statement from "this well is soft": the evidence is absent
    for most of it. Excludes folding the unmeasurable into the soft count,
    which would assert something about cells nobody could measure."""
    v = score_well("A01", _vals(20, 0, 30), cut=0.10)
    assert v.needs_review is True
    assert v.soft == 0
    assert "carry no focus value" in v.describe()
    assert "60%" in v.describe()


def test_unmeasurable_cells_do_not_drag_the_median_or_the_share():
    """They are excluded from both denominators. Excludes the implementation
    that treats a missing value as zero, which would call every plate with
    small nuclei soft."""
    v = score_well("A01", _vals(10, 0, 5), cut=0.10)
    assert v.median_focus == pytest.approx(0.30)
    assert v.soft == 0
    assert v.measurable == 10
    assert v.total == 15


# ── the bounds themselves ───────────────────────────────────────────────────

def test_the_soft_share_bound_is_strict_rather_than_inclusive():
    """Exactly at the bound is not over it. The fixture puts one well on each
    side of the same number, so an off-by-one in the comparison shows."""
    at = score_well("A01", _vals(90, 10), cut=0.10)      # exactly 10 %
    over = score_well("A02", _vals(89, 11), cut=0.10)    # 11 %
    assert at.soft / at.measurable == pytest.approx(SOFT_FRACTION)
    assert at.needs_review is False
    assert over.needs_review is True


def test_the_unmeasurable_bound_is_much_looser_than_the_soft_one():
    """It is a statement about missing evidence, not about focus, and a plate
    of genuinely small nuclei carries a standing share of unreadable rings.
    Excludes a later edit that quietly equalises them."""
    assert UNMEASURABLE_FRACTION > SOFT_FRACTION * 2


# ── the plate ───────────────────────────────────────────────────────────────

def test_a_flagged_well_with_no_cached_rows_still_gets_a_verdict():
    """Exactly the well a user has not opened yet. Excludes the plate walk
    keyed on the row cache, which would make the marker depend on what
    happens to be in memory."""
    plate = score_plate({"A01": _vals(50)}, cut=0.10,
                        flagged={"B02": (2, "2 fields flagged — worst: "
                                            "field 1, clipped")})
    assert set(plate) == {"A01", "B02"}
    assert plate["B02"].needs_review is True
    assert plate["A01"].needs_review is False


def test_the_marker_mapping_carries_a_reason_for_every_well_in_it():
    """The invariant that stops a glyph appearing with nothing to explain it:
    ``reasons`` is empty exactly when the well is not surfaced."""
    plate = score_plate(
        {"A01": _vals(50), "A02": _vals(20, 30), "A03": _vals(10, 0, 40)},
        cut=0.10)
    marked = wells_needing_review(plate)
    assert set(marked) == {"A02", "A03"}
    assert all(text for text in marked.values())
    for well, verdict in plate.items():
        assert bool(verdict.reasons) == bool(marked.get(well))


def test_the_count_comes_from_the_caller_rather_than_out_of_its_own_prose():
    """The field-quality layer has the count and the sentence; reading the
    count back out of the sentence would be a second derivation that could
    disagree with the marker beside it. Excludes it by handing over a note
    whose leading token is not the count."""
    plate = score_plate({}, cut=0.10,
                        flagged={"A01": (7, "seven fields flagged")})
    assert plate["A01"].flagged_fields == 7
    assert "seven fields flagged" in plate["A01"].describe()


# ── reasons accumulate ──────────────────────────────────────────────────────

def test_a_well_with_two_problems_reports_both():
    """A composite that shows only a total is a number nobody can act on."""
    v = score_well("A01", _vals(10, 0, 40), cut=0.10,
                   flagged_fields=1, flagged_note="1 field flagged")
    assert len(v.reasons) == 2
    assert "1 field flagged" in v.describe()
    assert "carry no focus value" in v.describe()
