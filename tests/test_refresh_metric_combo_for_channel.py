"""Characterization + ctx-native pin for ``refresh_metric_combo_for_channel``.

``refresh_metric_combo_for_channel(ctx)`` reshapes every per-tab Property combo
(``_plotting_metric_cb`` / ``_metric_cb`` / ``_metric_cb_bar``) to match the
active channel. This test pins the *data path*: given a ctx whose channel slice
names a real channel, each registered Property combo is repopulated with the
full ``METRIC_ORDER`` intensity set, and the composed channel label comes off
``ctx.state.channel`` (via the ctx-native ``active_channel_label``).

It drives the function through ``ctx.widgets`` (registered combos) + ``ctx.state``
so it holds both before and after the A8/D1 boundary-unwrap removal.
"""

from __future__ import annotations

from types import SimpleNamespace

from well_viewer.core.widget_registry import WidgetRegistry
from well_viewer.core.channel_state_controller import (
    refresh_metric_combo_for_channel,
    active_channel_label,
)


class _FakeCombo:
    def __init__(self) -> None:
        self.items: list[str] = []
        self._cur = ""

    def currentText(self) -> str:
        return self._cur

    def blockSignals(self, _b):
        return False

    def clear(self) -> None:
        self.items = []

    def addItem(self, t) -> None:
        self.items.append(t)

    def addItems(self, ts) -> None:
        self.items.extend(ts)

    def setEnabled(self, _b) -> None:
        pass

    def setCurrentText(self, t) -> None:
        self._cur = t

    def setCurrentIndex(self, i) -> None:
        self._cur = self.items[i] if 0 <= i < len(self.items) else ""

    def findText(self, t) -> int:
        return self.items.index(t) if t in self.items else -1

    def model(self):
        return None  # skips the smFISH enable/disable item-model path


class _Chan:
    active_channel = "gfp"
    active_val_col = "gfp_mean_intensity"
    active_metric = "mean_intensity"
    smfish_channels: list[str] = []
    ratio_metrics: list = []
    label_to_channel_key: dict[str, str] = {}


class _State:
    channel = _Chan()


def _make_ctx():
    """A fake app + ctx wired like the real one: the registry stamps combos onto
    the app (legacy ``app._metric_cb`` readers) *and* exposes them via
    ``ctx.widgets`` (the ctx-native path)."""
    app = SimpleNamespace()
    state = _State()
    app._app_state = state
    reg = WidgetRegistry(app)
    combos = {
        name: reg.register(name, _FakeCombo())
        for name in ("_plotting_metric_cb", "_metric_cb", "_metric_cb_bar")
    }
    ctx = SimpleNamespace(app=app, state=state, widgets=reg)
    app._ctx = ctx
    # Legacy behaviour hook the pre-conversion body reaches through ctx.app.
    app._active_channel_label = lambda: active_channel_label(ctx)
    return ctx, combos


def test_active_channel_label_is_the_channel_token_uppercased():
    ctx, _ = _make_ctx()
    assert active_channel_label(ctx) == "GFP"


def test_refresh_repopulates_every_property_combo_with_intensity_metrics():
    ctx, combos = _make_ctx()
    refresh_metric_combo_for_channel(ctx)
    # A real (non-ratio, non-shape) channel restores the full intensity set to
    # every per-tab Property combo.
    for name, cb in combos.items():
        assert "Mean Intensity" in cb.items, f"{name} missing Mean Intensity"
        assert "Total Intensity" in cb.items, f"{name} missing Total Intensity"


def test_refresh_is_a_noop_when_no_combos_are_registered():
    """No registered Property combos → the function must not raise (each combo
    resolves to ``None`` and is skipped)."""
    app = SimpleNamespace()
    state = _State()
    app._app_state = state
    reg = WidgetRegistry(app)
    ctx = SimpleNamespace(app=app, state=state, widgets=reg)
    app._ctx = ctx
    app._active_channel_label = lambda: active_channel_label(ctx)
    refresh_metric_combo_for_channel(ctx)  # must not raise
