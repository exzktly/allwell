"""Ratio metric data model.

A ``RatioMetric`` describes a virtual per-cell value computed at read time as
``numerator / denominator`` from two real CSV columns. Ratios appear as
virtual channels in the plot tabs alongside real fluorescence channels.

The encoding for a ratio "key" used in place of a CSV column name is
``"ratio:<name>"`` so it can never collide with a real column.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional


RATIO_PREFIX = "ratio:"


@dataclass
class RatioMetric:
    """User-defined ratio of two channel/metric pairs.

    ``epsilon`` is added to the denominator before division to avoid /0.
    When ``epsilon`` is 0 (the default) and the denominator is 0, the
    resolver returns NaN so the cell is silently dropped from plots.
    """

    name: str
    numerator_channel: str
    numerator_metric: str          # "mean_intensity" | "total_intensity" | "smfish_count" | ...
    denominator_channel: str
    denominator_metric: str
    epsilon: float = 0.0

    def key(self) -> str:
        return f"{RATIO_PREFIX}{self.name}"

    def display_label(self) -> str:
        return f"{self.numerator_channel.upper()}/{self.denominator_channel.upper()}"

    def numerator_col(self) -> str:
        return f"{self.numerator_channel}_{self.numerator_metric}"

    def denominator_col(self) -> str:
        return f"{self.denominator_channel}_{self.denominator_metric}"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "numerator_channel": self.numerator_channel,
            "numerator_metric": self.numerator_metric,
            "denominator_channel": self.denominator_channel,
            "denominator_metric": self.denominator_metric,
            "epsilon": float(self.epsilon),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RatioMetric":
        return cls(
            name=str(data.get("name", "")).strip(),
            numerator_channel=str(data.get("numerator_channel", "")).strip().lower(),
            numerator_metric=str(data.get("numerator_metric", "mean_intensity")).strip().lower(),
            denominator_channel=str(data.get("denominator_channel", "")).strip().lower(),
            denominator_metric=str(data.get("denominator_metric", "mean_intensity")).strip().lower(),
            epsilon=float(data.get("epsilon", 0.0) or 0.0),
        )


def is_ratio_key(key: str) -> bool:
    return isinstance(key, str) and key.startswith(RATIO_PREFIX)


def ratio_name_from_key(key: str) -> str:
    if is_ratio_key(key):
        return key[len(RATIO_PREFIX):]
    return ""


def ratios_to_dict(ratios: Iterable[RatioMetric]) -> List[dict]:
    return [r.to_dict() for r in ratios]


def ratios_from_dict(data: object) -> List[RatioMetric]:
    if not isinstance(data, list):
        return []
    out: List[RatioMetric] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        try:
            ratio = RatioMetric.from_dict(entry)
        except (TypeError, ValueError):
            continue
        if ratio.name and ratio.numerator_channel and ratio.denominator_channel:
            out.append(ratio)
    return out


def build_ratio_index(ratios: Iterable[RatioMetric]) -> Dict[str, RatioMetric]:
    """Return a dict keyed by the ``ratio:<name>`` form for fast lookups."""
    out: Dict[str, RatioMetric] = {}
    for r in ratios:
        if r.name:
            out[r.key()] = r
    return out
