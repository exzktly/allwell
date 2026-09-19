from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from review.domain.ratio_models import RatioMetric
from review.features.home.cell_gating.gating_compute import _optimize_thresh_frac_on_between_wells


def _df(values, *, included=None, den=None, qc=None, crop=None):
    if included is None:
        included = [1] * len(values)
    data = {
        "area_px": [100] * len(values),
        "Included": included,
        "gfp_mean_intensity": values,
    }
    if den is not None:
        data["mcherry_mean_intensity"] = den
    if qc is not None:
        data["QC_Pass"] = qc
    if crop is not None:
        data["Crop_Pass"] = crop
    return pd.DataFrame(data)



def _newcombe_floor(result, weight: float = 1.0) -> float:
    """The score these counts earn, recomputed from the result's own numbers.

    Written out here rather than imported from the module under test, so the
    assertion is a second statement of the arithmetic and not a restatement of
    whatever the code happens to do. A test that calls ``supported_separation``
    to check ``supported_separation`` measures nothing.
    """
    import math

    z = 1.959963984540054

    def wilson(k, n):
        p = k / n
        d = 1.0 + z * z / n
        c = (p + z * z / (2.0 * n)) / d
        h = (z / d) * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
        return max(0.0, c - h), min(1.0, c + h)

    p1 = result.k_a / result.n_a
    p2 = result.k_b / result.n_b
    lo1, _ = wilson(result.k_a, result.n_a)
    _, hi2 = wilson(result.k_b, result.n_b)
    return (p1 - weight * p2) - math.hypot(p1 - lo1, weight * (hi2 - p2))


def test_the_cut_is_not_pushed_into_the_tail_to_buy_a_cleaner_ratio():
    """The reported defect, stated as an assertion.

    Well A is 60% responders around 1200 against a background mode at 200; Well
    B is 5000 background cells with the overlapping tail that background has.
    The gate a person would draw sits at the crossing, around 450, and keeps
    roughly 60% of Well A.

    ``|fa-fb| / max(|fa|, |fb|)`` is algebraically "maximise the count ratio
    ``(k_a+1)/(k_b+1)``", which is scale-free in N: it keeps climbing out into
    the tail as long as Well B empties faster than Well A does, however few
    cells are left in either. Measured on this exact fixture it chose 1358,
    leaving 23.6% of Well A ON — and at 20,000 cells per well, 1890 and 11.0%.
    The error grows with N, because the ``+1`` that was holding it back is a
    smaller and smaller share of the counts.

    The confidence-bounded score cannot make that trade: the separation it
    claims has to survive the interval around it, and out in the tail the
    interval is wider than the claim.
    """
    rng = np.random.default_rng(0)
    values_a = np.concatenate([rng.lognormal(np.log(1200), 0.5, 3000),
                               rng.lognormal(np.log(200), 0.55, 2000)])
    values_b = rng.lognormal(np.log(200), 0.55, 5000)

    result = _optimize_thresh_frac_on_between_wells(
        _df(list(values_a)), _df(list(values_b)),
        channel_key="gfp", min_threshold=0, max_threshold=10000,
    )

    # The responders are what the gate exists to count, and it must keep them:
    # not "below 1358" but "in the half of Well A that is actually responding".
    assert 0.55 < result.frac_a < 0.70
    assert result.k_a > 2500
    assert 300.0 < result.threshold < 700.0
    assert result.objective == "supported-separation"
    assert result.difference == pytest.approx(_newcombe_floor(result))


def test_a_thin_but_real_responder_population_is_still_found():
    """Five responders in a hundred is a weak answer, not a refused one.

    The complaint the new objective answers is "a cut backed by almost nothing
    wins", and the crude reading of that complaint is a minimum cell count. It
    is the wrong rule: a positive control with a 5% transfection efficiency is a
    real experiment, and a ten-cell floor would refuse the channel outright. The
    confidence bound scores this cut a barely-positive number instead — which is
    both an answer and an accurate description of how much is behind it.
    """
    a = _df([0.0] * 95 + [500.0] * 5)
    b = _df([0.0] * 90 + [50.0] * 10)

    result = _optimize_thresh_frac_on_between_wells(
        a, b, channel_key="gfp", min_threshold=0, max_threshold=500,
    )

    assert result.threshold > 50.0
    assert result.k_a == 5 and result.k_b == 0
    assert result.support_floor == 0
    assert 0.0 < result.difference < 0.05
    assert result.difference == pytest.approx(_newcombe_floor(result))


def test_two_wells_that_do_not_separate_are_refused_not_answered():
    """Two draws from one distribution have no threshold, and saying so is the point.

    The old objective returned a number here — a confident-looking 499.9 on this
    shape of data — because a ratio of small counts reaches its maximum on noise
    like anything else. A lower confidence bound is signed, so "no candidate
    beats zero" is a statement about the wells rather than a tuning failure.
    """
    rng = np.random.default_rng(4)
    values_a = rng.lognormal(5.3, 0.4, 2000)
    values_b = rng.lognormal(5.3, 0.4, 2000)

    with pytest.raises(ValueError, match="no threshold separates"):
        _optimize_thresh_frac_on_between_wells(
            _df(list(values_a)), _df(list(values_b)),
            channel_key="gfp", min_threshold=0, max_threshold=1000,
        )


