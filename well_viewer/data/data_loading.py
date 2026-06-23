"""Pure data helpers (CSV loading, timepoint parsing, aggregation, beeswarm).

All row-level operations work on ``pandas.DataFrame``. The canonical row
container in the app is a DataFrame; ``WellViewerApp._get_rows(label)`` returns
one and the per-well cache stores it.
"""

from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import numpy as np
import pandas as pd

from well_viewer.domain.ratio_models import RatioMetric, is_ratio_key


_logger = logging.getLogger("well_viewer")

# One-shot dedupe for the "missing fluor column" warning so a 96-well
# dataset with a schema mismatch logs once per channel, not 96 times.
_missing_col_warned: set[str] = set()


# ── CSV loading and channel detection ────────────────────────────────────────

# Columns kept as strings (not coerced to float)
_STRING_COLS = {"filename", "experiment", "channel", "well", "fov", "timepoint"}

# Per-channel measurement column suffixes — the prefix is a channel token,
# which the rest of the app handles in lower-case (normalize_channel_tokens).
# Lower-casing these at read time keeps the CSV-on-disk schema (where casing
# depends on whatever wrote the column — pipeline, smFISH worker, hand edit)
# in sync with the lower-case convention every downstream lookup uses.
_METRIC_SUFFIXES = (
    "_mean_intensity", "_total_intensity", "_max_intensity",
    "_min_intensity", "_std_intensity", "_smfish_count",
)


from well_viewer.data.image_identity import (  # noqa: E402  (after _METRIC_SUFFIXES decl)
    parse_timepoint_hours,  # re-export: single source of truth in image_identity
    normalize_fov_series as _normalize_fov_series,
)
from well_viewer.core.safe import swallowed


def normalize_fov_series(fov: pd.Series) -> pd.Series:
    """Re-exported from :mod:`well_viewer.data.image_identity` — the
    single source of truth for FOV normalisation. Empty/``"-1"`` → ``"1"``
    matches the single-FOV-extractor convention so plots, gating, and the
    smFISH apply-global writer agree on which rows belong to which image."""
    return _normalize_fov_series(fov)


