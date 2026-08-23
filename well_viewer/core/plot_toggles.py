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

from well_viewer.core.plot_orchestrator import redraw_app
from well_viewer.core.tab_controller import is_tab_visible
from well_viewer.core.selection_controller import invalidate_stats_cache
from well_viewer.core.safe import swallowed
from well_viewer.core.lazy import lazy as _lazy

# Feature-specific wiring, bound as lazy proxies so importing this module never
# imports the bar feature (a ``lazy(...)`` binding is a call, not an import
# statement — see core/lazy.py).
_redraw_bars = _lazy("well_viewer.features.barplot.renderer", "redraw_bars")
_refresh_bar_layout_btn_state = _lazy(
    "well_viewer.features.barplot.controller", "refresh_bar_layout_btn_state"
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
        btn.setMinimumWidth(fm.horizontalAdvance("FOV ✓") + 26)
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    for btn in _live_buttons(ctx.runtime.fov.btns):
        _style_button(btn, _apply_fov)
    for obs in list(ctx.runtime.fov.observers or []):
        try:
            obs(is_on)
        except Exception:
            swallowed()  # was: pass (silent except)


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
    for obs in list(ctx.runtime.sem.observers or []):
        try:
            obs(is_sem)
        except Exception:
            swallowed()  # was: pass (silent except)
    redraw_app(ctx)
    # Redraw the bar pane too whenever it is on screen — in Split View it can be
    # visible alongside another pane without being the focused tab.
    if is_tab_visible(ctx, "Bar Plots"):
        _redraw_bars(ctx)


def toggle_fov_replicates(ctx) -> None:
    ctx.state.plot.toggle_fov_replicates()
    invalidate_stats_cache(ctx)
    refresh_fov_btn_state(ctx)
    # Gated on the bar feature being *present*. (``_redraw_bars`` below is
    # gated too, but on the tab being *visible* — a different question that
    # happens to imply this one.) This is an app-global button and
    # ``_refresh_bar_layout_btn_state`` is a lazy proxy into
    # ``features/barplot``: with that feature profiled out the call raised
    # ModuleNotFoundError *after* the state had already been flipped, so the
    # toggle both failed and took effect.
    if ctx.state.feature_slice("bar") is not None:
        _refresh_bar_layout_btn_state(ctx)
    redraw_app(ctx)
    # Split View: redraw the bar pane whenever it is on screen, even if unfocused.
    if is_tab_visible(ctx, "Bar Plots"):
        _redraw_bars(ctx)
