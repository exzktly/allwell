"""Centralized labels for the global intensity-property dropdown.

The CSV produced by ``process_microscopy`` carries five intensity columns
per fluorescence channel (``_total_intensity``, ``_mean_intensity``,
``_max_intensity``, ``_min_intensity``, ``_std_intensity``) plus an optional
``_smfish_count`` for smFISH channels. The global "Property" combo in the
plotting ctxbar lets the user pick which of these drives every plot, stat,
and CSV export — by swapping the ``_active_metric`` suffix that
``_active_val_col`` is built from.

These label / key / suffix tables are *derived* from the single declarative
metric catalogue in :mod:`well_viewer.domain.metric_registry` — add a metric
there and it flows through here (and into every Property combo) automatically.
This module remains the import site the axis / stats / scatter label helpers
reach for, and owns the column-splitting + title-wording helpers below.
"""

from __future__ import annotations


from well_viewer.domain.metric_registry import (
    METRICS,
    intensity_keys,
    metric_labels_in_order,
)

METRIC_LABEL_TO_KEY = {m.label: m.key for m in METRICS}

METRIC_KEY_TO_LABEL = {m.key: m.label for m in METRICS}

METRIC_ORDER = metric_labels_in_order()

INTENSITY_METRIC_KEYS = intensity_keys()

INTENSITY_SUFFIXES = tuple(f"_{k}" for k in INTENSITY_METRIC_KEYS)

SMFISH_SUFFIX = "_smfish_count"

# Display label used in Property combos when a ratio channel is the
# active selection. Ratios have a single computed value per cell
# (``ratio:<name>``), so none of the intensity flavours apply — the
# combo collapses to this single entry.
CALCULATED_VAL_LABEL = "Calculated Val"


def split_metric_col(col: str) -> tuple[str, str, str] | None:
    """Split ``<channel>_<metric_suffix>`` into ``(channel, metric_key, label)``.

    Returns ``None`` when *col* does not end in one of the known intensity
    or smFISH suffixes (e.g. when it's a ratio key or an unrelated column).
    The smFISH branch uses the legacy ``"(spots)"`` formatting so existing
    stats output stays stable.
    """
    if col.endswith(SMFISH_SUFFIX):
        ch = col[: -len(SMFISH_SUFFIX)]
        return ch, "smfish_count", f"{ch.upper()} (spots)"
    for suf, key in zip(INTENSITY_SUFFIXES, INTENSITY_METRIC_KEYS, strict=True):
        if col.endswith(suf):
            ch = col[: -len(suf)]
            return ch, key, f"{ch.upper()} {METRIC_KEY_TO_LABEL[key]}"
    return None


# ── Title qualifiers ────────────────────────────────────────────────────────
#
# Plot titles say things like "(above threshold)" / "Fraction above threshold"
# because the bar / line / heat-map data path filters cells via the channel's
# ThreshFracOn cutoff. smFISH counts now honour that cutoff too (a cell is "on"
# when its spot count exceeds ThreshFracOn), so the generic "above threshold"
# wording is accurate for every metric. These helpers keep the ``metric_key``
# param for API stability and in case a future metric needs distinct phrasing.


def threshold_qualifier(metric_key: str) -> str:
    """Suffix for the mean-axis title (' (above threshold)')."""
    return " (above threshold)"


def fraction_title(metric_key: str) -> str:
    """Title for the fraction-of-cells axis."""
    return "Fraction of Cells Above Threshold"


def events_per_fov_title(metric_key: str, band_lbl: str) -> str:
    """Title for the per-FOV events axis."""
    return f"Mean events above threshold per FOV ± {band_lbl}"


def events_total_title(metric_key: str) -> str:
    """Title for the per-timepoint total events axis."""
    return "Events above threshold (N)"
