"""A well's remembered population answers exactly what the direct computation answers.

``review/domain/well_population.py`` keeps each well's threshold-free filtered
cells so that a threshold edit is answered from memory instead of a read of
every well. That is only worth having if it is the same answer: these tests
build messy frames — excluded cells, NaN values, areas and timepoints, two
timepoints within the matching tolerance of each other, an untrusted field
labelling, a thresholding on-column, gates — and require the population's
aggregate and extras to equal ``aggregate_with_threshold_df`` and
``stats_compute._raw_extra`` bit for bit, at thresholds chosen so that each
timepoint has cells on both sides.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from review.data.data_loading import aggregate_with_threshold_df
from review.domain.stats_compute import _extra_inputs, _raw_extra
from review.domain.well_population import aggregate, build_population, extra_table

VAL = "gfp_mean_intensity"


def _messy(seed: int, *, n: int = 4000, fov_ok: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    # 24.0 and 24.01 are distinct for the aggregate's exact partition but one
    # timepoint for the extras' tolerance match (1e-3 relative), so the two
    # definitions disagree here and a population that shared them would fail.
    tps = rng.choice([0.0, 24.0, 24.01, 48.0, np.nan], n, p=[0.3, 0.25, 0.1, 0.3, 0.05])
    fov = rng.choice(["1", "2", "3", "4"], n) if fov_ok else np.full(n, "1")
    gfp = rng.lognormal(4, 1, n)
    gfp[rng.random(n) < 0.03] = np.nan
    area = rng.uniform(50, 400, n)
    area[rng.random(n) < 0.02] = np.nan
    return pd.DataFrame({
        "well": "A01", "fov": fov,
        "timepoint": [f"{t}h" for t in tps],
        "timepoint_hours": tps,
        "filename": [f"A01_f{f}_t{t}.tif" for f, t in zip(fov, tps)],
        "nucleus_id": np.arange(1, n + 1),
        "area_px": area,
        VAL: gfp,
        "mcherry_mean_intensity": rng.lognormal(3, 1, n),
        "Included": (rng.random(n) > 0.1).astype(int),
        "QC_Pass": (rng.random(n) > 0.05).astype(int),
        "Gate_On": (rng.random(n) > 0.5).astype(int),
    })


def _same(a, b) -> bool:
    if len(a) != len(b):
        return False
    for pa, pb in zip(a, b):
        for x, y in zip(pa, pb):
            if isinstance(x, float) and math.isnan(x):
                if not (isinstance(y, float) and math.isnan(y)):
                    return False
            elif x != y or type(x) is not type(y):
                return False
    return True


@pytest.mark.parametrize("seed", [0, 1, 2])
@pytest.mark.parametrize("per_fov", [False, True])
@pytest.mark.parametrize("on_col", [None, "Gate_On"])
@pytest.mark.parametrize("gates", [{}, {"mcherry": 15.0}])
def test_the_aggregate_is_the_direct_one_at_every_threshold(seed, per_fov, on_col, gates):
    df = _messy(seed)
    pop = build_population(df, tp_col="timepoint_hours", val_col=VAL, fluor_gates=gates,
                           ratios=None, on_col=on_col, per_fov=per_fov, extra_inputs=None)
    thresholds = np.quantile(df[VAL].dropna(), [0.1, 0.5, 0.9]).tolist() + [1e9]
    for use_sem in (False, True):
        for threshold in thresholds:
            want = aggregate_with_threshold_df(
                df, threshold=threshold, use_sem=use_sem, val_col=VAL, fluor_gates=gates,
                per_fov_spread=per_fov, on_col=on_col)
            got = aggregate(pop, threshold=threshold, use_sem=use_sem, per_fov_spread=per_fov)
            assert want, "the fixture produced no points; this measures nothing"
            assert _same(got, want), (threshold, use_sem, got, want)


def test_untrusted_field_labels_stay_undefined():
    df = _messy(3, fov_ok=False)
    df["filename"] = [f"A01_img{i % 7}.tif" for i in range(len(df))]  # seven images, one label
    pop = build_population(df, tp_col="timepoint_hours", val_col=VAL, fluor_gates={},
                           ratios=None, on_col=None, per_fov=True, extra_inputs=None)
    want = aggregate_with_threshold_df(df, threshold=50.0, val_col=VAL, per_fov_spread=True)
    got = aggregate(pop, threshold=50.0, use_sem=False, per_fov_spread=True)
    assert pop.fov_trusted is False
    assert _same(got, want)


@pytest.mark.parametrize("seed", [0, 1])
@pytest.mark.parametrize("on_col", [None, "Gate_On"])
def test_the_extras_are_the_direct_ones_at_every_timepoint(seed, on_col):
    df = _messy(seed)
    gates = {"mcherry": 15.0}
    inputs = _extra_inputs(df, VAL, gates, None, on_col)
    pop = build_population(df, tp_col="timepoint_hours", val_col=VAL, fluor_gates=gates,
                           ratios=None, on_col=on_col, per_fov=False, extra_inputs=inputs)
    times = np.unique(inputs[1][inputs[0]])
    assert {24.0, 24.01} <= set(times.tolist())
    for threshold in (20.0, 60.0, 200.0):
        table = extra_table(pop, threshold)
        for t in times:
            want = _raw_extra(df, float(t), threshold, VAL, gates, None, on_col)
            got = table.get(float(t))
            assert got is not None
            assert np.allclose(got, want, rtol=0, atol=0, equal_nan=True), (t, threshold, got, want)
    # 24.0's extras pool 24.01's cells (tolerance) while its aggregate does not.
    agg = {p[0]: p for p in aggregate(pop, threshold=60.0, use_sem=False, per_fov_spread=False)}
    extra = pop.extras[24.0]
    assert extra.vals.size > agg[24.0][5], "the tolerance pooling was not exercised"


def test_a_frame_that_cannot_answer_the_extras_says_so():
    df = _messy(0).drop(columns=["area_px"])
    pop = build_population(df, tp_col="timepoint_hours", val_col=VAL, fluor_gates={},
                           ratios=None, on_col=None, per_fov=False,
                           extra_inputs=_extra_inputs(df, VAL, {}, None, None))
    assert aggregate(pop, threshold=1.0, use_sem=False, per_fov_spread=False) == []
    assert extra_table(pop, 1.0) is None


def test_shared_arrays_are_counted_once():
    df = _messy(0)
    df["timepoint_hours"] = df["timepoint_hours"].replace(24.01, 24.0)
    # A NaN ``timepoint_hours`` is resolved from the text column by the
    # aggregate and dropped by the extras, so the two only agree without one.
    df = df[df["timepoint_hours"].notna()].reset_index(drop=True)
    inputs = _extra_inputs(df, VAL, {}, None, None)
    pop = build_population(df, tp_col="timepoint_hours", val_col=VAL, fluor_gates={},
                           ratios=None, on_col=None, per_fov=False, extra_inputs=inputs)
    assert all(pop.extras[float(g.t)].vals is g.vals for g in pop.groups)
    values = sum(g.vals.nbytes for g in pop.groups)
    assert values <= pop.nbytes < 2 * values