def load_well_csv(path: Path) -> pd.DataFrame:
    """Load a per-well CSV into a DataFrame.

    String columns (``_STRING_COLS``) stay as object dtype; any other column
    is coerced via ``pd.to_numeric(errors="coerce")``. Non-canonical casings
    of the string columns ("FOV" → "fov") are renamed, as are per-channel
    measurement columns whose channel-prefix uses upper-case
    ("W3Cy5_smfish_count" → "w3cy5_smfish_count"). The ``Included`` column
    is added with default value 1 if missing. Empty/"-1" FOVs are normalised
    to "1".
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if df.empty:
        return df

    def _should_lower(col: str) -> bool:
        lc = str(col).strip().lower()
        if lc in _STRING_COLS:
            return True
        return any(lc.endswith(suf) for suf in _METRIC_SUFFIXES)

    rename = {
        col: str(col).strip().lower()
        for col in df.columns
        if _should_lower(col) and col != str(col).strip().lower()
    }
    if rename:
        df = df.rename(columns=rename)

    if "fov" in df.columns:
        df["fov"] = normalize_fov_series(df["fov"])

    if "Included" not in df.columns:
        df["Included"] = 1

    for col in df.columns:
        if str(col).strip().lower() in _STRING_COLS:
            continue
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Backfill circularity for datasets processed before it became always-on,
    # when its inputs are present (perimeter + an area column). 4π·area/perimeter²
    # (1.0 = perfect circle). Lets the Segmentation QC circularity gate + the
    # plot dropdowns work without a full reprocess; datasets processed with the
    # current pipeline already carry ``shape_circularity`` directly.
    if "shape_circularity" not in df.columns and "shape_perimeter" in df.columns:
        area_col = ("shape_area" if "shape_area" in df.columns
                    else "area_px" if "area_px" in df.columns else None)
        if area_col is not None:
            from math import pi
            per = pd.to_numeric(df["shape_perimeter"], errors="coerce")
            area = pd.to_numeric(df[area_col], errors="coerce")
            circ = (4.0 * pi * area) / (per * per)
            df["shape_circularity"] = circ.where(per > 0)

    return df


def detect_fluor_channels(df: pd.DataFrame) -> List[str]:
    """Return sorted channel prefixes that have a ``*_mean_intensity`` column."""
    if df is None or df.empty:
        return []
    suffix = "_mean_intensity"
    return sorted({c[:-len(suffix)] for c in df.columns
                   if c.endswith(suffix) and len(c) > len(suffix)})


def detect_smfish_channels(df: pd.DataFrame) -> List[str]:
    """Return sorted channel prefixes that have a ``*_smfish_count`` column."""
    if df is None or df.empty:
        return []
    suffix = "_smfish_count"
    return sorted({c[:-len(suffix)] for c in df.columns
                   if c.endswith(suffix) and len(c) > len(suffix)})


def detect_shape_features(df: pd.DataFrame) -> List[str]:
    """Return the per-cell ``shape_*`` columns present in *df*, in catalog order.

    Channel-independent morphology columns emitted by the pipeline's
    ``compute_shape_features`` pass. Empty on datasets analyzed before the
    shape-feature work (backwards-compatible — the Review "Shape Features"
    dropdown section simply does not appear). Order follows the
    :mod:`shape_features_core` catalog so the dropdown is deterministic.
    """
    if df is None or df.empty:
        return []
    import pipeline.shape_features as sfc
    present = set(df.columns)
    return [c for c in sfc.ALL_COLUMNS if c in present]


def detect_nuclear_channel_token(df: pd.DataFrame) -> str:
    """Return the nuclear/segmentation channel token from CSV ``channel`` column."""
    if df is None or df.empty or "channel" not in df.columns:
        return ""
    val = df["channel"].iloc[0]
    return str(val or "").strip().lower()


def normalize_channel_tokens(tokens: List[str]) -> List[str]:
    """Stable lower-case de-dupe for channel tokens."""
    out: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        cleaned = str(tok or "").strip().lower()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def merge_fluor_channels(
    pipeline_fluor: List[str],
    detected_fluor: List[str],
    seg_channel_token: str = "",
) -> List[str]:
    """Merge pipeline + detected channel lists with stable order, always including seg token."""
    merged = normalize_channel_tokens(list(pipeline_fluor) + list(detected_fluor))
    seg_tok = str(seg_channel_token or "").strip().lower()
    if seg_tok and seg_tok not in merged:
        merged.append(seg_tok)
    return merged


def detect_review_image_channels(df: pd.DataFrame, fluor_channels: List[str], seg_channel_token: str = "") -> List[str]:
    """Return channel prefixes suitable for Review Image."""
    chans: list[str] = []
    seen: set[str] = set()
    for ch in fluor_channels:
        tok = str(ch or "").strip().lower()
        if tok and tok not in seen:
            seen.add(tok)
            chans.append(tok)
    seg_tok = str(seg_channel_token or "").strip().lower()
    if seg_tok and seg_tok not in seen:
        seen.add(seg_tok)
        chans.append(seg_tok)
    return chans


# ── DataFrame helpers ────────────────────────────────────────────────────────

def df_included_mask(df: pd.DataFrame, *, include_gate: bool = True) -> pd.Series:
    """Rows that count toward analysis: ``Included == 1`` composed with the QC,
    crop, and scatter-gate layers.

    Manual curation / cell-gating own the ``Included`` column; the QC layer owns
    a separate ``QC_Pass`` column (1 = passes debris gating); the crop layer owns
    a ``Crop_Pass`` column (1 = centroid inside the crop region); the scatter-gate
    inclusion mode owns ``Gate_Pass`` (1 = inside the drawn scatter gates). The
    effective inclusion is the AND of the four — ``manual-keep AND passes-QC AND
    inside-crop AND inside-gate`` — so each layer re-gating never clobbers the
    others. ``QC_Pass`` / ``Crop_Pass`` / ``Gate_Pass`` are optional: when absent
    (no such layer / older data) each is treated as all-pass.

    ``include_gate=False`` drops the ``Gate_Pass`` layer so the per-cell scatter
    plot — where the gates are drawn — keeps showing the full cloud (and the
    gates over it) rather than filtering itself by its own gates.
    """
    if "Included" not in df.columns:
        mask = pd.Series(True, index=df.index)
    else:
        incl = pd.to_numeric(df["Included"], errors="coerce").fillna(0)
        mask = incl.eq(1)
    if "QC_Pass" in df.columns:
        qc = pd.to_numeric(df["QC_Pass"], errors="coerce").fillna(1)
        mask = mask & qc.eq(1)
    if "Crop_Pass" in df.columns:
        crop = pd.to_numeric(df["Crop_Pass"], errors="coerce").fillna(1)
        mask = mask & crop.eq(1)
    if include_gate and "Gate_Pass" in df.columns:
        gate = pd.to_numeric(df["Gate_Pass"], errors="coerce").fillna(1)
        mask = mask & gate.eq(1)
    return mask


def resolve_value_series(
    df: pd.DataFrame,
    key: str,
    ratios: Optional[Dict[str, RatioMetric]] = None,
) -> pd.Series:
    """Vectorised counterpart of the old per-row ``resolve_value``.

    For ratio keys (``ratio:<name>``) returns ``num / (den + epsilon)`` with
    NaN where the denominator (after epsilon) is zero or either operand is
    non-finite. For real columns returns the column coerced via
    ``pd.to_numeric``, with non-finite values mapped to NaN.

    Returns NaN-filled Series of the right index when columns/ratios are
    missing.
    """
    if is_ratio_key(key):
        if not ratios:
            return pd.Series(np.nan, index=df.index)
        ratio = ratios.get(key)
        if ratio is None:
            return pd.Series(np.nan, index=df.index)
        ncol, dcol = ratio.numerator_col(), ratio.denominator_col()
        if ncol not in df.columns or dcol not in df.columns:
            return pd.Series(np.nan, index=df.index)
        num = pd.to_numeric(df[ncol], errors="coerce").to_numpy()
        den = pd.to_numeric(df[dcol], errors="coerce").to_numpy()
        # Clamp the denominator to ``epsilon`` (rather than adding
        # epsilon). Background-subtracted channels can go slightly
        # negative; ``den + epsilon`` was sign-flipping near zero and
        # producing arbitrarily large ratios for individual cells. The
        # clamp keeps the divisor strictly positive when epsilon > 0
        # and preserves the old NaN-on-zero behaviour when epsilon == 0.
        if ratio.epsilon > 0.0:
            denom = np.maximum(den, ratio.epsilon)
        else:
            denom = den
        finite = np.isfinite(num) & np.isfinite(den)
        nz = denom != 0.0
        with np.errstate(divide="ignore", invalid="ignore"):
            val = np.where(finite & nz, num / np.where(nz, denom, 1.0), np.nan)
        return pd.Series(val, index=df.index)

    if key not in df.columns:
        # Case-insensitive fallback: load_well_csv now normalises measurement
        # columns to lower-case, but older cached frames or upstream callers
        # may still hold mixed-case headers. Resolve by case-folded match
        # before declaring the column missing.
        lc = key.lower()
        match = next((c for c in df.columns if c.lower() == lc), None)
        if match is None:
            warn_key = f"resolve_value_series::{id(df)}::{key}"
            if warn_key not in _missing_col_warned:
                _missing_col_warned.add(warn_key)
                _logger.warning(
                    "resolve_value_series: column %r not found in DataFrame "
                    "(id=%s, %d cols); returning NaN-filled series. Columns: %s",
                    key, id(df), len(df.columns), sorted(df.columns.tolist()),
                )
            return pd.Series(np.nan, index=df.index)
        key = match
    v = pd.to_numeric(df[key], errors="coerce")
    return v.where(np.isfinite(v), np.nan)


# ── Aggregation ──────────────────────────────────────────────────────────────

# (time_h, mean_above_threshold, sd_above, fraction_above, n_above, n_total,
#  frac_spread, n_above_per_fov_mean, n_above_per_fov_spread)
# n_above                 : cells above threshold at this timepoint  → plot 1 denominator
# n_total                 : all cells at this timepoint              → plot 2 denominator
# frac_spread             : SD/SEM of the fraction across per-FOV fractions when
#                           per_fov_spread is enabled, else 0.0.
# n_above_per_fov_mean    : when per_fov_spread is enabled, mean of per-FOV
#                           above-threshold counts; else 0.0.
# n_above_per_fov_spread  : when per_fov_spread is enabled, SD/SEM of those
#                           per-FOV counts (the "Aggregate FOVs" toggle uses
#                           these to make the events panel show per-FOV mean ±
#                           error instead of a single total bar); else 0.0.
# Trailing positions so existing destructuring with `*_` (and `*extra`) keeps
# working.
AggPoint = Tuple[float, float, float, float, int, int, float, float, float]


def _ordinal_timepoints_df(df: pd.DataFrame, tp_col: str = "timepoint_hours") -> Dict[str, float]:
    """Build a string→ordinal mapping for rows whose numeric timepoint is NaN/missing."""
    if len(df) == 0 or "timepoint" not in df.columns:
        return {}
    if tp_col in df.columns:
        tp_num = pd.to_numeric(df[tp_col], errors="coerce").to_numpy()
    else:
        tp_num = np.full(len(df), np.nan)
    tp_str = df["timepoint"].fillna("").astype(str).to_numpy()
    not_finite = ~np.isfinite(tp_num)
    raw_strings: set[str] = set()
    for s in tp_str[not_finite]:
        s_str = str(s)
        if s_str and parse_timepoint_hours(s_str) is None:
            raw_strings.add(s_str)
    return {s: float(i) for i, s in enumerate(sorted(raw_strings))}


def aggregate_with_threshold_df(
    df: pd.DataFrame,
    threshold: float,
    use_sem: bool = False,
    tp_col: str = "timepoint_hours",
    val_col: str = "gfp_mean_intensity",
    fluor_gates: Optional[Dict[str, float]] = None,
    per_fov_spread: bool = False,
    ratios: Optional[Dict[str, RatioMetric]] = None,
    on_col: Optional[str] = None,
) -> List[AggPoint]:
    """Group rows by timepoint; compute stats for cells above threshold.

    Applies consistent gating across all rows up front — the inclusion chokepoint
    ``df_included_mask`` (``Included==1 AND QC_Pass==1 AND Crop_Pass==1``) plus
    the cell-area threshold and per-channel fluorescence gates — then computes
    statistics on the filtered cell population.

    When ``per_fov_spread`` is True, the spread on the mean is the SD/SEM
    across per-FOV mean intensities at each timepoint, the ``frac_spread`` is
    the SD/SEM across per-FOV ``n_above/n_total`` ratios, and the trailing
    fields summarise per-FOV above-threshold counts.
    """
    if df is None or len(df) == 0:
        return []
    if fluor_gates is None:
        fluor_gates = {}

    n = len(df)

    # Inclusion must go through the single chokepoint so the bar/line aggregation
    # honors the same gating as every other plot/stat: effective inclusion is
    # ``Included==1 AND QC_Pass==1 AND Crop_Pass==1``. Filtering ``Included``
    # alone here silently bypassed the QC and crop layers.
    mask = df_included_mask(df).to_numpy(copy=True)

    if "area_px" in df.columns:
        area = pd.to_numeric(df["area_px"], errors="coerce").to_numpy()
        with np.errstate(invalid="ignore"):
            # Match _all_fluor_values_filtered: drop NaN areas explicitly
            # rather than letting `~(NaN <= x)` keep them. Without this,
            # aggregation kept NaN-area cells but raw filtering dropped
            # them — same dataset, different counts.
            mask &= np.isfinite(area)
    else:
        return []

    for channel, gate in fluor_gates.items():
        col = f"{channel}_mean_intensity"
        if col not in df.columns:
            if col not in _missing_col_warned:
                _missing_col_warned.add(col)
                _logger.warning(
                    "Cell-gating channel %r references column %r which is "
                    "absent from the loaded CSVs — aggregation is returning "
                    "no rows for this channel. Check the gating thresholds "
                    "in Sample Definitions / Cell Gating against the "
                    "current dataset's schema.",
                    channel, col,
                )
            return []
        v = pd.to_numeric(df[col], errors="coerce").to_numpy()
        mask &= np.isfinite(v) & (v > gate)

    if not mask.any():
        return []

    ord_map = _ordinal_timepoints_df(df, tp_col)

    if tp_col in df.columns:
        tp_num = pd.to_numeric(df[tp_col], errors="coerce").to_numpy().astype(float)
    else:
        tp_num = np.full(n, np.nan)

    if "timepoint" in df.columns:
        tp_str = df["timepoint"].fillna("").astype(str).str.strip().to_numpy()
    else:
        tp_str = np.array([""] * n, dtype=object)

    need = ~np.isfinite(tp_num)
    if need.any():
        cache: Dict[str, float] = {}
        for s in np.unique(tp_str[need]):
            s_str = str(s)
            if s_str == "":
                cache[s_str] = 0.0
            else:
                v = parse_timepoint_hours(s_str)
                if v is None:
                    v = ord_map.get(s_str)
                cache[s_str] = float(v) if v is not None else float("nan")
        for i in np.flatnonzero(need):
            tp_num[i] = cache[str(tp_str[i])]

    val = resolve_value_series(df, val_col, ratios).to_numpy()
    if val.size == 0:
        return []

    final = mask & np.isfinite(tp_num) & np.isfinite(val)
    if not final.any():
        return []

    tp_arr = tp_num[final]
    val_arr = val[final]

    # Scatter-gate "thresholding" mode: a per-row boolean column defines the
    # on-population (fraction-on by gate) instead of ``value > threshold``.
    on_arr = None
    if on_col and on_col in df.columns:
        on_full = pd.to_numeric(df[on_col], errors="coerce").fillna(0).to_numpy() > 0.5
        on_arr = on_full[final]

    if per_fov_spread:
        if "fov" in df.columns:
            fov_series = (df["fov"].fillna("1").astype(str).str.strip()
                          .replace("", "1"))
        else:
            fov_series = pd.Series(["1"] * n, index=df.index)
        fov_arr = fov_series.to_numpy()[final]
    else:
        fov_arr = None

    return _aggregate_arrays(tp_arr, val_arr, fov_arr, threshold, use_sem,
                             per_fov_spread, on_arr=on_arr)


def _aggregate_arrays(
    tp_arr: np.ndarray,
    val_arr: np.ndarray,
    fov_arr: Optional[np.ndarray],
    threshold: float,
    use_sem: bool,
    per_fov_spread: bool,
    on_arr: Optional[np.ndarray] = None,
) -> List[AggPoint]:
    """Per-timepoint mean / fraction of the "on" population.

    "On" is normally ``value > threshold``; when ``on_arr`` is given (a per-row
    boolean aligned to ``val_arr``) it defines the on-population instead — the
    scatter-gate "thresholding" mode passes gate membership here so fraction-on
    (and the mean of the on-population) reflect the gate rather than the
    per-channel ThreshFracOn cutoff.
    """
    result: List[AggPoint] = []
    for t in np.unique(tp_arr):
        idx = tp_arr == t
        v = val_arr[idx]
        n_total = int(v.size)
        above_mask = on_arr[idx] if on_arr is not None else (v > threshold)
        above = v[above_mask]
        n_above = int(above.size)
        mean = float(above.sum() / n_above) if n_above else float("nan")

        spread = 0.0
        frac_spread = 0.0
        n_above_per_fov_mean = 0.0
        n_above_per_fov_spread = 0.0

        if per_fov_spread:
            f = fov_arr[idx]
            sub = pd.DataFrame({"fov": f, "v": v, "above": above_mask})
            grp = sub.groupby("fov", sort=False)
            fov_total = grp.size()
            fov_above_count = grp["above"].sum().astype(int)
            fov_mean = sub.loc[above_mask].groupby("fov", sort=False)["v"].mean()

            valid_fov = fov_total > 0
            fov_total = fov_total[valid_fov]
            fov_above_count = fov_above_count.reindex(fov_total.index, fill_value=0)
            fov_mean = fov_mean.reindex(fov_total.index)

            # Per-FOV spreads treat each FOV as a sample, so use the
            # sample SD (ddof=1) consistently — matches what the Stats
            # tab reports for the same groups. The per-cell SD branch
            # below (line ~454) stays ddof=0 because it's an explicit
            # population SD of cells around the well mean (labelled
            # accordingly in the axis title).
            fov_means = fov_mean.dropna().to_numpy()
            if fov_means.size > 1:
                sd = float(fov_means.std(ddof=1))
                spread = sd / math.sqrt(fov_means.size) if use_sem else sd

            fov_fracs = (fov_above_count.to_numpy() / fov_total.to_numpy()).astype(float)
            if fov_fracs.size > 1:
                fsd = float(fov_fracs.std(ddof=1))
                frac_spread = fsd / math.sqrt(fov_fracs.size) if use_sem else fsd

            fov_n_above = fov_above_count.to_numpy().astype(float)
            if fov_n_above.size >= 1:
                n_above_per_fov_mean = float(fov_n_above.sum() / fov_n_above.size)
            if fov_n_above.size > 1:
                nsd = float(fov_n_above.std(ddof=1))
                n_above_per_fov_spread = (nsd / math.sqrt(fov_n_above.size)
                                          if use_sem else nsd)
        else:
            if n_above > 1:
                m = above.sum() / n_above
                sd = float(np.sqrt(((above - m) ** 2).sum() / n_above))
                spread = sd / math.sqrt(n_above) if use_sem else sd

        result.append((float(t), mean, float(spread),
                       n_above / n_total if n_total else float("nan"),
                       int(n_above), int(n_total), float(frac_spread),
                       float(n_above_per_fov_mean),
                       float(n_above_per_fov_spread)))
    return result


def _all_fluor_values(df: pd.DataFrame, val_col: str = "gfp_mean_intensity") -> np.ndarray:
    """Return the Included rows' values for ``val_col`` as a numpy array."""
    if df is None or df.empty or val_col not in df.columns:
        return np.empty(0, dtype=float)
    mask = df_included_mask(df).to_numpy(copy=True)
    v = pd.to_numeric(df[val_col], errors="coerce").to_numpy()
    finite = np.isfinite(v)
    return v[mask & finite]


