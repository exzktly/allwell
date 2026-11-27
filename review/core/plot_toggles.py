"""Shared plot-toggle behaviour (SEM/SD spread, per-FOV replicates, FOV button
state).

These three helpers drive app-global plot controls: the SEM/SD error-band
toggle, the per-FOV replicate toggle, and the FOV button-bank refresh. They were
previously scattered across the lineplot / barplot / preview feature modules,
which meant a non-plot feature that wanted to drive the same toggles (the
batch-export inline Error-Band row) had to reach through the ``ReviewApp``
god-object — a direct ``from review.features...`` call would have been a
banned cross-feature import.

Relocated here (A8 enabler E3) so any feature can call them through ``ctx`` with
a plain ``core.`` import. The bodies are ctx-native: they read/write typed plot
state (``ctx.state.plot``) and the shared button banks (``ctx.runtime.sem`` /
``ctx.runtime.fov``). The only feature-specific wiring — the bar-layout button
refresh — is reached through a ``core.lazy`` proxy (a call, not an ``import``
statement), so this core module imports no feature at load time and the bar
feature stays independently deletable. The plots themselves are repainted
through the scope registry rather than by naming any tab.
"""

from __future__ import annotations

from review.data.metric_labels import spread_label

from review.core.plot_orchestrator import redraw_every_scope
from review.core.selection_controller import invalidate_threshold_results
from core.safe import swallowed
from review.core.lazy import lazy as _lazy

# Feature-specific wiring, bound as a lazy proxy so importing this module never
# imports the bar feature (a ``lazy(...)`` binding is a call, not an import
# statement — see core/lazy.py). The bar *redraw* used to be bound here too and
# is not any more: it reached the plots through a hardcoded "if Bar Plots is
# visible", which ``plot_orchestrator.redraw_every_scope`` now does for every
# registered scope rather than for one named tab.
_refresh_bar_layout_btn_state = _lazy(
    "review.features.barplot.controller", "refresh_bar_layout_btn_state"
)


def _live_buttons(bank) -> list:
    """The still-alive buttons in *bank*, with the dead ones dropped from it.

    The SEM and FOV banks are append-only: ``ui_helpers`` and the batch-export
    ``base_panel`` append to them and nothing ever removes an entry. A
    batch-export panel that has since been closed leaves its buttons behind as
    Python wrappers around deleted C++ objects, and the first call on one raises
    ``RuntimeError: Internal C++ object already deleted``. Because the button
    loops were unguarded, that aborted the toggle part-way: the buttons after
    the dead one kept the previous label and variant while the state had already
    flipped, and in ``toggle_sem`` the repaint below never ran, so the
    plots kept the old error band too. The observer loops immediately beneath
    each button loop already guarded the identical case.

    Pruning rather than skipping keeps the bank from growing without bound
    across a session of opening and closing export panels.
    """
    live = []
    for btn in list(bank or []):
        try:
            btn.objectName()
        except RuntimeError:
            continue
        live.append(btn)
    if bank is not None and len(live) != len(bank):
        bank[:] = live
    return live


def _style_button(btn, fn) -> None:
    """Apply *fn* to *btn*, letting one dead widget not abort the whole bank."""
    try:
        fn(btn)
    except RuntimeError:
        swallowed()


def _notify_observers(bank, value) -> None:
    """Call each observer in *bank*, dropping the ones whose widget has died.

    The observer banks grow the same way the button banks do — every
    batch-export panel appends and nothing removes — and an observer closing
    over a deleted widget raises ``RuntimeError`` on every toggle for the rest
    of the session. Guarding without pruning meant the failure was survivable
    but permanent: a log line per stale observer per click, for ever. The
    button banks were pruned here and these were not, which is the half-sweep
    CLAUDE.md §7.5 #5 is about — the same unbounded growth, in the list
    immediately below the one that was fixed.

    Only a ``RuntimeError`` prunes. Any other exception is the observer's own
    bug, not a dead widget, and dropping it would silently unsubscribe a live
    listener; those are logged and the observer is kept.
    """
    live = []
    for obs in list(bank or []):
        try:
            obs(value)
        except RuntimeError:
            swallowed()
            continue
        except Exception:
            swallowed()  # was: pass (silent except)
        live.append(obs)
    if bank is not None and len(live) != len(bank):
        bank[:] = live


