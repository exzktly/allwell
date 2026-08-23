"""Regression guards for the statistics contract (PR 2).

Every number a plot, a table or an export shows is produced by the functions
guarded here, and each rule below was broken at the time it was written. They
are grouped by the question they answer:

1. What does an *undefined* spread look like? (``NaN``, never ``0.0`` — the two
   are different facts and one of them is a measurement.)
2. Which timepoint is a "vs t0" baseline actually taken from?
3. What counts as a *number* — is a ``numpy.float32`` one?
4. Which cells does an "off" gate exclude? (None.)
5. Can a well whose FOV labels are provably wrong contribute a per-field
   replicate? (No.)
"""

from __future__ import annotations

import ast
import math
import pathlib

import numpy as np
import pandas as pd
import pytest

from well_viewer.data.data_loading import aggregate_with_threshold_df
from well_viewer.domain.fold_change import (
    _rel_err_sq,
    _scale_pt,
    normalize_pts,
    scale_bar_value,
)
from well_viewer.domain.stats_compute import _reduce_reps

# ``_as_finite`` and ``active_fluor_gates`` are imported inside the tests that
# use them, not at module scope. Copied into a worktree of the base branch to
# prove these guards bite, a module-scope import of a symbol the fix introduces
# turns the whole file into a collection error — which reads exactly like "it
# failed on the base" while measuring nothing at all (CLAUDE.md §7.4 #4).


class _Rep:
    """Minimal stand-in for a ``ReplicateStat`` — ``_reduce_reps`` only ever
    reads one named field off it."""

    def __init__(self, value: float) -> None:
        self.mean = value
        self.frac = value
        self.n_above = value


# ── 1. an undefined spread is NaN, never 0.0 ─────────────────────────────────

def test_single_replicate_spread_is_undefined_not_zero():
    """One replicate has no spread. Reporting ``0.0`` makes a set nobody could
    compute a spread for indistinguishable from a set whose replicates agreed
    exactly — and it exported as ``err_SD = 0.000000``."""
    mean, spread = _reduce_reps([_Rep(5.0)], "mean", use_sem=False)
    assert mean == 5.0
    assert math.isnan(spread)


def test_no_replicates_gives_undefined_mean_and_spread():
    mean, spread = _reduce_reps([], "mean", use_sem=False)
    assert math.isnan(mean) and math.isnan(spread)


def test_two_identical_replicates_have_a_measured_zero_spread():
    """The control case: this ``0.0`` is a measurement and must survive."""
    mean, spread = _reduce_reps([_Rep(5.0), _Rep(5.0)], "mean", use_sem=False)
    assert mean == 5.0 and spread == 0.0


def test_an_undefined_spread_survives_normalization_on_both_paths():
    """The line path folded it to 0.0 while the bar path preserved it, so one
    figure carried two conventions for "we could not compute this"."""
    line_spread = _scale_pt((0, 250.0, float("nan")), 100.0)[2]
    _, bar_spread = scale_bar_value(250.0, float("nan"), control_mean=100.0)
    assert math.isnan(line_spread)
    assert math.isnan(bar_spread)


# ── 2. which timepoint the baseline comes from ───────────────────────────────

_PTS = [
    # (t, mean, spread, frac, n_above, n_total, frac_spread, npf_m, npf_s, n_spread, n_total_x)
    (0.0, 100.0, 5.0, 0.0, 0, 200, 0.0, 0.0, 0.0, 0.0, 0),
    (24.0, 300.0, 9.0, 0.40, 80, 200, 0.0, 0.0, 0.0, 0.0, 0),
    (48.0, 500.0, 11.0, 0.60, 120, 200, 0.0, 0.0, 0.0, 0.0, 0),
]


def test_subtract_mode_accepts_a_zero_baseline():
    """Subtracting zero is well defined, and zero at t0 is the *expected*
    reading for a fraction-on or an event count at the start of an induction
    experiment. Requiring a non-zero baseline for both operations meant the
    requested timepoint was skipped and a later one silently substituted — so
    the mean panel normalised against t=0 while the fraction and events panels
    beside it normalised against t=24, under one axis label."""
    out = normalize_pts(_PTS, use_t0=True, baseline_tp=0.0,
                        op="subtract", normalize_frac_n=True)
    by_t = {p[0]: p for p in out}
    assert by_t[0.0][1] == 0.0 and by_t[24.0][1] == 200.0
    assert by_t[0.0][3] == pytest.approx(0.0)
    assert by_t[24.0][3] == pytest.approx(0.40)   # 0.40 - 0.0, not 0.40 / 0.40
    assert by_t[48.0][4] == pytest.approx(120.0)  # 120 - 0, not 120 / 80