def _all_fluor_values_filtered(
    df: pd.DataFrame,
    val_col: str = "gfp_mean_intensity",
    fluor_gates: Optional[Dict[str, float]] = None,
    ratios: Optional[Dict[str, RatioMetric]] = None,
    tp_filter: Optional[float] = None,
    tp_col: str = "timepoint_hours",
) -> np.ndarray:
    """Vectorised filter+resolve. Mirrors the legacy scalar filter exactly."""
    if df is None or df.empty:
        return np.empty(0, dtype=float)
    if fluor_gates is None:
        fluor_gates = {}

    mask = df_included_mask(df).to_numpy(copy=True)

    if "area_px" in df.columns:
        area = pd.to_numeric(df["area_px"], errors="coerce").to_numpy()
        with np.errstate(invalid="ignore"):
            mask &= np.isfinite(area)
    else:
        return np.empty(0, dtype=float)

    for channel, gate in fluor_gates.items():
        col = f"{channel}_mean_intensity"
        if col not in df.columns:
            return np.empty(0, dtype=float)
        v = pd.to_numeric(df[col], errors="coerce").to_numpy()
        mask &= np.isfinite(v) & (v > gate)

    if tp_filter is not None:
        if tp_col not in df.columns:
            return np.empty(0, dtype=float)
        tp = pd.to_numeric(df[tp_col], errors="coerce").to_numpy()
        with np.errstate(invalid="ignore"):
            mask &= np.isfinite(tp) & (np.abs(tp - tp_filter) <= 1e-6)

    if not mask.any():
        return np.empty(0, dtype=float)

    val = resolve_value_series(df, val_col, ratios).to_numpy()
    mask &= np.isfinite(val)
    return val[mask]


