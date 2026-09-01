"""Shared plot-toggle behaviour (SEM/SD spread, per-FOV replicates, FOV button
state).

These three helpers drive app-global plot controls: the SEM/SD error-band
toggle, the per-FOV replicate toggle, and the FOV button-bank refresh. They were
previously scattered across the lineplot / barplot / preview feature modules,
which meant a non-plot feature that wanted to drive the same toggles (the
batch-export inline Error-Band row) had to reach through the ``WellViewerApp``
god-object — a direct ``from well_viewer.features...`` call would have been a
banned cross-feature import.

Relocated here (A8 enabler E3) so any feature can call them through ``ctx`` with
a plain ``core.`` import. The bodies are ctx-native: they read/write typed plot
state (``ctx.state.plot``) and the shared button banks (``ctx.runtime.sem`` /
``ctx.runtime.fov``). The only feature-specific wiring — the bar renderer redraw
and the bar-layout button refresh — is reached through ``core.lazy`` proxies (a
call, not an ``import`` statement), so this core module imports no feature at
load time and the bar feature stays independently deletable.
"""

from __future__ import annotations

from well_viewer.core.selection_controller import invalidate_stats_cache
from well_viewer.core.safe import swallowed
from well_viewer.core.lazy import lazy as _lazy

# Feature-specific wiring, bound as a lazy proxy so importing this module never
# imports the bar feature (a ``lazy(...)`` binding is a call, not an import
# statement — see core/lazy.py). Only the button-state refresh is left: the
# redraws go out on the bus now, so no toggle here names a tab.
_refresh_bar_layout_btn_state = _lazy(
    "well_viewer.features.barplot.controller", "refresh_bar_layout_btn_state"
)


#: Room the FOV toggle reserves beyond its widest label, in pixels: the QSS
#: padding on both sides plus the border. Written at the call site until the
#: pixel-literal ratchet reached this module; named here so the number has a
#: reason attached rather than an allowlist entry.
_FOV_BTN_SLACK_PX = 26


def _live_buttons(bank) -> list:
    """The still-alive buttons in *bank*, with the dead ones dropped from it.

    The SEM and FOV banks are append-only: ``ui_helpers`` and the batch-export
    ``base_panel`` append to them and nothing ever removes an entry. A
    batch-export panel that has since been closed leaves its buttons behind as
    Python wrappers around deleted C++ objects, and the first call on one raises
    ``RuntimeError: Internal C++ object already deleted``. Because the button
    loops were unguarded, that aborted the toggle part-way: the buttons after
    the dead one kept the previous label and variant while the state had already
    flipped, and in ``toggle_sem`` the redraw below never ran, so the plots kept
    the old error band too. The observer loops immediately beneath
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


def refresh_fov_btn_state(ctx) -> None:
    """Sync every per-FOV toggle button's text/variant state."""
    is_on = bool(ctx.state.plot.use_fov_replicates)
    text = "FOV ✓" if is_on else "FOV"
    variant = "toggle_active" if is_on else "toggle"
    tooltip = (
        "Compute error bands across per-FOV means.\n"
        "With replicate sets active, FOVs are pooled across all wells in each set;\n"
        "otherwise FOVs are pooled within each well. Pairs with the SEM/SD toggle."
    )
    from PySide6.QtGui import QFontMetrics

    def _apply_fov(btn) -> None:
        btn.setEnabled(True)
        btn.setText(text)
        btn.setToolTip(tooltip)
        btn.setProperty("variant", variant)
        # Reserve room for the wider checked-state label ("FOV ✓") so the ✓ glyph
        # never clips — the button can live inside a QMenu-backed "Display ▾"
        # popover whose size is fixed at first show and won't re-expand when the
        # text grows. Size for the widest label + QSS padding/border slack.
        fm = QFontMetrics(btn.font())
        btn.setMinimumWidth(fm.horizontalAdvance("FOV ✓") + _FOV_BTN_SLACK_PX)
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    for btn in _live_buttons(ctx.runtime.fov.btns):
        _style_button(btn, _apply_fov)
    _notify_observers(ctx.runtime.fov.observers, is_on)


def _announce_a_plot_option(ctx) -> None:
    """Tell every plot the error-band settings moved.

    Through the bus, which is what ``plot_options_changed`` is for: the
    ``EventBus`` docstring names "error bars" in that signal's own description,
    and these two toggles were the ones never emitting it.

    What they did instead was ``redraw_app`` plus a hardcoded branch for the Bar
    tab. ``redraw_app`` fans out over Line, Distribution and Heat Map only, so
    the SD/SEM button reached four tabs out of nine by name: flipping it on
    Response Curves, Scatter, Multivariate or Spatial changed
    ``ctx.state.plot.use_sem``, which those collectors do read, and nothing
    repainted — a control that looked dead because the figure in front of it
    never redrew. The same roster shape as the other tab-name list this file's
    neighbours have been bitten by, one layer up.

    ``RedrawCoordinator`` turns the emit into ``redraw_scopes_or_defer``, which
    draws every visible scope and marks the rest dirty for the next tab switch.
    That subsumes the Bar branch as well, and correctly: ``visible_scope_names``
    is Split-View-aware, which is the case that branch was added for. The
    default scope's own redraw *is* ``redraw_app``, so Line, Distribution and
    Heat Map still go through exactly the path they did before.
    """
    ctx.bus.plot_options_changed.emit(ctx.state.plot)


def toggle_sem(ctx) -> None:
    invalidate_stats_cache(ctx)
    is_sem = ctx.state.plot.toggle_sem()
    text = "SEM" if is_sem else "SD"
    variant = "sem" if is_sem else "sem_warn"

    def _apply_sem(btn) -> None:
        btn.setText(text)
        btn.setProperty("variant", variant)
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    for btn in _live_buttons(ctx.runtime.sem.btns):
        _style_button(btn, _apply_sem)
    _notify_observers(ctx.runtime.sem.observers, is_sem)
    _announce_a_plot_option(ctx)


def toggle_fov_replicates(ctx) -> None:
    ctx.state.plot.toggle_fov_replicates()
    invalidate_stats_cache(ctx)
    refresh_fov_btn_state(ctx)
    # Gated on the bar feature being *present*, which is a different question
    # from whether its tab is visible — the announcement below leaves that one
    # to the scope coalescer. This is an app-global button and
    # ``_refresh_bar_layout_btn_state`` is a lazy proxy into
    # ``features/barplot``: with that feature profiled out the call raised
    # ModuleNotFoundError *after* the state had already been flipped, so the
    # toggle both failed and took effect.
    if ctx.state.feature_slice("bar") is not None:
        _refresh_bar_layout_btn_state(ctx)
    _announce_a_plot_option(ctx)
