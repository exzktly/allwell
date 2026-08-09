"""Single declarative catalogue of the per-cell measurement *metrics*.

Adding (or renaming) a metric used to be a 6–8-file change: its key, its combo
label, its display order, whether it's an intensity flavour, and whether it
bypasses the per-channel fluorescence gate were each spelled out separately in
``data/metric_labels.py``, ``core/channel_state_controller.populate_metric_combo``,
and inline ``col.endswith("_smfish_count")`` checks scattered through the plot
controllers. This module collapses all of that into one table: declare a
:class:`MetricDef` here and the combos, label maps, suffix lists, and
gate-exemption rule all follow.

This is foundational, Qt-free business data — it lives in ``domain`` so every
layer (``data/metric_labels`` derives its public tables from here; the plot
controllers read the gate-exemption helper) can import it *downward*.

The CSV produced by ``process_microscopy`` carries the five intensity columns
per fluorescence channel (``_total_intensity`` / ``_mean_intensity`` /
``_max_intensity`` / ``_min_intensity`` / ``_std_intensity``) plus an optional
``_smfish_count`` on smFISH channels. Ratios and shape/morphology features are
*not* listed here — they are single computed values per cell with no
intensity-flavour choice, so their Property combo collapses to a single entry
(handled by ``populate_metric_combo`` via ``is_ratio_key`` / ``is_shape_column``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricDef:
    """One selectable per-cell measurement.

    ``key`` is the CSV column suffix (``<channel>_<key>``) and the value stored
    in ``app._app_state.channel.active_metric``. ``label`` is the Property-combo
    display text. ``kind`` is ``"intensity"`` (the fluorescence flavours that
    share the threshold/fraction machinery) or ``"count"`` (integer tallies like
    smFISH spots). ``gating_exempt`` marks metrics that must bypass the
    per-channel fluorescence gate — an intensity floor like 50–1500 would wrongly
    exclude every cell of a non-intensity metric.
    """

    key: str
    label: str
    kind: str           # "intensity" | "count"
    gating_exempt: bool


# The order here is the order the Property combos present. Append new metrics to
# extend the dropdown everywhere at once.
METRICS: tuple[MetricDef, ...] = (
    MetricDef("mean_intensity",  "Mean Intensity",  "intensity", False),
    MetricDef("total_intensity", "Total Intensity", "intensity", False),
    MetricDef("max_intensity",   "Max Intensity",   "intensity", False),
    MetricDef("min_intensity",   "Min Intensity",   "intensity", False),
    MetricDef("std_intensity",   "Std Intensity",   "intensity", False),
    MetricDef("smfish_count",    "smFISH Count",    "count",     True),
)

METRIC_BY_KEY: dict[str, MetricDef] = {m.key: m for m in METRICS}
METRIC_BY_LABEL: dict[str, MetricDef] = {m.label: m for m in METRICS}


def metric_labels_in_order() -> list[str]:
    """Combo labels in declaration order (drives ``METRIC_ORDER``)."""
    return [m.label for m in METRICS]


def intensity_keys() -> tuple[str, ...]:
    """Keys of the intensity-flavour metrics (drives ``INTENSITY_METRIC_KEYS``)."""
    return tuple(m.key for m in METRICS if m.kind == "intensity")


def metric_key_gating_exempt(metric_key: str) -> bool:
    """True when the *active metric key* (e.g. ``"smfish_count"``) bypasses the
    fluorescence gate. Centralises the ``active_metric == "smfish_count"`` check
    duplicated across the line / bar / heatmap / distribution controllers — they
    OR this with the app's ``is_shape_active()`` (shape state isn't a metric key).
    """
    m = METRIC_BY_KEY.get(metric_key)
    return bool(m and m.gating_exempt)


def column_gating_exempt(col: str) -> bool:
    """True when a value column must bypass the per-channel fluorescence gate.

    Centralises the rule previously duplicated inline as
    ``col.endswith("_smfish_count") or is_shape_column(col)``: gate-exempt metric
    columns (counts) plus shape/morphology features are not intensities, so the
    fluorescence intensity-floor gate would gate out every such cell.
    """
    if not isinstance(col, str):
        return False
    for m in METRICS:
        if m.gating_exempt and col.endswith(f"_{m.key}"):
            return True
    # Shape/morphology columns aren't metrics in the combo sense, but share the
    # same "not an intensity → don't fluor-gate" property.
    from pipeline.shape_features import is_shape_column
    return is_shape_column(col)


# ── The ON cutoff a value column is measured against ────────────────────────
#
# Three kinds, because there are three scales. A ``shape_*`` morphology value has
# no ON/OFF question attached to it at all. A ``<channel>_smfish_count`` is an
# integer spot tally, typically a handful per cell. A ``<channel>_mean_intensity``
# is a fluorescence reading in the hundreds or thousands. Feeding one kind's
# cutoff to another does not merely give a wrong answer: an intensity cutoff sits
# above the entire range a count can occupy, so it empties the ON population and
# the plot draws nothing at all.

CUTOFF_NONE = "none"            # shape / morphology — no ON cutoff, keep every cell
CUTOFF_COUNT = "count"          # integer tallies — the per-channel ThreshCountOn
CUTOFF_INTENSITY = "intensity"  # fluorescence — the per-channel ThreshFracOn


def cutoff_kind_for_metric_key(metric_key: str) -> str:
    """Which ON cutoff the metric named by *metric_key* is measured against.

    Unknown keys answer :data:`CUTOFF_INTENSITY`: every metric in the combo that
    is not a declared count is an intensity flavour, and the shape features that
    would answer :data:`CUTOFF_NONE` are not metric keys at all (they are column
    names, so they reach :func:`cutoff_kind_for_column` instead).
    """
    m = METRIC_BY_KEY.get(metric_key)
    if m is not None and m.kind == "count":
        return CUTOFF_COUNT
    return CUTOFF_INTENSITY


def cutoff_kind_for_column(col: str) -> str:
    """Which ON cutoff the value column *col* is measured against.

    This is the single predicate; ``channel_state_controller.on_cutoff_for_column``
    is the single resolver that turns its answer into a number, and every
    aggregate plot path reaches its cutoff through that resolver rather than
    deciding for itself. ``tests/test_on_cutoff_sweep.py`` fails when a new path
    asks the question its own way.

    Ratio keys answer :data:`CUTOFF_INTENSITY`. They are computed values with no
    cutoff of their own, and each call site already handles them in front of the
    resolver — the three that exist disagree with one another, so folding them in
    here would silently change what those plots draw.
    """
    if not isinstance(col, str):
        return CUTOFF_INTENSITY
    for m in METRICS:
        if m.kind == "count" and col.endswith(f"_{m.key}"):
            return CUTOFF_COUNT
    from pipeline.shape_features import is_shape_column
    if is_shape_column(col):
        return CUTOFF_NONE
    return CUTOFF_INTENSITY


def channel_supports_smfish(channel_entry: str, smfish_channels) -> bool:
    """True when *channel_entry* names one of the dataset's smFISH channels.

    Every Property combo greys out ``smFISH Count`` for channels that carry no
    ``<channel>_smfish_count`` column, and each combo asks this question about
    whatever string its own channel dropdown holds. Those strings disagree on
    case: the global combo passes the normalised token (``"gfp"``) while the
    scatter, stats, and batch-export combos pass the *displayed* label
    (``"GFP"``), because their dropdowns are built from upper-cased channel
    names. ``smfish_channels`` is always normalised, so a raw ``in`` test left
    smFISH Count permanently disabled on the upper-case side. Case-fold both
    ends here, once, rather than at each call site.
    """
    if not channel_entry:
        return False
    target = str(channel_entry).strip().lower()
    return any(str(c).strip().lower() == target for c in (smfish_channels or ()))
