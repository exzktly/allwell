"""The Statistics tab's two-way ANOVA on the log scale, and what each scale says.

The fixture's raw difference of differences is exactly 0 while its ratio of
ratios is 1.5 (``tests/test_stats_tests.py``), so the raw and log runs must
disagree — a log entry that ran on raw values would pass any weaker check.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from review.domain import stats_tests as st
from review.features.stats import controller as sc
from tests.test_stats_tests import _CELLS


def _rows(cells):
    return [(c.split("-")[0], c.split("-")[1], v) for c, vals in cells.items() for v in vals]


def _run(monkeypatch, label, cells=_CELLS):
    monkeypatch.setattr(sc, "_chosen_variable",
                        lambda _ctx, name: {"_stats_var_a_cb": "rna",
                                            "_stats_var_b_cb": "treat"}[name])
    monkeypatch.setattr(sc, "_variable_label",
                        lambda _ctx, vid: {"rna": "RNA", "treat": "Treatment"}.get(vid, ""))
    monkeypatch.setattr(sc, "_two_way_rows", lambda *_a, **_k: _rows(cells))
    groups = [SimpleNamespace(name="all", wells=["A01"])]
    return "\n".join(sc._alternative_test_lines(
        SimpleNamespace(), kind="two_way", spec=sc.test_spec(label), groups=groups,
        group_vals=[], target_t=0.0, val_col="v", threshold=0.0, statistic="mean",
        use_fov=False))


def test_the_log_entry_is_offered_with_the_same_factor_pickers():
    spec = sc.test_spec("Two-way ANOVA (log scale)")
    assert spec["kind"] == "two_way" and spec.get("log") is True
    assert spec["needs"] == ("factor_a", "factor_b")


def test_the_log_run_tests_the_ratio_of_ratios(monkeypatch):
    text = _run(monkeypatch, "Two-way ANOVA (log scale)")
    assert "Log scale: the interaction tests a ratio of ratios." in text
    expected = st.two_way_anova([(a, b, math.log(v)) for a, b, v in _rows(_CELLS)],
                                factor_a="RNA", factor_b="Treatment").terms[-1]
    line = next(ln for ln in text.splitlines() if ln.startswith("RNA x Treatment"))
    _ss, _df, f_value = line[28:].split()[:3]
    assert float(f_value) == pytest.approx(expected.f, rel=1e-3)


def test_the_raw_run_names_itself_a_difference_of_differences(monkeypatch):
    text = _run(monkeypatch, "Two-way ANOVA")
    assert "Raw scale: the interaction tests a difference of differences." in text
    line = next(ln for ln in text.splitlines() if ln.startswith("RNA x Treatment"))
    assert "p = 1" in line or "p = 0.99" in line, line


def test_a_value_at_or_below_zero_declines_the_log_run(monkeypatch):
    cells = {**_CELLS, "Mut-drug": [150.0, 0.0, 140.0]}
    text = _run(monkeypatch, "Two-way ANOVA (log scale)", cells)
    assert text.startswith("Two-way ANOVA (log scale): not run — 1 of 13")
