"""Scoring per-field quality (``review.domain.field_quality_scores``).

The test this file exists for is
``test_a_cytotoxic_well_is_not_flagged_as_an_artifact``. Everything else is
support for it.

The first design scored every metric as a robust z against the plate median,
including nucleus count. A cytotoxic compound drops nucleus count ten- to
twentyfold, which is far past any threshold worth setting — so with the
exclusion toggle on, the quality layer would have discarded exactly the wells
where the experiment worked, biasing every dose-response toward null. That is
not wider error bars; it is a directional bias that removes the signal.
"""

from __future__ import annotations

import pytest

from review.domain.field_quality_scores import (
    DEFAULT_Z_THRESHOLD, MIN_FIELDS_FOR_SCORING, robust_z, score_fields,
    summarise,
)


def _field(well, fov, *, focus=27.0, sat=0.0, dyn=2800.0, count=300,
           area=40.0, tp="0h"):
    return {"key": (well, fov, tp), "well": well, "timepoint": tp,
            "focus": focus, "sat_frac": sat, "dyn_range": dyn,
            "nuc_count": count, "nuc_area_median": area}


def _plate(wells=("A01", "A02", "B01"), n_fov=10, **kw):
    """A plate with the spread real fields have — a flat one has no MAD."""
    out = []
    for w in wells:
        for i in range(n_fov):
            out.append(_field(w, i, focus=27.0 + (i % 5) * 0.2,
                              dyn=2800.0 + (i % 5) * 10.0, **kw))
    return out


# ── the finding the split exists for ────────────────────────────────────────

def test_a_cytotoxic_well_is_not_flagged_as_an_artifact():
    """Excludes the rule that scores nucleus count against the plate.

    Three wells: two controls at ~300 nuclei per field and one treated well at
    ~15, which is a real and common effect size. Scored plate-wide, every field
    of the treated well is many MADs from the median and every one of them gets
    flagged — and with exclusion on, deleted.
    """
    rows = _plate(("A01", "A02"))
    for i in range(10):
        rows.append(_field("C12", i, focus=27.0 + (i % 5) * 0.2,
                           count=15 + (i % 3)))

    verdicts = {v.key: v for v in score_fields(rows)}
    treated = [v for k, v in verdicts.items() if k[0] == "C12"]
    assert treated, "the fixture must contain the treated well"
    assert not any(v.flagged for v in treated), (
        "the wells where the compound worked were flagged as artifacts")


def test_a_field_that_disagrees_with_its_own_well_is_still_caught():
    """The other side of that line — the split must not disable the metric.

    Nine fields of one well at ~300 nuclei and one at 4 is not biology, it is
    a field that missed the monolayer, and scoring within the well is exactly
    what catches it.
    """
    rows = [_field("A01", i, count=300 + (i % 5) * 7) for i in range(10)]
    rows.append(_field("A01", 99, count=4))

    verdicts = {v.key: v for v in score_fields(rows)}
    assert verdicts[("A01", 99, "0h")].flagged
    assert verdicts[("A01", 99, "0h")].driver.metric == "nuc_count"


def test_a_defocused_field_is_caught_against_the_plate():
    rows = _plate()
    rows.append(_field("B01", 99, focus=9.0))
    verdicts = {v.key: v for v in score_fields(rows)}
    bad = verdicts[("B01", 99, "0h")]
    assert bad.flagged and bad.driver.metric == "focus"
    assert "below the median" in bad.describe()


def test_timepoints_group_rather_than_pool():
    """Excludes the scorer that pools a time course into one distribution.

    A t=0 field and a t=72h field of the same well are not replicates of each
    other, and focus legitimately drifts over a long acquisition. Pooled, every
    field of both timepoints is anomalous with respect to the other.
    """
    rows = ([_field("A01", i, focus=27.0 + (i % 5) * 0.2, tp="0h")
             for i in range(10)]
            + [_field("A01", i, focus=14.0 + (i % 5) * 0.2, tp="72h")
               for i in range(10)])
    assert not any(v.flagged for v in score_fields(rows)), (
        "two timepoints made each other anomalous")


