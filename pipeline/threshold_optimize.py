"""Two-population threshold optimizer — the shared numeric core.

Picks the threshold best separating two per-cell intensity populations (a
positive- and a negative-control well) by sweeping candidates and taking the
largest relative fraction-on gap, ``|fa-fb| / max(|fa|, |fb|)``.

Shared by ``process_microscopy``'s end-of-run pass and the Cell Gating tab's
"Optimize ThreshFracOn" button, so the two cannot give different answers on the
same data. The pipeline may not import ``well_viewer`` (ARCHITECTURE §6.2), so
the core lives here and the viewer's fold-change layer stays behind in
``gating_compute`` — moving it would drag ``domain.fold_change`` into the
pipeline's import graph.

Stdlib + numpy only, and listed in
``services.remote_runner._REQUIRED_REMOTE_FILES``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


# Defaults for callers with no user to ask (the pipeline); the GUI uses its own
# fields. The upper bound is a percentile, not the max: a few saturated cells
# would stretch the grid into a region where both fractions are 0 and the gap
# can never win the argmax.
DEFAULT_SWEEP_STEPS = 512
SWEEP_HI_PERCENTILE = 99.5
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

    Relative rather than absolute because for skewed distributions the ratio is
    what a fraction-on cut discriminates: 5% on vs 0.5% on is a 10x difference
    but only 0.045 absolute.

    The denominator is a **magnitude**, not a signed ``max(fa, fb)``: under a
    subtractive fold-change the fractions can go negative, and a signed
    denominator would zero the gap across that region and spike at the sign
    crossing, inverting the chosen threshold.
    """
    abs_gap = np.abs(fa - fb)
    larger = np.maximum(np.abs(fa), np.abs(fb))
    with np.errstate(invalid="ignore", divide="ignore"):
        diffs = np.divide(abs_gap, larger, out=np.zeros_like(abs_gap), where=larger > 0)
    return np.where(np.isfinite(diffs), diffs, 0.0)


def sweep_thresholds(min_threshold: float, max_threshold: float,
                     steps: int) -> np.ndarray:
    """The candidate-threshold grid, or ``ValueError`` describing why not.

    Shared so both callers reject the same inputs; the GUI shows the message.
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

    ``np.argmax`` takes the FIRST index at the maximum, so a run of tied
    thresholds (common — fraction-on is a step function) reports the lowest.
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
    """``(lo, hi)`` sweep bounds from the pooled *populations*.

    ``lo`` is the pooled minimum, not 0.0 — channels sit on a background offset,
    and starting at zero wastes half the grid where both fractions are 1.0.
    Returns a degenerate ``(lo, lo)`` when there is no spread to scan.
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

    The raw (no fold-change) sweep the pipeline uses; bounds default to
    :func:`sweep_bounds`. Raises ``ValueError`` on an empty population or an
    unusable range, so callers skip the channel rather than record a guess.
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
# Reads the per-well CSVs rather than re-deriving values from the ``*_out.zip``
# mask/tophat pairs. Two reasons: those CSV values are what the Cell Gating tab
# plots, so the threshold lands where the user will see it; and
# ``sample_cell_and_bg`` pools cell means with *background* pixels, which is
# right for an Otsu split and wrong here — background lands identically in both
# control wells and dilutes the gap being maximised.

def _row_passes(row: dict) -> bool:
    """Honour ``QC_Pass`` / ``Crop_Pass`` when the CSV carries them.

    The pipeline does not write these (they are Review-side layers), so this is
    all-pass in practice. It keeps a hand-curated or sidecar-merged CSV read on
    the same population as ``gating_compute._cell_gating_valid_mask``.
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

    # The pipeline writes the column as ``tok.lower()`` but the token arrives in
    # whatever case was typed on the command line, so match case-insensitively.
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

    A channel missing from either well, or with no spread to scan, is skipped
    with a warning rather than falling back to Otsu — a sidecar mixing two
    estimators is unexplainable. Rounded to integers, matching the Otsu pass.
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
        # The objective is symmetric, so it cannot tell the wells apart — this
        # is the only guard against a swap poisoning every downstream default.
        if result.frac_a < result.frac_b:
            log.warning(
                "Auto-threshold %s: the positive control (%s) has a LOWER "
                "fraction-on than the negative control (%s) at the chosen "
                "threshold. Are the two control wells swapped?",
                ch, pos_well, neg_well,
            )
    return out
