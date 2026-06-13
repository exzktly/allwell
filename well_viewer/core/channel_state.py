"""Channel / threshold domain state.

The active fluorescent channel, its derived value column, the smFISH and ratio
catalogues, and the threshold range — together with the invariants that keep
them consistent. Before this object the same logic was spread across
``channel_state_controller`` and ``runtime_app``; this module is the single
place those invariants live.

Qt-free: imports only stdlib and ``ratio_models``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from well_viewer.domain.ratio_models import (
    RatioMetric,
    build_ratio_index,
    is_ratio_key,
    ratio_name_from_key,
)
from core.shape_features import is_shape_column


@dataclass
class ChannelState:
    """Active-channel state with enforced invariants.

    ``active_val_col`` is *derived*: ``<channel>_<metric>`` for a real channel,
    or a ``ratio:<name>`` key when a ratio is active. The mutator methods below
    are the only place that derivation is performed — call them rather than
    assigning the fields piecemeal.
    """

    fluor_channels: list[str] = field(default_factory=list)
    review_image_channels: list[str] = field(default_factory=list)
    smfish_channels: set[str] = field(default_factory=set)
    # Channel-independent ``shape_*`` columns present in the loaded CSVs. Like
    # ratios, a shape feature is a single value per cell selectable in the plot
    # dropdown; its ``active_val_col`` is the column name itself.
    shape_features: list[str] = field(default_factory=list)

    active_channel: str = "gfp"
    active_image_channel: str = "gfp"
    active_metric: str = "mean_intensity"
    active_val_col: str = "gfp_mean_intensity"

    ratio_metrics: list[RatioMetric] = field(default_factory=list)
    ratio_index: dict[str, RatioMetric] = field(default_factory=dict)

    threshold_min: float = 0.0
    threshold_max: float = 1.0
    threshold: float = 50.0
    threshold_range_cache: dict[str, tuple[float, float]] = field(default_factory=dict)

    # ── derived value column ─────────────────────────────────────────────────

    def derive_val_col(self) -> str:
        """The ``<channel>_<metric>`` column for the active non-ratio channel."""
        return f"{self.active_channel}_{self.active_metric}"

    def is_shape_active(self) -> bool:
        """True when the active value column is a ``shape_*`` feature."""
        return is_shape_column(self.active_val_col)

    def sync_val_col(self) -> None:
        """Recompute ``active_val_col`` from channel + metric — unless a ratio
        or shape feature is active, in which case the key already lives there."""
        if not is_ratio_key(self.active_val_col) and not self.is_shape_active():
            self.active_val_col = self.derive_val_col()

    def normalize_metric(self) -> None:
        """Drop a stale ``smfish_count`` metric when the active channel carries
        no smFISH data, so the metric never names a column that cannot exist."""
        if (self.active_metric == "smfish_count"
                and self.active_channel not in self.smfish_channels):
            self.active_metric = "mean_intensity"

    # ── channel switching ────────────────────────────────────────────────────

    def set_active_channel(self, channel: str) -> bool:
        """Switch to a real (non-ratio) channel.

        Resets a stale ratio / smFISH metric so the new channel composes a real
        ``<channel>_mean_intensity`` column. Returns True when the active
        channel actually changed.
        """
        was_ratio = is_ratio_key(self.active_val_col)
        if channel == self.active_channel and not was_ratio:
            return False
        self.active_channel = channel
        if was_ratio or channel not in self.smfish_channels:
            self.active_metric = "mean_intensity"
        self.active_val_col = self.derive_val_col()
        return True

    def set_active_shape_feature(self, column: str) -> bool:
        """Switch to a ``shape_*`` virtual channel. The column name is the value
        column directly (no ``<channel>_<metric>`` composition). Returns True
        when the active value column actually changed."""
        if column == self.active_val_col:
            return False
        self.active_channel = column
        self.active_val_col = column
        return True

    def set_active_ratio(self, ratio: RatioMetric) -> bool:
        """Switch to a user-defined ratio channel. Returns True when the active
        value column actually changed."""
        new_val_col = ratio.key()
        if new_val_col == self.active_val_col:
            return False
        self.active_channel = ratio.name
        self.active_val_col = new_val_col
        return True

    def resolve_ratio(self, channel_key: str) -> RatioMetric | None:
        """Resolve a ``ratio:<name>`` key against the catalogue (None if absent)."""
        name = ratio_name_from_key(channel_key)
        return next((r for r in self.ratio_metrics if r.name == name), None)

    def set_ratio_metrics(self, ratios: list[RatioMetric]) -> None:
        """Replace the ratio catalogue and rebuild the ``ratio:<name>`` index in
        lockstep — the two must never disagree."""
        self.ratio_metrics = list(ratios)
        self.ratio_index = build_ratio_index(self.ratio_metrics)

    # ── threshold range ──────────────────────────────────────────────────────

    def cached_range(self, val_col: str) -> tuple[float, float] | None:
        return self.threshold_range_cache.get(val_col)

    def cache_range(self, val_col: str, lo: float, hi: float) -> None:
        self.threshold_range_cache[val_col] = (lo, hi)

    def apply_range(self, lo: float, hi: float) -> None:
        """Set the (min, max) intensity range the threshold slider spans."""
        self.threshold_min = lo
        self.threshold_max = hi