# ── Beeswarm jitter ──────────────────────────────────────────────────────────

def _beeswarm_jitter(
    values: List[float],
    x_center: float = 0.0,
    max_spread: float = 0.35,
    n_bins: int = 40,
) -> Tuple[List[float], List[float]]:
    """Random x-jitter for a beeswarm column.

    Each point keeps its real y value and is offset from the column midline by a
    *random* amount whose magnitude is proportional to the local point density
    at its value — the fraction of the column's points sharing its y-bin
    (densest bin → full ``max_spread``; sparse tails hug the midline). So the
    column is widest where points are most abundant and pinches in at the tails.

    The deviation is random (seeded for stable redraws), NOT assigned by rank
    along y: a rank-based outward fill correlates x with y within each bin and
    draws chevron / "V" artefacts instead of a cloud.
    """
    arr = np.asarray(values, dtype=float)
    n = arr.size
    if n == 0:
        return [], []

    lo = float(arr.min())
    hi = float(arr.max())
    rng_span = hi - lo if hi > lo else 1.0
    bin_w = rng_span / n_bins
    edges = lo + bin_w * np.arange(1, n_bins)
    bin_idx = np.minimum(np.digitize(arr, edges), n_bins - 1)

    counts = np.bincount(bin_idx, minlength=n_bins)
    max_count = max(int(counts.max()), 1)
    # Per-point half-width ∝ local abundance at that y (relative to the mode).
    half = (counts[bin_idx] / max_count) * max_spread
    gen = np.random.default_rng(0)  # deterministic so redraws are stable
    xs = x_center + gen.uniform(-1.0, 1.0, size=n) * half

    return xs.tolist(), arr.tolist()