def register_fov_button(ctx, btn) -> None:
    """Put *btn* on the per-FOV spread bank and bring it into the shared state.

    The one way onto ``ctx.runtime.fov.btns``. *btn* must be a
    :class:`widgets.toggle_switch.ToggleSwitch`: the setting acts at once, so it is
    a switch (design language §2.5), and the bank sets every member's checked state
    from the shared one. Anything else is refused here, at the moment it is wired,
    rather than restyled into something it is not.
    """
    from widgets.toggle_switch import ToggleSwitch

    if not isinstance(btn, ToggleSwitch):
        raise TypeError(
            f"a per-FOV spread control must be a ToggleSwitch, not {type(btn).__name__}")
    if ctx.runtime.fov.btns is None:
        ctx.runtime.fov.btns = []
    ctx.runtime.fov.btns.append(btn)
    if not ctx.runtime.fov.btn:
        ctx.runtime.fov.btn = btn
    refresh_fov_btn_state(ctx)


def _set_quietly(widget, setter, value) -> None:
    """Call ``setter(value)`` on *widget* with its signals blocked: the bank mirrors a state that has already
    changed, and a mirrored control that fired its own change signal would flip it back."""
    blocked = widget.blockSignals(True)
    try:
        setter(value)
    finally:
        widget.blockSignals(blocked)


def refresh_fov_btn_state(ctx) -> None:
    """Set every per-FOV spread switch from the shared state."""
    is_on = bool(ctx.state.plot.use_fov_replicates)
    tooltip = (
        "Compute error bands across per-FOV means.\n"
        "With replicate sets active, FOVs are pooled across all wells in each set;\n"
        "otherwise FOVs are pooled within each well. Pairs with the SEM / SD choice."
    )

    def _apply_fov(btn) -> None:
        btn.setEnabled(True)
        btn.setToolTip(tooltip)
        _set_quietly(btn, btn.setChecked, is_on)

    for btn in _live_buttons(ctx.runtime.fov.btns):
        _style_button(btn, _apply_fov)
    _notify_observers(ctx.runtime.fov.observers, is_on)


def toggle_sem(ctx) -> None:
    invalidate_threshold_results(ctx)
    is_sem = ctx.state.plot.toggle_sem()

    def _apply_sem(control) -> None:
        _set_quietly(control, control.setCurrentByData, bool(is_sem))

    for control in _live_buttons(ctx.runtime.sem.btns):
        _style_button(control, _apply_sem)
    _notify_observers(ctx.runtime.sem.observers, is_sem)
    redraw_every_scope(ctx)


def make_spread_choice(ctx, parent):
    """The SEM / SD choice: a segmented control of the two (§2.5), on the shared bank so every copy agrees.
    Choosing the one not in force flips the shared state through :func:`toggle_sem`."""
    from widgets.segmented_control import SegmentedControl

    seg = SegmentedControl(parent)
    seg.addSegment(text=spread_label(True), data=True)
    seg.addSegment(text=spread_label(False), data=False)
    seg.setCurrentByData(bool(ctx.state.plot.use_sem))
    seg.setToolTip("The error band: the standard error of the mean, or the standard deviation.")

    def _chosen(_i: int) -> None:
        if seg.currentData() != bool(ctx.state.plot.use_sem):
            toggle_sem(ctx)
    seg.currentChanged.connect(_chosen)
    if ctx.runtime.sem.btns is None:
        ctx.runtime.sem.btns = []
    ctx.runtime.sem.btns.append(seg)
    if not ctx.runtime.sem.btn:
        ctx.runtime.sem.btn = seg
    return seg


def make_fov_switch(ctx, parent, text: str = "Spread across FOVs"):
    """The per-FOV spread switch, registered on the shared bank."""
    from widgets.toggle_switch import ToggleSwitch

    switch = ToggleSwitch(text, parent)
    switch.clicked.connect(lambda _=False: toggle_fov_replicates(ctx))
    register_fov_button(ctx, switch)
    return switch


def toggle_fov_replicates(ctx) -> None:
    ctx.state.plot.toggle_fov_replicates()
    invalidate_threshold_results(ctx)
    refresh_fov_btn_state(ctx)
    # Gated on the bar feature being *present*. This is an app-global button and
    # ``_refresh_bar_layout_btn_state`` is a lazy proxy into
    # ``features/barplot``: with that feature profiled out the call raised
    # ModuleNotFoundError *after* the state had already been flipped, so the
    # toggle both failed and took effect.
    if ctx.state.feature_slice("bar") is not None:
        _refresh_bar_layout_btn_state(ctx)
    redraw_every_scope(ctx)
