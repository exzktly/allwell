"""The metric registry (``domain/metric_registry.py``) is the single source of
truth for the per-cell measurement metrics.

Acid test: adding a metric should be a *one-line* change to ``METRICS`` that
flows to every derived table (the ``data/metric_labels`` maps + ordering, the
gate-exemption rule) and surfaces in the Property combo — no other file edited.
These tests pin that wiring so the registry can't quietly stop being the single
source.
"""

from __future__ import annotations

import review.domain.metric_registry as reg
from review.domain.metric_registry import (
    MetricDef,
    column_gating_exempt,
    intensity_keys,
    metric_key_gating_exempt,
    metric_labels_in_order,
)


def test_metric_labels_tables_are_derived_from_the_registry():
    """``data/metric_labels`` must compute its public tables from ``METRICS`` —
    not maintain a parallel hand-written copy."""
    from review.data import metric_labels as ml

    assert ml.METRIC_ORDER == [m.label for m in reg.METRICS]
    assert ml.METRIC_LABEL_TO_KEY == {m.label: m.key for m in reg.METRICS}
    assert ml.METRIC_KEY_TO_LABEL == {m.key: m.label for m in reg.METRICS}
    assert ml.INTENSITY_METRIC_KEYS == tuple(
        m.key for m in reg.METRICS if m.kind == "intensity"
    )
    # Every intensity suffix is "_<key>" for an intensity metric.
    assert ml.INTENSITY_SUFFIXES == tuple(f"_{k}" for k in intensity_keys())


def test_gate_exemption_follows_the_registry_flag():
    for m in reg.METRICS:
        assert metric_key_gating_exempt(m.key) is m.gating_exempt
        assert column_gating_exempt(f"gfp_{m.key}") is m.gating_exempt
    # Unknown / non-metric keys are never exempt.
    assert metric_key_gating_exempt("not_a_metric") is False
    assert column_gating_exempt("gfp_mean_intensity") is False
    # Shape/morphology columns are exempt via is_shape_column even though they
    # aren't combo metrics.
    assert column_gating_exempt("shape_area") is True


def test_adding_a_metric_flows_everywhere(monkeypatch):
    """Simulate the one-line registry edit and prove it surfaces in (a) the
    derived ordering, (b) the gate-exemption rule, and (c) a Property combo."""
    fake = MetricDef("median_intensity", "Median Intensity", "intensity", False)
    fake_count = MetricDef("foci_count", "Foci Count", "count", True)
    extended = (*reg.METRICS, fake, fake_count)

    # (a) + (b): the pure derivation helpers pick the new metrics up.
    monkeypatch.setattr(reg, "METRICS", extended)
    monkeypatch.setattr(reg, "METRIC_BY_KEY", {m.key: m for m in extended})
    monkeypatch.setattr(reg, "METRIC_BY_LABEL", {m.label: m for m in extended})
    assert "Median Intensity" in metric_labels_in_order()
    assert "Foci Count" in metric_labels_in_order()
    assert "median_intensity" in intensity_keys()
    assert "foci_count" not in intensity_keys()
    assert metric_key_gating_exempt("foci_count") is True
    assert metric_key_gating_exempt("median_intensity") is False
    assert column_gating_exempt("gfp_foci_count") is True

    # (c): the Property combo renders whatever METRIC_ORDER holds, so the new
    # labels appear there too. Re-derive metric_labels' table from the patched
    # registry (mirrors what a real source edit produces at import time) and
    # drive populate_metric_combo with a minimal fake combo.
    from review.data import metric_labels as ml
    from review.core.channel_state_controller import populate_metric_combo

    monkeypatch.setattr(ml, "METRIC_ORDER", metric_labels_in_order())

    class _FakeCombo:
        def __init__(self):
            self.items: list[str] = []
            self._cur = ""
        def currentText(self):
            return self._cur
        def blockSignals(self, _b):
            return False
        def clear(self):
            self.items = []
        def addItem(self, t):
            self.items.append(t)
        def addItems(self, ts):
            self.items.extend(ts)
        def setEnabled(self, _b):
            pass
        def setCurrentText(self, t):
            self._cur = t
        def setCurrentIndex(self, i):
            self._cur = self.items[i] if 0 <= i < len(self.items) else ""
        def findText(self, t):
            return self.items.index(t) if t in self.items else -1
        def model(self):
            return None  # skips the smFISH enable/disable item path

    class _Chan:
        active_metric = "mean_intensity"
        smfish_channels: list[str] = []
        label_to_channel_key: dict[str, str] = {}   # X5: typed on ChannelState

    class _State:
        channel = _Chan()

    class _App:
        _app_state = _State()

    cb = _FakeCombo()
    from types import SimpleNamespace
    _a = _App()
    # populate_metric_combo reads the channel slice off ctx.state (A8).
    _a._ctx = SimpleNamespace(app=_a, state=_a._app_state)
    populate_metric_combo(_a._ctx, cb, channel_entry="gfp")
    assert "Median Intensity" in cb.items
    assert "Foci Count" in cb.items


# ── which metrics carry a cutoff of their own ────────────────────────────────

def test_only_metrics_with_their_own_cutoff_can_be_thresholded():
    """The question the threshold-based aggregations need answered.

    Not the same as ``cutoff_kind_for_metric_key``: ``total_intensity`` and
    ``mean_intensity`` are both ``CUTOFF_INTENSITY``, and only the second is the
    column ThreshFracOn is defined against. Cutting a total (area × mean) with a
    mean-scale cutoff is the same units error that feeding a spot tally a
    brightness was.
    """
    from review.domain.metric_registry import metric_has_own_cutoff

    assert metric_has_own_cutoff("mean_intensity") is True   # ThreshFracOn
    assert metric_has_own_cutoff("smfish_count") is True     # ThreshCountOn
    for key in ("total_intensity", "max_intensity", "min_intensity",
                "std_intensity"):
        assert metric_has_own_cutoff(key) is False, key
    # Not a metric at all (a shape column, a ratio key, a typo).
    assert metric_has_own_cutoff("shape_area") is False
    assert metric_has_own_cutoff("") is False


def test_a_new_count_metric_gets_a_cutoff_without_a_second_edit(monkeypatch):
    """Derived from ``kind``, so the registry stays the one place to declare it.

    A literal ``key in {"mean_intensity", "smfish_count"}`` would pass every
    other test in this file and quietly exclude the next count metric someone
    adds.
    """
    from review.domain.metric_registry import metric_has_own_cutoff

    fake_count = MetricDef("foci_count", "Foci Count", "count", True)
    fake_intensity = MetricDef("median_intensity", "Median Intensity",
                               "intensity", False)
    extended = (*reg.METRICS, fake_count, fake_intensity)
    monkeypatch.setattr(reg, "METRICS", extended)
    monkeypatch.setattr(reg, "METRIC_BY_KEY", {m.key: m for m in extended})

    assert metric_has_own_cutoff("foci_count") is True
    assert metric_has_own_cutoff("median_intensity") is False
