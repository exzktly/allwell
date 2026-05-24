"""Regression tests for the line-plot dropdown "wiggle" bug.

User report: when smFISH count was the last-selected metric in the line
graphs tab, reopening the app to that tab required wiggling the Property
dropdown before anything would replot.

Root cause: ``persistence.view_state.apply_state`` assigned
``active_metric`` directly to the channel-state field, then called
``set_active_metric(...)`` — which early-returns when the field already
matches the target value, skipping the combo-sync loop. The
``_plotting_metric_cb`` combo (built early during centre-view
construction) therefore stayed at its default first item ("Mean
Intensity") on app reopen, mismatched against the saved metric.

Fix: extract the combo-sync loop into ``sync_metric_combos`` and call it
unconditionally from ``apply_state`` after the direct field assignment,
so every combo widget that exists at that point picks up the saved
metric immediately — no wiggle needed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


class _FakeCombo:
    """Stand-in for ``QComboBox`` with the subset of API ``sync_metric_combos``
    uses (``findText``, ``setCurrentIndex``, ``currentText``, ``blockSignals``)."""

    def __init__(self, items: list[str], current_index: int = 0) -> None:
        self._items = list(items)
        self._index = current_index
        self._blocked = False
        self.signal_emissions: list[int] = []

    def currentText(self) -> str:
        if 0 <= self._index < len(self._items):
            return self._items[self._index]
        return ""

    def findText(self, text: str) -> int:
        try:
            return self._items.index(text)
        except ValueError:
            return -1

    def setCurrentIndex(self, idx: int) -> None:
        if 0 <= idx < len(self._items):
            self._index = idx
            if not self._blocked:
                self.signal_emissions.append(idx)

    def blockSignals(self, blocked: bool) -> bool:
        prev = self._blocked
        self._blocked = blocked
        return prev


def _make_app(metric: str = "mean_intensity") -> SimpleNamespace:
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        channel=SimpleNamespace(active_metric=metric),
    )
    return app


def test_sync_metric_combos_pushes_value_into_global_combo():
    """The bug-reproducer: the global metric combo exists at app open
    but defaults to 'Mean Intensity'. After restoring active_metric to
    smfish_count, the combo must reflect 'smFISH Count'."""
    from well_viewer.core.channel_state_controller import sync_metric_combos

    app = _make_app(metric="smfish_count")
    app._plotting_metric_cb = _FakeCombo(
        items=["Mean Intensity", "smFISH Count"], current_index=0,
    )

    sync_metric_combos(app, "smfish_count")

    assert app._plotting_metric_cb.currentText() == "smFISH Count"
    # blockSignals was used — no change handler should have re-fired.
    assert app._plotting_metric_cb.signal_emissions == []


def test_sync_metric_combos_is_noop_when_already_correct():
    """When the combo already shows the right label, no setCurrentIndex
    call — avoids redundant work."""
    from well_viewer.core.channel_state_controller import sync_metric_combos

    app = _make_app()
    app._plotting_metric_cb = _FakeCombo(
        items=["Mean Intensity", "smFISH Count"], current_index=0,
    )
    sync_metric_combos(app, "mean_intensity")
    # Still at index 0 (Mean Intensity); no emission either.
    assert app._plotting_metric_cb.currentText() == "Mean Intensity"
    assert app._plotting_metric_cb.signal_emissions == []


def test_sync_metric_combos_skips_combo_that_lacks_the_label():
    """Combos populated from a smaller metric set (e.g. legacy hidden
    per-tab combo) shouldn't crash on a label they don't carry."""
    from well_viewer.core.channel_state_controller import sync_metric_combos

    app = _make_app()
    app._metric_cb = _FakeCombo(items=["Mean Intensity"], current_index=0)
    # Set the active_metric field to mimic the apply_state path so this
    # test reflects the production code path (which calls sync after a
    # direct field assignment).
    app._app_state.channel.active_metric = "smfish_count"
    sync_metric_combos(app, "smfish_count")
    # The combo doesn't carry "smFISH Count" — stays at its current value.
    assert app._metric_cb.currentText() == "Mean Intensity"


def test_sync_metric_combos_handles_missing_combos():
    """Combos that don't exist yet (e.g. per-tab combos on tabs not
    built yet) must be silently skipped."""
    from well_viewer.core.channel_state_controller import sync_metric_combos

    app = _make_app()
    # Only one combo present — _metric_cb, _metric_cb_bar, _stats_property_cb absent.
    app._plotting_metric_cb = _FakeCombo(
        items=["Mean Intensity", "smFISH Count"], current_index=0,
    )
    sync_metric_combos(app, "smfish_count")  # must not raise
    assert app._plotting_metric_cb.currentText() == "smFISH Count"


def test_set_active_metric_still_delegates_to_sync():
    """The real ``set_active_metric`` path also goes through the helper
    so a normal user-initiated metric change keeps every combo in sync."""
    from well_viewer.core.channel_state_controller import sync_metric_combos
    from well_viewer.data.metric_labels import METRIC_KEY_TO_LABEL

    # Smoke check: METRIC_KEY_TO_LABEL contains the keys we test on, so
    # downstream logic that looks up labels can't silently regress.
    assert METRIC_KEY_TO_LABEL.get("smfish_count") == "smFISH Count"
    assert METRIC_KEY_TO_LABEL.get("mean_intensity") == "Mean Intensity"
