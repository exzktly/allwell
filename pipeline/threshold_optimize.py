"""Two-population threshold optimizer — the shared numeric core.

Picks the threshold best separating two per-cell intensity populations (a
positive- and a negative-control well) by sweeping candidates and taking the
largest relative fraction-on gap, ``|fa-fb| / max(|fa|, |fb|)``.

Shared by ``process_microscopy``'s end-of-run pass and the Cell Gating tab's
"Optimize ThreshFracOn" button, so the two cannot give different answers on the
same data. The pipeline may not import ``review`` (ARCHITECTURE §6.2), so
the core lives here and the viewer's fold-change layer stays behind in
``gating_compute`` — moving it would drag ``domain.fold_change`` into the
pipeline's import graph.

Stdlib + numpy only, and listed in
``services.remote_runner._REQUIRED_REMOTE_FILES``.
"""

from __future__ import annotations

# Names used only in annotations. Under ``from __future__ import
# annotations`` these are never evaluated, so importing them for real would
# add startup cost and, for several of these, an import cycle.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import math
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

# Significant figures kept when a threshold is stored. See
# :func:`round_stored_threshold`.
_STORED_SIG_FIGS = 3


def round_stored_threshold(value: float) -> float:
    """Round an estimated threshold for storage without rounding it away.

    Both estimators used to store ``float(round(x))``, which is right for camera
    counts — a gating cut of 547.7 reads better as 548, and it is the number the
    user can type back into the form — and catastrophic below 1. ``_safe_imwrite``
    writes float32 TIFFs, so a channel normalised to 0–1 is a real input, and its
    Otsu threshold of 0.4048 was stored as **0.0**: a gate that passes every cell
    (measured: 29.9 % of cells ON became 99.8 %), written as the one value a
    reader cannot tell apart from "no threshold was computed", which is exactly
    what both callers mean when they omit a channel.

    So: keep at least three significant figures, and never fewer digits than the
    integer rounding used to give. ``|value| >= 100`` — every real camera-intensity
    threshold — is unchanged, because three significant figures there is already
    the integer. Below that the value keeps enough resolution to stay non-zero:
    0.4048 → 0.405, 0.000123 → 0.000123.

    Non-finite input is returned unchanged; a caller storing a threshold checks
    for that itself rather than letting this function invent a number.
    """
    v = float(value)
    if not math.isfinite(v) or v == 0.0:
        return v
    digits = max(0, (_STORED_SIG_FIGS - 1) - int(math.floor(math.log10(abs(v)))))
    return float(round(v, digits))


@dataclass(frozen=True)
class ThreshFracOptimizationResult:
    """Best threshold scan result for a two-population fraction-on comparison.

    ``tie_count`` and ``tie_span`` describe the *plateau* the reported threshold
    was picked from: how many grid points scored the identical best objective,
    and how wide a range of thresholds those points span. ``argmax`` returns the
    first of them, so without these two numbers a flat run of 73 grid points is
    presented as if the optimiser had found one answer.

    ``tie_span`` is the distance between the lowest and highest tied threshold,
    which is only the width of a *plateau* when the tied points are adjacent on
    the grid. They need not be: a bimodal objective can score its maximum at two
    separated points with lower ground between them, and the span then covers
    thresholds that did not tie at all. ``tie_contiguous`` says which of the two
    a caller is holding, so a log line can describe a range or a set of runs
    rather than claiming a plateau it did not measure.
    """

    threshold: float
    frac_a: float
    frac_b: float
    difference: float
    n_a: int
    n_b: int
    tie_count: int = 1
    tie_span: float = 0.0
    tie_contiguous: bool = True


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


