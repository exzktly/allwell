"""Pure domain compute for Cell Gating (audit A3).

Extracted from ``cell_gating_tab_view.py`` (a 2400-line "view" that had numpy /
pandas domain logic embedded in it). Everything here is Qt-free and app-free:
DataFrames / numpy arrays in, results out — so it is unit-testable without a GUI
fixture. The view keeps the widget wiring and the QThread shells; those now call
these functions.

Two clusters live here:

* ``apply_fluor_gating`` — the per-well gating math that ``GatingWorker`` runs
  off the GUI thread (writes the ``Included`` / ``Gate_Pass`` / ``Gate_On``
  columns for one well's DataFrame).
* the ThreshFracOn optimizer's numeric scan core
  (``fraction_on_for_threshold`` / ``frac_curve`` / ``scan_fc_fractions`` and the
  ``ThreshFracOptimizationResult`` it produces).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from well_viewer.core.lazy import lazy as _lazy

# Lazy so this pure-compute module imports without eagerly pulling the scatter
# feature (keeps both deletable and avoids a features import cycle); resolves on
# first call. Mirrors the same binding cell_gating_tab_view uses — a module-level
# _lazy assignment, not a function-local feature import.
compute_gate_inclusion = _lazy("well_viewer.features.scatter.gating", "compute_gate_inclusion")


# ── Per-well fluorescence gating (GatingWorker core) ─────────────────────────
def apply_fluor_gating(
    df,
    *,
    fluor_gates: dict,
    use_scatter: bool,
    gating_gates: list,
    use_thresh: bool,
    thresh_gates: list,
    ratios: dict | None,
) -> int:
    """Write the gating columns for one well's DataFrame; return the included count.

    Mutates *df* in place (it is the app's cached row frame):

    * ``Included`` — the cell-inclusion (gating) layer. In "use scatter gates"
      mode inclusion is all-pass and gate membership is carried in ``Gate_Pass``;
      otherwise inclusion is the per-channel fluorescence floor (``FluorGating``).
    * ``Gate_Pass`` — scatter-gate membership (neutral ``1`` in intensity mode).
    * ``Gate_On`` — the thresholding layer's fraction-on population, from its own
      gate selection (neutral ``1`` when thresholding isn't gate-driven).

    Returns the number of included cells (for the run tally). An empty/None frame
    returns 0 without mutation.
    """
    if df is None or df.empty:
        return 0

    if use_scatter:
        # Scatter-gate inclusion REPLACES the per-channel FluorGating: Included is
        # all-pass for this layer and the gate membership lives in Gate_Pass, so
        # the per-cell scatter keeps the full cloud while every other view filters
        # via df_included_mask. Mode on + no selected gates ⇒ no restriction.
        if gating_gates:
            gate_mask = pd.Series(
                compute_gate_inclusion(df, gating_gates, ratios),
                index=df.index,
            )
        else:
            gate_mask = pd.Series(True, index=df.index)
        df["Included"] = 1
        df["Gate_Pass"] = gate_mask.astype(int)
        well_included = int(gate_mask.sum())
    else:
        mask = pd.Series(True, index=df.index)
        for channel, gate_threshold in fluor_gates.items():
            col = f"{channel}_mean_intensity"
            if col not in df.columns:
                mask = pd.Series(False, index=df.index)
                break
            v = pd.to_numeric(df[col], errors="coerce")
            mask &= v.notna() & (v > gate_threshold)
        df["Included"] = mask.astype(int)
        # Neutralise any Gate_Pass left over from a previous scatter-gate pass so
        # intensity gating isn't still filtered.
        df["Gate_Pass"] = 1
        well_included = int(mask.sum())

    # Thresholding layer → Gate_On (fraction-on by gate), using its OWN gate
    # selection. Only read by the aggregation when thresholding is active (see
    # stats_compute._thresh_on_col); neutral (1) otherwise so a stale membership
    # can't leak in.
    if use_thresh and thresh_gates:
        df["Gate_On"] = compute_gate_inclusion(df, thresh_gates, ratios).astype(int)
    else:
        df["Gate_On"] = 1
    return well_included


# ── ThreshFracOn optimizer numeric core ──────────────────────────────────────
@dataclass(frozen=True)
class ThreshFracOptimizationResult:
    """Best threshold scan result for a two-well fraction-on comparison."""

    threshold: float
    frac_a: float
    frac_b: float
    difference: float
    n_a: int
    n_b: int


def fraction_on_for_threshold(values: np.ndarray, threshold: float) -> float:
    if values.size == 0:
        return float("nan")
    return float(np.count_nonzero(values > threshold) / values.size)


def frac_curve(values: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    """Fraction-on of *values* at each scanned threshold."""
    return np.array(
        [fraction_on_for_threshold(values, float(t)) for t in thresholds],
        dtype=float,
    )


def scan_fc_fractions(
    thresholds: np.ndarray,
    values_a: np.ndarray,
    values_b: np.ndarray,
    *,
    ctrl_values: "np.ndarray | None" = None,
    base_values_a: "np.ndarray | None" = None,
    base_values_b: "np.ndarray | None" = None,
    op_ctrl: str = "ratio",
    op_t0: str = "ratio",
    order: str = "ctrl_first",
):
    """Per-threshold (fa, fb, relative-gap) curves, optionally fold-changed.

    Each well's raw fraction-on is computed at every scanned threshold, then —
    when control / baseline value arrays are supplied — adjusted at the SAME
    threshold via :func:`fold_change.scale_bar_value` (so numerator and the
    fold-change denominators stay self-consistent). ``ctrl_values`` is the pooled
    control population; ``base_values_*`` are each well's baseline-timepoint
    population. With all three ``None`` the result is the raw fraction-on scan
    (identical to the pre-fold-change behaviour). Returns ``(fa, fb, diffs)``
    where ``diffs`` is the relative gap ``|fa-fb| / max(|fa|, |fb|)`` (non-finite
    / zero-magnitude coerced to ``0.0`` so ``argmax`` is meaningful).

    The denominator uses the **magnitude** ``max(|fa|, |fb|)`` rather than
    ``max(fa, fb)``: under the *subtract* fold-change operator the adjusted
    fractions are differences that can go negative (a well below its control /
    baseline), and a signed ``max(fa, fb)`` denominator would zero the gap across
    the whole negative region and spike at the sign crossing — inverting the
    curve and the chosen threshold. Magnitude is sign-safe and, for the
    non-negative raw / ratio cases, identical to the old ``max(fa, fb)``.
    """
    from well_viewer.domain.fold_change import scale_bar_value

    fa = frac_curve(values_a, thresholds)
    fb = frac_curve(values_b, thresholds)

    fc_on = ctrl_values is not None or base_values_a is not None or base_values_b is not None
    if fc_on:
        fc = ctrl_values is not None and ctrl_values.size > 0
        fctrl = frac_curve(ctrl_values, thresholds) if fc else None
        fbase_a = frac_curve(base_values_a, thresholds) if base_values_a is not None else None
        fbase_b = frac_curve(base_values_b, thresholds) if base_values_b is not None else None
        for i in range(len(thresholds)):
            fa[i], _ = scale_bar_value(
                fa[i], 0.0,
                control_mean=(fctrl[i] if fctrl is not None else None),
                t0_mean=(fbase_a[i] if fbase_a is not None else None),
                op_ctrl=op_ctrl, op_t0=op_t0, order=order,
            )
            fb[i], _ = scale_bar_value(
                fb[i], 0.0,
                control_mean=(fctrl[i] if fctrl is not None else None),
                t0_mean=(fbase_b[i] if fbase_b is not None else None),
                op_ctrl=op_ctrl, op_t0=op_t0, order=order,
            )

    abs_gap = np.abs(fa - fb)
    # Sign-safe denominator: magnitude, not signed max — see docstring. For
    # non-negative raw / ratio fractions this equals the old max(fa, fb).
    larger = np.maximum(np.abs(fa), np.abs(fb))
    with np.errstate(invalid="ignore", divide="ignore"):
        diffs = np.divide(abs_gap, larger, out=np.zeros_like(abs_gap), where=larger > 0)
    diffs = np.where(np.isfinite(diffs), diffs, 0.0)
    return fa, fb, diffs
