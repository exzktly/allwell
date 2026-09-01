"""ON-cutoff plotting-tab quick-edit.

The plotting ctxbar carries a Stepper that edits the *active channel's* ON
cutoff by writing through the Cell Gating tab's field — the single source of
truth — so persistence and every consumer stay consistent.

Which cutoff it edits follows the selected Property: an intensity metric edits
``thresh_frac_on``, a spot count edits ``thresh_count_on``. The caption, the
range and the write target move together, because a box that displays one
number while writing another would quietly destroy the cutoff it is not
showing. Covers the controller layer with fakes, then the real wiring on an
offscreen ``WellViewerApp``.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


from well_viewer.core.channel_state_controller import (
    get_thresh_count_on,
    get_thresh_frac_on,
    refresh_thresh_frac_quick_edit,
    set_on_cutoff,
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
    """Stand-in for ``widgets.stepper.Stepper``, clamping like the real one.

    The clamp is the point: the count cutoff's default is negative, so a stepper
    left at the intensity range's ``minimum=0`` would display 0 — "cells with at
    least one spot" — for a value that means "every cell". The retarget has to
    widen the range before setting the value, and only a clamping double can
    show that it does.
    """

    def __init__(self, value: float = 0.0) -> None:
        self._value = value
        self.blocked = False
        self.minimum = 0.0
        self.maximum = 1e9
        self.decimals = 1
        self.single_step = 1.0
        self.tooltip = ""
        self.suffix = ""
        self.fixed_width = None

    def value(self) -> float:
        return self._value

    def setValue(self, v: float) -> None:
        self._value = min(max(float(v), self.minimum), self.maximum)

    def setRange(self, lo: float, hi: float) -> None:
        self.minimum, self.maximum = float(lo), float(hi)

    def setDecimals(self, d: int) -> None:
        self.decimals = int(d)

    def setSingleStep(self, s: float) -> None:
        self.single_step = float(s)

    def setToolTip(self, tip: str) -> None:
        self.tooltip = str(tip)

    def setSuffix(self, text: str) -> None:
        self.suffix = str(text or "")

    def setFixedWidth(self, w: int) -> None:
        # The real Stepper re-derives its width bounds inside setRange /
        # setDecimals / setSuffix, which is why the refresh has to re-pin this.
        self.fixed_width = int(w)

    def blockSignals(self, b: bool) -> bool:
        prev = self.blocked
        self.blocked = b
        return prev


class _FakeLabel:
    def __init__(self, text: str = "") -> None:
        self._text = text

    def text(self) -> str:
        return self._text

    def setText(self, t: str) -> None:
        self._text = str(t)


class _FakeChannel:
    active_channel = "gfp"
    active_metric = "mean_intensity"
    threshold = 50.0
    # Only "gfp" carries a spot-count column here, matching the count edit bank.
    smfish_channels = frozenset({"gfp"})


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
        # Epic-cellgating: ensure_cell_gating_tab_built() treats the fluor-gate
        # edits map as the "tab is built" signal (in production both maps are
        # created together); provide it so the quick-edit write path proceeds.
        self._cell_gating_fluor_gate_edits = {
            "gfp": _FakeEdit("0.0"),
            "mcherry": _FakeEdit("0.0"),
        }
        # Only the smFISH channels carry a count edit in production; "gfp" plays
        # that part here.
        self._cell_gating_thresh_count_edits = {"gfp": _FakeEdit("-1.0")}
        self.redraws = 0
        self._event_bus = _FakeBus()
        # The gate accessors read the slice via ctx (post-R6 pure state); the
        # cell_gating change handler also reaches ctx.bus (plot_options_changed)
        # and ctx.runtime.cell_gating (the edit banks stamped on app._cell_gating_*).
        from well_viewer.core.app_runtime import AppRuntime
        from well_viewer.core.widget_registry import WidgetRegistry
        # A8/D-thresh: refresh_thresh_frac_quick_edit resolves the steppers +
        # status chips via ctx.widgets, so the double needs a real registry.
        # And the write fires the Cell Gating change handler through the
        # feature-service lookup rather than an import, so the double needs the
        # real feature registry too — a stub without one would make the write
        # silently stop short of the handler, which is the half these tests are
        # about.
        from well_viewer.features.registry import build_feature_registry
        self._ctx = SimpleNamespace(
            state=self._app_state, app=self, bus=self._event_bus,
            runtime=AppRuntime(self), widgets=WidgetRegistry(self),
            features=build_feature_registry(),
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
    import well_viewer.features.home.cell_gating.controller as cg
    monkeypatch.setattr(cg, "redraw_app", lambda ctx: app._redraw())
    set_thresh_frac_on(app._ctx, 123.0)       # active channel == gfp
    assert app._cell_gating_thresh_frac_edits["gfp"].text() == "123.0"
    assert get_thresh_frac_on(app._ctx) == 123.0
    assert get_thresh_frac_on(app._ctx, "mcherry") == 50.0   # untouched
    assert app.redraws == 1                    # change handler redrew once
    # The edit's signals were restored (not left blocked) after the write.
    assert app._cell_gating_thresh_frac_edits["gfp"].blocked is False


def test_set_targets_explicit_channel(monkeypatch):
    app = _FakeApp()
    import well_viewer.features.home.cell_gating.controller as cg
    monkeypatch.setattr(cg, "redraw_app", lambda ctx: app._redraw())
    set_thresh_frac_on(app._ctx, 77.0, channel="mcherry")
    assert get_thresh_frac_on(app._ctx, "mcherry") == 77.0
    assert get_thresh_frac_on(app._ctx, "gfp") == 50.0


def test_set_noop_without_dataset():
    app = _FakeApp()
    app._app_state.dataset.data_dir = ""
    set_thresh_frac_on(app._ctx, 999.0)
    assert get_thresh_frac_on(app._ctx, "gfp") == 50.0
    assert app.redraws == 0


def test_refresh_syncs_stepper_with_signals_blocked():
    app = _FakeApp()
    # R6: a committed value lives on the typed slice (setText alone is an
    # uncommitted keystroke — the change handler is what syncs it).
    app._app_state.cell_gating.thresh_frac_on["gfp"] = 88.0
    stepper = app._ctx.widgets.register("_plotting_thresh_frac_stepper", _FakeStepper(0.0))
    refresh_thresh_frac_quick_edit(app._ctx)
    assert stepper.value() == 88.0
    # Signals are restored to unblocked after the display sync.
    assert stepper.blocked is False


def test_refresh_follows_active_channel():
    app = _FakeApp()
    app._app_state.cell_gating.thresh_frac_on["gfp"] = 10.0
    app._app_state.cell_gating.thresh_frac_on["mcherry"] = 20.0
    app._ctx.widgets.register("_plotting_thresh_frac_stepper", _FakeStepper(0.0))

    refresh_thresh_frac_quick_edit(app._ctx)
    assert app._plotting_thresh_frac_stepper.value() == 10.0
    app._app_state.channel.active_channel = "mcherry"
    refresh_thresh_frac_quick_edit(app._ctx)
    assert app._plotting_thresh_frac_stepper.value() == 20.0


def test_refresh_noop_without_stepper():
    app = _FakeApp()                            # no _plotting_thresh_frac_stepper
    refresh_thresh_frac_quick_edit(app._ctx)         # must not raise


# ── retargeting onto the count cutoff ────────────────────────────────────────

def test_refresh_shows_the_count_cutoff_for_a_count_metric():
    """The negative default must survive the stepper's clamp.

    Pinned because the intensity range starts at 0: without widening it first,
    -1 ("every cell, zero-spot ones included") displays as 0 ("at least one
    spot") — a different population, silently.
    """
    app = _FakeApp()
    app._app_state.channel.active_metric = "smfish_count"
    app._app_state.cell_gating.thresh_frac_on["gfp"] = 88.0     # must NOT show
    stepper = app._ctx.widgets.register("_plotting_thresh_frac_stepper", _FakeStepper(0.0))
    refresh_thresh_frac_quick_edit(app._ctx)
    assert stepper.value() == -1.0
    assert stepper.minimum == -1.0
    assert stepper.decimals == 0


def test_refresh_relabels_the_caption_with_the_target():
    app = _FakeApp()
    stepper = app._ctx.widgets.register("_review_thresh_frac_stepper", _FakeStepper(0.0))
    lbl = app._ctx.widgets.register("_review_thresh_frac_lbl", _FakeLabel("ON threshold"))

    refresh_thresh_frac_quick_edit(app._ctx)
    assert lbl.text() == "ON threshold"

    app._app_state.channel.active_metric = "smfish_count"
    refresh_thresh_frac_quick_edit(app._ctx)
    assert lbl.text() == "ON spots"
    assert stepper.value() == -1.0


def test_write_follows_the_displayed_target(monkeypatch):
    """The bug this guards: display retargeted, write not.

    Typing 3 into a box captioned "ON spots" must set the count cutoff and leave
    the channel's intensity cutoff exactly where the user put it.
    """
    app = _FakeApp()
    import well_viewer.features.home.cell_gating.controller as cg
    monkeypatch.setattr(cg, "redraw_app", lambda ctx: app._redraw())
    # A committed intensity cutoff: the edit is the input surface and the slice
    # the source of truth, so a real one has both (the change handler syncs the
    # whole edit bank back into the slice on every commit).
    app._cell_gating_thresh_frac_edits["gfp"].setText("88.0")
    app._app_state.cell_gating.thresh_frac_on["gfp"] = 88.0
    app._app_state.channel.active_metric = "smfish_count"

    set_on_cutoff(app._ctx, 3.0)

    assert get_thresh_count_on(app._ctx, "gfp") == 3.0
    assert get_thresh_frac_on(app._ctx, "gfp") == 88.0


def test_write_still_targets_the_intensity_cutoff_for_an_intensity_metric(monkeypatch):
    app = _FakeApp()
    import well_viewer.features.home.cell_gating.controller as cg
    monkeypatch.setattr(cg, "redraw_app", lambda ctx: app._redraw())
    set_on_cutoff(app._ctx, 120.0)
    assert get_thresh_frac_on(app._ctx, "gfp") == 120.0
    assert get_thresh_count_on(app._ctx, "gfp") == -1.0


# ── real wiring on an offscreen WellViewerApp ────────────────────────────────

def test_ctxbar_stepper_exists(loaded_app):
    assert hasattr(loaded_app, "_plotting_thresh_frac_stepper")
    assert hasattr(loaded_app, "_plotting_thresh_frac_grp")
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
    set_active_channel(loaded_app._ctx, "mcherry")
    assert loaded_app._plotting_thresh_frac_stepper.value() == 77.0
    set_active_channel(loaded_app._ctx, "gfp")
    assert loaded_app._plotting_thresh_frac_stepper.value() == 123.0


def test_scope_controls_swap_for_the_view_toggle_on_scatter(loaded_app):
    """The Scatter sub-tab has per-axis channel and property selectors, so the
    band's global Channel / Property / Threshold groups do not drive its plot.

    They used to stay in the row greyed out, beside a note reading "Scatter uses
    per-axis channels below" — three dead controls plus an apology for them.
    The groups are now removed from the band while Scatter is current and the
    scatter View toggle takes their place, so the row only ever carries controls
    that do something. ``isHidden`` reflects the explicit visibility flag
    without needing the offscreen window to be shown.
    """
    assert loaded_app._notebook.setCurrentByName("Plotting")
    scope = [loaded_app._plotting_channel_grp,
             loaded_app._plotting_metric_grp,
             loaded_app._plotting_thresh_frac_grp]
    view = loaded_app._plotting_scatter_grp

    assert loaded_app._plotting_notebook.setCurrentByName("Scatter Plot")
    assert all(g.isHidden() for g in scope), (
        "a global scope control is still in the band on the Scatter sub-tab"
    )
    assert not view.isHidden(), "the scatter View toggle did not take their place"

    assert loaded_app._plotting_notebook.setCurrentByName("Bar Plots")
    assert not any(g.isHidden() for g in scope)
    assert view.isHidden()


def test_scope_controls_leave_the_band_on_response_curves(loaded_app):
    """Response Curves owns its Channel and Property, so the band hides its own.

    It has to own them: its Property list carries `Fraction above threshold`,
    which is not a column and which the shared combo's consumers resolve to
    ``f"{channel}_{metric}"`` and cannot read. What shipped was both pairs at
    once — two Channel dropdowns and two Property dropdowns in one row, of
    which only the tab's moved the figure, above an ON-cutoff box editing the
    cutoff of whatever channel the *band* held rather than the one the curves
    were measuring.

    Driven through the real notebook rather than by reading the roster, because
    a roster entry that nothing consults is exactly the shape of the bug: the
    assertion is on the widgets the user is looking at.
    """
    assert loaded_app._notebook.setCurrentByName("Plotting")
    scope = [loaded_app._plotting_channel_grp,
             loaded_app._plotting_metric_grp,
             loaded_app._plotting_thresh_frac_grp]

    assert loaded_app._plotting_notebook.setCurrentByName("Response Curves")
    assert all(g.isHidden() for g in scope), (
        "the band still carries its global Channel / Property / ON-cutoff on "
        "Response Curves, beside the tab's own pair"
    )
    # The scatter View toggle is about scatter, not about owning a channel, so
    # it does not take their place here.
    assert loaded_app._plotting_scatter_grp.isHidden()
    # The tab's own pair is what is left on the row, and it is real.
    assert loaded_app._ctx.widgets.get_or_none("_response_ch_cb") is not None
    assert loaded_app._ctx.widgets.get_or_none("_response_metric_cb") is not None

    assert loaded_app._plotting_notebook.setCurrentByName("Bar Plots")
    assert not any(g.isHidden() for g in scope), (
        "the globals did not come back on a tab that shares them")


def test_view_toggle_drives_the_scatter_mode(loaded_app):
    """The toggle is in the shared band, in core; the Scatter page is a feature.
    They are wired through ``ctx.runtime.scatter.set_mode`` so the core never
    imports the feature — check the round trip actually lands."""
    assert loaded_app._notebook.setCurrentByName("Plotting")
    assert loaded_app._plotting_notebook.setCurrentByName("Scatter Plot")
    toggle = loaded_app._plotting_scatter_view
    pages = loaded_app._scatter_pages

    toggle.setCurrentIndex(1)
    assert loaded_app._ctx.state.scatter.mode == "aggregate"
    assert pages.currentName() == "aggregate"

    toggle.setCurrentIndex(0)
    assert loaded_app._ctx.state.scatter.mode == "cells"
    assert pages.currentName() == "cells"


def test_channel_and_property_combos_fold_their_own_label(loaded_app):
    """The band's Channel / Property combos paint their own "Channel: …" /
    "Property: …" prefix (``widgets.labeled_combo.LabeledComboBox``) rather
    than sitting beside a separate ``QLabel`` — the compact, one-widget-per-
    control layout every other Review tab's control band already uses.

    Regression: before this, ``ControlBand.add_scope("Channel", combo)``
    wrapped the combo with a ``QLabel`` in a ``ControlBandGroup`` container,
    so the row read as two widgets ("Channel" caption + dropdown) instead of
    one self-describing dropdown.
    """
    from PySide6.QtWidgets import QLabel
    from widgets.labeled_combo import LabeledComboBox

    assert loaded_app._notebook.setCurrentByName("Plotting")

    chan_cb = loaded_app._plotting_channel_cb
    metric_cb = loaded_app._plotting_metric_cb
    assert isinstance(chan_cb, LabeledComboBox)
    assert isinstance(metric_cb, LabeledComboBox)
    assert chan_cb.label() == "Channel"
    assert metric_cb.label() == "Property"

    # Added straight into the band's scope host, not wrapped in a
    # ControlBandGroup + caption QLabel.
    assert chan_cb.objectName() != "ControlBandGroup"
    assert metric_cb.objectName() != "ControlBandGroup"
    scope_host = loaded_app._plotting_band.scope_host
    caption_texts = {lbl.text() for lbl in scope_host.findChildren(QLabel)}
    assert "Channel" not in caption_texts
    assert "Property" not in caption_texts


def test_a_channel_without_counts_keeps_the_intensity_cutoff(monkeypatch):
    """The Image Table picks its own channel; the Property is app-wide.

    With a count Property selected but the Image Table pointed at a channel that
    has no spot-count column, retargeting would caption the box "ON spots", show
    a value that channel cannot store, and drop the edit — the Cell Gating tab
    only builds a count field for smFISH channels, so the write would find no
    edit and return. Such a channel stays on the cutoff it actually has.
    """
    from well_viewer.core.channel_state_controller import quick_edit_cutoff_is_count
    import well_viewer.features.home.cell_gating.controller as cg
    monkeypatch.setattr(cg, "redraw_app", lambda ctx: app._redraw())

    app = _FakeApp()
    app._app_state.channel.active_metric = "smfish_count"

    assert quick_edit_cutoff_is_count(app._ctx, "gfp") is True
    assert quick_edit_cutoff_is_count(app._ctx, "mcherry") is False

    set_on_cutoff(app._ctx, 42.0, "mcherry")
    assert get_thresh_frac_on(app._ctx, "mcherry") == 42.0
    assert get_thresh_count_on(app._ctx, "mcherry") == -1.0