def relative_gap(fa: np.ndarray, fb: np.ndarray, *,
                 n_a: int | None = None, n_b: int | None = None) -> np.ndarray:
    """Per-threshold ``|fa-fb| / max(|fa|, |fb|)``, with 0.0 where undefined.

    Relative rather than absolute because for skewed distributions the ratio is
    what a fraction-on cut discriminates: 5% on vs 0.5% on is a 10x difference
    but only 0.045 absolute.

    The denominator is a **magnitude**, not a signed ``max(fa, fb)``: under a
    subtractive fold-change the fractions can go negative, and a signed
    denominator would zero the gap across that region and spike at the sign
    crossing, inverting the chosen threshold.

    **Statistical support.** Given the population sizes *n_a* and *n_b*, both
    fractions are Laplace-smoothed — ``(k + 1) / (n + 2)`` — before the ratio is
    taken. Without that, the ratio is exactly 1.0 at *every* threshold where one
    arm is empty and the other holds even a single cell, so the objective could
    not tell "97 % of positives on against 0 % of negatives" from "1 % on
    against 0 %". Smoothing costs nothing where the counts are real (0.998
    rather than 1.000 for a clean separation) and collapses the score where they
    are not (0.5 for one cell against none, at n=500).

    **What that does and does not fix.** It shortens the tied run: measured on
    two well-separated control populations of 400 cells each, the grid points
    scoring the maximum fall from 485 of 512 to 354, and on overlapping
    populations from 80 to 3. Every point it removes is at the *top* of the run,
    where the positive arm has thinned to a handful of cells — the region the
    unsmoothed ratio could not distinguish from a clean separation.

    It does not move the reported threshold. ``argmax`` returns the *first* tied
    index, which is the bottom of the run, and the bottom is set by where the
    negative control's fraction-on reaches zero — a place smoothing does not
    touch. On that same pair, moving one negative-control cell out to 1500 moves
    the reported gate by 1252 intensity units, smoothed or not; only the tie
    count changes (174 to 43). The instability is real and is still there. What
    ships against it is visibility, not a cure: ``tie_count``, ``tie_span`` and
    ``tie_contiguous`` are recorded and logged, so a gate picked off a 354-point
    run is no longer printed like one picked off a peak. Which point of a
    plateau *should* be reported is a domain question about the assay, not one
    this module can decide.

    Counts are optional because the fold-change layer in
    ``review.features.home.cell_gating.gating_compute`` scans *adjusted*
    fractions, which are no longer ``k / n`` — they can exceed 1 and go
    negative — so there is no ``k`` to add one to. Pass counts only for genuine
    fraction-on curves.
    """
    fa = np.asarray(fa, dtype=float)
    fb = np.asarray(fb, dtype=float)
    if n_a is not None and n_b is not None and int(n_a) > 0 and int(n_b) > 0:
        fa = (fa * int(n_a) + 1.0) / (int(n_a) + 2.0)
        fb = (fb * int(n_b) + 1.0) / (int(n_b) + 2.0)
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
    """Package the argmax of *diffs* as a result, plateau included.

    ``np.argmax`` takes the FIRST index at the maximum, so a run of tied
    thresholds (common — fraction-on is a step function) reports the lowest.
    That is a defensible choice and an indefensible thing to leave unsaid, so
    the result also carries how many grid points scored that same maximum and
    how wide a threshold range they cover. A caller that prints one number can
    then say whether it was picked from a plateau or from a peak.
    """
    diffs = np.asarray(diffs, dtype=float)
    thresholds = np.asarray(thresholds, dtype=float)
    best_idx = int(np.argmax(diffs))
    best = float(diffs[best_idx])
    tied_idx = np.flatnonzero(diffs == best)
    tied = thresholds[tied_idx]
    # The tied points are a plateau only when they are adjacent on the grid.
    # Read off the indices rather than the threshold values: the sweep is
    # uniformly spaced, so an index run is exactly the question being asked and
    # it does not have to compare floats for equality a second time.
    contiguous = bool(
        tied_idx.size <= 1
        or tied_idx.size == int(tied_idx[-1] - tied_idx[0]) + 1
    )
    return ThreshFracOptimizationResult(
        threshold=float(thresholds[best_idx]),
        frac_a=float(fa[best_idx]),
        frac_b=float(fb[best_idx]),
        difference=best,
        n_a=int(n_a),
        n_b=int(n_b),
        tie_count=int(tied.size),
        tie_span=float(tied.max() - tied.min()) if tied.size else 0.0,
        tie_contiguous=contiguous,
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
    diffs = relative_gap(fa, fb, n_a=values_a.size, n_b=values_b.size)
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


def find_well_csv(output_dir, csv_prefix: str, well: str) -> Path | None:
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
    estimators is unexplainable. Stored through
    :func:`round_stored_threshold`, matching the Otsu pass.
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
        if not np.isfinite(result.threshold):
            # Not a number the sidecar can carry, and storing it as 0.0 would be
            # a gate that passes everything, written as the value that also
            # means "not computed".
            log.warning(
                "Auto-threshold: skipping %s — the optimiser returned a "
                "non-finite threshold (%r).", ch, result.threshold,
            )
            continue
        out[ch] = round_stored_threshold(result.threshold)
        log.info(
            "Auto-threshold %s: %.4g  (%s %.1f%% on / %s %.1f%% on, "
            "supported gap %.3f; n=%d/%d)",
            ch, out[ch], pos_well, 100.0 * result.frac_a,
            neg_well, 100.0 * result.frac_b, result.difference,
            result.n_a, result.n_b,
        )
        # The scan is a step function, so the best score is usually shared by a
        # run of grid points and the reported threshold is merely the lowest of
        # them. A plateau spanning a meaningful share of the swept range is a
        # weak answer, and it used to be reported exactly like a sharp one.
        if result.tie_count > 1:
            # Rounded the same way the stored value is, so the two lines cannot
            # disagree about the number they both name.
            #
            # "Spanning" is only true of a contiguous run. When the tied points
            # are separated the span covers thresholds that scored lower, so the
            # line says "between" and names the gap rather than describing a
            # plateau nobody measured.
            shape = (
                "spanning %.4g intensity unit(s) (%.4g to %.4g)"
                if result.tie_contiguous else
                "scattered across %.4g intensity unit(s) (between %.4g and "
                "%.4g, not adjacent on the grid)"
            )
            log.info(
                "Auto-threshold %s: that score is shared by %d of %d grid "
                "point(s), " + shape + " — the lowest of them is reported.",
                ch, result.tie_count, steps, result.tie_span, out[ch],
                round_stored_threshold(result.threshold + result.tie_span),
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
