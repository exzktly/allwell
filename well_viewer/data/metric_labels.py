"""Centralized labels for the global intensity-property dropdown.

The CSV produced by ``process_microscopy`` carries five intensity columns
per fluorescence channel (``_total_intensity``, ``_mean_intensity``,
``_max_intensity``, ``_min_intensity``, ``_std_intensity``) plus an optional
``_smfish_count`` for smFISH channels. The global "Property" combo in the
plotting ctxbar lets the user pick which of these drives every plot, stat,
and CSV export — by swapping the ``_active_metric`` suffix that
``_active_val_col`` is built from.

This module is the single source of truth for the label↔key mapping and
the suffix→display-label table used by axis / stats / scatter label
helpers across the viewer.
"""

from __future__ import annotations

from typing import Optional, Tuple


METRIC_LABEL_TO_KEY = {
    "Mean Intensity":  "mean_intensity",
    "Total Intensity": "total_intensity",
    "Max Intensity":   "max_intensity",
    "Min Intensity":   "min_intensity",
    "Std Intensity":   "std_intensity",
    "smFISH Count":    "smfish_count",
}

METRIC_KEY_TO_LABEL = {v: k for k, v in METRIC_LABEL_TO_KEY.items()}

METRIC_ORDER = list(METRIC_LABEL_TO_KEY.keys())

INTENSITY_METRIC_KEYS = (
    "total_intensity",
    "mean_intensity",
    "max_intensity",
    "min_intensity",
    "std_intensity",
)

INTENSITY_SUFFIXES = tuple(f"_{k}" for k in INTENSITY_METRIC_KEYS)

SMFISH_SUFFIX = "_smfish_count"

# Display label used in Property combos when a ratio channel is the
# active selection. Ratios have a single computed value per cell
# (``ratio:<name>``), so none of the intensity flavours apply — the
# combo collapses to this single entry.
CALCULATED_VAL_LABEL = "Calculated Val"


def split_metric_col(col: str) -> Optional[Tuple[str, str, str]]:
    """Split ``<channel>_<metric_suffix>`` into ``(channel, metric_key, label)``.

    Returns ``None`` when *col* does not end in one of the known intensity
    or smFISH suffixes (e.g. when it's a ratio key or an unrelated column).
    The smFISH branch uses the legacy ``"(spots)"`` formatting so existing
    stats output stays stable.
    """
    if col.endswith(SMFISH_SUFFIX):
        ch = col[: -len(SMFISH_SUFFIX)]
        return ch, "smfish_count", f"{ch.upper()} (spots)"
    for suf, key in zip(INTENSITY_SUFFIXES, INTENSITY_METRIC_KEYS):
        if col.endswith(suf):
            ch = col[: -len(suf)]
            return ch, key, f"{ch.upper()} {METRIC_KEY_TO_LABEL[key]}"
    return None


# ── Title qualifiers ────────────────────────────────────────────────────────
#
# Plot titles say things like "(above threshold)" / "Fraction above threshold"
# because the bar / line / heat-map data path filters cells via the channel's
# cell-gating threshold. For smfish_count we use threshold=0 — every cell
# with at least one detected spot counts — so the "above threshold" phrasing
# is misleading. These helpers centralise the per-metric wording so every
# title and axis label stays in sync.


def threshold_qualifier(metric_key: str) -> str:
    """Suffix for the mean-axis title (' (above threshold)' / ' (all spots)')."""
    if metric_key == "smfish_count":
        return " (all spots, thresholds ignored)"
    return " (above threshold)"


def fraction_title(metric_key: str) -> str:
    """Title for the fraction-of-cells axis."""
    if metric_key == "smfish_count":
        return "Fraction of Cells with Detected Spots"
    return "Fraction of Cells Above Threshold"


def events_per_fov_title(metric_key: str, band_lbl: str) -> str:
    """Title for the per-FOV events axis."""
    if metric_key == "smfish_count":
        return f"Cells with detected spots per FOV ± {band_lbl}"
    return f"Mean events above threshold per FOV ± {band_lbl}"


def events_total_title(metric_key: str) -> str:
    """Title for the per-timepoint total events axis."""
    if metric_key == "smfish_count":
        return "Cells with detected spots (N)"
    return "Events above threshold (N)"