def test_the_two_wells_are_not_interchangeable():
    """Well A is the positive arm, and passing the pair backwards is refused.

    The relative-gap objective was symmetric — ``|fa-fb|`` cannot tell which well
    is which — so a user who swapped the two combos got a confidently optimised
    threshold and a warning afterwards that they might have. ``TPR(A) - w·FPR(B)``
    is signed, so the swap has nowhere to hide: every candidate scores below zero
    and the scan refuses.
    """
    hot = _df([200.0] * 300 + [1500.0] * 700)
    cold = _df([200.0] * 1000)

    forward = _optimize_thresh_frac_on_between_wells(
        hot, cold, channel_key="gfp", min_threshold=0, max_threshold=2000)
    assert forward.frac_a > forward.frac_b

    with pytest.raises(ValueError, match="no threshold separates"):
        _optimize_thresh_frac_on_between_wells(
            cold, hot, channel_key="gfp", min_threshold=0, max_threshold=2000)


def test_an_indistinguishable_range_is_reported_from_its_middle():
    """Well A is 1000 cells at 1000, Well B 1000 cells at 100.

    Every threshold in (100, 1000) is the same classifier, so there is nothing
    in the data to choose between them and the answer is the middle of the
    range. Two things conspire to give that:

    * the candidate grid is one point between each pair of neighbouring pooled
      values, so the whole indistinguishable range collapses to the single
      candidate 550 rather than to 1100 evenly-spaced ones; and
    * where several candidates do tie, ``plateau_pick`` reports the midpoint of
      the run rather than ``argmax``'s first index.

    The old scan put 1101 linspace points across the same range, tied on 900 of
    them, and reported the lowest — 100.0, the bottom edge, which is set by the
    brightest cell in the negative control and moves whenever that cell does.
    """
    a = _df([1000.0] * 1000)
    b = _df([100.0] * 1000)

    result = _optimize_thresh_frac_on_between_wells(
        a, b, channel_key="gfp", min_threshold=0, max_threshold=1100)

    assert result.threshold == pytest.approx(550.0)
    assert result.frac_a == pytest.approx(1.0)
    assert result.frac_b == pytest.approx(0.0)


def test_one_cell_in_the_negative_control_does_not_move_the_gate():
    """The instability the new objective removes, on data with a real shape.

    Moving a single Well B cell out into the gap moved the old optimiser's
    answer by 260 intensity units on this fixture, because the answer *was* that
    cell: ``|fa-fb| / max(|fa|, |fb|)`` peaks at the first threshold above the
    negative well's maximum, wherever that happens to be. The confidence-bounded
    score is a property of both whole populations, so one cell out of a thousand
    moves it by a rounding error.

    Lognormal rather than two point masses on purpose: with only two distinct
    values every threshold between them is the same classifier and both
    objectives look stable, so the fixture would agree with whichever it was
    given. The overlap here is what makes the two answers differ.
    """
    rng = np.random.default_rng(3)
    values_a = np.concatenate([rng.lognormal(np.log(1500), 0.35, 700),
                               rng.lognormal(np.log(200), 0.35, 300)])
    values_b = rng.lognormal(np.log(200), 0.35, 1000)
    moved = values_b.copy()
    moved[0] = 900.0

    kw = dict(channel_key="gfp", min_threshold=0, max_threshold=4000)
    r0 = _optimize_thresh_frac_on_between_wells(
        _df(list(values_a)), _df(list(values_b)), **kw)
    r1 = _optimize_thresh_frac_on_between_wells(
        _df(list(values_a)), _df(list(moved)), **kw)

    assert r0.threshold == pytest.approx(r1.threshold)
    assert r0.k_a == r1.k_a


def test_optimizer_ignores_included_gate():
    # ``Included`` is set by the asynchronous gating worker from FluorGating —
    # the very threshold the optimizer is helping pick. Filtering by it would
    # be circular (and would silently shift the answer the first time the
    # worker writes the column). The optimizer must see every QC+Crop-valid
    # cell regardless of the per-channel fluorescence gate.
    #
    # The fixture is built so the two answers differ: ``Included`` is 0 for
    # every one of Well A's ON cells, so an optimizer that honoured it would see
    # Well A as entirely off and refuse the pair outright.
    values_a = [200.0] * 400 + [1500.0] * 600
    a = _df(values_a, included=[int(v < 1000) for v in values_a])
    b = _df([200.0] * 1000)

    result = _optimize_thresh_frac_on_between_wells(
        a, b, channel_key="gfp", min_threshold=0, max_threshold=2000)

    assert result.n_a == 1000
    assert result.k_a == 600


