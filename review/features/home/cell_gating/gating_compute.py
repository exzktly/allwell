"""Pure domain compute for the Cell Gating package.

Everything here is Qt-free and app-free: DataFrames / numpy arrays in, results
out — so it is unit-testable without a GUI fixture. The package's other modules
(the tab builder, the controller and its QThread shells, the renderer) all sit
above this one and call into it; nothing here imports them.

Two clusters live here:

* ``apply_fluor_gating`` — the per-well gating math that ``GatingWorker`` runs
  off the GUI thread (writes the ``Included`` / ``Gate_Pass`` / ``Gate_On``
  columns for one well's DataFrame).
* the ThreshFracOn optimizer's viewer-side layer — the fold-change-aware
  ``scan_fc_fractions`` and the DataFrame/ratio plumbing that feeds it. Its
  numeric primitives (``fraction_on_for_threshold`` / ``frac_curve`` / the
  relative-gap objective / ``ThreshFracOptimizationResult``) live in
  ``pipeline.threshold_optimize``, shared with the pipeline's end-of-run
  positive/negative-control pass, and are re-exported here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from util.fov import sort_key as fov_sort_key
from pipeline.threshold_optimize import (  # noqa: F401 (re-export)
    DEFAULT_MAX_CANDIDATES,
    DEFAULT_SPECIFICITY_WEIGHT,
    MAX_SWEEP_STEPS,
    ThreshFracOptimizationResult,
    best_from_curves as _best_from_curves,
    floor_unsupported as _floor_unsupported,
    frac_curve,
    fraction_on_for_threshold,
    optimize_between_populations as _optimize_between_populations,
    relative_gap as _relative_gap,
    sweep_thresholds as _sweep_thresholds,
)
from review.data.data_loading import resolve_value_series
from domain.text_columns import fov_text, timepoint_text
from review.core.lazy import lazy as _lazy
from review.domain.gating_state import active_fluor_gates

# Lazy so this pure-compute module imports without eagerly pulling the scatter
# feature (keeps both deletable and avoids a features import cycle); resolves on
# first call — a module-level _lazy assignment, not a function-local feature
# import.
compute_gate_inclusion = _lazy("review.features.scatter.gating", "compute_gate_inclusion")


# ── Inclusion counts (the Cell Gating tab's readout) ─────────────────────────

def inclusion_counts(frames) -> tuple[int, int, int]:
    """``(included, total, qc_failed)`` across *frames*.

    ``included`` is the **composed** inclusion every plot, statistic and export
    consumes — ``df_included_mask``'s AND of manual curation, the QC debris
    gate, the crop region, and the cell-gating / scatter-gate layer. It is
    deliberately not ``GatingWorker``'s own tally: that counts the gating layer
    alone, so pairing it with a QC number produces a line whose figures do not
    reconcile (most QC failures sit *inside* the gating layer's "kept" count).

    ``qc_failed`` is a diagnostic alongside, not a disjoint bucket — a cell can
    fail QC and be excluded by gating at once. Frames missing a verdict column
    are treated as all-pass there, matching ``df_included_mask``.

    Pure: DataFrames in, three ints out. ``None`` / empty frames are skipped.
    """
    from review.data.data_loading import df_included_mask

    included = total = qc_failed = 0
    for df in frames or ():
        if df is None or getattr(df, "empty", True):
            continue
        total += len(df)
        included += int(df_included_mask(df).sum())
        if "QC_Pass" in df.columns:
            qc = pd.to_numeric(df["QC_Pass"], errors="coerce").fillna(1)
            qc_failed += int((qc == 0).sum())
    return included, total, qc_failed


# The inclusion layers, in the order ``df_included_mask`` composes them, with
# the column each one owns. ``Included`` is listed first because it is the
# column the manual curation *and* the FluorGating pass both write — they cannot
# be separated from the frame, and the label says so rather than implying a
# split the data does not support.
_FUNNEL_LAYERS: tuple[tuple[str, str], ...] = (
    ("Curation + intensity gate", "Included"),
    ("QC debris gate", "QC_Pass"),
    ("Crop region", "Crop_Pass"),
    ("Scatter gates", "Gate_Pass"),
    ("Touching image border", "Border_Pass"),
)


def inclusion_funnel(frames) -> list[tuple[str, int | None, int]]:
    """Per-layer inclusion counts: ``[(label, removed, surviving), …]``.

    Each entry is that layer applied *on top of* the ones before it, in the
    order :func:`~review.data.data_loading.df_included_mask` composes them,
    so the last ``surviving`` equals the tab's Included figure. The first entry
    of the returned list is always the ``("Segmented cells", None, total)``
    starting population.

    ``removed`` is ``None`` — not ``0`` — for a layer whose column is absent
    from every frame. A layer that was never applied and a layer that removed
    nothing are different facts, and rendering both as "−0" is exactly the
    substitution that makes a partial result indistinguishable from a complete
    one. Callers render ``None`` as "not applied".

    Pure: DataFrames in, plain tuples out.
    """
    frames = [df for df in (frames or ())
              if df is not None and not getattr(df, "empty", True)]
    total = sum(len(df) for df in frames)
    stages: list[tuple[str, int | None, int]] = [("Segmented cells", None, total)]
    if not frames:
        return stages

    masks = {id(df): pd.Series(True, index=df.index) for df in frames}
    surviving = total
    for label, column in _FUNNEL_LAYERS:
        present = False
        after = 0
        for df in frames:
            mask = masks[id(df)]
            if column in df.columns:
                present = True
                # ``Included`` defaults to excluded when unparseable (matching
                # df_included_mask); the verdict columns default to pass.
                fill = 0 if column == "Included" else 1
                col = pd.to_numeric(df[column], errors="coerce").fillna(fill)
                mask = mask & col.eq(1)
                masks[id(df)] = mask
            after += int(mask.sum())
        stages.append((label, (surviving - after) if present else None, after))
        surviving = after
    return stages


def format_inclusion_funnel(stages) -> str:
    """One line per layer, for the tab's funnel readout. ``""`` when no data."""
    if not stages or stages[0][2] <= 0:
        return ""
    lines = [f"{stages[0][2]:,} segmented cells"]
    for label, removed, surviving in stages[1:]:
        if removed is None:
            lines.append(f"   {label}: not applied")
        else:
            lines.append(f"   {label}: −{removed:,}  →  {surviving:,}")
    return "\n".join(lines)


def format_inclusion_counts(
    included: int,
    total: int,
    qc_failed: int,
    *,
    pending: bool = False,
    failed: bool = False,
) -> str:
    """The Cell Gating tab's one-line readout, or ``""`` when no data is loaded.

    Blank rather than a row of zeros on an empty dataset — the same convention
    the QC row above it uses. ``pending`` marks the line while a gating pass is
    in flight (the numbers shown are the previous pass's, so they must not read
    as authoritative); ``failed`` marks numbers that describe settings which
    were never applied.
    """
    if total <= 0:
        return ""
    text = (f"Included for analysis: {included:,} / {total:,} cells"
            f"  ·  {qc_failed:,} failed QC")
    if pending:
        return f"{text}  ·  updating…"
    if failed:
        return f"{text}  ·  last gating pass failed"
    return text


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
        for channel, gate_threshold in active_fluor_gates(fluor_gates).items():
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


def gating_input_columns(ctx, label: str | None = None) -> set[str] | None:
    """The columns :func:`apply_gating_to_cache` reads under the recorded pass (``narrow_rows``).

    The intensity floors read each gated channel's mean intensity; the scatter
    gates and the thresholding gates read their axes, ratios included. ``None``
    when an axis cannot be resolved to columns — an unknown ratio — so the read
    takes the whole frame.
    """
    from review.data.narrow_rows import value_inputs

    config = getattr(getattr(ctx.state, "cell_gating", None), "applied_gating", None)
    if not config:
        return set()
    ratios = config.get("ratios")
    wanted: set[str] = set()
    axes: list[str] = []
    if config.get("use_scatter"):
        axes += [g.get(k) for g in config.get("gating_gates") or [] for k in ("x_col", "y_col")]
    else:
        wanted |= {f"{ch}_mean_intensity" for ch in active_fluor_gates(config.get("fluor_gates") or {})}
    if config.get("use_thresh"):
        axes += [g.get(k) for g in config.get("thresh_gates") or [] for k in ("x_col", "y_col")]
    for axis in axes:
        cols = value_inputs(axis or "", ratios)
        if cols is None:
            return None
        wanted |= cols
    return wanted


def apply_gating_to_cache(ctx, label: str | None = None) -> None:
    """Re-apply the last gating pass to cached frame(s) — the gating projector.

    Gating used to be written onto the cached frames once, by ``GatingWorker``,
    and never again. That was sound while the row cache held every well for the
    whole session. It is bounded now (``review/data/row_cache.py``), so a well
    gated by the pass can be evicted and read back from disk — and without this
    it came back with the CSV's ``Included`` and no ``Gate_Pass`` / ``Gate_On``,
    so every plot counted its ungated cells with nothing to say so.

    Registered as a cache projector *before* curation, matching the pass: the
    worker writes these columns and then re-applies the user's per-cell
    overrides on top, and the projector registry runs curation next.

    Applies ``ctx.state.cell_gating.applied_gating`` — the configuration the
    last pass applied — rather than the live settings, so a reloaded well agrees
    with the wells the pass wrote. ``None`` (no pass on this dataset) writes
    nothing, as no pass did. No ``rows_version`` bump: on the load path the
    frame is new and has no memoized aggregate (``cache_projectors`` says why).
    """
    if label is None:
        # The walk over every held frame is the gating pass's own job, and it
        # has already written them: ``recompute_qc_all`` re-runs every registered
        # projector with no label on each QC edit, and re-gating every held well
        # there would cost a full gating pass per keystroke to rewrite the same
        # values. Only a frame read from disk lacks the columns.
        return
    config = getattr(getattr(ctx.state, "cell_gating", None), "applied_gating", None)
    if not config:
        return
    # Cache-only: a projector writes onto the frame being loaded, which
    # ``load_and_project`` has stored (and pinned) before calling it.
    frame = ctx.state.repo.rows_or_none(label)
    if frame is None or frame.empty:
        return
    apply_fluor_gating(frame, **config)


# ── ThreshFracOn optimizer numeric core ──────────────────────────────────────
#
# The sweep primitives and the relative-gap objective live in
# ``pipeline.threshold_optimize`` because the pipeline needs the same answer for
# its end-of-run positive/negative-control pass, and may not import
# ``review`` (ARCHITECTURE §6.2). They are re-exported here under their
# original names so this module's callers and tests are unaffected. What stays
# here is everything that depends on the viewer: the fold-change layer below and
# the DataFrame/ratio plumbing further down.
def scan_fc_fractions(
    thresholds: np.ndarray,
    values_a: np.ndarray,
    values_b: np.ndarray,
    *,
    ctrl_values: np.ndarray | None = None,
    base_values_a: np.ndarray | None = None,
    base_values_b: np.ndarray | None = None,
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
    from review.domain.fold_change import scale_bar_value

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

    # The fold-changed branch is unsmoothed, and not by omission: its fractions
    # have been through ``scale_bar_value`` and are no longer ``k / n`` — they
    # exceed 1 and go negative — so there is no ``k`` to add one to.
    #
    # The ``not fc_on`` branch below is smoothed, and is reached only from tests
    # now: ``_optimize_thresh_frac_on_between_wells`` returns to
    # ``optimize_between_populations`` before calling this when no fold change is
    # set, and ``opt_curve_view`` re-derives that path's own curve. It is kept
    # because the function is a public scan over two populations and answering
    # "what is the relative gap here" differently depending on whether a caller
    # happened to pass a control array would be a trap; the smoothing is what
    # makes the raw answer match what the pipeline used to compute.
    if not fc_on:
        return fa, fb, _relative_gap(fa, fb,
                                     n_a=int(values_a.size), n_b=int(values_b.size))
    return fa, fb, _relative_gap(fa, fb)


# ── Cell-gating row/timepoint selection + the two-well optimiser ──────────────
#
# Pure DataFrame/array work with no Qt and no ``ctx``, which is what this module
# is for: these depend on nothing but their arguments, so they are exercised
# directly rather than through a full-app fixture.


def _cell_gating_valid_mask(frame_df):
    """Boolean mask: rows that pass crop AND QC (absent columns ⇒ all-pass).

    The per-channel fluorescence gate (``Included``) is deliberately NOT applied:
    the CDFs and the threshold optimizer are what the user reads to SET that
    gate, so filtering by it would be circular — raising the threshold would
    shrink the very curve being read, and the optimizer would only see cells
    that already pass its own (asynchronously-rewritten) cut. Crop + QC are
    independent of the fluorescence gate, so they show the valid population
    without that feedback loop.
    """
    mask = pd.Series(True, index=frame_df.index)
    for col in ("QC_Pass", "Crop_Pass"):
        if col in frame_df.columns:
            vals = pd.to_numeric(frame_df[col], errors="coerce").fillna(1)
            mask = mask & vals.eq(1)
    return mask


def _cell_gating_valid_rows(frame_df):
    """Rows of ``frame_df`` selected by :func:`_cell_gating_valid_mask`."""
    return frame_df[_cell_gating_valid_mask(frame_df)]


def _gate_geom_sig(gates):
    return tuple(
        (g.get("x_col"), g.get("y_col"),
         round(float(g.get("x0", 0.0)), 6), round(float(g.get("y0", 0.0)), 6),
         round(float(g.get("x1", 0.0)), 6), round(float(g.get("y1", 0.0)), 6))
        for g in gates
    )


def _timepoint_label(timepoint) -> str:
    """Human-readable label for a ``(kind, value)`` timepoint token."""
    kind, value = timepoint
    if kind == "hours":
        return f"{value:g} h"
    return str(value)


def _distinct_timepoints(df) -> list[tuple[str, object]]:
    """Ordered distinct ``(kind, value)`` timepoint tokens present in ``df``.

    Prefers the numeric ``timepoint_hours`` column; falls back to the
    ``timepoint`` string column. Returns ``[]`` when neither is available.
    """
    if df is None or getattr(df, "empty", True):
        return []
    if "timepoint_hours" in df.columns:
        h = pd.to_numeric(df["timepoint_hours"], errors="coerce").dropna().unique()
        if len(h):
            return [("hours", float(v)) for v in sorted(float(x) for x in h)]
    if "timepoint" in df.columns:
        s = timepoint_text(df)
        vals = [v for v in s.unique() if v != ""]

        def _key(v: str):
            try:
                return (0, float(v), "")
            except ValueError:
                return (1, 0.0, v.lower())

        return [("str", v) for v in sorted(vals, key=_key)]
    return []


def _earliest_timepoint(df) -> tuple | None:
    """The earliest ``(kind, value)`` timepoint token present in *df* (or None)."""
    tps = _distinct_timepoints(df)
    if not tps:
        return None
    return sorted(tps, key=lambda t: (0, t[1]) if t[0] == "hours" else (1, str(t[1])))[0]


def _filter_rows_by_timepoint(df, timepoint):
    """Rows of ``df`` at ``timepoint`` (a ``(kind, value)`` token).

    ``None`` ⇒ all rows. Unknown / missing timepoint columns ⇒ all rows (so the
    optimizer degrades to its previous all-timepoints behaviour).
    """
    if timepoint is None or df is None or df.empty:
        return df
    kind, value = timepoint
    if kind == "hours" and "timepoint_hours" in df.columns:
        col = pd.to_numeric(df["timepoint_hours"], errors="coerce")
        return df.loc[col == value]
    if "timepoint" in df.columns:
        col = timepoint_text(df)
        sel = col == str(value)
        if not sel.any():
            num = pd.to_numeric(col, errors="coerce")
            try:
                sel = num == float(value)
            except (TypeError, ValueError):
                pass
        return df.loc[sel]
    return df


def _valid_values_for_channel(df, channel_key: str, ratios=None,
                              timepoint=None) -> np.ndarray:
    """Finite per-cell values for ``channel_key`` over the QC+Crop-valid
    population (at ``timepoint`` if given). Mirrors the CDF population — the
    fluorescence ``Included`` gate is deliberately not applied so the optimizer
    isn't filtered by the very threshold it's helping pick (see
    :func:`_cell_gating_valid_mask`)."""
    if df is None or df.empty:
        return np.array([], dtype=float)
    df = _filter_rows_by_timepoint(df, timepoint)
    if df is None or df.empty:
        return np.array([], dtype=float)
    val_col = channel_key if str(channel_key).startswith("ratio:") else f"{channel_key}_mean_intensity"
    values = resolve_value_series(df, val_col, ratios).to_numpy(dtype=float)
    mask = _cell_gating_valid_mask(df).to_numpy(copy=False) & np.isfinite(values)
    return values[mask]


def _optimize_thresh_frac_on_between_wells(
    df_a,
    df_b,
    *,
    channel_key: str,
    min_threshold: float,
    max_threshold: float,
    steps: int = DEFAULT_MAX_CANDIDATES,
    ratios=None,
    timepoint=None,
    weight: float = DEFAULT_SPECIFICITY_WEIGHT,
    ctrl_values=None,
    base_values_a=None,
    base_values_b=None,
    op_ctrl: str = "ratio",
    op_t0: str = "ratio",
    order: str = "ctrl_first",
) -> ThreshFracOptimizationResult:
    """The threshold best separating well A from well B, on one of two objectives.

    **Without fold change** — the default, and what the button does unless the
    user picks a control or a baseline — this is
    :func:`~pipeline.threshold_optimize.optimize_between_populations`, the same
    call the pipeline's end-of-run control-well pass makes, so the tab and the
    run cannot answer the same question differently. Well A is the *positive*
    arm: the score is a lower confidence bound on ``TPR(A) - weight * FPR(B)``,
    which is asymmetric on purpose, and two wells passed the wrong way round are
    refused rather than silently optimised backwards.

    **With fold change** the scanned fractions have been through
    ``scale_bar_value`` and are no longer counts over a total — they exceed 1 and
    go negative — so the confidence bound is undefined and the scan keeps the
    relative-gap objective ``|fa-fb| / max(|fa|, |fb|)``. That objective is the
    one with no N term, so :func:`~pipeline.threshold_optimize.floor_unsupported`
    excludes any candidate cut that leaves neither well enough cells to judge it
    on. A count floor is coarser than a confidence bound and is the most that can
    be done on a metric that is no longer a proportion; the result says which
    objective produced it.

    Measured, so that the floor is not credited with more than it does: on this
    branch the tail is mostly kept out of reach by ``best_from_curves``' tie-break
    rather than by the floor. The relative gap ties across a long run on real
    data and the first tied index wins, which is the lowest threshold attaining
    the maximum and so structurally not a tail cut. Several fixtures built to
    make the floor change the chosen threshold did not — it changed only which
    candidates were scored. The floor is the backstop for the case the tie-break
    does not cover, a tail that is the unique maximum.

    ``timepoint`` is an optional ``(kind, value)`` token (see
    :func:`_distinct_timepoints`); when given, only rows at that timepoint
    contribute to each well's fraction-on. ``steps`` caps the candidate grid — it
    is a ceiling on resolution, not the resolution itself, which comes from the
    data.
    """
    values_a = _valid_values_for_channel(df_a, channel_key, ratios, timepoint)
    values_b = _valid_values_for_channel(df_b, channel_key, ratios, timepoint)
    if values_a.size == 0:
        raise ValueError("Well A has no valid finite values for the selected channel.")
    if values_b.size == 0:
        raise ValueError("Well B has no valid finite values for the selected channel.")

    fold_change_on = (ctrl_values is not None or base_values_a is not None
                      or base_values_b is not None)
    if not fold_change_on:
        return _optimize_between_populations(
            values_a, values_b,
            min_threshold=min_threshold, max_threshold=max_threshold,
            max_candidates=int(steps), weight=weight,
        )

    # ``steps`` is a CEILING on the candidate count, and this branch's grid is a
    # linspace with its own hard cap, so the ceiling is clamped rather than
    # rejected. Without the clamp the field's own default (DEFAULT_MAX_CANDIDATES,
    # 20001) exceeded MAX_SWEEP_STEPS and every fold-change optimise raised
    # "steps must be 10,001 or fewer" — a limit the user never chose, named after
    # a field the window no longer has. Every fold-change test passed an explicit
    # small ``steps``, so none of them saw it.
    thresholds = _sweep_thresholds(
        min_threshold, max_threshold, min(int(steps), MAX_SWEEP_STEPS))
    fa, fb, diffs = scan_fc_fractions(
        thresholds, values_a, values_b,
        ctrl_values=ctrl_values,
        base_values_a=base_values_a,
        base_values_b=base_values_b,
        op_ctrl=op_ctrl, op_t0=op_t0, order=order,
    )
    scores, floor = _floor_unsupported(diffs, values_a, values_b, thresholds)
    if not np.isfinite(scores).any() or float(np.nanmax(scores)) <= 0.0:
        raise ValueError(
            "no scanned threshold separates the two wells' fold-change-adjusted "
            "fraction-on. Adjust Min/Max to span the data, or pick different "
            "wells, timepoint or fold-change settings."
        )
    return _best_from_curves(thresholds, fa, fb, scores,
                             n_a=values_a.size, n_b=values_b.size,
                             support_floor=floor)


def format_optimizer_result(result, well_a: str, well_b: str) -> str:
    """The optimizer's one-line readout. Pure: a result and two names in.

    Reports the cut, what it leaves standing in each well as a **count over the
    well total** rather than a bare percentage, and the score — named, because
    the two objectives produce numbers on different scales and a reader cannot
    tell 0.87 of one from 0.87 of the other.

    The plateau is stated whenever there is one. A clean assay has a real
    plateau, the empty valley between the two populations, and every cut inside
    it classifies identically; printing one number out of a run of two hundred
    as though the scan had found a peak is the false precision this line exists
    to avoid.
    """
    from pipeline.threshold_optimize import OBJECTIVE_SUPPORTED

    if result.objective == OBJECTIVE_SUPPORTED:
        arms = (f"{well_a} {result.k_a:,}/{result.n_a:,} ON ({100 * result.frac_a:.1f}%), "
                f"{well_b} {result.k_b:,}/{result.n_b:,} ON ({100 * result.frac_b:.2f}%)")
        score = (f"separation \u2265 {result.difference:.3f} at 95% confidence "
                 f"(Youden J = {result.youden:.3f}")
        score += ")" if result.weight == 1.0 else f", weight {result.weight:g})"
    else:
        arms = (f"{well_a} adjusted fraction-on {result.frac_a:.3f} (n={result.n_a:,}), "
                f"{well_b} {result.frac_b:.3f} (n={result.n_b:,})")
        score = f"relative gap {result.difference:.3f} (fold-change scan)"
    line = f"threshold = {result.threshold:.6g}; {arms}; {score}."
    if result.tie_count > 1:
        shape = ("a contiguous run spanning"
                 if result.tie_contiguous else
                 "scattered, not adjacent on the grid, across")
        # Which point of the run was taken differs by objective, and saying the
        # wrong one is worse than saying nothing: the bounded path reports the
        # midpoint of the run (``plateau_pick``) while the fold-change path
        # reports ``np.argmax``'s first index, which is the run's bottom edge.
        picked = ("the midpoint of the run around the best is what was applied"
                  if result.objective == OBJECTIVE_SUPPORTED else
                  "the lowest of them is what was applied")
        line += (f" {result.tie_count:,} cuts scored that same best, {shape} "
                 f"{result.tie_span:.4g} intensity unit(s); {picked}.")
    return line


def _cell_gating_first_frame_rows(df) -> tuple:
    """Return ``(sub_df, description)`` for the first FOV's first timepoint."""
    if df is None or df.empty:
        return df, ""

    fov_series = fov_text(df)

    unique_fovs = sorted(fov_series.unique(), key=fov_sort_key)
    first_fov = unique_fovs[0] if unique_fovs else "1"
    same_fov = df.loc[fov_series == first_fov]
    if same_fov.empty:
        return same_fov, ""

    tp_h = (pd.to_numeric(same_fov.get("timepoint_hours"), errors="coerce")
            if "timepoint_hours" in same_fov.columns
            else pd.Series(np.nan, index=same_fov.index, dtype=float))
    tp_str = timepoint_text(same_fov)
    tp_str_num = pd.to_numeric(tp_str, errors="coerce")

    if tp_h.notna().any():
        min_h = float(tp_h.min())
        mask = (tp_h == min_h)
        frame_desc_tp = f"{min_h:g}"
    elif tp_str_num.notna().any():
        min_n = float(tp_str_num.min())
        mask = tp_str_num == min_n
        frame_desc_tp = f"{min_n:g}"
    else:
        non_empty = tp_str[tp_str != ""]
        if non_empty.empty:
            mask = pd.Series(True, index=same_fov.index)
            frame_desc_tp = ""
        else:
            first = sorted(non_empty.unique(), key=str.lower)[0]
            mask = tp_str == first
            frame_desc_tp = first

    frame_df = same_fov.loc[mask]
    frame_desc = f"fov={first_fov}, tp={frame_desc_tp}"
    return frame_df, frame_desc


def _opt_curve_ylim(*series) -> tuple:
    """Y-limits for the optimizer-curve popup.

    Defaults to the familiar [0, 1] fraction-on frame, but expands to cover
    whatever the plotted curves actually span (with padding). Under fold change
    the adjusted fractions can be ratios (>1) or differences (<0), and the
    relative gap can exceed 1 when fa/fb straddle zero — without this they'd be
    clipped above / below the axis and vanish.
    """
    ys = np.concatenate([np.asarray(s, dtype=float).ravel() for s in series]) \
        if series else np.array([], dtype=float)
    ys = ys[np.isfinite(ys)]
    if not ys.size:
        return (-0.02, 1.02)
    ymin = min(-0.02, float(ys.min()))
    ymax = max(1.02, float(ys.max()))
    pad = 0.02 * (ymax - ymin)
    return (ymin - pad, ymax + pad)