def test_ratio_mode_still_refuses_a_zero_divisor():
    """Division by zero is undefined however inconvenient, so the fall-through
    stands here — it is now logged rather than silent."""
    out = normalize_pts(_PTS, use_t0=True, baseline_tp=0.0,
                        op="ratio", normalize_frac_n=True)
    by_t = {p[0]: p for p in out}
    assert by_t[24.0][1] == pytest.approx(3.0)    # mean divides by t=0
    assert by_t[24.0][3] == pytest.approx(1.0)    # fraction falls back to t=24


def test_the_zero_baseline_fallback_is_announced(caplog):
    with caplog.at_level("WARNING"):
        normalize_pts(_PTS, use_t0=True, baseline_tp=0.0,
                      op="ratio", normalize_frac_n=True)
    assert any("baseline timepoint" in r.getMessage() for r in caplog.records)


# ── 3. a numpy float is a number ─────────────────────────────────────────────

def test_numpy_floats_are_numbers():
    from well_viewer.domain.fold_change import _as_finite

    """``isinstance(x, float)`` is False for ``numpy.float32``, so the spelling
    it replaced answered "not a number" — silently, in the most numerically
    sensitive module in the app, turning a real spread into a vanished error
    bar rather than an exception."""
    assert _as_finite(np.float32(2.5)) == pytest.approx(2.5)
    assert _as_finite(np.float64(2.5)) == pytest.approx(2.5)
    assert _as_finite(np.nan) is None
    assert _as_finite(None) is None
    assert _as_finite(True) is None      # a flag is not a measurement
    assert _as_finite("nope") is None


def test_float32_and_float_normalize_identically():
    assert _rel_err_sq(np.float32(10), np.float32(2)) == pytest.approx(
        _rel_err_sq(10.0, 2.0))
    f32 = _scale_pt((0, np.float32(10), np.float32(1)), 2.0)
    f64 = _scale_pt((0, 10.0, 1.0), 2.0)
    assert f32[1] == pytest.approx(f64[1])
    assert f32[2] == pytest.approx(f64[2])


# ── 4. an "off" gate excludes nothing ────────────────────────────────────────

def test_a_zero_gate_is_not_a_filter():
    from well_viewer.domain.gating_state import active_fluor_gates

    assert active_fluor_gates({"gfp": 0.0, "mcherry": 0.0}) == {}
    assert active_fluor_gates({"gfp": 0.0, "mcherry": 10.0}) == {"mcherry": 10.0}
    # A negative floor is a choice, not a default: background-subtracted
    # channels legitimately run below zero.
    assert active_fluor_gates({"gfp": -3.0}) == {"gfp": -3.0}


def _gating_frame():
    return pd.DataFrame({
        "timepoint_hours": [0.0] * 4,
        "gfp_mean_intensity": [-5.0, 0.0, 10.0, 20.0],
        # A NaN in an unrelated gated channel used to remove the row.
        "mcherry_mean_intensity": [1.0, 2.0, np.nan, 4.0],
        "area_px": [100.0] * 4,
        "Included": [1] * 4,
    })


def test_default_gates_do_not_remove_cells():
    """With every gate at its documented "off" value, the filter was still
    applied as ``isfinite(v) & (v > 0)`` — so a cell whose background-subtracted
    intensity was zero or negative, or which carried a NaN in any *other* gated
    channel, was dropped by a choice the user never made."""
    pts = aggregate_with_threshold_df(
        _gating_frame(), threshold=-1.0, use_sem=False,
        val_col="gfp_mean_intensity",
        fluor_gates={"gfp": 0.0, "mcherry": 0.0},
    )
    assert pts, "an all-default gate set removed every row"
    assert int(pts[0][5]) == 4


def test_a_real_gate_still_filters():
    pts = aggregate_with_threshold_df(
        _gating_frame(), threshold=-1.0, use_sem=False,
        val_col="gfp_mean_intensity",
        fluor_gates={"gfp": 15.0, "mcherry": 0.0},
    )
    assert int(pts[0][5]) == 1

# ── 6. the ratio cache signature actually signs the definition ───────────────

def _ratio_ctx(metric):
    import types

    from well_viewer.domain.ratio_models import build_ratio_index
    channel = types.SimpleNamespace(
        active_val_col=metric.key(), ratio_index=build_ratio_index([metric]),
    )
    return types.SimpleNamespace(state=types.SimpleNamespace(channel=channel))


