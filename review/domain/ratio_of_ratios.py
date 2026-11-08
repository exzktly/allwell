"""The second stage of fold change: each bar relative to its baseline bar.

Stage 1 is the per-sample control — ``Mut·Drug / WT·Drug``. The question this
answers is the next one: is that ratio different under the drug from under the
vehicle? So each bar is divided by its **baseline** bar, the bar that agrees
with it on every "within each" variable but holds the baseline value
(``Treatment = Vehicle``)::

    R = FC(Mut·Drug) / FC(Mut·Veh) = (Mut·Drug / WT·Drug) / (Mut·Veh / WT·Veh)

With the subtract operator it is the difference of differences instead. The
rule is the Bar tab's *Then relative to* setting (``BarPlotState``); which bar
is whose baseline comes from ``domain/variable_pairing.match_by_variable``.

**The interaction test is a contrast over the bars' wells**, not over the bars:
``{X: +1, control(X): −1, B(X): −1, control(B(X)): +1}``, run by
``stats_tests.contrast_t_test`` on the replicate values. Cells are identified
by their **resolved wells**, never by key or reference — a well key ``A01`` and
a well reference to ``A01`` are the same cell and must cancel — so a bar whose
contrast collapses (a WT bar is its own control; the vehicle bar is its own
baseline) is recognised as a reference condition rather than tested against
itself. A contrast over two cells is not an interaction and is labelled for
what it is (see :func:`contrast_label`).

Qt-free, ctx-free and free of ``features/`` imports.
"""

from __future__ import annotations

import math

from review.domain.fold_change import scale_bar_value

#: Why a bar's stage-2 value is what it is. A vocabulary of its own rather than
#: rows added to ``ControlOutcome``, which describes the stage-1 control and is
#: a closed set with its own roster guard.
OK = "ok"
#: The bar is a reference condition: its R is 1 (0 for subtract) by
#: construction, because it is its own baseline or its own control. Hidden
#: from the figure and drawn as the reference line instead.
REFERENCE = "reference"
NO_BASELINE = "no_baseline"
AMBIGUOUS = "ambiguous"
NOT_ANNOTATED = "not_annotated"
WELLS_DISAGREE = "wells_disagree"
#: The baseline bar resolved but has no value at this timepoint.
BASELINE_NO_VALUE = "baseline_no_value"
#: The bar's own stage-1 value is missing, so there is nothing to divide.
NO_VALUE = "no_value"

OUTCOMES = (OK, REFERENCE, NO_BASELINE, AMBIGUOUS, NOT_ANNOTATED,
            WELLS_DISAGREE, BASELINE_NO_VALUE, NO_VALUE)

#: ``variable_pairing`` reasons → this vocabulary.
FROM_PAIRING = {"no_match": NO_BASELINE, "ambiguous": AMBIGUOUS,
                "not_annotated": NOT_ANNOTATED,
                "wells_disagree": WELLS_DISAGREE}


def combine(fc_x: float, s_x: float, fc_b: float, s_b: float,
            op: str) -> tuple[float, float]:
    """``(R, spread)`` — *fc_x* over (or minus) *fc_b*, errors propagated.

    Through ``fold_change.scale_bar_value``, so the ratio's relative errors and
    the difference's absolute errors add in quadrature exactly as stage 1's do.
    One departure, deliberate: a **baseline** spread that is undefined makes
    the result's spread undefined. Stage 1 treats an undefined control spread
    as exact; doing that here would hand a single-well baseline's ratio an
    error bar that is only the numerator's, which reads as more certain than
    anything measured.
    """
    if fc_b is None or not math.isfinite(fc_b):
        return float("nan"), float("nan")
    value, spread = scale_bar_value(
        float(fc_x), float(s_x) if s_x is not None else float("nan"),
        control_mean=float(fc_b), control_spread=float(s_b) if s_b is not None and math.isfinite(s_b) else 0.0,
        op_ctrl=op if op in ("ratio", "subtract") else "ratio")
    if s_b is None or not math.isfinite(s_b):
        spread = float("nan")
    return value, spread


def interaction_weights(x_wells, control_wells, baseline_wells,
                        baseline_control_wells) -> dict:
    """``{frozenset(wells): weight}`` for the bar's contrast, zeros dropped.

    ``control_wells`` / ``baseline_control_wells`` may be ``None`` when the bar
    has no stage-1 control; the contrast is then just ``X − B(X)``.
    """
    weights: dict = {}
    for wells, w in ((x_wells, 1.0), (control_wells, -1.0),
                     (baseline_wells, -1.0), (baseline_control_wells, 1.0)):
        if wells is None:
            continue
        cell = frozenset(wells)
        if not cell:
            continue
        weights[cell] = weights.get(cell, 0.0) + w
    return {cell: w for cell, w in weights.items() if w != 0.0}


def is_reference(weights: dict) -> bool:
    """A bar whose contrast cancels entirely is a reference condition."""
    return not weights


def contrast_label(weights: dict, *, log: bool) -> str:
    """What the test on this bar actually is, in words for the caption.

    Four cells with ±1 weights are the interaction. Two cells are a plain
    comparison of the bar with its baseline — which is what the contrast
    reduces to when stage 1 used one control for every bar (it cancels) — and
    calling that an interaction would claim a question the data was not
    arranged to answer.
    """
    scale = "log scale" if log else "raw scale"
    if len(weights) == 4:
        return f"interaction, {scale}"
    if len(weights) == 2:
        return f"bar vs its baseline only (not an interaction), {scale}"
    return f"contrast over {len(weights)} conditions, {scale}"


def suffix(variable_name: str, value_label: str, op: str) -> str:
    """`` ÷ (Treatment = Vehicle)`` — what the y axis and title append."""
    sign = "−" if op == "subtract" else "÷"
    return f" {sign} ({variable_name} = {value_label})"
