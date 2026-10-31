"""Review leaves out a nanowell at a timepoint where its own rim was out of focus (``rim_focus_compute``).

The rule is relative: a rim softer than a fraction of its well's sharp rims (the 90th percentile at that
timepoint) is out of focus. The flagged row becomes an ``out_of_focus`` gap — no count, confluence or fluorescence,
no fold change from or to it, no growth rate through it — with the reason named, and the figure's note counts what
was left out and what could not be judged.

The fixtures are built so the right and the wrong answers differ: the soft timepoint's count is far off the
nanowell's growth curve, so a growth rate that still used it would be a different number, and the soft rim sits
between the default fraction and a lower one, so a rule that ignored its fraction would flag both or neither.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from review.features.nanowell import distribution_compute as dc
from review.features.nanowell import rim_focus_compute as rfc
from review.features.nanowell.collect import OUT_OF_FOCUS, load_nanowell_data
from review.features.nanowell.series_compute import exclusion_reason, long_format, site_metrics
from review.features.nanowell.state import NanowellState
from tests.test_nanowell_review_data import _dataset, _row

STARTS = {"t1": "2026-03-01T00:00:00", "t2": "2026-03-01T10:00:00", "t3": "2026-03-01T20:00:00",
          "t4": "2026-03-02T06:00:00"}
#: (0, 0) doubles every ten hours: 2, 4, 8, 16 — but t3 reads 100, as a soft tile miscounts.
SOFT_COUNTS = {"t1": 2, "t2": 4, "t3": 100, "t4": 16}
SHARP, SOFT = 0.24, 0.15          # 0.15 / 0.24 = 0.625: under the default 0.75, over 0.6


def _rows(*, soft_focus=SOFT, sites=12, focus_column=True):
    rows = []
    for tp in STARTS:
        for k in range(sites):
            q, r = k % 4, k // 4
            n = SOFT_COUNTS[tp] if (q, r) == (0, 0) else 3
            row = _row(tp, q, r, n=n, c=0.1 * n / 3)
            row["focus"] = SHARP * (1.0 - 0.01 * k)                     # a little spread, as real rims have
            if (q, r) == (0, 0) and tp == "t3":
                row["focus"] = soft_focus
            if (q, r) == (1, 0) and tp == "t3":
                row["focus"] = None                                      # predicted: rim not fitted
            if not focus_column:
                row.pop("focus")
            rows.append(row)
    return rows


def _metrics(tmp_path, rule, **kwargs):
    out = _dataset(tmp_path, {"A01": _rows(**kwargs)}, starts=STARTS)
    return site_metrics(load_nanowell_data(out), "earliest", focus=rule)


def _at(metrics, q, r, tp):
    return metrics.set_index(["nw_q", "nw_r", "timepoint"]).loc[(q, r, tp)]


def test_a_soft_rim_is_a_named_gap_and_its_count_leaves_every_figure(tmp_path):
    metrics = _metrics(tmp_path, rfc.RimFocusRule())
    soft = _at(metrics, 0, 0, "t3")
    assert soft["state"] == OUT_OF_FOCUS and soft["recorded_state"] == "measured"
    assert not soft["rim_focus_ok"]
    assert "rim focus 0.15" in soft["reason"] and "75%" in soft["reason"]
    assert math.isnan(soft["n"]) and math.isnan(soft["c"]) and math.isnan(soft["count_fc"])
    assert soft["n_cells"] == 100                                           # the run's number is still on the row
    # The growth rate is the slope of ln n over t1, t2 and t4 alone: ln 2 per ten hours.
    assert _at(metrics, 0, 0, "t1")["growth_rate"] == pytest.approx(math.log(2) / 10)
    assert _at(metrics, 0, 0, "t4")["count_fc"] == 8.0                      # the sharp timepoints still join
    assert exclusion_reason(metrics, "n")[metrics["state"].eq(OUT_OF_FOCUS)].eq(OUT_OF_FOCUS).all()
    assert dc.reason_label(OUT_OF_FOCUS) == "rim out of focus"
    exported = long_format(metrics, ["GFP"]).set_index(["nw_q", "nw_r", "timepoint"]).loc[(0, 0, "t3")]
    assert (exported["state"], exported["recorded_state"], bool(exported["rim_focus_ok"])) == (
        OUT_OF_FOCUS, "measured", False)


def test_only_the_soft_nanowell_is_flagged_and_an_unfitted_rim_is_kept(tmp_path):
    metrics = _metrics(tmp_path, rfc.RimFocusRule())
    assert metrics["state"].eq(OUT_OF_FOCUS).sum() == 1
    unfitted = _at(metrics, 1, 0, "t3")
    assert unfitted["state"] == "measured" and unfitted["n"] == 3 and bool(unfitted["rim_focus_ok"])
    t = rfc.tally(metrics)
    assert (t.flagged, t.nanowells, t.unjudged, t.unreferenced, t.recorded) == (1, 1, 1, 0, True)
    said = rfc.note(metrics, rfc.RimFocusRule())
    assert said.startswith("1 nanowell-timepoint (1 nanowell) left out as out of focus")
    assert "1 with no rim focus (rim not fitted) were kept unchecked." in said


def test_the_fraction_decides_and_off_flags_nothing(tmp_path):
    assert _metrics(tmp_path / "a", rfc.RimFocusRule(fraction=0.6))["state"].eq(OUT_OF_FOCUS).sum() == 0
    off = _metrics(tmp_path / "b", rfc.RimFocusRule(enabled=False))
    assert off["state"].eq(OUT_OF_FOCUS).sum() == 0 and _at(off, 0, 0, "t3")["n"] == 100
    assert rfc.note(off, rfc.RimFocusRule(enabled=False)) == ""
    none = _metrics(tmp_path / "c", None)
    assert none["state"].eq(OUT_OF_FOCUS).sum() == 0


def test_a_well_with_too_few_rims_is_not_judged_and_says_so(tmp_path):
    metrics = _metrics(tmp_path, rfc.RimFocusRule(), sites=rfc.MIN_SITES - 1)
    assert metrics["state"].eq(OUT_OF_FOCUS).sum() == 0
    assert metrics["rim_focus_ref"].isna().all()
    assert f"under {rfc.MIN_SITES} rims" in rfc.note(metrics, rfc.RimFocusRule())


def test_a_run_without_rim_focus_flags_nothing_and_says_it_was_not_recorded(tmp_path):
    metrics = _metrics(tmp_path, rfc.RimFocusRule(), focus_column=False)
    assert metrics["state"].eq(OUT_OF_FOCUS).sum() == 0
    assert rfc.note(metrics, rfc.RimFocusRule()) == (
        "Rim focus was not recorded in this run, so no nanowell was checked for focus.")


def test_a_soft_baseline_leaves_the_nanowell_with_no_fold_change(tmp_path):
    rows = _rows()
    for row in rows:
        if (row["nw_q"], row["nw_r"]) == (0, 0):
            row["focus"] = SOFT if row["timepoint"] == "t1" else SHARP
    out = _dataset(tmp_path, {"A01": rows}, starts=STARTS)
    metrics = site_metrics(load_nanowell_data(out), "earliest", focus=rfc.RimFocusRule())
    site = metrics[(metrics["nw_q"] == 0) & (metrics["nw_r"] == 0)]
    assert site["count_fc"].isna().all() and site["occupancy_class"].isna().all()
    reasons = exclusion_reason(site, "count_fc")
    assert set(reasons[site["timepoint"] != "t1"]) == {"baseline " + OUT_OF_FOCUS}
    assert dc.reason_label("baseline " + OUT_OF_FOCUS) == "baseline rim out of focus"


def test_the_reference_is_the_sharp_rims_so_a_mostly_soft_well_still_finds_its_soft_nanowells():
    import pandas as pd

    # Seven of twelve soft, five sharp: a median would sit among the soft ones and flag nothing.
    focus = [0.12] * 7 + [0.24] * 5
    frame = pd.DataFrame({"well": "A01", "timepoint": "t1", "nw_q": range(12), "nw_r": 0,
                          "state": "measured", "reason": "", "focus": focus})
    rfc.flag(frame, rfc.RimFocusRule())
    assert list(frame["state"]) == [OUT_OF_FOCUS] * 7 + ["measured"] * 5
    assert frame["rim_focus_ref"].iloc[0] == pytest.approx(np.percentile(focus, rfc.REFERENCE_PERCENTILE))


def test_the_page_keeps_its_rule_and_a_setting_saved_before_it_existed_restores_it_on():
    st = NanowellState()
    assert st.rim_focus_rule() == rfc.RimFocusRule(True, rfc.DEFAULT_FRACTION)
    st.rim_focus_on, st.rim_focus_fraction = False, 0.6
    restored = NanowellState()
    restored.apply_config(st.to_config())
    assert (restored.rim_focus_on, restored.rim_focus_fraction) == (False, 0.6)
    old = NanowellState()
    old.apply_config({"view": "distribution"})
    assert (old.rim_focus_on, old.rim_focus_fraction) == (True, rfc.DEFAULT_FRACTION)
    out_of_range = NanowellState()
    out_of_range.apply_config({"rim_focus_fraction": 1.5})
    assert out_of_range.rim_focus_fraction == rfc.DEFAULT_FRACTION
