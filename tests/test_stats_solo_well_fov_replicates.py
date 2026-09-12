"""Significance testing for a SOLO well when FOV mode is on.

Reported: bar-plot significance brackets don't draw when the compared groups are
single wells and per-FOV replicate mode is enabled. The brackets are drawn from
``StatsState.pairwise``, which ``compute_pairwise`` produces — so if that returns
``[]`` for two solo-well groups, there is nothing to draw and the fault is here
rather than in the renderer.

The contract these tests pin, from ``stats_compute.replicate_unit``: FOV mode ON
means *every FOV is a replicate*, pooled across whatever wells the group has —
so a one-well group has ``n = its FOV count``, not ``n = 1``. Only with FOV mode
OFF is a solo well ``n=1`` and legitimately untestable.

Qt-free: exercises the real ``collect_group_values`` / ``compute_pairwise``
against a hand-built frame, with a stub context carrying only the state those
functions read.
"""

from __future__ import annotations

import types

import numpy as np
import pandas as pd
import pytest

from review.domain import stats_compute as sc

N_FOV = 6
N_CELL = 300
VAL_COL = "gfp_mean_intensity"


def _well_frame(seed: int, *, loc: float, n_fov: int = N_FOV) -> pd.DataFrame:
    """One well: *n_fov* fields, one timepoint, a lognormal intensity per cell.

    Every FOV has cells comfortably above the thresholds used below, so a
    declined test cannot be blamed on empty fields.
    """
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "fov": np.repeat([f"F{i:03d}" for i in range(1, n_fov + 1)], N_CELL),
        "timepoint_hours": 0.0,
        VAL_COL: rng.lognormal(loc, 0.35, n_fov * N_CELL),
        "area_px": 500.0,
        "Included": 1,
    })


@pytest.fixture
def ctx_with(monkeypatch):
    """Build a stub ctx whose ``_safe_rows`` serves the given per-well frames."""
    def _build(frames: dict[str, pd.DataFrame]):
        ctx = types.SimpleNamespace(
            state=types.SimpleNamespace(
                dataset=types.SimpleNamespace(
                    well_paths={k: f"/tmp/{k}.csv" for k in frames},
                    cache=dict(frames)),
                channel=types.SimpleNamespace(
                    ratio_index={}, active_val_col=VAL_COL, threshold=0.0,
                    fluor_channels=[], active_channel="gfp"),
                cell_gating=types.SimpleNamespace(
                    use_scatter_thresh=False, thresh_gate_ids=[]),
            ),
            runtime=types.SimpleNamespace(
                aggregate=types.SimpleNamespace(well=None, group=None)),
        )
        monkeypatch.setattr(sc, "_safe_rows", lambda _c, lbl: frames.get(lbl))
        return ctx
    return _build


def _group(name: str, wells: list[str]):
    return types.SimpleNamespace(name=name, wells=list(wells))


# ── the producer: does a solo well yield per-FOV replicates? ────────────────

def test_solo_well_yields_one_replicate_per_fov_in_fov_mode(ctx_with):
    ctx = ctx_with({"A01": _well_frame(0, loc=6.0)})
    reps = sc.group_replicate_stats(
        ctx, ["A01"], 0.0, use_fov=True, val_col=VAL_COL, threshold=100.0,
        fluor_gates={})
    assert len(reps) == N_FOV, "FOV mode must split a solo well into its fields"


def test_solo_well_is_one_replicate_with_fov_mode_off(ctx_with):
    """The other half of the contract — this one is *meant* to be untestable."""
    ctx = ctx_with({"A01": _well_frame(0, loc=6.0)})
    reps = sc.group_replicate_stats(
        ctx, ["A01"], 0.0, use_fov=False, val_col=VAL_COL, threshold=100.0,
        fluor_gates={})
    assert len(reps) == 1


# ── the collector: do those replicates survive to the test? ────────────────

@pytest.mark.parametrize("statistic", ["mean", "median", "fraction", "mean_all"])
def test_collect_group_values_returns_n_fov_values_for_a_solo_well(
    ctx_with, statistic,
):
    """Every statistic must yield one finite value per FOV.

    ``collect_group_values`` drops non-finite summaries, and the mean of an
    empty above-threshold population is NaN — so a statistic that silently
    loses FOVs here shows up as "fewer than 2 FOV samples" downstream.
    """
    from review.features.stats.controller import collect_group_values

    ctx = ctx_with({"A01": _well_frame(0, loc=6.0)})
    vals = collect_group_values(
        ctx, _group("G1", ["A01"]), 0.0, val_col=VAL_COL, threshold=100.0,
        statistic=statistic, use_fov=True)
    assert len(vals) == N_FOV, f"{statistic}: expected one value per FOV"
    assert all(np.isfinite(v) for v in vals)


# ── the end of the chain: brackets have something to draw ──────────────────

@pytest.mark.parametrize("statistic", ["mean", "median", "fraction"])
def test_pairwise_runs_for_two_solo_well_groups_in_fov_mode(ctx_with, statistic):
    """The reported bug, at the function the brackets are drawn from.

    Two groups, one well each, FOV mode on. n = 6 per group, so the test is
    well-defined; ``compute_pairwise`` returning ``[]`` is what leaves
    ``StatsState.pairwise`` empty and the bar plot bracket-less.
    """
    pytest.importorskip("scipy")
    from review.features.stats.controller import compute_pairwise

    ctx = ctx_with({
        "A01": _well_frame(0, loc=6.0),
        "B02": _well_frame(1, loc=6.9),      # clearly separated
    })
    res = compute_pairwise(
        ctx, groups=[_group("Ctrl", ["A01"]), _group("Treated", ["B02"])],
        target_t=0.0, val_col=VAL_COL, statistic=statistic, threshold=100.0,
        test="t-test", use_fov=True)

    assert res, "no pairwise result -> no significance brackets get drawn"
    assert len(res) == 1, "one pair compared -> exactly one bracket record"
    # The record's internal shape is deliberately not asserted here: what the
    # brackets need is that a record EXISTS. Its layout is the renderer's
    # contract and is covered where that is tested.


def test_pairwise_declines_two_solo_wells_with_fov_mode_off(ctx_with):
    """Control: with FOV mode off the same two groups are n=1 and must decline,
    so a passing test above cannot be an artefact of the harness."""
    pytest.importorskip("scipy")
    from review.features.stats.controller import compute_pairwise

    ctx = ctx_with({
        "A01": _well_frame(0, loc=6.0),
        "B02": _well_frame(1, loc=6.9),
    })
    res = compute_pairwise(
        ctx, groups=[_group("Ctrl", ["A01"]), _group("Treated", ["B02"])],
        target_t=0.0, val_col=VAL_COL, statistic="mean", threshold=100.0,
        test="t-test", use_fov=False)
    assert res == []
