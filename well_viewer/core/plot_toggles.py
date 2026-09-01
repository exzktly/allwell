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
``ctx.runtime.fov``). The only feature-specific wiring — the bar-layout button
refresh — is reached through a ``core.lazy`` proxy (a call, not an ``import``
statement), so this core module imports no feature at load time and the bar
feature stays independently deletable. The plots themselves are repainted
through the scope registry rather than by naming any tab.
"""

from __future__ import annotations

from well_viewer.core.plot_orchestrator import redraw_app
from well_viewer.core.selection_controller import invalidate_stats_cache
from well_viewer.core.safe import swallowed
from well_viewer.core.lazy import lazy as _lazy

# Feature-specific wiring, bound as a lazy proxy so importing this module never
# imports the bar feature (a ``lazy(...)`` binding is a call, not an import
# statement — see core/lazy.py). The bar *redraw* used to be bound here too and
# is not any more: it reached the plots through a hardcoded "if Bar Plots is
# visible", which the scope fan-out in ``_redraw_every_scope`` now does for
# every registered scope rather than for one named tab.
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
    flipped, and in ``toggle_sem`` the ``redraw_app`` below never ran, so the
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


def _redraw_every_scope(ctx) -> None:
    """Repaint the plots after an error-band or replicate-unit change.

    Both toggles change the number every plot draws, so every plot has to be
    told. ``redraw_app`` covers the three scopes that share it — Line,
    Distribution and Heat Map — and the rest go through the scope coalescer:
    the visible ones now, the others marked dirty and flushed the moment their
    tab is next shown, which is the contract every other cross-tab setting
    already follows.

    That fan-out replaces a hardcoded "and redraw Bar Plots if it is visible".
    A named tab is a roster of one, and it was written when Bar was the only
    other scope: Response Curves is a registered scope too, and toggling
    SEM/SD or Spread: FOV with it on screen changed the state, repainted three
    figures the user was not looking at, and left the curves in front of them
    drawn to the previous error band — until they left the tab and came back,
    which is what ``on_activate`` was quietly doing for them.

    ``skip`` is what keeps the fan-out from redrawing Line, Distribution and
    Heat Map a second time, since ``redraw_app`` has already drawn them.
    """
    from well_viewer.core.scope_registry import redraw_app_scope
    from well_viewer.domain.fold_change_scopes import redraw_scopes_or_defer

    redraw_app(ctx)
    redraw_scopes_or_defer(ctx.window, skip=redraw_app_scope)


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
    _redraw_every_scope(ctx)


def toggle_fov_replicates(ctx) -> None:
    ctx.state.plot.toggle_fov_replicates()
    invalidate_stats_cache(ctx)
    refresh_fov_btn_state(ctx)
    # Gated on the bar feature being *present*. This is an app-global button and
    # ``_refresh_bar_layout_btn_state`` is a lazy proxy into
    # ``features/barplot``: with that feature profiled out the call raised
    # ModuleNotFoundError *after* the state had already been flipped, so the
    # toggle both failed and took effect.
    if ctx.state.feature_slice("bar") is not None:
        _refresh_bar_layout_btn_state(ctx)
    _redraw_every_scope(ctx)