def test_two_ratios_with_one_name_have_different_cache_signatures():
    """The stats cache keys fold in ``active_val_col``, which for a ratio is the
    bare string ``ratio:<name>`` — so editing a ratio in place left every cached
    replicate-set statistic valid by key and wrong by value.

    The first attempt at this signature looked the metric up by its bare name
    (the index is keyed by the full ``ratio:<name>``) and read three attributes
    that do not exist on ``RatioMetric``, so it returned a constant. A
    signature that signs nothing is worse than none: it reads as covered.
    """
    from well_viewer.domain.ratio_models import RatioMetric
    from well_viewer.domain.stats_compute import _ratio_cache_sig

    a = RatioMetric("R", "gfp", "mean_intensity", "mch", "mean_intensity", 0.0)
    b = RatioMetric("R", "rfp", "total_intensity", "dapi", "mean_intensity", 1e-6)
    sig_a = _ratio_cache_sig(_ratio_ctx(a))
    sig_b = _ratio_cache_sig(_ratio_ctx(b))
    assert sig_a != sig_b
    # And it names the parts, rather than repeating what the key already has.
    assert "gfp" in sig_a and "mch" in sig_a


def test_a_plain_channel_has_no_ratio_signature():
    """A non-ratio session's keys must stay byte-identical."""
    import types

    from well_viewer.domain.stats_compute import _ratio_cache_sig
    channel = types.SimpleNamespace(active_val_col="gfp_mean_intensity",
                                    ratio_index={})
    ctx = types.SimpleNamespace(state=types.SimpleNamespace(channel=channel))
    assert _ratio_cache_sig(ctx) is None


# ── 7. normalization rescales fields; it does not invent them ────────────────

_WELL_PT_9 = [
    (0.0, 100.0, 5.0, 0.5, 10, 20, 0.0, 0.0, 0.0),
    (24.0, 200.0, 6.0, 0.6, 12, 20, 0.0, 0.0, 0.0),
]


def test_a_single_well_point_gains_no_events_spread():
    """Index 9 is the *cross-well* events spread, and a plain per-well point is
    a 9-tuple that has no such field because one well has no replicates.

    Naming index 9 unconditionally made the padding helper invent the slot and
    fill it with a propagated error, so a single well normalised against a
    control came back carrying an events error band — and the Line tab, which
    now draws that field, painted a ribbon around it.
    """
    out = normalize_pts(
        _WELL_PT_9,
        control_stats={0.0: (50.0, 5.0), 24.0: (50.0, 5.0)},
        control_n_stats={0.0: (5.0, 1.0), 24.0: (5.0, 1.0)},
        normalize_frac_n=True,
    )
    assert all(len(pt) == 9 for pt in out)


def test_a_replicate_point_keeps_its_events_spread_normalized():
    """The 11-tuple a replicate set builds does carry index 9, and it must be
    rescaled with its value rather than left in raw units."""
    pts = [(0.0, 100.0, 5.0, 0.5, 10.0, 20, 0.0, 0.0, 0.0, 2.0, 20)]
    out = normalize_pts(
        pts,
        control_n_stats={0.0: (5.0, 0.0)},
        normalize_frac_n=True,
    )
    assert out[0][4] == pytest.approx(2.0)      # 10 / 5
    assert out[0][9] == pytest.approx(0.4)      # 2 / 5, not left at 2.0


def test_an_undefined_spread_survives_on_every_field():
    """The fraction and events fields go through ``_scale_field``, which folded
    NaN to 0.0 while the mean field's ``_scale_pt`` preserved it — two
    conventions for "we could not compute this", in panels of one figure."""
    nan = float("nan")
    pts = [(0.0, 100.0, nan, 0.5, 10.0, 20, nan, 0.0, 0.0, nan, 20)]
    out = normalize_pts(
        pts,
        control_stats={0.0: (50.0, 0.0)},
        control_frac_stats={0.0: (0.25, 0.0)},
        control_n_stats={0.0: (5.0, 0.0)},
        normalize_frac_n=True,
    )
    assert math.isnan(out[0][2]) and math.isnan(out[0][6]) and math.isnan(out[0][9])


# ── 8. the bar path resolves the same baseline as the line path ──────────────

_BASE_PTS = [
    (0.0, 100.0, 5.0, 0.0, 0.0, 200, 0.0, 0.0, 0.0, 0.0, 0),
    (24.0, 300.0, 9.0, 0.40, 80.0, 200, 0.0, 0.0, 0.0, 0.0, 0),
]


def test_bar_baselines_agree_across_panels_in_subtract_mode():
    """The line path's ``_field_baseline`` was taught the operation; the bar
    path's five ``baseline_*_from_points*`` finders were not, so the reported
    symptom — the mean panel normalised against t=0 and the fraction and events
    panels against t=24, under one axis label — survived untouched there."""
    from well_viewer.domain import fold_change as fc

    mean_tp = fc.baseline_from_points_resolved(
        _BASE_PTS, baseline_tp=0.0, op="subtract")[0]
    frac_tp = fc.baseline_frac_from_points_resolved(
        _BASE_PTS, baseline_tp=0.0, op="subtract")[0]
    n_tp = fc.baseline_n_from_points_resolved(
        _BASE_PTS, baseline_tp=0.0, op="subtract")[0]
    assert mean_tp == frac_tp == n_tp == 0.0


