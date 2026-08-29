"""The heat map's aggregation-metric labels, and which of them take a cutoff.

The four labels the heat map's aggregation combo offers. They live here rather
than in the feature because they are **vocabulary**, not behaviour: the labels
are the values stored in view state and read back, the text
``core/channel_state_controller`` enables and disables as the active Property
changes, and the strings the heat-map renderer branches on. A constant shared
between a feature and core is exactly the case ``core/dnd``'s ``WELL_MIME``
already settled — core needs the token, not the feature that acts on it.

The heat-map controller re-exports these under its own names, so the feature's
callers are unchanged.
"""

from __future__ import annotations

METRIC_MEAN = "Mean above threshold"
METRIC_MEAN_ALL = "Mean (all cells)"
METRIC_FRACTION = "Fraction above threshold"
METRIC_COUNT = "Cell count"
#: Legacy; not exposed in :data:`METRIC_OPTIONS` — ratios are picked via the
#: channel dropdown.
METRIC_RATIO = "Ratio value"

# "Mean above threshold" applies the per-channel ThreshFracOn cut (only
# meaningful for MFI-based columns); "Mean (all cells)" pools every cell
# that passes cell-area + FluorGating without the per-channel threshold
# filter, which is the right default when the active Property isn't MFI.
METRIC_OPTIONS = [METRIC_MEAN, METRIC_MEAN_ALL, METRIC_FRACTION, METRIC_COUNT]

#: The metrics whose value depends on the active Property's ON cutoff, so they
#: are offered only while that Property carries one. The pair is named here
#: rather than spelled out at the two places that gate on it, because a list
#: that drifts from :data:`METRIC_OPTIONS` disables the wrong row.
METRICS_NEEDING_CUTOFF = (METRIC_MEAN, METRIC_FRACTION)