# ── Well-token parsing ───────────────────────────────────────────────────────

def extract_well_token(label: str) -> Optional[str]:
    """'measurements_B10' → 'B10'."""
    m = re.search(r"([A-Ha-h])(\d{1,2})$", label)
    return f"{m.group(1).upper()}{int(m.group(2)):02d}" if m else None


def parse_well_token(token: str) -> Optional[Tuple[int, int]]:
    """Convert a well token like ``"A01"`` into ``(row, col)`` zero-based."""
    if not token:
        return None
    m = re.fullmatch(r"\s*([A-Za-z])(\d{1,2})\s*", str(token))
    if not m:
        return None
    row = ord(m.group(1).upper()) - ord("A")
    col = int(m.group(2)) - 1
    if row < 0 or col < 0:
        return None
    return row, col


# ── Plot-group iteration ─────────────────────────────────────────────────────

def iter_plot_groups(app, fallback_to_all: bool = True) -> Iterator[Tuple[str, str, pd.DataFrame]]:
    """Yield ``(name, color, df)`` for each replicate set or selected well.

    When replicate sets are defined, one entry per replicate set with rows
    pooled (``pd.concat``) from all wells in the set; otherwise one entry per
    selected well. When ``fallback_to_all`` is True (default) and no wells are
    selected, all loaded wells are yielded.

    Colours come from the same rank-based palette the sidebar plate map +
    the Line / Bar / Scatter renderers use (``_rank_color_well`` /
    ``_rank_color_rset`` in ``runtime_app``). Optional ``_color_for_label``
    and ``_color_for_well`` callbacks on *app* still get first dibs so a
    caller can override the canonical lookup.
    """
    get_active = getattr(app, "_rep_sets_active", None)
    rep_sets = list(get_active() if callable(get_active) else [])
    well_paths = app._app_state.dataset.well_paths or {}
    selected = set(app._app_state.selection.selected_wells or set())
    color_for_label = getattr(app, "_color_for_label", None)
    color_for_well = getattr(app, "_color_for_well", None)
    rank_rset = getattr(app, "_rank_color_rset", None)
    rank_well = getattr(app, "_rank_color_well", None)

    def _override(fn, key):
        if not callable(fn):
            return None
        try:
            c = fn(key)
        except Exception:
            return None
        return c if c else None

    def _color_rset(rset) -> str:
        c = _override(color_for_label, rset.name)
        if c:
            return c
        if callable(rank_rset):
            try:
                return rank_rset(rset)
            except Exception:
                swallowed()  # was: pass (silent except)
        return "#808080"

    def _color_well(w: str) -> str:
        c = _override(color_for_well, w)
        if c:
            return c
        if callable(rank_well):
            try:
                return rank_well(w)
            except Exception:
                swallowed()  # was: pass (silent except)
        return "#808080"

    if rep_sets:
        # With groups defined the active set on the plot is exactly
        # ``_rep_sets_active()`` (every non-hidden group) plus any solo
        # wells the user has explicitly toggled on via
        # ``_active_solo_wells``. ``_selected_wells`` is not used in
        # rep-mode — group visibility is the single source of truth.
        in_any_group: set = set()
        for rset in rep_sets:
            wells = [w for w in rset.wells if w in well_paths]
            in_any_group.update(wells)
            if not wells:
                continue
            frames = [app._get_rows(w) for w in wells]
            pooled = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
            yield rset.name, _color_rset(rset), pooled
        active_solo = app._app_state.selection.active_solo_wells or set()
        for w in sorted(w for w in active_solo
                        if w in well_paths and w not in in_any_group):
            yield w, _color_well(w), app._get_rows(w)
        return

    if not selected:
        if not fallback_to_all:
            return
        selected = set(well_paths.keys())
    for w in sorted(selected):
        if w not in well_paths:
            continue
        yield w, _color_well(w), app._get_rows(w)