def test_bar_baselines_still_refuse_a_zero_divisor():
    from well_viewer.domain import fold_change as fc

    assert fc.baseline_from_points_resolved(
        _BASE_PTS, baseline_tp=0.0, op="ratio")[0] == 0.0
    assert fc.baseline_frac_from_points_resolved(
        _BASE_PTS, baseline_tp=0.0, op="ratio")[0] == 24.0


# ── 9. every gate filter answers the "is this gate on?" question the same way ─

def _gate_filter_offenders(root: pathlib.Path) -> list[str]:
    """Loops over ``fluor_gates`` that *compare against* the gate value without
    first asking whether the gate is on.

    Written against the loop's shape rather than one spelling of it. The
    previous version matched the literal ``"> gate"``, which the repo's own
    sites already defeat — two of them bind the value as ``gate_threshold`` —
    and which any of ``>=``, a reversed comparison, or a differently-named
    binding would slip past. Here the loop's own target names are read out of
    the AST and any comparison against either of them counts.
    """
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.For):
                continue
            iter_src = ast.unparse(node.iter)
            if "fluor_gates" not in iter_src:
                continue
            bound = {n.id for n in ast.walk(node.target)
                     if isinstance(n, ast.Name)}
            compares_gate = any(
                isinstance(cmp, ast.Compare)
                and any(isinstance(side, ast.Name) and side.id in bound
                        for side in [cmp.left, *cmp.comparators])
                for cmp in ast.walk(node)
            )
            if compares_gate and "active_fluor_gates" not in iter_src:
                offenders.append(f"{path}:{node.lineno}")
    return offenders


def test_no_filter_applies_a_gate_without_asking_whether_it_is_on():
    """Six sites iterate ``fluor_gates`` and cut on it. Converting some of them
    left the rest describing a different population from the ones beside them —
    a half-swept predicate is worse than an unswept one, because one function
    then holds two definitions of the same word and neither looks wrong."""
    repo = pathlib.Path(__file__).resolve().parent.parent
    offenders = _gate_filter_offenders(repo / "well_viewer")
    assert not offenders, (
        "these filter on a raw gate value, so a gate of 0.0 (which means off) "
        "still excludes cells:\n" + "\n".join(offenders)
    )


def test_the_gate_guard_bites(tmp_path):
    """The guard is only worth its runtime if it fires on the shapes a future
    author would actually write — not just on the one that was there when it
    was written."""
    violations = {
        "plain.py": "for channel, gate in fluor_gates.items():\n"
                    "    mask &= v > gate\n",
        "renamed.py": "for channel, floor in fluor_gates.items():\n"
                      "    mask &= v > floor\n",
        "gte.py": "for channel, gate in fluor_gates.items():\n"
                  "    mask &= v >= gate\n",
        "reversed.py": "for channel, gate in fluor_gates.items():\n"
                       "    mask &= gate < v\n",
        "other_name.py": "for ch, gate_threshold in fluor_gates.items():\n"
                         "    mask &= v > gate_threshold\n",
    }
    for name, src in violations.items():
        d = tmp_path / name.removesuffix(".py")
        d.mkdir()
        (d / name).write_text(src)
        assert _gate_filter_offenders(d), f"guard missed {name}"

    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "ok.py").write_text(
        "for channel, gate in active_fluor_gates(fluor_gates).items():\n"
        "    mask &= v > gate\n"
    )
    # A loop that only *reads* the mapping is not a filter and must not fire.
    (clean / "reader.py").write_text(
        "for channel, gate in fluor_gates.items():\n"
        "    rows.append((channel, gate))\n"
    )
    assert not _gate_filter_offenders(clean)


# ── 10. the collector passes the operation, not just the finder accepting it ──

def test_the_bar_collector_passes_the_t0_operation():
    """Teaching the five finders an ``op`` parameter does nothing if the caller
    never passes it — and nothing in the suite noticed when all six arguments
    were stripped. This asserts the outbound call, per CLAUDE.md §7.4 #1."""
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "well_viewer" / "features" / "barplot" / "collect.py").read_text()
    tree = ast.parse(src)
    finders = {
        "baseline_from_points_resolved",
        "baseline_frac_from_points_resolved",
        "baseline_n_from_points_resolved",
    }
    seen: set[str] = set()
    missing: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name not in finders:
            continue
        seen.add(name)
        if not any(kw.arg == "op" for kw in node.keywords):
            missing.append(f"{name} at line {node.lineno}")
    assert seen == finders, f"expected all three finders to be called: {seen}"
    assert not missing, (
        "the bar collector resolves a baseline without saying which operation "
        "will use it, so a zero baseline is skipped even under subtraction:\n"
        + "\n".join(missing)
    )