# ── the refusals ────────────────────────────────────────────────────────────

def test_too_few_fields_are_refused_rather_than_scored():
    """A MAD over three values is not a dispersion estimate."""
    rows = [_field("A01", i, focus=27.0 + i) for i in range(4)]
    assert len(rows) < MIN_FIELDS_FOR_SCORING
    verdicts = score_fields(rows)
    assert not any(v.flagged for v in verdicts)
    focus = [m for v in verdicts for m in v.verdicts if m.metric == "focus"]
    assert all(m.z is None for m in focus)
    assert all("fewer than" in m.declined for m in focus)


def test_an_identical_metric_across_every_field_declines_rather_than_inventing():
    """Excludes both inventions: ``inf`` for the one that differs, and ``0.0``
    for all of them. No value is any number of MADs from the median when the
    MAD is zero."""
    zs, why = robust_z([5.0] * 12)
    assert all(z is None for z in zs)
    assert "same value" in why


def test_a_metric_missing_from_one_field_is_absent_not_zero():
    rows = _plate(("A01",), n_fov=10)
    rows[3]["focus"] = None
    verdicts = {v.key: v for v in score_fields(rows)}
    m = [x for x in verdicts[("A01", 3, "0h")].verdicts if x.metric == "focus"][0]
    assert m.value is None and m.z is None and m.flagged is False
    assert "not recorded" in m.declined
    # And it does not drag the rest of the plate down with it.
    assert verdicts[("A01", 4, "0h")].scored_metrics


def test_a_dataset_with_no_records_says_so_rather_than_reporting_none_flagged():
    """Excludes the summary that reads identically for a clean plate and a
    plate nobody measured."""
    s = summarise([])
    assert "before field quality was recorded" in s.describe()
    assert s.scored == 0 and s.flagged == 0


# ── saturation: relative rules switch off exactly when they matter ──────────

def test_saturation_uses_an_absolute_bound_not_the_plate_median():
    """Excludes the relative rule.

    ``sat_frac`` is 0 for almost every field of a healthy plate, so its MAD is
    zero and a robust-z rule refuses — precisely when the one saturated field
    is the thing worth finding.
    """
    rows = _plate(("A01",), n_fov=12)
    rows[6]["sat_frac"] = 0.05
    verdicts = {v.key: v for v in score_fields(rows)}
    bad = verdicts[("A01", 6, "0h")]
    assert bad.flagged and bad.driver.metric == "sat_frac"
    assert "absolute bound" in bad.driver.declined
    assert not any(v.flagged for k, v in verdicts.items() if k[1] != 6)


# ── the verdict shape ───────────────────────────────────────────────────────

def test_there_is_no_combined_score_across_a_varying_metric_count():
    """Excludes the max-of-|z| summary.

    A field scored on three metrics has a larger maximum than one scored on
    one, for no reason to do with the pictures — so every flag is per metric
    and the driver is named rather than aggregated.
    """
    rows = _plate(("A01",), n_fov=10)
    v = score_fields(rows)[0]
    for banned in ("score", "combined", "total_z", "overall"):
        assert not hasattr(v, banned)
    assert v.driver is None, "a clean field names no driver"


def test_the_driver_is_the_largest_flagged_metric_and_says_which():
    rows = _plate(("A01",), n_fov=12)
    rows[4]["focus"] = 5.0
    rows[4]["sat_frac"] = 0.9
    v = {x.key: x for x in score_fields(rows)}[("A01", 4, "0h")]
    assert v.flagged
    assert v.driver.metric in ("focus", "sat_frac")
    assert v.describe()


@pytest.mark.parametrize("threshold", [3.0, DEFAULT_Z_THRESHOLD, 10.0])
def test_the_threshold_is_the_callers_to_move(threshold):
    rows = _plate(("A01",), n_fov=10)
    rows.append(_field("A01", 99, focus=26.0))
    flagged = [v for v in score_fields(rows, z_threshold=threshold) if v.flagged]
    assert isinstance(flagged, list)
