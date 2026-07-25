"""Two-population threshold optimizer — the shared numeric core.

Given two per-cell intensity populations that are known to differ — canonically
a positive-control well and a negative-control well — this picks the threshold
that best separates them, by sweeping candidate thresholds and taking the one
where the *relative* fraction-on gap is largest:

    |fraction_on(A) - fraction_on(B)| / max(|fraction_on(A)|, |fraction_on(B)|)

Two callers share it, which is why it lives here rather than beside either:

* ``process_microscopy`` — the end-of-run threshold pass, when the run named a
  positive and a negative control well.
* ``well_viewer.features.grouping.cell_gating.gating_compute`` — the Cell Gating
  tab's "Optimize ThreshFracOn" button, which adds an optional fold-change layer
  on top of these primitives.

Keeping one implementation is the point. The pipeline may not import
``well_viewer`` (ARCHITECTURE §6.2), so without this module the pipeline would
need its own copy of the sweep — and a second copy of an objective function is
how the two sides start giving different answers on the same data. The
fold-change layer deliberately does *not* move here: it depends on
``well_viewer.domain.fold_change``, which would drag the viewer into the
pipeline's import graph.

Stdlib + numpy only, for the same reason ``pipeline.auto_threshold`` is. Listed
in ``services.remote_runner._REQUIRED_REMOTE_FILES`` so a remote run receives it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


# Sweep defaults for callers with no user to ask (the pipeline). The GUI takes
# these from its own fields instead.
DEFAULT_SWEEP_STEPS = 512
# The sweep's upper bound is a high percentile of the pooled populations rather
# than their max: a handful of saturated cells sit orders of magnitude above the
# bulk, and a grid stretched to reach them spends most of its resolution in a
# region where both fractions are 0 — where the relative gap is 0.0 by
# definition and can never win the argmax.
SWEEP_HI_PERCENTILE = 99.5
# Guard rails shared with the GUI path, so both reject the same inputs.
MIN_SWEEP_STEPS = 2
MAX_SWEEP_STEPS = 10001


@dataclass(frozen=True)
class ThreshFracOptimizationResult:
    """Best threshold scan result for a two-population fraction-on comparison."""

    threshold: float
    frac_a: float
    frac_b: float
    difference: float
    n_a: int
    n_b: int


def fraction_on_for_threshold(values: np.ndarray, threshold: float) -> float:
    """Fraction of *values* strictly above *threshold*; NaN for an empty array."""
    if values.size == 0:
        return float("nan")
    return float(np.count_nonzero(values > threshold) / values.size)


def frac_curve(values: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    """Fraction-on of *values* at each scanned threshold."""
    return np.array(
        [fraction_on_for_threshold(values, float(t)) for t in thresholds],
        dtype=float,
    )


def relative_gap(fa: np.ndarray, fb: np.ndarray) -> np.ndarray:
    """Per-threshold ``|fa-fb| / max(|fa|, |fb|)``, with 0.0 where undefined.

    The absolute gap ``|fa - fb|`` peaks wherever the populations diverge most
    *in count* — useful when both share an "on" tail. But for two skewed
    fluorescence distributions where most cells are barely-on, the absolute gap
    stays small even where the well *ratio* differs sharply (one well 5% on, the
    other 0.5% on is a 10x difference but only 0.045 in absolute terms).
    Normalising by the larger fraction surfaces those relative differences,
    which is what a fraction-on cut is meant to discriminate.

    The denominator uses the **magnitude** ``max(|fa|, |fb|)`` rather than
    ``max(fa, fb)``: under a subtractive fold-change the adjusted fractions are
    differences that can go negative, and a signed denominator would zero the gap
    across the whole negative region and spike at the sign crossing — inverting
    the curve and the chosen threshold. Magnitude is sign-safe and, for the
    non-negative raw / ratio cases, identical to a signed ``max``.

    Zero-magnitude and non-finite entries become ``0.0`` so ``argmax`` stays
    meaningful rather than selecting a NaN.
    """
    abs_gap = np.abs(fa - fb)
    larger = np.maximum(np.abs(fa), np.abs(fb))
    with np.errstate(invalid="ignore", divide="ignore"):
        diffs = np.divide(abs_gap, larger, out=np.zeros_like(abs_gap), where=larger > 0)
    return np.where(np.isfinite(diffs), diffs, 0.0)


def sweep_thresholds(min_threshold: float, max_threshold: float,
                     steps: int) -> np.ndarray:
    """The candidate-threshold grid, or raise ``ValueError`` describing why not.

    Shared so the pipeline and the GUI reject the same inputs with the same
    wording; the GUI surfaces the message directly to the user.
    """
    lo = float(min_threshold)
    hi = float(max_threshold)
    if not np.isfinite(lo) or not np.isfinite(hi):
        raise ValueError("threshold range must be finite.")
    if hi < lo:
        raise ValueError("max threshold must be greater than or equal to min threshold.")
    if int(steps) < MIN_SWEEP_STEPS:
        raise ValueError(f"steps must be at least {MIN_SWEEP_STEPS}.")
    if int(steps) > MAX_SWEEP_STEPS:
        raise ValueError(f"steps must be {MAX_SWEEP_STEPS:,} or fewer.")
    return np.linspace(lo, hi, int(steps))


def best_from_curves(
    thresholds: np.ndarray,
    fa: np.ndarray,
    fb: np.ndarray,
    diffs: np.ndarray,
    *,
    n_a: int,
    n_b: int,
) -> ThreshFracOptimizationResult:
    """Package the argmax of *diffs* as a result.

    ``np.argmax`` returns the FIRST index attaining the maximum, so when a run
    of thresholds ties (fraction-on is a step function, so ties are common
    between two separated populations) the lowest tied threshold is reported.
    """
    best_idx = int(np.argmax(diffs))
    return ThreshFracOptimizationResult(
        threshold=float(thresholds[best_idx]),
        frac_a=float(fa[best_idx]),
        frac_b=float(fb[best_idx]),
        difference=float(diffs[best_idx]),
        n_a=int(n_a),
        n_b=int(n_b),
    )


def sweep_bounds(*populations: np.ndarray) -> tuple[float, float]:
    """``(lo, hi)`` sweep bounds derived from the pooled *populations*.

    ``lo`` is the pooled minimum, not 0.0: channels routinely sit on a large
    background offset, and starting the grid at zero spends its lower half on
    thresholds where both fractions are 1.0 and the gap is 0.0. ``hi`` is the
    :data:`SWEEP_HI_PERCENTILE` percentile — see that constant for why not the
    max, and note the fallback to the true max when the percentile collapses onto
    ``lo``.

    Returns a degenerate ``(lo, lo)`` when there is no spread to scan; the caller
    treats that as "no usable threshold for this channel".
    """
    finite = [np.asarray(p, dtype=float).ravel() for p in populations if p is not None]
    pooled = np.concatenate(finite) if finite else np.array([], dtype=float)
    pooled = pooled[np.isfinite(pooled)]
    if pooled.size == 0:
        return 0.0, 0.0
    lo = float(pooled.min())
    hi = float(np.percentile(pooled, SWEEP_HI_PERCENTILE))
    if not np.isfinite(hi) or hi <= lo:
        hi = float(pooled.max())
    return lo, hi


def optimize_between_populations(
    values_a: np.ndarray,
    values_b: np.ndarray,
    *,
    min_threshold: float | None = None,
    max_threshold: float | None = None,
    steps: int = DEFAULT_SWEEP_STEPS,
) -> ThreshFracOptimizationResult:
    """Threshold best separating *values_a* from *values_b*.

    The raw (no fold-change) sweep, which is what the pipeline needs. Bounds
    default to :func:`sweep_range_for` over both populations. Raises
    ``ValueError`` when either population is empty or the sweep range is
    unusable — callers skip that channel rather than record a guess.
    """
    values_a = np.asarray(values_a, dtype=float)
    values_b = np.asarray(values_b, dtype=float)
    if values_a.size == 0:
        raise ValueError("Population A has no valid finite values.")
    if values_b.size == 0:
        raise ValueError("Population B has no valid finite values.")

    if min_threshold is None or max_threshold is None:
        auto_lo, auto_hi = sweep_bounds(values_a, values_b)
        lo = auto_lo if min_threshold is None else float(min_threshold)
        hi = auto_hi if max_threshold is None else float(max_threshold)
    else:
        lo, hi = float(min_threshold), float(max_threshold)
    if hi <= lo:
        raise ValueError(
            "threshold range is degenerate — the populations carry no spread "
            "to scan across."
        )

    thresholds = sweep_thresholds(lo, hi, steps)
    fa = frac_curve(values_a, thresholds)
    fb = frac_curve(values_b, thresholds)
    diffs = relative_gap(fa, fb)
    return best_from_curves(thresholds, fa, fb, diffs,
                            n_a=values_a.size, n_b=values_b.size)


# ── Pipeline-side layer: per-well CSVs → per-channel thresholds ──────────────
#
# The pipeline writes one CSV per well (``pipeline.io.write_csvs_per_well``),
# one row per segmented cell, carrying a ``<channel>_mean_intensity`` column.
# Those are the values the Cell Gating tab plots and gates on, so deriving the
# threshold from them puts it exactly where the user will later see it on the
# CDF. Deliberately NOT re-derived from the ``*_out.zip`` mask/tophat pairs:
# ``pipeline.auto_threshold.sample_cell_and_bg`` pools cell means with matched
# *background* pixels, which is right for an Otsu split against background and
# wrong here — background pixels land identically in both control wells and
# dilute the very gap this objective maximises.

def _row_passes(row: dict) -> bool:
    """Honour ``QC_Pass`` / ``Crop_Pass`` when the CSV carries them.

    The pipeline does not write these today — they are Review-side layers
    computed after the fact — so this is all-pass in practice. It is here so a
    sidecar-merged or hand-curated CSV is read on the same population the Cell
    Gating tab's optimizer uses (``gating_compute._cell_gating_valid_mask``)
    rather than silently on a wider one.
    """
    for col in ("QC_Pass", "Crop_Pass"):
        raw = row.get(col)
        if raw is None or str(raw).strip() == "":
            continue
        if str(raw).strip().lower() in ("0", "false", "no"):
            return False
    return True


def read_channel_values(csv_path, channel: str) -> np.ndarray:
    """Finite per-cell ``<channel>_mean_intensity`` values from one per-well CSV.

    Returns an empty array for a missing file, a missing column, or a column with
    nothing parseable — every one of which the caller treats the same way, by
    skipping the channel rather than recording a guess.
    """
    import csv

    # ``process_microscopy`` writes the column as ``tok.lower()``, and the
    # viewer's channel keys are lower-cased too, but the token reaching here
    # comes straight off the command line in whatever case the user typed
    # (``--fluor_tokens GFP``). Match case-insensitively rather than trusting
    # either end.
    want = f"{str(channel).strip().lower()}_mean_intensity"
    out: list[float] = []
    try:
        with open(csv_path, newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                return np.array([], dtype=float)
            col = next((f for f in reader.fieldnames
                        if str(f).strip().lower() == want), None)
            if col is None:
                return np.array([], dtype=float)
            for row in reader:
                if not _row_passes(row):
                    continue
                try:
                    val = float(row.get(col, ""))
                except (TypeError, ValueError):
                    continue
                if np.isfinite(val):
                    out.append(val)
    except OSError:
        return np.array([], dtype=float)
    return np.asarray(out, dtype=float)


def find_well_csv(output_dir, csv_prefix: str, well: str):
    """The per-well CSV for *well*, or ``None``.

    Tries the exact spelling first, then falls back to canonicalising both sides
    so a user who typed ``b3`` still matches ``<prefix>_B03.csv``.
    """
    from pathlib import Path

    from util.well_token import canonical_well_label

    output_dir = Path(output_dir)
    exact = output_dir / f"{csv_prefix}_{well}.csv"
    if exact.is_file():
        return exact
    want = canonical_well_label(well)
    if not want:
        return None
    for path in sorted(output_dir.glob(f"{csv_prefix}_*.csv")):
        stem_well = path.stem[len(csv_prefix) + 1:]
        if canonical_well_label(stem_well) == want:
            return path
    return None


def estimate_thresholds_from_controls(
    output_dir,
    *,
    fluor_channels,
    csv_prefix: str,
    pos_well: str,
    neg_well: str,
    log,
    steps: int = DEFAULT_SWEEP_STEPS,
) -> dict[str, float]:
    """Per-channel thresholds separating the positive from the negative control.

    One entry per channel in *fluor_channels* that both control wells carry
    usable values for; a channel missing from either well, or with no spread to
    scan, is skipped with a warning rather than silently falling back to a
    different objective — a sidecar mixing two estimators is unexplainable.

    Values are rounded to the nearest integer, matching the Otsu pass.
    """
    pos_csv = find_well_csv(output_dir, csv_prefix, pos_well)
    neg_csv = find_well_csv(output_dir, csv_prefix, neg_well)
    if pos_csv is None or neg_csv is None:
        missing = ", ".join(
            w for w, p in ((pos_well, pos_csv), (neg_well, neg_csv)) if p is None
        )
        log.warning(
            "Auto-threshold: no per-well CSV for control well(s) %s — "
            "skipping the control-based pass.", missing,
        )
        return {}

    out: dict[str, float] = {}
    for channel in fluor_channels:
        ch = str(channel).strip().lower()
        if not ch:
            continue
        values_pos = read_channel_values(pos_csv, channel)
        values_neg = read_channel_values(neg_csv, channel)
        if values_pos.size == 0 or values_neg.size == 0:
            log.warning(
                "Auto-threshold: skipping %s — no usable values in %s (%d) / %s (%d).",
                ch, pos_well, values_pos.size, neg_well, values_neg.size,
            )
            continue
        try:
            result = optimize_between_populations(values_pos, values_neg, steps=steps)
        except ValueError as exc:
            log.warning("Auto-threshold: skipping %s — %s", ch, exc)
            continue
        out[ch] = float(round(result.threshold))
        log.info(
            "Auto-threshold %s: %.0f  (%s %.1f%% on / %s %.1f%% on, "
            "relative gap %.3f; n=%d/%d)",
            ch, out[ch], pos_well, 100.0 * result.frac_a,
            neg_well, 100.0 * result.frac_b, result.difference,
            result.n_a, result.n_b,
        )
        # The objective is symmetric, so it cannot tell the two wells apart and
        # will happily maximise a gap pointing the wrong way. This is the only
        # guard against a two-character typo poisoning every downstream default.
        if result.frac_a < result.frac_b:
            log.warning(
                "Auto-threshold %s: the positive control (%s) has a LOWER "
                "fraction-on than the negative control (%s) at the chosen "
                "threshold. Are the two control wells swapped?",
                ch, pos_well, neg_well,
            )
    return out
