"""In a small design (``rare_exceedance_compute.SMALL_SAMPLE_WELLS`` wells or fewer), "rare responders detected"
needs the exceedance regression's q **and** its whole-well relabeling q below the level (user decision, option C):
the regression's p is a large-sample approximation that comes out too small with few wells (7-8 % below 0.05 on
null plates of 4 + 4 wells), and relabeling whole wells is exact whatever the model gets wrong.

Each fixture is built so a wrong rule gives a different answer:

* one hit among four groups at 4 + 4 wells sits at the relabeling floor 1/70, and 1/70 × 4 = 0.057 does not pass —
  "cannot be decided"; the same hit tested alone passes (q = 0.014), so a gate that ignored the correction, or one
  that never ran, gives "detected" in both;
* three hits among four sit at the floor together, and Benjamini–Hochberg divides by rank (1/70 × 4 / 3 = 0.019), so
  they pass — a rule written as "floor × groups ≥ 0.05 means undecided" refuses them;
* a design above the size keeps the regression's verdict even with a relabeling q above the level.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pandas as pd
import pytest

from review.features.nanowell import rare_export_compute as rx
from review.features.nanowell import rare_report_compute as rr
from review.features.nanowell.rare_data_compute import RareParams
from review.features.nanowell.statistics_compute import bh_adjust


def _wells(condition: str, prefix: str, k: int, rng, responders: int = 0, n: int = 400) -> list:
    rows = []
    for w in range(k):
        mu = rng.normal(0.0, 0.1)
        n0 = rng.choice([1, 2, 3], size=n)
        nt = rng.poisson(n0 * 2 ** (2 + mu + rng.normal(0, 0.5, n)))
        if responders:
            idx = rng.choice(n, responders, replace=False)
            nt[idx] = (n0[idx] * 2 ** 6.5).astype(int)
        rows += [{"plate_id": "P1", "well_id": f"{prefix}{w}", "condition": condition, "nanowell_id": str(i),
                  "n0": n0[i], "nt": nt[i], "t0_h": 0.0, "t_h": 72.0, "crop_path": ""} for i in range(n)]
    return rows


def _plate(hits: list, flat: list, *, controls: int = 4, treated: int = 4, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = _wells("ctrl", "c", controls, rng)
    for i, name in enumerate(hits):
        rows += _wells(name, f"h{i}_", treated, rng, responders=12)
    for i, name in enumerate(flat):
        rows += _wells(name, f"f{i}_", treated, rng)
    return pd.DataFrame(rows)


def _by(result) -> dict:
    return {u.treatment: u for u in result.units}


def test_a_lone_hit_among_four_groups_at_4_plus_4_wells_cannot_be_decided_and_says_why():
    result = rr.analyse(_plate(["A"], ["B", "C", "D"]), RareParams(control="ctrl", max_permutations=200))
    a = _by(result)["A"]
    assert a.q["exceedance"] < 0.05                                  # the regression alone would call it
    assert a.primary.perm.p == pytest.approx(1 / 70) and a.relabel_m == 4
    assert a.q["relabel"] == pytest.approx(4 / 70)                   # 0.057: the floor, corrected over four
    assert a.interpretation == rr.UNDECIDED and a.gate == rr.GATE_FLOOR
    head = rx.headline(a, result.prepared.params)
    assert head.startswith(
        f"A: cannot be decided with these wells — with 4 + 4 wells the regression's q = {rx._num(a.q['exceedance'])} "
        "needs relabeling the wells to agree, and relabeling gives p = 0.0143, the lowest 4 + 4 wells allow (1 in 70), "
        "which corrected across the 4 groups tested is q = 0.0571. These wells cannot say more: add control wells, or "
        "test fewer groups.")
    assert result.table.set_index("treatment").loc["A", "small_design_gate"] == rr.GATE_FLOOR


def test_the_same_hit_tested_alone_is_detected():
    result = rr.analyse(_plate(["A"], []), RareParams(control="ctrl", max_permutations=200))
    a = result.units[0]
    assert a.q["relabel"] == pytest.approx(1 / 70)
    assert a.interpretation == rr.DETECTED and a.gate == ""


def test_several_hits_at_the_floor_pass_together_because_the_correction_divides_by_rank():
    result = rr.analyse(_plate(["A", "B", "C"], ["D"]), RareParams(control="ctrl", max_permutations=200))
    by = _by(result)
    for name in "ABC":
        assert by[name].q["relabel"] == pytest.approx(4 / 70 / 3)     # 0.019
        assert by[name].interpretation == rr.DETECTED, name
    assert by["D"].interpretation == rr.NOT_DETECTED


def test_the_relabeling_q_is_corrected_within_each_donor():
    table = _plate(["A"], ["B", "C", "D"])
    families = (("Donor 1", "ctrl", ("A",)), ("Donor 2", "ctrl", ("B", "C", "D")))
    result = rr.analyse(table, RareParams(control="ctrl", families=families, max_permutations=200))
    by = _by(result)
    # Donor 1 tests A alone: its relabeling q is its p, and it is detected; across all four it would not be.
    assert by["A"].relabel_m == 1 and by["A"].q["relabel"] == pytest.approx(1 / 70)
    assert by["A"].interpretation == rr.DETECTED
    mine = [by[n] for n in "BCD"]
    want, _ = bh_adjust([u.primary.perm.p for u in mine])
    assert [u.q["relabel"] for u in mine] == pytest.approx(want)


def test_relabeling_that_could_have_confirmed_and_did_not_is_not_detected():
    result = rr.analyse(_plate(["A"], []), RareParams(control="ctrl", max_permutations=200))
    a = result.units[0]
    a.primary.perm = dataclasses.replace(a.primary.perm, p=0.2)
    a.q["relabel"] = 0.2
    assert rr.interpret(a, 0.05) == rr.NOT_DETECTED and a.gate == rr.GATE_NOT_CONFIRMED
    a.interpretation = rr.NOT_DETECTED
    assert rx.headline(a, result.prepared.params).startswith(
        f"A: rare responders not detected — with 4 + 4 wells the regression's q = {rx._num(a.q['exceedance'])} needs "
        "relabeling the wells to agree, and it gives q = 0.2.")


def test_a_design_above_the_size_keeps_the_regression_s_verdict():
    result = rr.analyse(_plate(["A"], [], controls=9, treated=8), RareParams(control="ctrl", max_permutations=200))
    a = result.units[0]
    assert len(a.controls) + len(a.treated) == 17 > rr.ex.SMALL_SAMPLE_WELLS
    a.q["relabel"] = 0.5                                             # even a relabeling that disagrees
    assert rr.interpret(a, 0.05) == rr.DETECTED and a.gate == ""


def test_a_small_design_with_no_relabeling_p_is_not_called_detected():
    result = rr.analyse(_plate(["A"], []), RareParams(control="ctrl", max_permutations=200))
    a = result.units[0]
    a.primary.perm = None
    assert rr.interpret(a, 0.05) == rr.UNDECIDED and a.gate == rr.GATE_NO_RELABELING
    assert math.isnan(rr._relabel_p(a))
