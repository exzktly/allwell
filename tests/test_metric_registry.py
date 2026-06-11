"""The metric registry (``domain/metric_registry.py``) is the single source of
truth for the per-cell measurement metrics.

Acid test: adding a metric should be a *one-line* change to ``METRICS`` that
flows to every derived table (the ``data/metric_labels`` maps + ordering, the
gate-exemption rule) and surfaces in the Property combo — no other file edited.
These tests pin that wiring so the registry can't quietly stop being the single
source.
"""

from __future__ import annotations

import well_viewer.domain.metric_registry as reg
from well_viewer.domain.metric_registry import (
    MetricDef,
    column_gating_exempt,
    intensity_keys,
    metric_key_gating_exempt,
    metric_labels_in_order,
)


def test_metric_labels_tables_are_derived_from_the_registry():
    """``data/metric_labels`` must compute its public tables from ``METRICS`` —
    not maintain a parallel hand-written copy."""
    from well_viewer.data import metric_labels as ml

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
    from well_viewer.data import metric_labels as ml
    from well_viewer.core.channel_state_controller import populate_metric_combo

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

    class _State:
        channel = _Chan()

    class _App:
        _label_to_channel_key: dict[str, str] = {}
        _app_state = _State()

    cb = _FakeCombo()
    populate_metric_combo(_App(), cb, channel_entry="gfp")
    assert "Median Intensity" in cb.items
    assert "Foci Count" in cb.items
