"""ThreshFracOn plotting-tab quick-edit.

The plotting ctxbar carries a Stepper that edits the *active channel's*
``thresh_frac_on`` (the per-channel ON/OFF intensity threshold) by writing
through the Cell Gating tab's field — the single source of truth — so persistence
and every consumer stay consistent. Covers the controller layer with fakes, then
the real wiring on an offscreen ``WellViewerApp``.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


from well_viewer.core.channel_state_controller import (
    get_thresh_frac_on,
    refresh_thresh_frac_quick_edit,
    set_thresh_frac_on,
)


# ── controller layer (fakes only for the Qt widgets) ─────────────────────────

class _FakeEdit:
    """Minimal stand-in for the Cell Gating ``QLineEdit`` (text + blockSignals)."""

    def __init__(self, text: str = "50.0") -> None:
        self._text = text
        self.blocked = False

    def text(self) -> str:
        return self._text

    def setText(self, t: str) -> None:
        self._text = str(t)

    def blockSignals(self, b: bool) -> bool:
        prev = self.blocked
        self.blocked = b
        return prev


class _FakeStepper:
    def __init__(self, value: float = 0.0) -> None:
        self._value = value
        self.blocked = False

    def value(self) -> float:
        return self._value

    def setValue(self, v: float) -> None:
        self._value = float(v)

    def blockSignals(self, b: bool) -> bool:
        prev = self.blocked
        self.blocked = b
        return prev


class _FakeChannel:
    active_channel = "gfp"
    threshold = 50.0


class _FakeDataset:
    data_dir = "/tmp/somewhere"


class _FakeSignal:
    def emit(self, *args) -> None:
        pass


class _FakeBus:
    plot_options_changed = _FakeSignal()


class _FakeState:
    def __init__(self) -> None:
        from types import SimpleNamespace
        from well_viewer.core.cell_gating_state import CellGatingState
        self.channel = _FakeChannel()
        self.dataset = _FakeDataset()
        # Doubles as the plot_options_changed emit payload AND the source the
        # ctx-native invalidate_stats_cache → refresh_fov_btn_state reads.
        self.plot = SimpleNamespace(use_fov_replicates=False)
        # invalidate_stats_cache(ctx) discards the group-stats cache; the double
        # only needs the invalidate() method to exist.
        self.stats_cache = SimpleNamespace(invalidate=lambda: None)
        # R6: gate values live on the typed slice; the edits are the input
        # surface, synced into the slice by the change handlers.
        self.cell_gating = CellGatingState()

    def feature_slice(self, name):
        # No Image Browser in this controller-layer double → no review_image
        # slice (invalidate_stats_cache skips the override-version bump).
        return None


class _FakeApp:
    """A SimpleNamespace-ish app exposing exactly what the controller touches."""

    def __init__(self) -> None:
        from types import SimpleNamespace
        self._app_state = _FakeState()
        self._cell_gating_thresh_frac_edits = {
            "gfp": _FakeEdit("50.0"),
            "mcherry": _FakeEdit("50.0"),
        }
        self.redraws = 0
        self._event_bus = _FakeBus()
        # The gate accessors read the slice via ctx (post-R6 pure state); the
        # cell_gating change handler also reaches ctx.bus (plot_options_changed)
        # and ctx.runtime.cell_gating (the edit banks stamped on app._cell_gating_*).
        from well_viewer.core.app_runtime import AppRuntime
        self._ctx = SimpleNamespace(
            state=self._app_state, app=self, bus=self._event_bus,
            runtime=AppRuntime(self),
        )

    def _redraw(self) -> None:
        self.redraws += 1

    def _invalidate_stats_cache(self) -> None:
        # No-op stand-in: the cell-gating change handler invalidates the stats
        # cache on the real app; the double only needs the method to exist.
        pass


def test_set_writes_through_active_channel_edit(monkeypatch):
    app = _FakeApp()
    # The cell-gating change handler fans out via the ctx-native redraw_app
    # orchestrator (was ctx.app._redraw()); route it back to the fake's counter.
    import well_viewer.features.grouping.cell_gating_tab_view as cg
    monkeypatch.setattr(cg, "redraw_app", lambda ctx: app._redraw())
    set_thresh_frac_on(app, 123.0)            # active channel == gfp
    assert app._cell_gating_thresh_frac_edits["gfp"].text() == "123.0"
    assert get_thresh_frac_on(app._ctx) == 123.0
    assert get_thresh_frac_on(app._ctx, "mcherry") == 50.0   # untouched
    assert app.redraws == 1                    # change handler redrew once
    # The edit's signals were restored (not left blocked) after the write.
    assert app._cell_gating_thresh_frac_edits["gfp"].blocked is False


def test_set_targets_explicit_channel(monkeypatch):
    app = _FakeApp()
    import well_viewer.features.grouping.cell_gating_tab_view as cg
    monkeypatch.setattr(cg, "redraw_app", lambda ctx: app._redraw())
    set_thresh_frac_on(app, 77.0, channel="mcherry")
    assert get_thresh_frac_on(app._ctx, "mcherry") == 77.0
    assert get_thresh_frac_on(app._ctx, "gfp") == 50.0


def test_set_noop_without_dataset():
    app = _FakeApp()
    app._app_state.dataset.data_dir = ""
    set_thresh_frac_on(app, 999.0)
    assert get_thresh_frac_on(app._ctx, "gfp") == 50.0
    assert app.redraws == 0


def test_refresh_syncs_stepper_with_signals_blocked():
    app = _FakeApp()
    # R6: a committed value lives on the typed slice (setText alone is an
    # uncommitted keystroke — the change handler is what syncs it).
    app._app_state.cell_gating.thresh_frac_on["gfp"] = 88.0
    stepper = _FakeStepper(0.0)
    app._plotting_thresh_frac_stepper = stepper
    refresh_thresh_frac_quick_edit(app)
    assert stepper.value() == 88.0
    # Signals are restored to unblocked after the display sync.
    assert stepper.blocked is False


def test_refresh_follows_active_channel():
    app = _FakeApp()
    app._app_state.cell_gating.thresh_frac_on["gfp"] = 10.0
    app._app_state.cell_gating.thresh_frac_on["mcherry"] = 20.0
    app._plotting_thresh_frac_stepper = _FakeStepper(0.0)

    refresh_thresh_frac_quick_edit(app)
    assert app._plotting_thresh_frac_stepper.value() == 10.0
    app._app_state.channel.active_channel = "mcherry"
    refresh_thresh_frac_quick_edit(app)
    assert app._plotting_thresh_frac_stepper.value() == 20.0


def test_refresh_noop_without_stepper():
    app = _FakeApp()                            # no _plotting_thresh_frac_stepper
    refresh_thresh_frac_quick_edit(app)         # must not raise


# ── real wiring on an offscreen WellViewerApp ────────────────────────────────

def test_ctxbar_stepper_exists(loaded_app):
    assert hasattr(loaded_app, "_plotting_thresh_frac_stepper")
    assert hasattr(loaded_app, "_plotting_thresh_frac_lbl")
    # Default before any edit.
    assert loaded_app._plotting_thresh_frac_stepper.value() == 50.0


def test_stepper_edit_writes_through_to_cell_gating(loaded_app):
    # Simulate a user nudge: setValue emits valueChanged → _set_thresh_frac_on.
    loaded_app._plotting_thresh_frac_stepper.setValue(123.0)
    assert loaded_app._get_thresh_frac_on("gfp") == 123.0
    # The Cell Gating tab was force-built and its field is the source of truth.
    edit = loaded_app._cell_gating_thresh_frac_edits.get("gfp")
    assert edit is not None and float(edit.text()) == 123.0


def test_stepper_follows_channel_switch(loaded_app):
    from well_viewer.core.channel_state_controller import set_active_channel

    loaded_app._set_thresh_frac_on(123.0, "gfp")
    loaded_app._set_thresh_frac_on(77.0, "mcherry")
    set_active_channel(loaded_app, "mcherry")
    assert loaded_app._plotting_thresh_frac_stepper.value() == 77.0
    set_active_channel(loaded_app, "gfp")
    assert loaded_app._plotting_thresh_frac_stepper.value() == 123.0


def test_stepper_disabled_on_scatter_subtab(loaded_app):
    # S2: on the Scatter sub-tabs the global Channel/Property/Threshold controls
    # stay visible but DIMMED (disabled) — with an inline note pointing at the
    # per-axis selectors — instead of vanishing (a row that disappeared read as
    # a glitch). They re-enable on the other plot sub-tabs.
    assert loaded_app._notebook.setCurrentByName("Plotting")
    stepper = loaded_app._plotting_thresh_frac_stepper
    lbl = loaded_app._plotting_thresh_frac_lbl

    assert loaded_app._plotting_notebook.setCurrentByName("Scatter Plot")
    assert not stepper.isHidden() and not lbl.isHidden()
    assert not stepper.isEnabled() and not lbl.isEnabled()
    # The explanatory note is shown on scatter (isHidden reflects the explicit
    # visibility flag without needing the offscreen window to be shown).
    assert not loaded_app._plotting_scatter_note.isHidden()

    assert loaded_app._plotting_notebook.setCurrentByName("Bar Plots")
    assert stepper.isEnabled() and lbl.isEnabled()
    assert loaded_app._plotting_scatter_note.isHidden()