def test_optimizer_excludes_qc_and_crop_failures():
    # QC_Pass / Crop_Pass are independent of the fluorescence gate and ARE
    # honoured — they remove debris and out-of-region cells from the population
    # the optimizer scans. Well A's 200 QC failures are all ON cells and Well
    # B's 200 crop failures are all OFF, so honouring the columns and ignoring
    # them give different counts in both wells and in both directions.
    values_a = [200.0] * 400 + [1500.0] * 600
    a = _df(values_a, qc=[0 if v > 1000 and i % 3 == 0 else 1
                          for i, v in enumerate(values_a)])
    values_b = [200.0] * 1000
    b = _df(values_b, crop=[0 if i % 5 == 0 else 1 for i in range(len(values_b))])

    result = _optimize_thresh_frac_on_between_wells(
        a, b, channel_key="gfp", min_threshold=0, max_threshold=2000)

    assert result.n_a == 1000 - sum(
        1 for i, v in enumerate(values_a) if v > 1000 and i % 3 == 0)
    assert result.n_b == 800
    assert result.k_a == result.n_a - 400


def test_optimizer_supports_ratio_channels():
    ratio = RatioMetric(
        name="gfp_over_mcherry",
        numerator_channel="gfp",
        numerator_metric="mean_intensity",
        denominator_channel="mcherry",
        denominator_metric="mean_intensity",
    )
    # Well A's ratios are 4 and 8, Well B's are 1 and 2 — the separation lives in
    # the ratio and not in either channel, since both wells share a denominator.
    a = _df([40.0] * 100 + [80.0] * 100, den=[10.0] * 200)
    b = _df([10.0] * 100 + [20.0] * 100, den=[10.0] * 200)

    result = _optimize_thresh_frac_on_between_wells(
        a, b, channel_key=ratio.key(),
        min_threshold=0, max_threshold=10,
        ratios={ratio.key(): ratio},
    )

    assert 2.0 < result.threshold < 4.0
    assert result.frac_a == pytest.approx(1.0)
    assert result.frac_b == pytest.approx(0.0)
    assert result.difference == pytest.approx(_newcombe_floor(result))


def test_optimizer_filters_by_timepoint():
    values = [200.0] * 200 + [1500.0] * 200
    a = pd.DataFrame({
        "area_px": [100] * 800,
        "Included": [1] * 800,
        "timepoint_hours": [0.0] * 400 + [24.0] * 400,
        # Off at 0 h, half on at 24 h.
        "gfp_mean_intensity": [200.0] * 400 + values,
    })
    b = pd.DataFrame({
        "area_px": [100] * 800,
        "Included": [1] * 800,
        "timepoint_hours": [0.0] * 400 + [24.0] * 400,
        "gfp_mean_intensity": [200.0] * 800,
    })

    at_24 = _optimize_thresh_frac_on_between_wells(
        a, b, channel_key="gfp", min_threshold=0, max_threshold=2000,
        timepoint=("hours", 24.0),
    )
    assert at_24.n_a == 400 and at_24.n_b == 400
    assert at_24.k_a == 200 and at_24.k_b == 0

    # At 0 h neither well is on, so there is nothing for a threshold to separate
    # and the scan says so rather than returning the argmax of a flat curve.
    with pytest.raises(ValueError):
        _optimize_thresh_frac_on_between_wells(
            a, b, channel_key="gfp", min_threshold=0, max_threshold=2000,
            timepoint=("hours", 0.0),
        )


def test_optimizer_rejects_invalid_range_and_empty_values():
    with pytest.raises(ValueError, match="max threshold"):
        _optimize_thresh_frac_on_between_wells(
            _df([1.0]), _df([2.0]), channel_key="gfp",
            min_threshold=10, max_threshold=1,
        )

    with pytest.raises(ValueError, match="Well A has no valid finite values"):
        # QC_Pass=0 removes the only row, leaving no valid finite values.
        _optimize_thresh_frac_on_between_wells(
            _df([1.0], qc=[0]), _df([2.0]), channel_key="gfp",
            min_threshold=0, max_threshold=1,
        )


def test_optimizer_stable_across_gating():
    # Re-running the optimizer after the background gating worker has written
    # FluorGating's choices into ``Included`` must return the same threshold —
    # otherwise the first Optimize is right and every later one drifts (the
    # original bug: the answer landed at the range edge once Included was
    # filled in). The gate chosen here removes most of Well A's ON cells, so an
    # optimizer that read ``Included`` would answer differently on the second
    # call rather than merely wobbling.
    values_a = [200.0] * 400 + [1500.0] * 600
    values_b = [200.0] * 950 + [700.0] * 50

    a_pre = _df(values_a)   # cached frame: Included all 1 (no gating pass yet)
    b_pre = _df(values_b)

    fluor_gate = 1000.0
    a_post = _df(values_a, included=[int(v > fluor_gate) for v in values_a])
    b_post = _df(values_b, included=[int(v > fluor_gate) for v in values_b])

    kw = dict(channel_key="gfp", min_threshold=0, max_threshold=2000)
    pre = _optimize_thresh_frac_on_between_wells(a_pre, b_pre, **kw)
    post = _optimize_thresh_frac_on_between_wells(a_post, b_post, **kw)

    assert post.threshold == pytest.approx(pre.threshold)
    assert post.difference == pytest.approx(pre.difference)
    assert post.n_a == pre.n_a == 1000
    assert post.n_b == pre.n_b == 1000
